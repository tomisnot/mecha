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
- ``Service.monitor``：⭐ 这个服务的**监控面声明**（``MonitorSpec``，三层架构 §7.3.6）。
  ⚠ 它是"声明 ↔ 面板"之间**唯一那份参数**的家：路由 / 端口文件名 / 标题 / 附加页
  都只在这里写一次，框架只**生成与转交**（:mod:`mecha.dsh_panel`），
  **不解释、不写死任何项目值**。缺省 ``None`` ⇒ 这个服务没有监控面板。
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

import os
import re
from dataclasses import dataclass, field, replace
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


# --------------------------------------------------------------- 启动者声明的**读取口**
#
# ⭐ 这两个函数是**契约的另一半**：上面是框架**注入**（`Service.child()`），
# 这里是软件**读取**。⚠ 放同一个模块是刻意的 —— 一个契约的两半**必须住在一起**，
# 否则"名字/语义/空串规则"会在两边各自漂。
#
# ⚠ **为什么要上提到框架**（实测）：三家各自写了读取代码，做法**三种**：
# EL 有 `_initial_mode()`（读了）；**ML/GP 全是"注释里自认还没读"**，各按旧口径开闸。
# ⇒ 于是"框架声明的"与"软件实际用的"成了**两份真值**，而**谁都没报错**。
#
# ⚠ **空串 = 显式取消该变量**（与框架统一注入 `PYTHONIOENCODING` 同一纪律）⇒ 视同没给。


def _warn(msg: str) -> None:
    """打一行警告 —— ⚠ **不许因为控制台编码而崩**。

    ⚠ **实测（GP 报的，真 bug）**：Windows **cp936** 控制台下，这行里的 ``⚠``
    会让 ``print`` 抛 ``UnicodeEncodeError`` ⇒ 本该"**回落 + 出声**"实际变成
    **崩掉** —— ⚠ 那比"不出声"更坏：**软件根本起不来**。

    ⚠ 为什么修在**框架**而不是各家各自防：这是**框架自己输出**的一句话
    （ML/EL/GP 都会踩）⇒ 修一处，三家都不踩。

    策略：原样打 ⇒ 编码不接受就退成 **ASCII 安全版** ⇒ 再不行**静默放弃**
    （一行警告打不出来，**不能拖垮软件本身**）。
    """
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        try:
            print(msg.encode("ascii", "replace").decode("ascii"), flush=True)
        except Exception:                                  # noqa: BLE001 - 警告不能致命
            pass
    except Exception:                                      # noqa: BLE001 - 同上
        pass


def initial_mode_from_env(default: Mode = Mode.LOCKED) -> Mode:
    """读启动者声明的初始写权（``MECHA_INITIAL_MODE``）。

    ⚠ **缺省 / 空串 ⇒ 回落 ``default``**；⚠ **非法值 ⇒ 回落并出声**（不静默：
    一个拼错的模式名会让人以为"声明生效了"，而实际按默认走）。
    ⚠ "出声"走 :func:`_warn`（**编码安全**：cp936 控制台下不会崩）。

    ⚠ 软件侧**必须用它装配闸门**（不能只声明不读 —— 那正是本函数存在的理由）。
    """
    raw = os.environ.get(MECHA_MODE_ENV, "").strip()
    if not raw:
        return Mode(default)
    try:
        return Mode(raw)
    except ValueError:
        _warn(f"[mecha] ⚠ {MECHA_MODE_ENV}={raw!r} 不是合法写权 ⇒ 回落 {Mode(default).value}")
        return Mode(default)


def software_root_from_env(default: "Path | None" = None) -> "Path | None":
    """读框架声明的数据根（``MECHA_SOFTWARE_ROOT``）。

    ⚠ **缺省 / 空串 ⇒ 回落 ``default``**（通常是项目自己算出来的仓根）。

    ⚠ 为什么要有它：数据根决定"账写在哪、写租约钉在哪"。若软件**不读**框架声明的那个，
    就会出现"**框架以为软件在用 A 根，软件实际在写 B 根**"——现象是"看不到刚才的改动"，
    **且无任何报错**（EL 实测过同一形状的坑，故它自建了 `resolve_data_root`）。
    """
    raw = os.environ.get(MECHA_ROOT_ENV, "").strip()
    if not raw:
        return default
    return Path(raw)


