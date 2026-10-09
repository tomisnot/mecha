"""站点声明：**一个 site = 一组软件 + 一个 dsh**（框架不持有任何项目字面量）。

## 这是什么

从 mecha 启动 = **永远是 AI 组**（没有"启动模式"这个东西）。项目选择在**浏览器选择页**
上勾，默认全勾；纯 GUI 用户不走 mecha。⇒ 需要一个**声明**告诉 supervisor：
"这台机器上有哪些软件、各自怎么起、什么时候算就绪、dsh 怎么起"。

**声明从哪来**：按约定发现 **``mecha_site.py``**、里面暴露模块级 ``SITE: Site``
（见 :func:`launch.load_sites`）。**框架只认形状，不认项目**——名字、路径、端口文件名
全在声明里（§5.4「写机制不写分类」）。

## 字段为什么长这样（每条都对着一条既有纪律）

- ``Service.data_root``：**各软件自己的写租约根**（``write_lease`` 按数据根独占）。
  三层架构 A1：它是**软件自己的**锁（软件是它状态数据的唯一写宿主）；
  互斥的是"**同一数据根两个软件实例**"，与"换操作者"无关。
- ``Service.has_gui``：这个项目**有没有 GUI 窗口**（⇒ 面板据此决定显不显示"切到手动"）。
  ⚠ 人那条腿**不是 child**，它跑在**软件进程内**（A1/A2）⇒ 这里只声明"有没有"，
  不声明"怎么起它"。
- ``Service.initial_mode``：开始时用什么写权，**由启动者（人）声明**。
- ``DSHSpec.command`` 是 ``tuple`` 而不是单个字符串：dsh 有时要经 ``cmd /c`` 起
  （EL/ML 的实测形态），有时是裸可执行——由声明决定，框架不猜。
- ``Strategy`` 的取值与 OTP **同名**（``one_for_one`` / ``rest_for_one`` / ``one_for_all``）；
  ``auto_shutdown`` 同理。本步不驱动编排（步骤 2 才用），但**先声明清楚**免得步骤 2 改签名。

## Known Limitations and Deferred Work

- **不含 aggregator**：``Site`` 里没有聚合端点的位置——那是步骤 2 的事。修正 1 要求的
  "启动顺序 + 就绪后钩子"已经由 :class:`launch.StartupPlan` 与 ``Supervisor`` 的钩子留好出口。
- 一个站点只声明**一个 dsh**：多 dsh 的形态没有实测依据（n=1 不预建）。
- 不做"服务之间的依赖图"：实测只有一条链 ``svc* → aggregator → dsh``，
  用**显式顺序**表达即可，引入通用 DAG 是给不存在的需求造机制。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from collections.abc import Mapping

from .errors import MechaError
from .supervise import ChildSpec, ReadySpec
from .authority import Mode

#: 服务名的形状（它将来也是**工具前缀**，所以现在就要约束住）。
_SERVICE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")

#: ⭐ 框架往**软件进程**里送的两个"启动者声明"（三层架构 §5.1）。
#: ⚠ 它们是**框架与软件之间的约定名字**，不是项目的字段 ⇒ 项目**不该**自己拼这两个名字。
#: ⚠ 走 env（而不是 argv）的理由见 :meth:`Service.child`。
MECHA_MODE_ENV = "MECHA_INITIAL_MODE"
MECHA_ROOT_ENV = "MECHA_SOFTWARE_ROOT"


@dataclass(frozen=True)
class Service:
    """一个软件（"各自一条脊椎"）。

    ⚠ **三层架构（2026-10-08 定稿）改了本类的形状**：
    - ➖ 删 ``manual: ChildSpec``：**人那条腿不是 supervisor 的子进程**，
      它是**软件进程内的一个窗口**（A1/A2）⇒ 不该在这里声明成要 spawn 的东西；
    - ➕ 加 ``has_gui`` / ``initial_mode``：前者决定面板显不显示"切到手动"，
      后者由**启动者**（人）在 spawn 时声明。
    """

    #: 全局唯一；将来也是工具前缀（``mcp__<id>__<tool>``）。
    id: str
    #: 选择页上的一句话简介（人看的）。
    brief: str
    #: 怎么起**软件自己**——它自带状态、自带写租约、自带能力面、（可选）自带 GUI 窗口。
    argv: tuple[str, ...]
    cwd: Path
    ready: ReadySpec
    #: 该软件**自己的**写租约根（不跨软件共享）。⚠ 它是**软件自己的锁**（A1），
    #: 不是"人机互斥"的东西：两条腿都是客户端、可以同时挂着（A2）。
    data_root: Path
    #: ⭐ 开始时用什么写权（**由启动者声明** —— 人是启动者，故合法）。
    initial_mode: Mode = Mode.LOCKED
    #: ⭐ 这个项目**有没有 GUI 窗口** ⇒ 面板据此决定显不显示"切到手动"。
    #: ``False`` ⇒ 它只有 AI 一条腿，面板上**不该**出现那一档。
    has_gui: bool = False
    #: ⭐ "怎么把写权切到人工"的**自由文案**，由**项目**给（框架**不解释、不写死**）。
    #: 为什么归项目：那是我项目自己的入口（哪个页面、哪个开关、还是命令行）
    #: ⇒ 框架里写死任何一句都等于把**领域知识**塞进框架（且必然对某一家是错的）。
    #: ⚠ 它会进 ``read_write_authority`` 的返回给 AI 看 ⇒ 别塞端口/路径/凭据。
    how_to_open: str = ""
    default_on: bool = True
    env: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # ⚠ 这里原本用 None 再补默认值（避免把 LOCKED 抄第二遍）；但 dataclass 的默认值
        #   直接写 ``Mode.LOCKED`` 更好：它就是 ``authority.DEFAULT_MODE`` 的值，
        #   而"默认写权是 LOCKED"这条纪律由 ``authority`` 拥有 —— 这里只消费它。
        return None

    def child(self) -> ChildSpec:
        """把声明翻成 supervisor 要的 :class:`ChildSpec`（一处翻译，别处别抄）。

        ⚠ 曾经有一个 ``log_id`` 字段想让"日志名"与"child id"分开 —— 已删：
        ``ChildSpec.id`` 本身就是 ``<svc_id>``，第二个名字**没有任何消费者**
        （R17：参数没人读就是死缝）。

        ## ⭐ 这里注入两个"启动者声明"的环境变量（三层架构 §5.1）

        闸门在**软件进程内**（公理 A3）⇒ "把值送到软件"是双方约定：

        | 变量 | 含义 |
        |---|---|
        | ``MECHA_SOFTWARE_ROOT`` | 这份软件的数据根（≈ ``data_root``） |
        | ``MECHA_INITIAL_MODE`` | 启动者声明的初始写权（``locked``/``human``/``ai``/``open``） |

        ⚠ **为什么走 env 而不是 argv**：三家的 CLI 风格完全不同（argparse / typer / …），
        env 是**唯一**对三家都一样的通道；且它**不占**软件自己的参数面。

        ⚠ **取值必须用 `.value`**：``Mode`` 是 ``str`` 枚举，而 ``str(Mode.AI)`` 给的是
        ``'Mode.AI'`` 而**不是** ``'ai'`` —— 这个坑框架里踩过一次（``SvcState.DOWN``），
        三家接入时**又各自踩了一次**（实测：三个子代理都断在这上面）。

        ⚠ **软件的显式声明优先**：若项目在自己的 ``env`` 里给了同名键，**以项目为准**
        （``setdefault`` 语义，与框架统一注入 ``PYTHONIOENCODING`` 同一纪律）。
        """
        env = dict(self.env)
        env.setdefault(MECHA_ROOT_ENV, str(self.data_root))
        env.setdefault(MECHA_MODE_ENV,
                       str(getattr(self.initial_mode, "value", self.initial_mode)))
        return ChildSpec(
            id=self.id,
            argv=self.argv, cwd=self.cwd, env=env,
            ready=self.ready, data_root=self.data_root,
        )

    def validate(self) -> None:
        if not _SERVICE_ID_RE.match(self.id):
            raise MechaError(
                f"服务 id 不合法：{self.id!r}",
                kind="bad_service_id",
                hint="id 将来是工具前缀 ⇒ 必须 ^[A-Za-z][A-Za-z0-9_-]*$",
            )
        # ⚠ 这里**曾经**校验 `manual` 的 id 形状与数据根（`bad_manual_id` /
        #   `manual_root_mismatch`）。三层架构删掉了 `manual`（人那条腿不是 child）
        #   ⇒ 那两条校验随之删除：它们校验的对象在新架构里**不存在**。
        #   （R13：删机制时把承诺改写成行为级判据；这里的承诺由 `has_gui` 承担。）


@dataclass(frozen=True)
class DSHSpec:
    """dsh（AI 的宿主）怎么起。字段全是声明，框架不猜。"""

    #: ```("cmd", "/c", "dsh", "web", ...)``` 这类 argv 片段；第一段按 PATH 解析。
    command: tuple[str, ...]
    cwd: Path
    #: ``--patch`` 列表（**顺序即命令行顺序**）。
    patches: tuple[Path, ...] = ()
    #: ⚠ **端口偏好**，不是"实际端口"：实际端口一律**从 bind 结果回读**
    #: （ML 的 ``:762`` 把预选值写进运行期描述符，就是这条的反例）。
    port_preference: int = 3081
    port_tries: int = 10
    #: 插件产物（就绪前置检查用）：**不存在就响亮失败**，别等 dsh 起来报一个看不懂的错。
    plugin_artifacts: tuple[Path, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    ready: ReadySpec | None = None
    stop_timeout: float = 8.0
    #: dsh **不持**软件的写租约（它不碰域状态）：显式 ``None`` 表明"它不该被判成写宿主"。
    data_root: None = None

    def argv(self) -> tuple[str, ...]:
        """把声明拼成命令行（``--patch`` 与端口偏好在这里落成 argv）。"""
        out = list(self.command)
        for p in self.patches:
            out += ["--patch", str(p)]
        return tuple(out)

    def validate(self) -> None:
        if not self.command:
            raise MechaError("DSHSpec.command 为空", kind="empty_dsh_command",
                             hint="至少要有第一段（可执行）")
        missing = [str(p) for p in self.plugin_artifacts if not Path(p).exists()]
        if missing:
            raise MechaError(
                f"dsh 的插件产物不存在：{missing}",
                kind="plugin_artifact_missing",
                hint="先在 dsh 目录跑构建（产物是 dsh 侧插件的入口，少了它 dsh 起不来）",
            )


class StrategyMode(str, Enum):
    """重启策略（与 OTP **同名**，不造新词）。"""

    ONE_FOR_ONE = "one_for_one"
    REST_FOR_ONE = "rest_for_one"
    ONE_FOR_ALL = "one_for_all"


class AutoShutdown(str, Enum):
    """整站何时自动收尾（步骤 2 用；本步只声明）。"""

    NEVER = "never"
    ANY_SIGNIFICANT = "any_significant"
    ALL_SIGNIFICANT = "all_significant"


@dataclass(frozen=True)
class Strategy:
    mode: StrategyMode = StrategyMode.ONE_FOR_ONE
    max_restarts: int = 5
    period_s: float = 60.0
    auto_shutdown: AutoShutdown = AutoShutdown.NEVER


@dataclass(frozen=True)
class Site:
    """一个站点：一组软件 +（可选的）dsh 贡献 + 一份策略。

    ⚠ **dsh 可以不管它**（三层架构 §1.1.1）：dsh 是**全生态一个**，由各站点**共同贡献**：
    ``None`` ⇒ 框架用默认（``command=("dsh",)``、cwd 取第一个站点）；
    声明了 ⇒ 它的 ``patches``/``plugin_artifacts``/``env`` **并进那一个 dsh**
    （每个项目都要能把自己的监控面板挂上）。
    """

    name: str
    services: tuple[Service, ...]
    #: ⭐ 本站点给**那一个共用 dsh** 的贡献（可 ``None`` = 我不管 dsh）。
    dsh: DSHSpec | None = None
    strategy: Strategy = Strategy()

    def validate(self) -> None:
        if not self.name.strip():
            raise MechaError("Site.name 不能为空", kind="empty_site_name",
                             hint="站点名用于状态页与日志归属")
        if not self.services:
            raise MechaError(
                "Site.services 为空：没有任何软件可起",
                kind="empty_site",
                hint="站点声明至少要有一条 Service（空站点不是合法状态，不许静默起个空站）",
            )
        seen: set[str] = set()
        seen_lower: dict[str, str] = {}
        for svc in self.services:
            svc.validate()
            if svc.id in seen:
                raise MechaError(
                    f"服务 id 重复：{svc.id!r}",
                    kind="duplicate_service_id",
                    hint="id 是全局唯一的（将来也是工具前缀）",
                )
            seen.add(svc.id)
            #: ⚠ **大小写不敏感的唯一性**：暴露给模型的工具名会**小写化**
            #: （`Aggregator._compose` = ``f"{svc_id.lower()}_{tool}"``）⇒
            #: 只有大小写不同的两个 id 会**静默撞成同一批工具名**（后挂的覆盖先挂的）。
            #: ⇒ 必须在声明期就响亮拒绝，不能等运行期"工具怎么少了一批"。
            low = svc.id.lower()
            if low in seen_lower:
                raise MechaError(
                    f"服务 id 大小写不敏感地重复：{svc.id!r} 与 {seen_lower[low]!r}",
                    kind="duplicate_service_id",
                    hint="工具名会小写化（<svc_id.lower()>_<tool>）⇒ 两者会撞名；改掉其中一个",
                )
            seen_lower[low] = svc.id
            # ⚠ 这里**曾经**还要查 `svc.manual.id` 有没有撞上服务 id（约定 `<id>:manual`）。
            #   三层架构删了 `manual`（人那条腿不是 child）⇒ 那条查重随对象一起消失（R13）。
        #: ⚠ dsh **可选**（§1.1.1：项目可以不管 dsh）⇒ 只在声明了才校验。
        if self.dsh is not None:
            self.dsh.validate()
