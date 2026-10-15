"""Surface：项目查询与注册项的分派面。

mecha 不规定项目必须拥有计算引擎、流水线或统一的 ``run``。项目把查询、
生命周期钩子和其它可组合能力注册到这里；受治理的写动作注册到同一对象的
``commands``。取消与进度使用通用 ``ExecutionContext``，长任务交给 Jobs。
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum

from .commands import CommandRegistry
from .errors import MechaError, UnknownOp
from .roles import Disposer, Role, RoleRegistry

@dataclass
class ExecutionContext:
    """run 的执行上下文：取消与进度是显式字段（消 D-16 三槽约定）。"""

    cancel_event: threading.Event = field(default_factory=threading.Event)
    _progress: list[tuple[float, str]] = field(default_factory=list)

    def request_cancel(self) -> None:
        self.cancel_event.set()

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def report_progress(self, frac: float, stage: str = "") -> None:
        if not 0.0 <= frac <= 1.0:
            raise MechaError(
                f"进度分数越界：{frac!r}",
                kind="bad_progress",
                hint="report_progress(frac, stage) 的 frac 必须在 [0, 1]",
            )
        self._progress.append((frac, stage))

    def progress(self) -> list[tuple[float, str]]:
        return list(self._progress)


class EntryKind(str, Enum):
    """注册项的三种角色（取代 v1"钩子"混合概念，消 D-18）。"""

    QUERY = "query"           # 只读查询：框架经 Surface.query() 分派
    HOOK = "hook"             # 生命周期钩子：框架在固定挂点调用
    CUSTOM_TYPE = "custom_type"  # 类型声明：schema 投影时消费


@dataclass(frozen=True)
class RegisteredEntry:
    kind: EntryKind
    name: str
    fn: Callable
    #: 谁调它——注册时必须声明（框架 / 宿主 / 消费者），spec 里不再含糊
    called_by: str


class Surface:
    """项目查询和生命周期注册项的分派面。

    计算引擎、流水线和其它长操作属于项目；它们以命令或查询注册，
    不再被框架要求实现一个通用 ``Engine``。
    """

    def __init__(self, roles: RoleRegistry | None = None) -> None:
        self._roles = roles or RoleRegistry()
        self._entries: dict[tuple[EntryKind, str], RegisteredEntry] = {}
        # 通用命令面与查询面共用一个注册表。
        # 角色表共用装配点那张 ⇒ command:<name> 能力行与声明表同源。
        self._commands = CommandRegistry(roles=self._roles, caller="Surface")
        self._roles.register("ops_pipeline", Role.CONSUMER, consumer="Surface")

    @property
    def roles(self) -> RoleRegistry:
        return self._roles

    @property
    def commands(self) -> CommandRegistry:
        """命令面入口：``surface.commands.register(spec, handler) → disposer``。

        与 QUERY 注册项并列（注册项三角色不变；命令是分派目标，不是 Definition
        的第 5 个必实现）。只读命令经 ``query`` 走，有副作用的命令在这里注册。
        """
        return self._commands

    # ---- 注册项（register → disposer，dsh AGENTS.md:106）----

    def register(self, kind: EntryKind | str, name: str, fn: Callable,
                 *, called_by: str) -> Disposer:
        kind = EntryKind(kind)
        key = (kind, name)
        if key in self._entries:
            raise MechaError(
                f"注册项 {kind.value}/{name!r} 已存在",
                kind="duplicate_entry",
                hint="同名注册先 dispose 再 register",
            )
        if not called_by.strip():
            raise MechaError(
                f"注册项 {kind.value}/{name!r} 必须声明 called_by",
                kind="bad_registration",
                hint="谁调它（framework/host/consumer）必须写明（消 D-18）",
            )
        self._entries[key] = RegisteredEntry(kind, name, fn, called_by)
        # 角色登记的 disposer 必须收着，dispose 时一并撤销——否则能力表残留
        # 已撤销的注册（幽灵条目，map 从角色表生成会带出；重注册累积陈旧行）。
        dispose_role = self._roles.register(
            f"{kind.value}:{name}",
            Role.CONSUMER if kind is EntryKind.QUERY else Role.PROVIDER,
            provider=name, consumer=called_by)

        def dispose() -> None:
            self._entries.pop(key, None)
            dispose_role()

        return dispose

    def entries(self, kind: EntryKind | str) -> list[RegisteredEntry]:
        kind = EntryKind(kind)
        return [e for (k, _), e in sorted(self._entries.items()) if k is kind]

    def query(self, op_name: str, **args: object) -> object:
        """只读查询分派。查询**不经写权门**（这是 D-4 的解法：不存在"只读动词
        被判不可逆"）；未注册的查询名报 ``UnknownOp`` 并带 hint——先 register 再调。

        参数名是 ``op_name`` 而不是 ``name``：第二个宿主的详情查询自带
        ``name`` 参数，若本方法形参也叫 ``name``，``query("detail",
        name="x")`` 会撞成"重复实参"——通用机制不能占用宿主的参数名。
        """
        entry = self._entries.get((EntryKind.QUERY, op_name))
        if entry is None:
            raise UnknownOp(
                f"查询 {op_name!r} 未注册",
                kind="unknown_query",
                hint=f"已注册查询见 Surface.entries('query')；新动词先 register",
            )
        return entry.fn(**args)

    def fire_hooks(self, point: str, payload: Mapping[str, object]) -> list[object]:
        """固定挂点：框架按 point 调已注册 HOOK。挂点不存在=空列表（不是错）。"""
        return [e.fn(payload) for (k, n), e in sorted(self._entries.items())
                if k is EntryKind.HOOK and n == point]