@dataclass(frozen=True)
class ExtraColumn:
    """附加页表格的**一列**（纯数据：**取哪个字段**，不取"怎么格式化"）。

    ⚠ **为什么只有 `label` + `field`**（三层架构 §7.3.8 第 4 条）：声明是**数据**
    （JSON 要装得下、要能给浏览器），而"取值 + 格式化"是**代码**（ML 的 `metricCell`
    要按来源各取一次 `score` / `metrics[primary_metric]`）⇒ 那种函数**进不了声明**，
    它走"**自定义 UI**"那条路（资产 `contributeExtraPageRender`）。
    """

    #: 表头文案（人看的；项目词）。
    label: str
    #: 从附加路由回的 `rows[i]` 里**取哪个键**。
    field: str


@dataclass(frozen=True)
class ExtraPage:
    """该项目**自己的**一个附加页（面板右栏里额外的一页）。"""

    #: 页签标识（**只在本服务内唯一**）。
    id: str
    #: 页签文案。
    label: str
    #: 附加路由的**请求路径**（含查询串，如 `/runs?limit=50`）。
    path: str
    #: 页内标题。缺省 ⇒ 用 :attr:`label`。
    title: str = ""
    #: 空表文案。⚠ **必须与"读不到"分开**（铁律 8）；缺省由资产给**中性**文案。
    empty_text: str = ""
    #: 读不到时的文案。⚠ **面板必画它**，不许渲染成空表。
    unreadable_text: str = ""
    #: 常驻脚注（可选）。
    note: str = ""
    #: 表格列（**至少一列**：没有列的表看着像坏了）。
    columns: tuple[ExtraColumn, ...] = ()

    def validate(self) -> None:
        for name, value in (("id", self.id), ("label", self.label), ("path", self.path)):
            if not str(value).strip():
                raise MechaError(
                    f"ExtraPage({self.id!r}).{name} 不能为空",
                    kind="bad_extra_page",
                    hint="附加页的 id/label/path 都是**人看/路由用**的必填值（声明只放数据）",
                )
        if not self.path.startswith("/"):
            raise MechaError(
                f"ExtraPage({self.id!r}).path 要以 '/' 开头：{self.path!r}",
                kind="bad_extra_page",
                hint="它是**请求路径**（附加路由），不是 URL —— 写完整 URL 会让端口/来源进声明",
            )
        if not self.columns:
            raise MechaError(
                f"ExtraPage({self.id!r}) 没有列",
                kind="bad_extra_page",
                hint="没有列的表在面板上是一张空表 —— 看着像坏了。至少给一列（label + field）",
            )
        for col in self.columns:
            if not col.label.strip() or not col.field.strip():
                raise MechaError(
                    f"ExtraPage({self.id!r}) 的列 label/field 都不能为空：{col!r}",
                    kind="bad_extra_page",
                    hint="field 是**从 rows[i] 里取哪个键**（取值函数是代码，走自定义 UI）",
                )


