"""具名装配点（L6/L7：机制建好必须装配接上，判据从这里取数）。

把稳定脊椎 + Provider 层 + 工具形态层组装成一台可运行的软件实例，并在此
**声明每个 seam 的角色**（§5.4 交付 1）。capability map 由
``scripts/gen_capability_map.py`` 调 ``build_roles()`` 生成——分类是生成的，
不是手写的。

## Known Limitations and Deferred Work

- 装配是纯构造（不启动线程/进程）；宿主生命周期归 Phase 5 的启动器。
- 角色声明集中在这里而非散布各模块：单点可读，且 map 生成只需要一个入口。
  散布式声明（装饰器自注册）等第二个项目接入时再评估（n=1 不建）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .authority import Authority
from .data_layout import DataLayout, resolve_data_dir
from .format_version import MigrationChain
from .gate import Channel, Gate, Validator
from .history import History
from .monitor import Monitor, Summarizer
from .providers.approval import LocalApproval
from .providers.jobs import JobRegistry
from .providers.log_local import JsonlLogSink
from .providers.spill import LocalSpillStore
from .roles import Role, RoleRegistry
from .surface import Engine, Surface
from .tools import ToolRegistry
from .visibility import VisibilityPolicy


@dataclass
class Software:
    """一台软件实例：四件正职 + Provider 层 + 工具面，全部经装配点接好。"""

    gate: Gate
    history: History
    surface: Surface
    monitor: Monitor
    jobs: JobRegistry
    spill: LocalSpillStore
    approval: LocalApproval
    tools: ToolRegistry
    visibility: VisibilityPolicy
    roles: RoleRegistry
    channels: dict[str, Channel]
    #: 出厂 LOCKED（nothing ships enabled 用在写权上）；人类通道经 switch_mode 开闸
    authority: Authority

    def close(self) -> None:
        """关停实例：释放 History 的写租约（宿主关停/租约交接用）。幂等。"""
        self.history.close()

    def __enter__(self) -> "Software":
        return self

    def __exit__(self, *exc) -> bool:
        self.close()
        return False


def declare_roles(roles: RoleRegistry, *, engine_name: str, project: str) -> None:
    """§5.4 交付 1：每个 seam 声明角色。无角色标注的扩展点 = 拒收。"""
    # 稳定脊椎
    roles.register("gate", Role.DEFINITION, provider=project)
    roles.register("history", Role.DEFINITION, provider=project)
    roles.register("surface", Role.DEFINITION, provider=project)
    # Surface 的 Provider 是项目引擎本身（Definition 我们拥有、Provider 项目提供）
    roles.register("surface.engine", Role.PROVIDER, provider=engine_name, consumer="surface")
    roles.register("monitor", Role.DEFINITION, provider=project)
    # 操作者适配器（平级，不是主子）
    roles.register("adapter.gui", Role.CONSUMER, consumer="human", provider=project)
    roles.register("adapter.ai", Role.CONSUMER, consumer="ai", provider=project)
    # Provider 层四 seam（consumer 只写**真实存在且真会消费**的能力名——
    # 审查勘误 §3：原 surface.run/tools/gate 三条是假声明，Surface.run 不碰
    # jobs、tools 层不 import spill、gate 不 import approval。无真实消费者的
    # 留空（诚实推迟），由 capability_map 守卫盯"声明的 consumer 必须是表内
    # 能力"+"每个出厂机制有非判据引用"）。
    roles.register("provider.log", Role.DEFINITION, provider=project)
    roles.register("provider.log.local", Role.PROVIDER, provider="jsonl", consumer="history")
    roles.register("provider.jobs", Role.DEFINITION, provider=project)
    roles.register("provider.jobs.local", Role.PROVIDER, provider="thread", consumer="tools.registry")
    roles.register("provider.spill", Role.DEFINITION, provider=project)
    roles.register("provider.spill.local", Role.PROVIDER, provider="local-file")
    roles.register("provider.approval", Role.DEFINITION, provider=project)
    roles.register("provider.approval.local", Role.PROVIDER, provider="fail-closed")
    # 工具形态层与可见性
    roles.register("tools.registry", Role.CONSUMER, consumer="adapter.ai", provider=project)
    roles.register("visibility.policy", Role.DEFINITION, provider=project)
    # 编排与目录机制（勘误 §4.2：未消费清单要在 map 里可见，守卫才盯得住）
    roles.register("provider.codeact", Role.PROVIDER, provider="actions-interp",
                   consumer="tools.registry")
    roles.register("provider.skills", Role.PROVIDER, provider="catalog")


def build_roles(*, engine_name: str = "unconfigured", project: str = "mecha") -> RoleRegistry:
    """只取角色表（map 生成入口，不构造实例）。"""
    roles = RoleRegistry()
    declare_roles(roles, engine_name=engine_name, project=project)
    return roles


def assemble(*, root: Path, data_dir_name: str | None, engine: Engine,
             summarizer: Summarizer, validate: Validator,
             project: str = "mecha",
             migration: MigrationChain | None = None,
             approval_decider=None) -> Software:
    """完整装配。data_dir_name 不注入 → 当场报错（D-6 的通道在此接上）。

    validate 必填透传给 Gate（审查 P5-2：不校验请显式传 accept_all）。
    """
    layout: DataLayout = resolve_data_dir(root, data_dir_name)
    roles = build_roles(engine_name=type(engine).__name__, project=project)
    sink = JsonlLogSink(layout, chain=migration)
    history = History(sink)
    authority = Authority()
    gate = Gate(authority, history, validate=validate)
    surface = Surface(engine, roles=roles)
    monitor = Monitor(history.events, summarizer)
    jobs = JobRegistry()
    spill = LocalSpillStore(layout)
    approval = LocalApproval(approval_decider)
    tools = ToolRegistry()
    visibility = VisibilityPolicy()
    channels = {"human": Channel("human"), "ai": Channel("ai")}
    return Software(gate=gate, history=history, surface=surface, monitor=monitor,
                    jobs=jobs, spill=spill, approval=approval, tools=tools,
                    visibility=visibility, roles=roles, channels=channels,
                    authority=authority)
