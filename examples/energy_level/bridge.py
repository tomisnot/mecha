"""V2Bridge：GUI ↔ v2 Gate 的操作者适配器（EL 消费配置，Phase 5 第 2 步）。

让 EL 的 GUI（`_mecha_bind.Binder` + `app_entry`）经**同一道 v2 写入门**读写域
状态——与 AI（MCP Hub）共享**一个 Gate、一份 History、一份真值**。这是"人机同
一道门"（L1）在真项目里落地的那块砖。

接口对齐 v1 的 AppBridge（Binder 零改动即可复用）：
``read / write / subscribe / can_write / mode / switch_mode / save / load /
cache / session``。actor 字符串 → 通道的映射在**这里**（适配器层）做，库不认
'gui'/'mcp' 这些宿主词。

## Known Limitations and Deferred Work

- actor→通道映射表写死在此（gui/human→human 通道；ai/mcp/codeact/job→ai 通道）；
  新操作者类别出现时改这张表，不改库。
- ``session.set`` 兼容垫片只服务启动种子（面板初值回填，不经写权门）；正常写
  一律走 write→Gate。种子用 actor='session' 记史，归因上区别于 human/ai。
- save/load 的 JSON 格式是本适配器的读档格式（快照 dict），非 v1 的
  'mecha-session-v1'——不兼容读 v1 旧存档（迁移是另一件事，需 ADR）。
- 线程：Gate 写口用一把锁串行（GUI 主线程 + Hub 线程并发写）；读快照无锁
  （CPython dict 读原子性足够展示用）。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from mecha.authority import Authority, Mode
from mecha.data_layout import DataLayout
from mecha.errors import GateDenied, MechaError
from mecha.gate import Channel, Gate
from mecha.history import Event, History
from mecha.providers.log_local import JsonlLogSink


def make_jsonl_history(layout: DataLayout):
    """建一份从磁盘 journal 续写的 History（Hub/GUI 各自进程独立续）。"""
    sink = JsonlLogSink(layout)
    return History(sink), sink

# actor 字符串 → 通道。宿主词表在这层，不在库。
_ACTOR_CHANNEL: dict[str, str] = {
    "gui": "human", "human": "human",
    "ai": "ai", "mcp": "ai", "codeact": "ai", "job": "ai",
}


class _SessionShim:
    """兼容 v1 的 bridge.session.set（仅启动种子用）。"""

    def __init__(self, bridge: "V2Bridge") -> None:
        self._b = bridge

    def set(self, key, value, *, actor="session", reason=""):
        self._b._seed(key, value, actor=actor, reason=reason)


class V2Bridge:
    """GUI 适配器：把 AppBridge 接口翻译成 v2 Gate/History/Authority 操作。"""

    def __init__(self, gate: Gate, history: History, authority: Authority,
                 *, cache=None) -> None:
        self._gate = gate
        self._history = history
        self._authority = authority
        self._lock = threading.Lock()
        self.cache = cache
        self.session = _SessionShim(self)

    # ---- 写（经门）----

    def write(self, key, value, *, reason="", actor="gui", call_id=""):
        chan = self._channel_for(actor)
        with self._lock:
            return self._gate.set(chan, key, value, reason=reason, call_id=call_id)

    def write_many(self, items, *, reason="", actor="gui", call_id=""):
        """原子批量写（GUI 运行前同步配置用）。任一键非法 ⇒ 一个都不写。"""
        chan = self._channel_for(actor)
        with self._lock:
            return self._gate.set_batch(chan, dict(items), reason=reason, call_id=call_id)

    def _seed(self, key, value, *, actor="session", reason=""):
        """启动种子：经 Gate.seed 落快照 + 记史，不走写权门（面板初值回填）。"""
        with self._lock:
            return self._gate.seed(key, value, actor, reason=reason or "seed")

    @staticmethod
    def _channel_for(actor: str) -> Channel:
        side = _ACTOR_CHANNEL.get(str(actor).split(":")[0])
        if side is None:
            raise GateDenied(
                f"未知 actor {actor!r}", kind="unknown_actor",
                hint="actor 必须映射到 human/ai 通道",
                suggest=f"已知 actor：{sorted(_ACTOR_CHANNEL)}",
            )
        return Channel(side)

    # ---- 读 ----

    def read(self, key):
        return self._gate.snapshot.get(key)

    @property
    def snapshot(self) -> dict:
        return self._gate.snapshot

    def can_write(self, actor: str) -> bool:
        side = _ACTOR_CHANNEL.get(str(actor).split(":")[0])
        if side is None:
            return False
        try:
            self._authority.gate(side)
            return True
        except GateDenied:
            return False

    @property
    def mode(self) -> str:
        return self._authority.mode.value

    def switch_mode(self, new_mode: str, *, actor="human"):
        self._authority.switch_mode(Mode(new_mode), actor=actor)

    # ---- 订阅（GUI 面板刷新）----

    def subscribe(self, callback):
        """把 v2 Event 适配成 v1 事件 dict（{kind,actor,target,value,seq,call_id}）。"""
        def _ev(e: Event) -> None:
            callback({"kind": "set", "actor": e.actor, "target": e.key,
                      "value": e.value, "seq": e.seq, "call_id": e.call_id})
        return self._history.subscribe(_ev)

    # ---- 存档（原子批量：真实调用方，兑现 set_batch 的存在理由）----

    def save(self, path, *, reason="GUI 保存状态"):
        Path(path).write_text(
            json.dumps(self._gate.snapshot, ensure_ascii=False, default=str),
            encoding="utf-8")
        return path

    def load(self, path, *, reason="GUI 恢复状态", actor="gui"):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise MechaError("存档格式非法（顶层须是对象）", kind="bad_archive",
                             hint="save 写出的是 {键:值} 快照")
        chan = self._channel_for(actor)
        with self._lock:
            self._gate.set_batch(chan, data, reason=reason)   # 原子：坏一键全不写
        return len(data)