@dataclass(frozen=True)
class MonitorSpec:
    """一个服务的**监控面**声明（它自己有监控端点时才写）。

    ⭐ **唯一原则**（三层架构 §7.3.6）：端点的地址 / 标题 / 附加页
    **只能有【一处】真值 —— 就在这里**。框架只**生成与转交**，不解释、不写死任何项目值。

    ⚠ 框架把它翻成一份参数（:mod:`mecha.dsh_panel`）交给**框架自己的 dsh 插件**
    （那插件由**框架的 patch** 带上）⇒ **不含端口号**（端口由路由**现取**）。
    """

    #: host 半注册的只读路由；client 半 fetch **同一个串**。**必填**。
    route_path: str
    #: 权威写的**监控端口文件名**（相对**数据根** `data_root`）或绝对路径。**必填**。
    port_file: str
    #: 面板标题（纯展示；模型不读它）。缺省 ⇒ 用 `Service.id`（由 ``Service`` 补齐）。
    title: str = ""
    #: 该项目**自己的**附加页。缺省 ⇒ 无。
    extra_pages: tuple[ExtraPage, ...] = ()

    def validate(self) -> None:
        if not self.route_path.strip():
            raise MechaError(
                "MonitorSpec.route_path 不能为空",
                kind="bad_monitor_spec",
                hint="它是监控地址的**只读路由**（浏览器 fetch 的那个串）",
            )
        if not self.route_path.startswith("/"):
            raise MechaError(
                f"MonitorSpec.route_path 要以 '/' 开头：{self.route_path!r}",
                kind="bad_monitor_spec",
                hint="路由是**路径**，不是 URL —— 写完整 URL 会把主机/端口带进声明",
            )
        if not self.port_file.strip():
            raise MechaError(
                "MonitorSpec.port_file 不能为空",
                kind="bad_monitor_spec",
                hint="它是**权威实际写**的那个端口文件名（⚠ 只放**文件名**，"
                     "不放端口号 —— 端口由路由**现取**；框架也不持有任何项目字面量）",
            )
        #: ⚠ **端口号不许进这份参数**（三层架构 §7.3.3：它会给浏览器、也会进日志）——
        #: `port_file` 是**文件名**，写成一个 URL 就是把这个坑挖回来。
        if "://" in self.port_file:
            raise MechaError(
                f"MonitorSpec.port_file 是**文件名**，不是地址：{self.port_file!r}",
                kind="bad_monitor_spec",
                hint="端口**现取**（读那个文件），不写进声明 ⇒ 也不进给 AI/浏览器的任何文本",
            )
        seen: set[str] = set()
        for page in self.extra_pages:
            page.validate()
            if page.id in seen:
                raise MechaError(
                    f"附加页 id 重复：{page.id!r}（服务 {self.title or self.route_path}）",
                    kind="duplicate_extra_page_id",
                    hint="附加页 id 是页签的键（在本服务内唯一）",
                )
            seen.add(page.id)


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
    #: ⭐ **这个服务的监控面**（三层架构 §7.3.6）：它有监控端点时才写。
    #: ⚠ 这是**声明与面板之间唯一那份参数**的住处 —— 框架只生成与转交，
    #: **不解释、不写死任何项目值**（路由/标题/附加页都只有这一个家）。
    monitor: MonitorSpec | None = None
    default_on: bool = True
    env: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # ⚠ 这里原本用 None 再补默认值（避免把 LOCKED 抄第二遍）；但 dataclass 的默认值
        #   直接写 ``Mode.LOCKED`` 更好：它就是 ``authority.DEFAULT_MODE`` 的值，
        #   而"默认写权是 LOCKED"这条纪律由 ``authority`` 拥有 —— 这里只消费它。
        #
        # ⭐ **路径字段归一成 `Path`**（实测踩到的真坑）：项目手写 ``mecha_site.py`` 时
        #   很容易写成 ``cwd="C:\\..."``（字符串，尤其从配置里抄来的），而框架深处会抛
        #   ``TypeError: unsupported operand type(s) for /: 'str' and 'str'``
        #   —— ⚠ **报错完全看不出是哪份声明、哪个字段**（我这次就是这么撞上的）。
        #   ⇒ 声明是**数据**（M1：项目只交数据）⇒ 在**入口处**把"像路径的东西"归一，
        #     别让类型错误飘到几百行之外才炸。
        #   ⚠ **只归一 `str`/`os.PathLike`**；别的类型**仍然响亮失败**（不猜、不吞）——
        #     "能解释的输入就解释、解释不了就当场说清楚"。
        for field_name in ("cwd", "data_root"):
            value = getattr(self, field_name)
            if value is None or isinstance(value, Path):
                continue
            if isinstance(value, (str, os.PathLike)):
                object.__setattr__(self, field_name, Path(value))
            else:
                raise MechaError(
                    f"Service({self.id!r}).{field_name} 要是**路径**，给的是 "
                    f"{type(value).__name__}（{value!r}）",
                    kind="bad_service_field",
                    hint=f"写 `Path(...)` 或一个字符串；⚠ 声明里只放**数据**，"
                         f"放不了会被框架解释的东西就别放",
                )
        #: ⭐ `MonitorSpec.title` **缺省 ⇒ 用 `Service.id`**（§7.3.6）。
        #: ⚠ 这不是"给资产一个中性默认"（资产**未注入即抛**是刻意的）：这个缺省是
        #:   **框架从声明推出来的**（`svc_id`）⇒ 仍然只有一处真值，而且不必逼项目编一句标题。
        if self.monitor is not None and not self.monitor.title.strip():
            object.__setattr__(self, "monitor",
                               replace(self.monitor, title=self.id))  # type: ignore[arg-type]
        return None

    def monitor_port_file(self) -> "Path | None":
        """声明的**监控端口文件**落在哪儿（相对根拼成**绝对**路径；绝对路径原样）。

        ⭐ 为什么要有这个口子（而不是各调用点各拼一遍）：它是"**声明 → 文件落点**"的
        **唯一那处翻译**。今天有两个消费者，将来只会更多：

        | 消费者 | 要它干什么 |
        |---|---|
        | 框架的 host 插件参数（`dsh_panel.panel_port_files`） | 那个 node 侧插件**替项目取端口**（§7.3.8 末段的上提） |
        | `launch._detect_stale`（同族规则） | 判"陈旧端口文件"（⚠ 那里扫的是**就绪**用的那个文件，字段不同、**规则相同**） |

        ⚠ **相对哪个根**：`MonitorSpec.port_file` 写的是"相对根的文件名"，而这个"根"就是
        **`data_root`** —— 它正是框架 spawn 软件时注入的 `MECHA_SOFTWARE_ROOT`
        （软件**真正写文件**的那个根）。⚠ 三个真项目里 `data_root == cwd`（所以看不出差别），
        但两者一旦分家，**按 cwd 拼就会读错文件**（症状是"那个 tab 永远 503"）。
        """
        if self.monitor is None:
            return None
        p = Path(self.monitor.port_file)
        if p.is_absolute():
            return p
        root = self.data_root if self.data_root is not None else self.cwd
        joined = Path(root) / p
        #: ⚠ Windows 上 `/foo` 是"**有根无盘**"（`is_absolute()` 仍是 `False`）⇒
        #: 这种声明按根的盘符解析；再不行兜一层 `abspath`，保证**这一层出口恒为绝对路径**
        #: （下游那个变量的契约就是"绝对路径"，别把判断推给下游）。
        return joined if joined.is_absolute() else Path(os.path.abspath(joined))

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
        #: ⭐ 监控声明（有就校验）：路由/端口文件名/附加页都必须在**声明期**就响亮失败
        #: —— 它们会变成浏览器 fetch 的地址与面板上的页签，写歪的症状是"面板空的"
        #: （最坏的一类假绿），所以不许飘到运行期。
        if self.monitor is not None:
            self.monitor.validate()


