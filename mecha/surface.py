"""Surface：能力面（总纲 §5.2③，Phase 2 瘦身；硬纪律 3）。

一个 Engine seam = Definition（本模块）+ Provider（项目引擎）+ Consumer
（ops 管道）。**必实现只有 4 个**：``schema`` / ``run`` / ``summary`` /
``estimate``（消 D-13：v1 的 13 必实现对"没有运行"的项目也全强制）。

铁律：**框架自己会调用的，才配进 Definition；框架不调、只做分派目标的，
必须是注册项**（消 D-18 的"钩子"混合概念——三类东西各有明确角色与"谁调它"）。

- Definition 无默认值（消 D-17）：回退值只存在于 Provider；本模块不藏
  ``?? default``。
- 隐式契约键显式化（消 D-15）：``schema()`` 必须声明 ``run`` 回执的必填键，
  ops 管道逐条校验，缺键即报错——契约不再"只在 spec 与判据里"。
- 取消与进度是 ExecutionContext 的显式字段（消 D-16），不是数组三槽约定。
- 命令面（``commands``）是 **additive** 的另一条分派路径：命令是注册项，
  不是 Definition 的第 5 个必实现（消"具名 ABC 方法膨胀"）。``run`` / ``query``
  语义一字未改——单操作引擎照旧，多命令宿主多了归宿。

## Known Limitations and Deferred Work

- ``queries`` / ``mcp_tools`` / ``hooks`` / ``custom_types`` 先按注册项实现，
  是否升格为独立 seam 推迟到 n=2（ADR 边界写机制不写分类）。
- ``schema`` 合一 v1 的五个视图，但视图切分逻辑（给 GUI 的树形 vs 给模型的
  JSON Schema）归 Consumer；Definition 只保证"一处声明"。
- run 的长任务化（job 化）归 Phase 3 Jobs seam；这里只定同步契约。
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum

from .commands import CommandRegistry
from .errors import InvalidSpec, MechaError, UnknownOp
from .roles import Disposer, Role, RoleRegistry

#: 框架真会调的名单（判据 checks/phase2_surface_selfcheck.py 引用同一常量，
#: Definition 方法名必须是它的子集——总纲 §7 Phase 2 第 2 条）。
FRAMEWORK_CALLS: frozenset[str] = frozenset(
    {"schema", "run", "summary", "estimate"}
)


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


class Engine(ABC):
    """引擎 Definition：具体注册服务形态的 ABC，只有 4 个必实现。

    项目 Provider 继承它；"没有运行"的项目 run/estimate 可以返回
    ``{"ok": True, "skipped": "无运行概念"}`` 之类的显式声明——但**必须**
    显式写，不许框架替你默认（D-17）。
    """

    @abstractmethod
    def schema(self) -> Mapping[str, object]:
        """一处声明的 schema（合一 v1 五视图）。

        必须含 ``run_output_required``: list[str] —— run 回执的必填键名单
        （消 D-15：隐式键变显式声明，缺键即校验失败）。
        """

    @abstractmethod
    def run(self, spec: Mapping[str, object], *, context: ExecutionContext,
            **opts: object) -> Mapping[str, object]:
        """合一 v1 的 build+check+evolve。长任务经 context 感知取消。"""

    @abstractmethod
    def summary(self) -> Mapping[str, object]:
        """合一 v1 的两个摘要方法。"""

    @abstractmethod
    def estimate(self, spec: Mapping[str, object]) -> Mapping[str, object]:
        """契约先行：框架真会调（回执必须含 ``est_sec``，由 ops 校验）。"""


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
    """Consumer 侧装配点：持有 Engine Provider + 注册项，跑 ops 管道。"""

    def __init__(self, engine: Engine, roles: RoleRegistry | None = None) -> None:
        self._engine = engine
        self._roles = roles or RoleRegistry()
        self._entries: dict[tuple[EntryKind, str], RegisteredEntry] = {}
        # 通用命令面（additive；不占 Engine 必实现，不改 run/query 语义）。
        # 角色表共用装配点那张 ⇒ command:<name> 能力行与声明表同源。
        self._commands = CommandRegistry(roles=self._roles, caller="Surface")
        self._roles.register("engine", Role.DEFINITION)
        self._roles.register("engine", Role.PROVIDER, provider=type(engine).__name__)
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

    # ---- ops 管道（框架真会调的 4 个方法 + 校验）----

    def query(self, name: str, **args: object) -> object:
        """只读查询分派。查询**不经写权门**（这是 D-4 的解法：不存在"只读动词
        被判不可逆"）；未注册的查询名报 ``UnknownOp`` 并带 hint——先 register 再调。
        """
        entry = self._entries.get((EntryKind.QUERY, name))
        if entry is None:
            raise UnknownOp(
                f"查询 {name!r} 未注册",
                kind="unknown_query",
                hint=f"已注册查询见 Surface.entries('query')；新动词先 register",
            )
        return entry.fn(**args)

    def schema(self) -> Mapping[str, object]:
        s = self._engine.schema()
        if "run_output_required" not in s:
            raise InvalidSpec(
                "engine.schema() 未声明 run_output_required",
                kind="missing_output_contract",
                hint="schema() 必须含 run_output_required: list[str]（run 回执必填键）",
            )
        return s

    def run(self, spec: Mapping[str, object], *, context: ExecutionContext,
            **opts: object) -> Mapping[str, object]:
        result = dict(self._engine.run(spec, context=context, **opts))
        required = list(self.schema()["run_output_required"])  # type: ignore[arg-type]
        missing = [k for k in required if k not in result]
        if missing:
            raise MechaError(
                f"run 回执缺必填键 {missing}",
                kind="bad_run_output",
                hint="回执键契约由 schema()['run_output_required'] 声明（消 D-15）",
            )
        return result

    def summary(self) -> Mapping[str, object]:
        return dict(self._engine.summary())

    def estimate(self, spec: Mapping[str, object]) -> Mapping[str, object]:
        est = dict(self._engine.estimate(spec))
        if "est_sec" not in est:
            raise MechaError(
                "estimate 回执缺必填键 'est_sec'",
                kind="bad_estimate_output",
                hint="estimate() 必须返回含 est_sec（秒）的映射（D7 契约先行）",
            )
        return est

    def fire_hooks(self, point: str, payload: Mapping[str, object]) -> list[object]:
        """固定挂点：框架按 point 调已注册 HOOK。挂点不存在=空列表（不是错）。"""
        return [e.fn(payload) for (k, n), e in sorted(self._entries.items())
                if k is EntryKind.HOOK and n == point]
