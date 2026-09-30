"""History：账（总纲 §5.2②，L2）。

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

import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from .errors import MechaError


@dataclass(frozen=True)
class Event:
    """一条账事件（append-only 的单位）。"""

    seq: int
    #: "state"（改域状态）| "history"（只记史：审计/编排日志）
    kind: str
    #: 动作名；state 事件固定 "set"
    op: str
    #: 作用对象；state 事件 = 键名
    target: str
    #: 后像；state 事件 = 新值
    after: object
    #: 前像；state 事件 = 旧值（键此前不存在 ⇒ None）
    before: object = None
    actor: str = ""
    reason: str = ""
    call_id: str = ""
    #: **墙钟时间**（ISO 8601 UTC；2026-09-26 加）。⚠ 老记录（无此字段）读回 **`""`**
    #: ＝"早于本特性"，不是"时间未知的错"。⛔ **不许用 `ts` 排序/去重**：`seq` 仍是
    #: **唯一顺序**（同一秒多条、时钟回拨都可能）——`ts` 只给人看。
    ts: str = ""
    #: **能不能撤**（项目声明的**领域知识**；2026-09-26 加）：`True`/`False`/**`None`＝未声明**。
    #: ⚠ 框架**只给槽位**：撤销需要"旧值 + 领域规则 + 冲突处理"，三样都归项目。
    #: ⚠ **三态是刻意的**：用布尔会把"**没声明**"冒充成"不可撤"。
    undoable: bool | None = None

    def to_record(self) -> dict:
        return {
            "seq": self.seq, "kind": self.kind, "op": self.op, "target": self.target,
            "after": self.after, "before": self.before, "actor": self.actor,
            "reason": self.reason, "call_id": self.call_id, "ts": self.ts,
            "undoable": self.undoable,
        }

    @staticmethod
    def from_record(rec: dict) -> "Event":
        """从盘上记录还原。⚠ **老记录缺 `ts`/`undoable` 也要能读**（零迁移，见 ADR）。"""
        if "kind" in rec:
            return Event(
                seq=rec["seq"], kind=str(rec["kind"]), op=str(rec.get("op", "")),
                target=str(rec.get("target", "")), after=rec.get("after"),
                before=rec.get("before"), actor=rec.get("actor", ""),
                reason=rec.get("reason", ""), call_id=rec.get("call_id", ""),
                ts=str(rec.get("ts", "") or ""), undoable=rec.get("undoable", None),
            )
        # **老记录零迁移**（照 ts/undoable 的先例，推理**只有这一处**）：
        #   key 以 "command." 开头 ⇒ 审计 ⇒ kind="history"
        #   否则                  ⇒ kind="state"
        key = str(rec["key"])
        if key.startswith("command."):
            return Event(seq=rec["seq"], kind="history", op=key, target="", after=None,
                         before=None, actor=rec.get("actor", ""),
                         reason=rec.get("reason", ""), call_id=rec.get("call_id", ""),
                         ts=str(rec.get("ts", "") or ""),
                         undoable=rec.get("undoable", None))
        return Event(seq=rec["seq"], kind="state", op="set", target=key,
                     after=rec.get("value"), before=None, actor=rec.get("actor", ""),
                     reason=rec.get("reason", ""), call_id=rec.get("call_id", ""),
                     ts=str(rec.get("ts", "") or ""), undoable=rec.get("undoable", None))


@runtime_checkable
class LogSink(Protocol):
    """落盘 seam（Definition）。Phase 3 提供 local JSONL / dsh 两个 Provider。

    **可选能力** ``precheck(record) -> None``：**落地之前**的可预见失败预检
    （如"这个值不可序列化"），失败即抛。**缺失视为"没有预检"**（与 ``close``
    同款的**可选**能力，经 ``getattr`` 发现——本文件已有此先例）。

    ⚠ 为什么预检归 sink：写口要做到"**全有或全无**"，而"这个值能不能落盘"的
    **知识只有 sink 有**（格式归它）⇒ 由它回答，Gate 在**落地之前**问。
    """

    def append(self, record: dict) -> None: ...
    def since(self, seq: int, *, actor: str | None = None,
              op: str | None = None, limit: int | None = None) -> Iterable[dict]: ...
    def last_seq(self) -> int: ...


def fold(events: Iterable[Event]) -> dict[str, object]:
    """纯函数：从事件史折叠出当前快照。后写覆盖先写。"""
    snapshot: dict[str, object] = {}
    for e in events:
        if e.kind != "state":       # 只折域状态；审计/编排日志不是状态（本轮的 bug 就在这一行）
            continue
        snapshot[e.target] = e.after
    return snapshot


class History:
    """账句柄。append 是唯一写口，且只追加。"""

    def __init__(self, sink: LogSink | None = None) -> None:
        self._sink = sink
        self._events: list[Event] = []
        self._seq = 0
        self._subs: list[Callable[[Event], None]] = []
        # 同一进程内的写口互斥：Job Contract 让命令在后台线程里跑，没有这把锁
        # 两个线程会各自 ++seq，破坏"seq 单调"不变量（跨进程仍靠写租约）。
        self._lock = threading.RLock()
        if sink is not None:
            self._resume(sink)

    def _resume(self, sink: LogSink) -> None:
        # 从盘上续：重放已有事件，seq 接在最后一条之后。
        existing = [Event.from_record(r) for r in sink.since(0)]
        self._events.extend(existing)
        self._seq = sink.last_seq()

    def append_history(self, op: str, target: str = "", after: object = None,
                       before: object = None, actor: str = "", reason: str = "",
                       call_id: str = "", *, ts: str | None = None,
                       undoable: bool | None = None) -> Event:
        """**只记史**（审计 / 编排日志）：`fold` 不折它 ⇒ **永不进快照**。"""
        if not op.strip():
            raise MechaError("事件 op 不能为空", kind="bad_event",
                             hint="append_history 必须带非空 op")
        when = ts if ts is not None else datetime.now(timezone.utc).isoformat()
        with self._lock:
            seq = self._seq + 1
            ev = Event(seq, "history", op, target, after, before, actor, reason,
                       call_id, when, undoable)
            if self._sink is not None:
                self._sink.append(ev.to_record())
            self._seq = seq
            self._events.append(ev)
            subs = list(self._subs)
        for cb in subs:
            try:
                cb(ev)
            except Exception:              # noqa: BLE001 - 一个坏订阅者不拖垮写口
                pass
        return ev

    def precheck(self, key: str, value: object) -> None:
        """**落地之前**的可预见失败预检：委托 sink 的可选 ``precheck``（缺失即跳过）。

        ⚠ 为什么要有它：写口要"**全有或全无**"，而"这个值能不能落盘"的知识**只有 sink
        有**（格式归它，如 JSONL 的 ``json.dumps``）⇒ 让 sink 自己回答，Gate 在**落地
        之前**问一句，于是"可预见的失败"全都发生在**一个字节都没改**的时候。
        """
        if self._sink is None:
            return
        check = getattr(self._sink, "precheck", None)     # 可选能力（同 close 的先例）
        if check is None:
            return
        # ⚠ 这个伪事件**只在 state 路径被调**（gate.set / gate.set_batch）：
        # 它给 sink 做"能不能编码"的预检，与 history 路径无关。
        check(Event(0, "state", "set", key, value).to_record())

    def append_state(self, target: str, after: object, before: object = None,
                     actor: str = "", reason: str = "", call_id: str = "",
                     *, ts: str | None = None, undoable: bool | None = None) -> Event:
        """**改域状态**（唯一会被 `fold` 折进快照的那类事件）。"""
        """追加一条事件。``ts`` 缺省**现取墙钟**（UTC ISO；给人看，不参与排序）；
        ``undoable`` 是**项目声明的槽位**（`None`＝未声明），框架不据此实现撤销。"""
        if not target.strip():
            raise MechaError("事件 target 不能为空", kind="bad_event",
                             hint="append_state 必须带非空 target")
        when = ts if ts is not None else datetime.now(timezone.utc).isoformat()
        with self._lock:
            seq = self._seq + 1
            ev = Event(seq, "state", "set", target, after, before, actor, reason,
                       call_id, when, undoable)
            # ⚠ **先落盘、后推进内存**（2026-09-26 起）：反过来的话，sink 抛错时内存里
            # 已经多了一条**盘上没有**的事件 ⇒ `events()` 与 journal 不一致，
            # 而"快照必须能从史折叠出来"这条不变量正是建在这上面。
            if self._sink is not None:
                self._sink.append(ev.to_record())
            self._seq = seq
            self._events.append(ev)
            subs = list(self._subs)      # 快照迭代：回调里可退订
        for cb in subs:
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
        with self._lock:
            return self._seq

    def events(self) -> list[Event]:
        """只读副本（append-only：外部拿不到可改的内部列表）。"""
        with self._lock:
            return list(self._events)

    def close(self) -> None:
        """关停：释放 sink 的写租约（若有）。宿主关停/租约交接用；幂等。"""
        closer = getattr(self._sink, "close", None)
        if callable(closer):
            closer()

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return fold(self._events)
