"""History：域状态史（总纲 §5.2②，L2）。

append-only：只追加、永不改写；seq 单调递增；可从盘上续。它是软件自己的
资产，与操作者无关——人操作时也写，必须能在 dsh 消失后独立工作。
每次 set 带当次操作的 call_id，与 dsh 的操作者行为史**互引**（不是第二真值）。

折叠函数 ``fold(events) -> snapshot`` 是纯函数；启动时按日志重建当前快照。

## Known Limitations and Deferred Work

- 落盘通过注入的 ``LogSink``（Provider 层，Phase 3 提供 local JSONL 与 dsh 两
  个实现）。本模块只保证内存语义 + 从 sink 重建；不自己碰文件系统。
- ``fold`` 的语义是"最后一个 set 的 value 即当前值"（无 merge、无删除事件）。
  需要"删除键"时引入 tombstone 事件，届时写 ADR 并 bump FORMAT_VERSION。
- seq 从 sink 的 last_seq 续，进程内假定单写者；多进程写同一 sink 需外部串行
  （当前 n=1，无此需求）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .errors import MechaError


@dataclass(frozen=True)
class Event:
    """一条域状态史事件（append-only 的单位）。"""

    seq: int
    key: str
    value: object
    actor: str
    reason: str
    call_id: str = ""

    def to_record(self) -> dict:
        return {
            "seq": self.seq,
            "key": self.key,
            "value": self.value,
            "actor": self.actor,
            "reason": self.reason,
            "call_id": self.call_id,
        }

    @staticmethod
    def from_record(rec: dict) -> "Event":
        return Event(
            seq=rec["seq"], key=rec["key"], value=rec["value"],
            actor=rec["actor"], reason=rec["reason"], call_id=rec.get("call_id", ""),
        )


@runtime_checkable
class LogSink(Protocol):
    """落盘 seam（Definition）。Phase 3 提供 local JSONL / dsh 两个 Provider。"""

    def append(self, record: dict) -> None: ...
    def since(self, seq: int) -> Iterable[dict]: ...
    def last_seq(self) -> int: ...


def fold(events: Iterable[Event]) -> dict[str, object]:
    """纯函数：从事件史折叠出当前快照。后写覆盖先写。"""
    snapshot: dict[str, object] = {}
    for e in events:
        snapshot[e.key] = e.value
    return snapshot


class History:
    """域状态史句柄。append 是唯一写口，且只追加。"""

    def __init__(self, sink: LogSink | None = None) -> None:
        self._sink = sink
        self._events: list[Event] = []
        self._seq = 0
        self._subs: list[Callable[[Event], None]] = []
        if sink is not None:
            self._resume(sink)

    def _resume(self, sink: LogSink) -> None:
        # 从盘上续：重放已有事件，seq 接在最后一条之后。
        existing = [Event.from_record(r) for r in sink.since(0)]
        self._events.extend(existing)
        self._seq = sink.last_seq()

    def append(self, key: str, value: object, actor: str, reason: str,
               call_id: str = "") -> Event:
        if not key.strip():
            raise MechaError("事件 key 不能为空", kind="bad_event",
                             hint="append 必须带非空 key")
        self._seq += 1
        ev = Event(self._seq, key, value, actor, reason, call_id)
        self._events.append(ev)
        if self._sink is not None:
            self._sink.append(ev.to_record())
        for cb in list(self._subs):        # 快照迭代：回调里可退订
            try:
                cb(ev)
            except Exception:              # noqa: BLE001 - 一个坏订阅者不拖垮写口
                pass
        return ev

    def subscribe(self, callback: Callable[[Event], None]) -> Callable[[], None]:
        """订阅新事件（GUI 面板刷新等真实消费者）。返回 disposer（可逆副作用）。

        同步回调：在 append 之后、写口返回之前逐个调用；回调抛错被吞
        （坏订阅者不得影响权威史）。回调里若再写门（set）会递归——
        订阅者应是只读消费者（GUI 刷新、监控投影），写回路径归 Gate。
        """
        self._subs.append(callback)

        def dispose() -> None:
            try:
                self._subs.remove(callback)
            except ValueError:
                pass

        return dispose

    @property
    def last_seq(self) -> int:
        return self._seq

    def events(self) -> list[Event]:
        """只读副本（append-only：外部拿不到可改的内部列表）。"""
        return list(self._events)

    def snapshot(self) -> dict[str, object]:
        return fold(self._events)