#: ⭐ **那一个共用 dsh 的 child id**（全生态一个；框架的约定，不是项目的字段）。
#: ⚠ 它**必须只有一个家**：这个 id 同时是
#: ① 面板上「dsh 存活滑块」要操作的对象（§7）、② supervisor 子进程表里的键（`dsh` **永远最后**）、
#: ③ `prepare_hooks` 里"只给 dsh 塞 overlay"的判据（`spec.id == DSH_CHILD_ID`）。
#: ⇒ 三处各写一遍字面量 = 迟早对不上（改一处漏一处，症状是"滑块点了没反应"或"dsh 没拿到聚合端点"）。
DSH_CHILD_ID = "dsh"


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
        """把声明拼成命令行（``--patch`` 与**端口偏好**在这里落成 argv）。

        ⚠⭐ **`port_preference` 必须真的进 argv**（实测抓到的"死缝"，R17）：
        它原来只被**比对**（合并时的身份字段）与**搬运**，**从没被使用** ⇒
        ⚠ **框架不知道 dsh 会绑哪个端口** ⇒ 它**没法替用户打开 dsh**
        （用户实测："dsh 面板还没唤起"）。
        ⇒ 现在显式 `--port <pref>`（⚠ dsh 的 `--port` 是 **web-app** 的参数，经 `webStartup` 传）。
        """
        out = list(self.command)
        for p in self.patches:
            out += ["--patch", str(p)]
        out += ["--port", str(int(self.port_preference))]
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
