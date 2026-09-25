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

from .artifacts import LocalArtifactStore
from .authority import Authority, DEFAULT_MODE, Mode
from .commands import CommandRegistry
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
from .toolhost import LocalToolHost
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
    #: 宿主产物托管（不透明 locator；与 spill 分工见 artifacts 模块 docstring）
    artifacts: LocalArtifactStore
    approval: LocalApproval
    tools: ToolRegistry
    #: 工具面的宿主传输 seam（local 出厂 = 进程内直调；dsh/MCP 是另一个
    #: Provider）。托管的就是上面那份 ``tools``（同一对象，非第二张表）。
    toolhost: LocalToolHost
    #: 通用命令面。**同一对象**即 ``surface.commands``（不是第二张注册表）：
    #: 装配点这里多一个取用入口，是为了让宿主"只拿 Software 就能挂命令"，
    #: 而不必去摸 Surface 内部（L7 单一来源）。
    commands: CommandRegistry
    visibility: VisibilityPolicy
    roles: RoleRegistry
    channels: dict[str, Channel]
    #: 出厂 LOCKED（nothing ships enabled 用在写权上）；人类通道经 switch_mode 开闸
    authority: Authority
    #: 数据目录布局（宿主派生 artifact_root 等子路径用；D-33：真实路径经
    #: assemble 后不再自己 resolve_data_dir）
    layout: DataLayout

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
    # 写权模式机（审查完整性守卫抓出的漏声明：脊椎件却不在表里）
    roles.register("authority", Role.DEFINITION, provider=project)
    # 操作者适配器（平级，不是主子）
    roles.register("adapter.gui", Role.CONSUMER, consumer="human", provider=project)
    roles.register("adapter.ai", Role.CONSUMER, consumer="ai", provider=project)
    # Provider 层四 seam（consumer 写真实消费它的能力；"是否真有人引用"由
    # capability_map 守卫 grep 实现符号独立证明——见 check_real_consumers）。
    roles.register("provider.log", Role.DEFINITION, provider=project)
    roles.register("provider.log.local", Role.PROVIDER, provider="jsonl", consumer="history")
    roles.register("provider.jobs", Role.DEFINITION, provider=project)
    roles.register("provider.jobs.local", Role.PROVIDER, provider="thread", consumer="tools.registry")
    roles.register("provider.spill", Role.DEFINITION, provider=project)
    roles.register("provider.spill.local", Role.PROVIDER, provider="local-file")
    # 宿主产物托管：Definition 归我们，local 写文件；无内置消费者（宿主拿 locator
    # 自行落进 result_ref / History），进 capability_map_selfcheck 的推迟登记。
    roles.register("provider.artifacts", Role.DEFINITION, provider=project)
    roles.register("provider.artifacts.local", Role.PROVIDER, provider="local-file")
    roles.register("provider.approval", Role.DEFINITION, provider=project)
    roles.register("provider.approval.local", Role.PROVIDER, provider="fail-closed")
    # 工具形态层与可见性
    roles.register("tools.registry", Role.CONSUMER, consumer="adapter.ai", provider=project)
    # 工具面的宿主传输 seam（定义与传输分开）：Definition 归我们，local 出厂
    # 实现是进程内直调；dsh/MCP 侧注册进 ctx.tools 的是另一个 Provider。
    # provider.toolhost.local 目前无内置消费者（声明/投影/调用由适配器层取用），
    # 进 capability_map_selfcheck 的推迟登记（不许沉默推迟）。
    roles.register("provider.toolhost", Role.DEFINITION, provider=project)
    roles.register("provider.toolhost.local", Role.PROVIDER, provider="in-process")
    # 工具面的 MCP 传输 Provider（住 extra `mecha.providers.mcp`）：核心不 import
    # 它，装配点只声明"这个 Provider 存在"（capability map 的诚实性）。
    # 消费者 = AI 操作面：下游把它接成宿主里的 MCP 服务端，AI 经它调工具。
    roles.register("provider.toolhost.mcp", Role.PROVIDER,
                   provider="mcp-streamable-http", consumer="adapter.ai")
    # 监控端点的传输 seam（与 provider.toolhost 同款分工：Definition 归我们，
    # local 出厂实现是"回环 HTTP、只读、无鉴权"）。Monitor 此前只有读面没有传输，
    # 补上它不新增权限面（可见性归 visibility、写权归 gate）。
    # 消费者 = 人类监控面（adapter.gui）：面板轮询这个只读端点。
    roles.register("monitor.transport", Role.DEFINITION, provider=project)
    roles.register("monitor.transport.local", Role.PROVIDER,
                   provider="http-loopback", consumer="adapter.gui")
    # 通用命令面（命令是注册项：Surface.commands / Software.commands 同一对象；
    # 具体 command:<name> 能力行由注册时动态登记，不进出厂 map）
    roles.register("commands.registry", Role.CONSUMER, consumer="adapter.ai",
                   provider=project)
    roles.register("visibility.policy", Role.DEFINITION, provider=project)
    # 编排与目录机制（consumer=真消费它的适配器层；spill/approval/skills 无
    # 真实消费者，进 DEFERRED_PROVIDERS 显式登记为诚实推迟）
    roles.register("provider.codeact", Role.PROVIDER, provider="actions-interp",
                   consumer="adapter.ai")
    roles.register("provider.skills", Role.PROVIDER, provider="catalog")


def build_roles(*, engine_name: str = "unconfigured", project: str = "mecha") -> RoleRegistry:
    """只取角色表（map 生成入口，不构造实例）。"""
    roles = RoleRegistry()
    declare_roles(roles, engine_name=engine_name, project=project)
    return roles


def assemble(*, root: Path, data_dir_name: str | None, engine: Engine,
             summarizer: Summarizer, validate: Validator,
             project: str = "mecha",
             mode: Mode = DEFAULT_MODE,
             migration: MigrationChain | None = None,
             approval_decider=None) -> Software:
    """完整装配（唯一具名装配点，L6/D-33：真实部署路径也走这里）。

    validate 必填透传给 Gate（审查 P5-2：不校验请显式传 accept_all）。
    mode 默认出厂 LOCKED（nothing ships enabled）；GUI 类宿主显式传 HUMAN。
    """
    layout: DataLayout = resolve_data_dir(root, data_dir_name)
    roles = build_roles(engine_name=type(engine).__name__, project=project)
    sink = JsonlLogSink(layout, chain=migration)
    history = History(sink)
    authority = Authority(mode)
    gate = Gate(authority, history, validate=validate)
    surface = Surface(engine, roles=roles)
    monitor = Monitor(history.events, summarizer)
    jobs = JobRegistry()
    spill = LocalSpillStore(layout)
    artifacts = LocalArtifactStore(layout)
    approval = LocalApproval(approval_decider)
    tools = ToolRegistry()
    toolhost = LocalToolHost(tools)
    # 命令面 = Surface 上那一份（同一对象，非第二张表）：注册进它即可被
    # surface.commands.invoke 分派，capability map 也共用装配点角色表。
    commands = surface.commands
    visibility = VisibilityPolicy()
    channels = {"human": Channel("human"), "ai": Channel("ai")}
    return Software(gate=gate, history=history, surface=surface, monitor=monitor,
                    jobs=jobs, spill=spill, artifacts=artifacts,
                    approval=approval, tools=tools, toolhost=toolhost,
                    commands=commands, visibility=visibility, roles=roles,
                    channels=channels, authority=authority, layout=layout)
