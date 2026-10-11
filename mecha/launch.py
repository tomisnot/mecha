"""启动入口：发现站点 → 清陈旧残留 → 按**显式顺序**起 → 开选择/状态页。

## 一件事，两次交付

``python -m mecha`` 起 supervisor + 打开**选择页**（阶段 A）；
勾完点「启动 ▸」后**同一个页面**原地变成**状态页**（阶段 B）。``--all`` 跳过选择页
（默认全启用）——给 ``.bat`` 用。

## 三条实测教训，写进代码的地方

1. **启动顺序是拓扑序**，不是"声明顺序"：``svc* → aggregator → dsh``。聚合端点要用各
   软件**回读到的端口**；dsh 要用聚合端点的 URL ⇒ 顺序与"就绪后钩子"都必须**显式**
   （修正 1）。本步不实现聚合，但 :class:`StartupPlan.ordered` 与 ``Supervisor`` 的
   钩子已经把这个形状留好。
2. **启动前必须清陈旧残留**：上次被强杀留下的孤儿 + 陈旧端口文件。实测教训：
   EL 的 ``.mcp-port`` 曾静躺 **264.5 小时**、还被人当成"就绪"（假绿）。
   清掉的东西**必须出现在状态里**（``Supervisor.cleaned``，修正 2）。
3. **单实例**：同一份 runtime 只能有一个 supervisor。⚠ 探活**不许用 ``os.kill(pid, 0)``**
   （Windows 上它会真把进程杀掉）⇒ 用 :func:`mecha.supervise.pid_alive`。

## Known Limitations and Deferred Work

- **不做聚合端点**（步骤 2）：``StartupPlan`` 里没有 aggregator 条目，钩子也留空。
- **不做 GUI 窗口**（三层架构 §3）：窗口开在**软件进程内**，归项目的步骤 2。
  框架侧只声明 ``has_gui``（面板据此决定显不显示「切到手动」），**不 spawn 人的那条腿**
  —— 人的那条腿不是 supervisor 的子进程（A1/A2）。
- 站点发现只扫**约定文件名** ``mecha_site.py``：不做插件入口点/配置文件格式——
  那会引入第二套"声明住哪"的真值（n=1 不预建）。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import socket
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .authority import Mode
from .dsh_panel import with_panel_contributions
from .errors import MechaError
from .software_control import SoftwareControl
from .site import (DSH_CHILD_ID, AutoShutdown, DSHSpec, Service, Site, Strategy)
from .supervise import ChildSpec, StaleItem, Supervisor, pid_alive, which_or_raise

#: 站点声明的**约定文件名**（暴露模块级 ``SITE: Site``）。
SITE_FILENAME = "mecha_site.py"
#: 站点声明的搜索根（``os.pathsep`` 分隔）；缺省用启动时的 CWD。
SITE_ROOTS_ENV = "MECHA_SITE_ROOTS"
#: 运行期目录名（相对启动根）。
RUNTIME_DIR_NAME = "_mecha_runtime"
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}


# --------------------------------------------------------------------------- 发现


def _iter_site_files(root: Path) -> list[Path]:
    """在 ``root`` 下找 ``mecha_site.py``（跳过 .git / node_modules / 隐藏目录）。"""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in _SKIP_DIRS and not d.startswith(".")]
        if SITE_FILENAME in filenames:
            found.append(Path(dirpath) / SITE_FILENAME)
    return sorted(found)


def _load_site_module(path: Path) -> Site:
    """把 ``mecha_site.py`` 当**按路径加载**的模块读进来，取 ``SITE``。

    ⚠ 按路径加载（而不是 import 包名）：站点声明住在**项目**里、不在 sys.path 上，
    而且多个项目可能各有一份同名模块 ⇒ 用包名 import 会撞。
    """
    spec = importlib.util.spec_from_file_location(f"mecha_site_{abs(hash(str(path)))}", path)
    if spec is None or spec.loader is None:
        raise MechaError(f"读不了站点声明：{path}", kind="site_load_failed")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:                            # noqa: BLE001 - 声明本身坏了要响亮
        raise MechaError(
            f"站点声明执行失败：{path}（{type(exc).__name__}: {exc}）",
            kind="site_load_failed",
            hint="站点声明是被**执行**的 Python：语法/import 错误都会在这里炸出来",
        ) from exc
    site = getattr(mod, "SITE", None)
    if site is None:
        raise MechaError(
            f"站点声明里没有模块级 SITE：{path}",
            kind="site_missing_SITE",
            hint="约定：mecha_site.py 暴露 SITE: mecha.site.Site",
        )
    if not isinstance(site, Site):
        raise MechaError(
            f"SITE 不是 mecha.site.Site：{path}（收到 {type(site).__name__}）",
            kind="site_bad_type",
        )
    site.validate()
    return site


def load_sites(roots: Sequence[Path] | None = None) -> tuple[Site, ...]:
    """扫出所有站点声明。**一个都没有 ⇒ 响亮失败**（不静默起个空站）。"""
    if roots is None:
        env = os.environ.get(SITE_ROOTS_ENV, "").strip()
        roots = ([Path(p) for p in env.split(os.pathsep) if p.strip()]
                 if env else [Path.cwd()])
    sites: list[Site] = []
    seen_files: list[Path] = []
    for root in roots:
        root = Path(root)
        if not root.exists():
            raise MechaError(
                f"站点搜索根不存在：{root}",
                kind="site_root_missing",
                hint=f"检查 --root / ${SITE_ROOTS_ENV}",
            )
        for f in _iter_site_files(root):
            seen_files.append(f)
            sites.append(_load_site_module(f))
    if not sites:
        raise MechaError(
            f"没找到任何站点声明（找过 {[str(r) for r in roots]} 下的 {SITE_FILENAME}）",
            kind="no_site_found",
            hint=f"每个项目在仓根放一个 {SITE_FILENAME} 并暴露 SITE；"
                 f"或用 --root / ${SITE_ROOTS_ENV} 指定搜索根",
        )
    names = [s.name for s in sites]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise MechaError(
            f"站点名重复：{sorted(dupes)}",
            kind="duplicate_site_name",
            hint="站点名要唯一（状态页与状态文件按它归属）",
        )
    return tuple(sites)


# --------------------------------------------------------------------------- 计划


@dataclass(frozen=True)
class StartupPlan:
    """一份**可执行的启动计划**（顺序是显式的，见修正 1）。

    ⚠ **多站点（§1.1）**：``sites`` 是**全部**站点（原来的 ``site: Site`` 在多站点下
    **没有意义**——没有"那个站点"）。需要"第一个"的地方走 :attr:`first`，
    且**只许用于展示/命名**；⚠ §1.1 规则 3：**runtime 不取决于谁排第一**。
    """

    sites: tuple[Site, ...]
    #: ⭐ **启动顺序**（拓扑序）：``svc* → aggregator → dsh``，**dsh 永远最后**。
    ordered: tuple[ChildSpec, ...]
    #: 待清理的陈旧残留（交给 :meth:`Supervisor.clean_stale`）。
    stale: tuple[StaleItem, ...]
    enabled: tuple[str, ...]

    @property
    def first(self) -> Site:
        """第一个站点（**只给展示/命名用**，不代表"这次启动的站点"）。"""
        return self.sites[0]


def _port_file_of(svc: Service) -> Path | None:
    name = svc.ready.port_file
    if not name:
        return None
    return (svc.cwd / name) if not Path(name).is_absolute() else Path(name)


def flat_services(sites: Sequence[Site]) -> tuple[Service, ...]:
    """⭐ **服务平铺**（§1.1 规则 1）：全部站点的服务按**稳定顺序**排成一个序列。

    稳定顺序 = **站点出现顺序**、站点内**声明顺序**（§1.1 规则 4）。
    ⚠ 站点之间互不知道对方存在 ⇒ 这里**不做**任何"跨站点依赖/组"的编排。
    """
    return tuple(svc for site in sites for svc in site.services)


def assert_service_ids_global_unique(sites: Sequence[Site]) -> None:
    """⭐ **服务 id 跨站点全局唯一**（§1.1 规则 2/7）——它是**工具前缀**。

    ⚠ 站内查重已经在 ``Site.validate`` 里（含**大小写不敏感**）；这里补的是
    **跨站点**那一层：同一个 launcher 会把多个仓的服务平铺进**同一张** child 表，
    撞名 ⇒ ``supervisor`` 的 child id 冲突、AI 面前工具名冲突。
    """
    seen: dict[str, str] = {}
    for site in sites:
        for svc in site.services:
            key = svc.id.lower()          # ⚠ 工具名会小写化 ⇒ 查重也按小写
            if key in seen:
                raise MechaError(
                    f"服务 id 跨站点重复：{svc.id!r}（{site.name}）与 {seen[key]!r}",
                    kind="duplicate_service_id",
                    hint="服务 id 是**工具前缀**（<svc_id>_<tool>）⇒ 全生态唯一；"
                         "改掉其中一个项目的声明",
                )
            seen[key] = f"{svc.id}（{site.name}）"


def merge_dsh(sites: Sequence[Site]) -> "tuple[ChildSpec, DSHSpec]":
    """把各站点的 dsh 声明合并成**那一个** dsh（三层架构 §1.1.1）。⭐ 站点们**共同贡献**。

    ## 为什么不是"只准一家声明"（实测踩到的）

    初次实现按"只准一个站点声明 dsh"写 ⇒ 而 `site.py` 里 `dsh` **必填**、
    三家真项目**各声明一个** ⇒ **多站点必然报错**（`有 N 个站点声明了 dsh`）。
    ⚠ 而"只准一家"本身也是错的：**每个项目都要把自己的监控面板挂上**
    ⇒ 一个 dsh 要能看到**所有**项目 ⇒ 插件清单必须是**并集**。

    ## 规则

    | 项 | 规则 |
    |---|---|
    | 身份（`command`/`port_preference`/`port_tries`/`stop_timeout`/`ready`） | 都给了就**必须一致**；不一致 ⇒ **响亮失败** |
    | `cwd` | 取**第一个**声明者（站点顺序稳定） |
    | `patches`/`plugin_artifacts`/`env` | ⭐ **取并集**（顺序 = 站点出现顺序）；⚠ `env` 同名不同值 ⇒ **响亮失败** |
    | ⭐ **框架那一份** | **框架的 patch + 框架的插件 + 两个面板变量**（`dsh_panel.with_panel_contributions`）——**恒加上**，两个分支都加 |
    | 一个都没声明 | 框架默认（`command=("dsh",)`、`cwd=`第一个站点的 cwd、3081/10/8.0） |
    """
    decls = [(s, s.dsh) for s in sites if getattr(s, "dsh", None) is not None]
    if not decls:
        #: ⭐ "架构直接安排好了"：项目**可以完全不管 dsh**。
        first_svc = flat_services(sites)
        cwd = first_svc[0].cwd if first_svc else Path.cwd()
        #: ⚠⭐ **`dsh` 不带 profile 会立刻退出**（实测：日志里刷 `error: --profile <name> is required`
        #: ⇒ supervisor 反复重启 ⇒ `state=unhealthy` ⇒ **用户根本看不到 dsh**）。
        #: `dsh --help` 明写 `dsh web` = 起 web profile ⇒ 默认值必须是它。
        dsh = DSHSpec(command=("dsh", "web"), cwd=cwd)
        #: ⚠ 这一支也**必须**带上框架自己的面板插件：项目连 dsh 都没声明时，
        #: 更不该轮到它去挂框架插件（§7.3.3）。
        dsh = with_panel_contributions(dsh, sites)
        return _dsh_child(dsh), dsh

    head_site, head = decls[0]
    #: 身份必须一致（"一个 dsh" = **只有一个身份**；静默取一家 = 我以为起的是我那种）
    for site, d in decls[1:]:
        for field in ("command", "port_preference", "port_tries", "stop_timeout", "ready"):
            if getattr(d, field) != getattr(head, field):
                raise MechaError(
                    f"两个站点的 dsh 声明不一致：{head_site.name} 与 {site.name} "
                    f"的 `{field}` 不同（{getattr(head, field)!r} vs {getattr(d, field)!r}）",
                    kind="dsh_decl_conflict",
                    hint="dsh 是**全生态一个**（§1.1.1）⇒ 各站点给的**身份字段必须一致**；"
                         "**插件清单（patches/plugin_artifacts/env）**才是各站点各自贡献的部分",
                )
    patches: list[Path] = []
    artifacts: list[Path] = []
    env: dict[str, str] = {}
    for site, d in decls:
        patches += [p for p in d.patches if p not in patches]
        artifacts += [p for p in d.plugin_artifacts if p not in artifacts]
        for k, v in d.env.items():
            if k in env and env[k] != v:
                raise MechaError(
                    f"两个站点给 dsh 的同名环境变量值不同：`{k}` "
                    f"（{env[k]!r} vs {v!r}，来自 {site.name}）",
                    kind="dsh_env_conflict",
                    hint="dsh 只有一个进程 ⇒ 同名变量只能有一个值；请改成不同名，或统一取值",
                )
            env.setdefault(k, v)
    merged = DSHSpec(
        command=head.command, cwd=head.cwd,
        patches=tuple(patches), plugin_artifacts=tuple(artifacts),
        port_preference=head.port_preference, port_tries=head.port_tries,
        env=env, ready=head.ready, stop_timeout=head.stop_timeout)
    #: ⭐ **框架自己的 dsh 贡献**（监控面板插件 + 那两个变量）：由**框架的 patch** 带上
    #: （§7.3.3 定案）⇒ 新项目**不挂**框架插件、也不手抄那份参数。
    #: ⚠ 放最后：框架的变量与站点声明撞名要**响亮失败**（见 :func:`with_panel_contributions`）。
    merged = with_panel_contributions(merged, sites)
    return _dsh_child(merged), merged


def _dsh_child(dsh: DSHSpec) -> ChildSpec:
    """dsh 声明 → child（⚠ 名字解析走 `which_or_raise`：起不来要**响亮**）。"""
    return ChildSpec(
        id=DSH_CHILD_ID, argv=which_or_raise(dsh.argv()), cwd=dsh.cwd, env=dict(dsh.env),
        ready=dsh.ready, stop_timeout=dsh.stop_timeout, data_root=None,
    )


def build_children(sites: Sequence[Site], enabled: Sequence[str]) -> tuple[ChildSpec, ...]:
    """把勾中的服务 + dsh 翻成 child 列表（**顺序 = 拓扑序**，dsh **永远最后**）。"""
    kids: list[ChildSpec] = []
    for svc in flat_services(sites):            # ⭐ 站点出现顺序、站内声明顺序
        if svc.id in enabled:
            kids.append(svc.child())
    dsh_child, _ = merge_dsh(sites)
    kids.append(dsh_child)                      # ⭐ dsh 最后（它要用聚合端点的地址）
    return tuple(kids)


def _detect_stale(sites: Sequence[Site],
                  known_pids: dict[str, int]) -> tuple[StaleItem, ...]:
    """探"上次留下的残留"：**活着的孤儿进程** + **陈旧端口文件**。

    ⚠ 判定与清理必须同一处（清理在 :meth:`Supervisor.clean_stale`）：分家会出现
    "判了没清 / 清了没记"。
    """
    stale: list[StaleItem] = []
    for cid, pid in known_pids.items():
        if pid and pid_alive(pid):
            stale.append(StaleItem(
                kind="orphan", target=f"{cid} pid={pid}", pid=pid,
                why="上次记录在案、现在**还活着**（上次收尾没杀干净）"))
    for svc in flat_services(sites):
        pf = _port_file_of(svc)
        if pf is None or not pf.exists():
            continue
        # 端口文件还在，但它记的端口**没人在听** ⇒ 陈旧（这正是那个假绿的现场）
        from .portfile import read_port
        port = read_port(pf)
        listening = _listening(port) if port else False
        if not listening:
            stale.append(StaleItem(
                kind="port_file", target=str(pf), path=pf,
                why=f"端口文件在、但 {port} 上没人在听 ⇒ 陈旧残留"
                    f"（不清理会让'存在且非空'被误判成就绪）"))
    #: ⭐ **控制端点的端口文件也要探/清**（原来只管就绪用的 `.mcp-port`）：
    #: 陈旧的 `.control-port` 会留到下次启动，而**面板按它定位控制端点** ⇒
    #: 若那个端口被别的东西占用，面板会读到**假值**。⚠ 实测：EL/GP 仓根残留的
    #: `.control-port` 甚至让它们在各自工作树里显示成**未跟踪文件**。
    from .portfile import read_port
    from .software_control import control_port_path
    for svc in flat_services(sites):
        if svc.data_root is None:
            continue
        cpf = control_port_path(svc.data_root)
        if not cpf.exists():
            continue
        cport = read_port(cpf)
        if not (cport and _listening(cport)):
            stale.append(StaleItem(
                kind="port_file", target=str(cpf), path=cpf,
                why="控制端点的端口文件在、但没人在听 ⇒ 陈旧残留"
                    "（⚠ 面板按它定位控制端点 ⇒ 不清理可能让它读到**假值**）"))
    return tuple(stale)


def _listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def plan_startup(sites: Sequence[Site], enabled: Sequence[str],
                 known_pids: dict[str, int] | None = None) -> StartupPlan:
    """产出一份启动计划（顺序 + 待清理残留 + 勾选）。**不做任何副作用**。

    ⚠ **收全部站点**（§1.1）；``enabled`` 是**跨站点**的服务 id 集合。
    """
    ids = {s.id for s in flat_services(sites)}
    chosen = tuple(sid for sid in (s.id for s in flat_services(sites))
                   if sid in enabled and sid in ids)
    return StartupPlan(
        sites=tuple(sites),
        ordered=build_children(sites, chosen),
        stale=_detect_stale(sites, known_pids or {}),
        enabled=chosen,
    )


# --------------------------------------------------------------------------- 单实例


#: ⭐ **面板端口文件**：无黑框入口下 stdout 没人看 ⇒ 端口必须落文件，
#: 否则 `--stop` / `stop_mecha.pyw` **没有确定的找法**（与 `.mcp-port` 同族）。
PAGE_PORT_FILE = ".page-port"
#: ⭐ **聚合端点（AI 那条腿）的端口文件**：`<runtime>/.aggregate-port`。
#: 存在的意义：别的入口（人、脚本、面板）能在**启动时**重新解析"当前 AI 面在哪"——
#: 与监控面那套端口文件**同一套发现手法**（⚠ 具体文件名归项目声明，框架不写死）。
AGGREGATE_PORT_FILE = ".aggregate-port"


def _pid_file(runtime: Path) -> Path:
    return runtime / "supervisor.pid"


def acquire_singleton(runtime: Path) -> int:
    """单实例锁：pid 文件 + 探活接管。返回本进程 pid。

    ⚠ **不许用 ``os.kill(pid, 0)``**：Windows 上它会把被探的进程**杀掉**
    （ML 的 ``pid_alive`` 注释记着同一实测）⇒ 用 ``pid_alive``。
    """
    runtime.mkdir(parents=True, exist_ok=True)
    pf = _pid_file(runtime)
    if pf.exists():
        try:
            old = int(pf.read_text(encoding="utf-8").strip() or "0")
        except (OSError, ValueError):
            old = 0
        if old and old != os.getpid() and pid_alive(old):
            raise MechaError(
                f"已经有一个 supervisor 在跑（pid={old}）",
                kind="supervisor_already_running",
                hint="同一份 runtime 只允许一个 supervisor。要停它："
                     "① 双击入口旁的 stop_mecha.pyw；② python -m mecha --stop；"
                     "③ 面板底部的「收尾退出」。"
                     "⚠ 别用任务管理器强杀（会跳过收尾）。换一份 runtime 用 --root",
            )
        # 陈旧 pid 文件（进程已死）⇒ 接管，不静默留下"永远锁着"的假象
    pf.write_text(str(os.getpid()), encoding="utf-8")
    return os.getpid()


# --------------------------------------------------------------------------- 状态文件


def _runtime_root() -> Path:
    """运行期根：**启动根**下的约定目录。

    ⚠ §1.1 规则 3：**不取决于任何站点** —— 原先签名是 ``_runtime_root(site)``（虽然
    参数没用过，但调用点写的是 ``_runtime_root(sites[0])``）⇒ **去掉入参**，
    免得下次有人真拿它去算，让 runtime 随"谁排第一"漂。
    """
    return Path.cwd() / RUNTIME_DIR_NAME


def _read_last_status(runtime: Path) -> dict:
    p = runtime / "site.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# --------------------------------------------------------------------------- 控制器


class LaunchController:
    """选择页/状态页共用的控制面（**页面只读它 + 一个 start**）。"""

    def __init__(self, *, sites: tuple[Site, ...], runtime: Path,
                 supervisor: Supervisor) -> None:
        self.sites = sites
        self.runtime = runtime
        self.super = supervisor
        self._started = False
        self._enabled: tuple[str, ...] = ()
        self._last_error = ""
        #: 起完 child 之后的挂点（``launch.main`` 用它在启动后把后端接进聚合端点）。
        #: ⚠ 做成**属性**而不是构造参数：控制器不该认识聚合端点（那是 launcher 的编排），
        #: 而"起完之后做点什么"是它唯一需要提供的口子。
        self.after_start: Callable[[], None] | None = None
        #: 运行中开关单个软件要走**聚合端点**那条正规路径（含端点同步）；
        #: 没接时退化成"只动 supervisor"（判据/最小装配够用）。
        self.agg_runtime: object | None = None
        #: 面板在**启动之前**也要能表达"我打算开哪几个" ⇒ 意图单独存一份。
        self._intent: dict[str, bool] = {}
        #: 面板**意图**上的写权归属（仅在按钮成功之后更新；见 control_write_authority）
        self._mode_intent: dict[str, str] = {}

    # ---- 读 ----
    def last_status(self) -> dict:
        """上次的状态（选择页用：让人看见"上次谁是不健康的"）。"""
        return _read_last_status(self.runtime)

    def started(self) -> bool:
        return self._started

    def state(self) -> dict:
        st = self.super.state()
        st["started"] = self._started
        st["enabled"] = list(self._enabled)
        st["sites"] = [s.name for s in self.sites]
        # ⚠ **"启动失败"与"读不到状态"必须分开**（页面文案也分开）：
        #    - last_error 非空 ⇒ 启动/清理**失败过**（有具体原因）
        #    - 否则 state 为空列表 ⇒ 还没起（不是"读不到"）
        st["last_error"] = self._last_error
        #: 启动**之前**的勾选意图（面板在未启动时也要能表达"我打算开哪几个"）。
        st["intent"] = self.intent()
        #: ⭐ **AI 那条腿的两个地址**（T4，2026-10-10）：给人核对"当前世界"用 ——
        #: 聚合端点按**端口文件现取**（每次启动会变，所以不能写死在页面里）；
        #: dsh 用声明的首选端口。⚠ 这两个地址**只给人看**，不进 AI 面（控制信息不上 AI 腿）。
        from .portfile import read_port
        agg_port = read_port(self.super.log_dir.parent / AGGREGATE_PORT_FILE)
        spec = self.dsh_spec()
        st["ai_face"] = {
            "aggregate_url": f"http://127.0.0.1:{agg_port}/mcp" if agg_port else "",
            "dsh_url": (f"http://127.0.0.1:{int(spec.port_preference)}/"
                        if spec is not None else ""),
        }
        return st

    def dsh_spec(self) -> "DSHSpec | None":
        """⭐ 那**一个**共用 dsh 的声明（合并后）—— 供面板**始终**列出它一行（§7）。

        ⚠ 为什么要有这个口子：面板原先只在"child 表里有 dsh"时才渲染它
        ⇒ **启动前那一行是缺的**，而 §7 的定稿形态是
        ``dsh [●───] 存活（它只是 AI 那条腿的载体）`` —— **控制台的一项**，不是"起了才有"。

        ⚠ 合并失败（例如两个站点的 dsh 身份不一致）⇒ 返回 ``None``：
        那种情况 ``main()`` 会**响亮失败**，面板没必要在这里再抛一次。
        """
        try:
            _, dsh = merge_dsh(self.sites)
        except MechaError:
            return None
        return dsh

    def services(self) -> tuple[Service, ...]:
        """⭐ **全部站点的全部已声明服务**（§1.1 规则 6：面板**始终**列全部）。

        ⚠ 与"勾过的"无关；⚠ 与"哪个站点排第一"无关（平铺）。
        """
        return flat_services(self.sites)

    def intent(self) -> dict[str, bool]:
        """每个软件"打算开着 / 打算关着"。

        ⚠ 它是**意图**、不是运行事实：启动前由勾选决定；启动后由**真状态**决定
        （面板上"存活"那一栏永远读 `supervisor.state()`，不读这里——铁律 7）。
        """
        out: dict[str, bool] = {}
        for svc in self.services():
            if svc.id in self._intent:
                out[svc.id] = self._intent[svc.id]
            else:
                out[svc.id] = bool(svc.default_on)
        return out

    def set_intent(self, svc_id: str, enabled: bool) -> None:
        """只记**意图**（启动前用）——它不下任何"起/停"的副作用。"""
        if svc_id not in {s.id for s in self.services()}:
            raise MechaError(
                f"没有这个软件：{svc_id!r}",
                kind="unknown_service",
                hint=f"已知软件：{sorted(s.id for s in self.services())}（见站点声明）",
            )
        self._intent[svc_id] = bool(enabled)

    def set_service_enabled(self, svc_id: str, enabled: bool) -> dict:
        """⭐ **运行中开关单个软件**（面板的"存活滑块"）。

        - 关 ⇒ ``supervisor.stop(child_id)``（**停进程、不删声明** ⇒ 面板那项不消失）；
        - 开 ⇒ ``supervisor.start(svc.child())``（⚠ 走 ``Service.child()`` 那**一处**翻译：
          ``MECHA_SOFTWARE_ROOT`` / ``MECHA_INITIAL_MODE`` 的注入就在那里，**别在这儿抄第二遍**）；
        - ⚠ **端点侧跟着同步**（``sync_backends``）：否则 AI 手里还留着已关软件的工具。
          （"调它响亮失败"由 2a 的墓碑语义负责 —— 那是**第二条**防线，不是替代。）

        未启动整站时 ⇒ **只记意图**（不 spawn）：面板在启动前也要能表达"我打算开哪几个"。

        ⚠ **dsh（`DSH_CHILD_ID`）走另一支**（:meth:`set_dsh_enabled`）：它不在 `services()` 里
        —— 载体不是服务 ⇒ 不能落到下面"没有这个软件"那条上去（那正是它原先点了报错的原因）。
        """
        #: ⭐ dsh 那一行的滑块：**关→开 = 重启 harness**（§7 定案），恒为 live、不走意图。
        if svc_id == DSH_CHILD_ID:
            return self.set_dsh_enabled(enabled)
        svc = next((s for s in self.services() if s.id == svc_id), None)
        if svc is None:
            raise MechaError(
                f"没有这个软件：{svc_id!r}",
                kind="unknown_service",
                hint=f"已知软件：{sorted(s.id for s in self.services())}",
            )
        self._intent[svc_id] = bool(enabled)
        if not self._started:
            return {"ok": True, "id": svc_id, "enabled": bool(enabled), "applied": "intent"}
        try:
            if self.agg_runtime is not None:
                # ⭐ 正规路径在 AggregateRuntime 里（它在 supervisor 控制面之外**还**管端点同步）
                if enabled:
                    self.agg_runtime.open_service(svc_id)
                else:
                    self.agg_runtime.close_service(svc_id)
            else:                       # 没接聚合端点时（判据/最小装配）也能开关
                cid = svc.child().id
                if enabled:
                    self.super.start(svc.child())
                elif cid in self.super.state_ids():
                    self.super.stop(cid)
            if enabled:
                self._enabled = tuple(dict.fromkeys((*self._enabled, svc_id)))
            else:
                self._enabled = tuple(x for x in self._enabled if x != svc_id)
            self._save_status()
            return {"ok": True, "id": svc_id, "enabled": bool(enabled), "applied": "live"}
        except MechaError as exc:
            self._last_error = f"{exc.kind}：{exc.message}"
            self._save_status()
            return {"ok": False, "id": svc_id, "error": self._last_error}

    # ---- dsh 的存活（①：载体自己的起停；§7 定案与软件滑块**同形**）----
    def dsh_child(self) -> ChildSpec | None:
        """⭐ 那**一个**共用 dsh 的 child（**每次现算**）。

        ⚠ 为什么返回 ``None`` 而不是抛：合并失败（各站点 dsh 身份/环境变量冲突）时
        ``main()`` 会**响亮失败**，面板不该在这里再炸一次（与 :meth:`dsh_spec` 同一口径）。

        ⚠ 为什么**每次现算**而不是缓存：聚合端点 overlay 由 ``prepare_hooks`` 在
        **spawn 那一刻**塞进 ``--patch``（``with_overlay_patch``）——
        若把"上一次 spawn 用过的那份 argv"缓存下来再喂给 ``start()``，
        就会**叠出第二个 ``--patch``**（overlay 写两遍）。
        """
        try:
            child, _ = merge_dsh(self.sites)
        except MechaError:
            return None
        return child

    def set_dsh_enabled(self, enabled: bool) -> dict:
        """⭐ 面板上 **dsh 那一行**的存活滑块（§7 定案：**关掉再打开 = 重启 harness**）。

        ## 为什么它就是"其他软件那个滑块"的同形

        | | 软件滑块 | dsh 滑块 |
        |---|---|---|
        | 谁执行 | `supervisor.start/stop`（**唯一进程控制面**） | ⭐ **同一条路** |
        | 关→开 | 新进程（= 热更新的结构前提） | ⭐ **新进程**（改了 dsh 侧插件/配置 ⇒ 关开即生效） |
        | 失败 | 响亮（`MechaError` ⇒ 面板如实报） | ⭐ 同 |

        ## 三点与软件滑块**不同**（都不是随手，各对着一条纪律）

        1. ⚠ **不走"勾选意图"**：意图是"**打算开哪几个服务**"的集合，而 dsh 是**载体**、
           不在 ``services()`` 里 ⇒ 它**恒为 live**：点开就真起、点关就真停，
           **即使整站还没启动**（§1 的三个生命周期：①软件存活与②客户端连接是两件事；
           需求 11 也要求"滑块随时可动"）。
        2. ⚠ **每次现算 child**（:meth:`dsh_child`）：见那里"别叠 overlay"的说明。
        3. ⚠ **起不来要响亮**：`merge_dsh` 会校验站点间 dsh 身份一致、`which_or_raise`
           会解析命令 ⇒ 失败**抛**，绝不回一句"已启动"（"静默假成功"是本工程最忌的形态）。
        """
        child = self.dsh_child()
        if child is None:
            self._last_error = ("dsh 声明合并失败（各站点的 dsh 身份/环境变量不一致）"
                                "⇒ 面板没法起它")
            self._save_status()
            return {"ok": False, "id": DSH_CHILD_ID, "error": self._last_error}
        try:
            if enabled:
                #: 已停/已退出 ⇒ supervisor **重启**它（新实例）；还在跑 ⇒ 响亮拒绝
                #: （面板的 checkbox 只在"当前是关"时才发 on=1，所以正常路径不会撞）。
                self.super.start(child)
            elif DSH_CHILD_ID in self.super.state_ids():
                self.super.stop(DSH_CHILD_ID)      # 停进程、**保留 spec**（关→开才可能）
            self._save_status()
            return {"ok": True, "id": DSH_CHILD_ID, "enabled": bool(enabled),
                    "applied": "live"}
        except MechaError as exc:
            self._last_error = f"{exc.kind}：{exc.message}"
            self._save_status()
            return {"ok": False, "id": DSH_CHILD_ID, "error": self._last_error}

    # ---- 写权（③）：走"控制端点"那条腿（§7.1.1）----
    def write_authority_state(self, svc_id: str) -> dict:
        """面板要的事实：⭐ **能不能切** / **当前写权真值** / **该给哪个动作**。

        ⭐ **真值从控制端点读回来**（契约 §7.1.1 的 ``read_write_authority``），
        **不是**面板记着自己点过什么——面板记账 = **第二份真值**。

        ⚠ 读不到时**如实说"读不到"**：不显示旧值、也不显示 0
        （铁律 8：读不到 ≠ 确实没有）。

        ## ⚠⚠ 两件事**必须分开报**（2026-10-09 审计抓到的偏差，§7.1.1 已补）

        | 问的问题 | 取决于 |
        |---|---|
        | **能不能切写权**？ | ⭐ **控制端点通不通**（= `ok`） |
        | **有没有"手动面"可开**？ | ⭐ **`has_gui`** |

        ⚠ 原先 `has_gui=False` 时直接回 `ok=False` ⇒ **切写权被拒**
        ⇒ ⭐ **正是 §7.1.1 那条裁决要避免的盲区**（"裁决写在端点那层，
        同一件事在控制器这层又长回来了"）。⇒ 现在**解耦**：
        `has_gui=False` 只是**没有"手动面"那一档**，**不是**"不能切写权"。

        ⭐ 面板据此给**不同名字的动作**（干的事真的不同）：
        `has_gui=True` ⇒ 「切到手动」（切写权 **+** 开窗）；
        `has_gui=False` ⇒ 「收回写权（冻结）」（**只**切写权 ⇒ `locked`，⚠ **不**调开窗）。
        """
        svc = next((s for s in self.services() if s.id == svc_id), None)
        if svc is None:
            return {"ok": False, "why": f"没有这个软件：{svc_id!r}"}
        if svc.data_root is None:
            #: ⚠ 控制端点按**数据根**定位（`.control-port` 在数据根下）⇒ 没声明就定位不到。
            #: ⚠ **不许把原始 `TypeError` 漏给页面**（实测就是这么漏的：
            #: `expected str, bytes or os.PathLike object, not NoneType` —— 看页面的人读不懂）。
            #: 铁律 8：读不到要**说得清**，而且与"确实没有"分开说。
            return {"ok": False, "why": "这个软件没声明 data_root ⇒ 定位不到它的控制端点",
                    "has_gui": bool(svc.has_gui), "read": None, "read_error": "读不到",
                    "action": self._authority_action(svc)}
        #: ⭐ 能不能切，只看**控制端点通不通** —— ⚠ 与 `has_gui` **无关**。
        ctl = SoftwareControl.connect(svc_id, svc.data_root)
        avail, why = ctl.available()
        read = self._read_true_authority(ctl, avail)
        return {"ok": avail, "why": why, "has_gui": bool(svc.has_gui),
                "read": read, "read_error": "" if read else ("读不到" if avail else why),
                "action": self._authority_action(svc)}

    @staticmethod
    def _authority_action(svc: Service) -> str:
        """面板上"归人"那个按钮该发**哪个动作**（⚠ 与 `control_write_authority` **同一套词**）。

        | 返回 | 按钮（面板的标签） | 干什么 |
        |---|---|---|
        | `"human"` | 「切到手动」 | 切写权(=human) **+ 开窗**（两步） |
        | `"freeze"` | 「收回写权（冻结）」 | **只**切写权 ⇒ `locked` |
        | ⭐ `"none"` | **不画按钮** | ⭐ **共用档**（`Mode.OPEN`）：本项目**没有写权切换需求** |

        ⚠ **返回值必须是 `control_write_authority` 认的动作名**（`human`/`freeze`），
        **不是**按钮的显示文字 —— ⚠ 我第一版在这里返回 `"manual"`，
        而控制器只认 `"human"` ⇒ 面板点下去会**报 `bad_write_action`**
        （**两套词**是这种 bug 的温床：一个东西两个名字，迟早对不上）。
        **标签**由面板按这个动作决定（UI 的事归 UI）。

        ⚠ **为什么无手动面时是 `freeze`（`locked`）而不是 `human`**：
        `Mode.HUMAN` 的语义是"**人侧通道可写**"，而**没有手动面的软件里人没有可写通道**
        ⇒ 给 `HUMAN` 等于**贴一个"写权归人"的标签、实际谁都不能写** —— **骗人的标签**。
        人的真实意图是"**先把写权收回**" ⇒ `locked`（谁都不能写）**才是老实的那一个**。

        ⭐⭐ **为什么共用档没有按钮**（2026-10-10 用户裁决，`"none"` 这一档：
        **"如果有 AI 与人共用的模式，说明该项目没有写权切换的需求"**）：
        `Mode.OPEN` 下**两侧都能写** ⇒ "切到手动/交还给 AI"这两个标签**描述不了任何真实差别**
        （按下去也不改变谁能写）⇒ 画出来就是**假按钮**。这类项目（如 GP：设计上只有一种模式）
        的切换入口在**它自己**（`how_to_open` 那句话），不在启动控制台。
        ⚠ 判据 `checks/authority_shared_mode_selfcheck.py` 钉住"共用档不画按钮"，
        并留红证（同一个夹具换成 `ai` 档 ⇒ 按钮**必须**回来）。
        """
        if getattr(svc.initial_mode, "value", svc.initial_mode) == Mode.OPEN.value:
            return "none"                   # ⭐ 共用档：无切换需求（声明层面的事实）
        return "human" if bool(svc.has_gui) else "freeze"

    @staticmethod
    def _read_true_authority(ctl: object, available: bool) -> dict | None:
        """读真值（读不到 ⇒ ``None``，**不编造**）。

        ⚠ **必须有 `mode` 才算读到**：一个"有回执但没字段"的字典（例如对端回了
        `{"is_error": true}` 而翻译层没拦住的形状）**不是**真值 ⇒ 也报读不到。
        否则页面会画出"写权 ? · 无人可写"这种**看着像事实、其实全是空**的栏。
        """
        if not available:
            return None
        try:
            out = ctl.read_write_authority()          # type: ignore[attr-defined]
        except MechaError:
            return None
        if not isinstance(out, dict) or not out.get("mode"):
            return None
        #: ⭐ 白名单**必须带上 `shared`**（2026-10-10）：控制台按它判"共用档"。
        #: ⚠ 实测踩到：漏了它 ⇒ 页面那条 `shared` 分支**永远为假**，
        #: 只能靠"声明是 open"那条兜底（两处判据只对了一半），而**聚合面**那份自省
        #: （给 AI 看的）会**静默少一格** —— 消费方各自补默认值 = 第二个真值。
        return {k: out.get(k) for k in ("ok", "mode", "ai_can_write", "human_holds", "shared")}

    def control_write_authority(self, svc_id: str, action: str) -> dict:
        """面板的写权按钮。⭐ **三种动作**（面板按 `write_authority_state()['action']` 选）。

        | action | 按钮 | 做什么 |
        |---|---|---|
        | `"human"` | 「切到手动」（**只** `has_gui=true` 有） | ① `set_write_authority(human)` ② `open_manual_surface()` |
        | `"freeze"` | 「收回写权（冻结）」（`has_gui=false`） | ⭐ **只** `set_write_authority(locked)`，⚠ **不**调开窗 |
        | `"ai"` | 「交还给 AI」 | `set_write_authority(ai)` |

        ⭐ **切到手动 = 两次调用、按顺序、分开报告**（契约 §7.1.1）：
        ⚠ **不许合并成一次"看起来成功了"**：写权切成功、窗口开失败时，人必须看到
        **哪一步成了、哪一步没成**——否则他会对着一台"以为开着窗口"的机器发呆。

        ⚠ 交还给 AI **只切写权、不关窗**（§1：切写权不动进程/连接；关窗是另一件事）。

        ## ⚠ `freeze` 为什么必须存在（2026-10-09，§7.1.1 补）

        没有手动面的软件**仍然要能切写权**（否则就是那条裁决要避免的盲区）。
        但它**没有面可开** ⇒ 归人的动作只能是"**把写权收回**" ⇒ 语义 = `locked`
        （⚠ **不是** `human`：那会贴一个"写权归人"却谁也不能写的**骗人标签**）。
        """
        st = self.write_authority_state(svc_id)
        if not st.get("ok"):
            # ⚠ 文案必须**明说没切成**：人只看这一句就知道"没成"，而不是"看不太出来"。
            return {"ok": False, "id": svc_id, "steps": [],
                    "error": f"写权没切成：{st.get('why', '不可用')}"}
        svc = next(s for s in self.services() if s.id == svc_id)
        ctl = SoftwareControl.connect(svc_id, svc.data_root)
        steps: list[dict] = []

        def step(name: str, fn: Callable[[], object]) -> bool:
            try:
                steps.append({"step": name, "ok": True, "result": fn()})
                return True
            except MechaError as exc:
                steps.append({"step": name, "ok": False,
                              "error": f"{exc.kind}：{exc.message}"})
                return False

        if action == "freeze":
            #: ⭐ 只切写权 ⇒ `locked`（谁都不能写）。⚠ **不调** `open_manual_surface`
            #: —— 它没有面，调了只会响亮失败，而那不是这次操作的目的。
            ok = step("set_write_authority(locked)",
                      lambda: ctl.set_write_authority(Mode.LOCKED.value))
            if ok:
                self._mode_intent[svc_id] = Mode.LOCKED.value
                self._save_status()
            return {"ok": ok, "id": svc_id, "steps": steps,
                    "note": "写权已收回（**冻结**：谁都不能写）。"
                            "⚠ 这不是「改由人写」—— 这个软件**没有人手面**；"
                            "要恢复请点「交还给 AI」"}

        if action == "human":
            if not step("set_write_authority(human)",
                        lambda: ctl.set_write_authority(Mode.HUMAN.value)):
                # 第一步就没成 ⇒ **不接着开窗**（开了窗而写权没给它，那是更坏的状态）
                return {"ok": False, "id": svc_id, "steps": steps,
                        "error": "写权没切成，**没有**开窗口"}
            if not step("open_manual_surface", ctl.open_manual_surface):
                # ⭐ 这一步失败必须**如实说**：写权已成、窗口没开
                return {"ok": False, "id": svc_id, "steps": steps,
                        "error": "写权已交给人工，但**窗口没开成**"
                                 "（⚠ 写权那一步是成功的，别当成整件事没做）"}
            self._mode_intent[svc_id] = "human"
            self._save_status()
            return {"ok": True, "id": svc_id, "steps": steps,
                    "note": "已切到人工并开了窗口；面板与 dsh **不关闭**，只是待命"}
        if action == "ai":
            ok = step("set_write_authority(ai)",
                      lambda: ctl.set_write_authority(Mode.AI.value))
            if ok:
                self._mode_intent[svc_id] = "ai"
                self._save_status()
            return {"ok": ok, "id": svc_id, "steps": steps,
                    "note": "⚠ 窗口**不关**（切写权不动进程/连接；关窗是另一件事）"}
        raise MechaError(f"未知的写权动作：{action!r}", kind="bad_write_action",
                         hint="只认 human（切到手动，需有手动面）/ freeze（收回写权，无手动面时用）"
                              "/ ai（交还给 AI）")

    def plan(self, enabled: Sequence[str]) -> StartupPlan:
        return plan_startup(self.sites, enabled, self._known_pids())   # ⭐ 全部站点

    def _known_pids(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for row in self.last_status().get("children", []):
            pid = row.get("pid")
            if isinstance(pid, int):
                out[str(row.get("id"))] = pid
        return out

    # ---- 写（页面唯一的那个 POST）----
    def start(self, enabled: Sequence[str]) -> dict:
        if self._started:
            return {"ok": False, "error": "已经启动过了（本步不支持重启整站）"}
        try:
            plan = plan_startup(self.sites, enabled, self._known_pids())   # ⭐ 全部站点
            if plan.stale:
                self.super.clean_stale(plan.stale)      # 清掉的会进 state().cleaned
            self._enabled = plan.enabled
            for child in plan.ordered:                  # ⭐ 按**显式顺序**起
                self.super.start(child)
            self._started = True
            # ⭐ 起完之后把"已就绪的 svc"接进聚合端点（有回读端口才挂，见 sync_backends）
            if self.after_start is not None:
                self.after_start()
            self._save_status()
            return {"ok": True, "started": list(self._enabled),
                    "cleaned": list(self.super.cleaned)}
        except MechaError as exc:
            self._last_error = f"{exc.kind}：{exc.message}"
            self._save_status()
            return {"ok": False, "error": self._last_error}

    def shutdown(self) -> dict:
        self.super.shutdown()
        self._started = False
        self._save_status()
        return {"ok": True, "state": self.super.state()}

    def _save_status(self) -> None:
        try:
            self.runtime.mkdir(parents=True, exist_ok=True)
            (self.runtime / "site.json").write_text(
                json.dumps({"children": self.super.state()["children"],
                            "enabled": list(self._enabled),
                            "last_error": self._last_error,
                            "sites": [s.name for s in self.sites]},
                           ensure_ascii=False, indent=2),
                encoding="utf-8")
        except OSError:
            pass


# --------------------------------------------------------------------------- CLI


def build_aggregate_wiring(*, sites: Sequence[Site], supervisor: Supervisor,
                           aggregate_port: int = 0):
    """把**聚合端点**接起来，返回 ``(endpoint, runtime, registry_facade)``。

    ⚠ **多站点（§1.1 规则 5）**：收**全部站点的全部服务**（平铺后的全集）——
    一个聚合端点要能报/操作**所有**开着的软件。原来只收一个 ``site``。
    ``server_name`` 取**第一个站点名**（只用于给 dsh 的 ``mcp__<serverName>__``
    前缀；⚠ 它是**稳定**的：站点顺序稳定 ⇒ 名字稳定，见 §1.1 规则 4 的稳定顺序）。

    ## 三条设计决定（都不是随手）

    1. **聚合端点是 launcher 自己持有的进程内服务端，不是 supervisor 的 child**：
       dsh 的 overlay 要写"聚合端点的地址"，而"对 dsh 稳定"最自然的做法是
       让它在**同一个进程**里、由 launcher 直接持有 ⇒ 不占 child 名额、不用再套一层
       启停与端口发现。**理由（若将来要做成 child）**：那时它自己的生存期要独立于
       launcher；当前没有这个需求（n=1 不预建）。
    2. ⭐ **必须在起任何 child 之前建**：dsh 的 ``--patch`` 要指向它，地址得先存在
       （所以本函数在 ``Supervisor`` 起 child 之前调用，并由 ``prepare_hook`` 在
       spawn 那一刻把 overlay 塞进 dsh 的 argv）。
    3. ⭐ **工具表用"门面"喂给服务端**：``build_mcp_server`` 是**纯构造、快照工具表**，
       而聚合端点的工具表**随后端 attach/detach 变**。若把 ``Aggregator.registry()``
       的**结果**直接给出去，表就被冻在上线那一刻。
       ⇒ 这里传一个**每次 ``schemas()`` 都重新问 ``Aggregator`` 的门面**——
       不引入新机制（只要 ``schemas()``，与 ``build_mcp_server`` 的用法一致），
       却让"开关软件"立刻反映到服务端。
    """
    from .aggregate import AGGREGATE_SERVER_NAME, Aggregator
    from .aggregate_runtime import AggregateRuntime, make_prepare_hook
    from .providers.mcp import McpEndpoint
    from .providers.mcp_client import Backend

    #: ⭐ 聚合端点/``mcp__<serverName>__`` 前缀用的名字 = **框架自己的**（§7.2）：
    #: ⚠ **不是**"第一个站点的名字"（原实现取 `sites[0].name`）—— 那是**位置**的偶然产物：
    #: 实测用户只勾了 GP，dsh 里那个 MCP 客户端却叫 `ML Toolbox`（因为 ML 在
    #: `MECHA_SITE_ROOTS` 里排第一）⇒ 现场就是"**只选了 GP，载入的却是 ML 的 mcp**"。
    #: 这个端点是**框架的**聚合面、同时代表所有站点 ⇒ 名字由框架给，与站点顺序无关。
    name_of_site = AGGREGATE_SERVER_NAME
    agg = Aggregator(server_name=name_of_site)

    class _LiveRegistry:
        """聚合端点的**活工具表门面**：每次问它都现算，别冻在上线那一刻。

        ⚠ 需要 ``schemas()`` **与** ``get()``：``build_mcp_server`` 先列
        ``schemas()``、再用 ``registry.get(name)`` 取回定义本体去注册
        （``providers/mcp.py:451``）⇒ 只给 ``schemas()`` 会当场
        ``AttributeError: 'get'``（本门面第一版就是这样红的）。
        ⚠ 两个方法**各自**读一次当前表（不缓存）：缓存会让"刚摘掉的后端"仍出现在
        ``get()`` 里，那是另一种陈旧。
        """

        def schemas(self) -> list[dict]:
            return agg.registry().schemas()

        def get(self, name: str):
            return agg.registry().get(name)

        def execute(self, name: str, args: Mapping[str, object]) -> dict:
            """⭐ **真调**这一口：``LocalToolHost`` 调的就是它（``toolhost.py:199``）。

            ⚠ 2026-10-10 实测抓到的真缺陷：本门面原先只实现了 ``schemas()``/``get()``
            ⇒ 端点**列得出**工具、**一个都调不动**：
            ``AttributeError: '_LiveRegistry' object has no attribute 'execute'``
            （连 ``query_software`` 这种框架自己的元工具都一起废）。
            ⚠ 为什么判据没抓到：既有判据只验"门面列得出元工具"，**从不经端点真调一个工具**。
            ⇒ 现在``checks/aggregate_tools_hot_sync_selfcheck.py`` 钉住"经端点真调"。
            """
            return agg.registry().execute(name, dict(args or {}))

    registry = _LiveRegistry()
    #: ⭐ **端口 = 启动者给的**（默认 `0`）：固定值先 `ensure_free` ⇒ 被占**响亮失败**，
    #: 绝不偷偷换（换端口 = "AI 工具突然没了"的根源）。⚠ 库内仍然零端口字面量。
    run_root = supervisor.log_dir.parent          # `<runtime>/logs` ⇒ `<runtime>`
    if aggregate_port:
        from .portfile import ensure_free
        ensure_free("127.0.0.1", int(aggregate_port), label="聚合端点（--aggregate-port）")
    endpoint = McpEndpoint(registry, server_name=name_of_site,
                           instructions=f"{name_of_site} 的聚合端点（各软件一条脊椎合在这里）",
                           port=int(aggregate_port),
                           #: ⭐ **公布端口文件**（`.aggregate-port`）：别的东西（人、脚本、
                           #: 面板）能在**启动时**重新解析当前地址 —— 与监控面那套同一手法。
                           port_file=run_root / AGGREGATE_PORT_FILE,
                           #: ⭐ 聚合端点专属：登记活连接 + **声明** ``tools.listChanged``
                           #: （控制端点**不**开它：它不发通知 ⇒ 声明了就是假声明）。
                           track_connections=True)
    endpoint.start()                       # ⚠ 起在任何 child 之前；端口**回读**
    mcp_port = endpoint.port
    if mcp_port is None:
        raise MechaError("聚合端点起来了但拿不到回读端口", kind="aggregate_port_unknown",
                         hint="McpEndpoint.start() 应回读真实 bind 端口")

    def backend_factory(url: str, *, server_name: str) -> object:
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                Backend.connect(url, server_name=server_name, timeout_s=30.0))
        finally:
            loop.close()

    # ⭐ 平铺全集：**全部站点的全部服务**（§1.1 规则 5）
    all_svcs = flat_services(sites)
    runtime = AggregateRuntime(supervisor=supervisor,
                               services=all_svcs,
                               aggregator=agg, backend_factory=backend_factory,
                               #: ⭐ 活工具表那一侧：每次 sync_backends 后"对账 + 广播"
                               endpoint=endpoint)
    # ⭐ 铁律 7：进程事实**只有一个来源**（supervisor）；``known`` 交出**声明里全部** id
    #   （否则"存在但没跑"会被误报成"压根没有这个软件"）。
    agg.wire(state_fn=supervisor.state,
             open_fn=runtime.open_service, close_fn=runtime.close_service,
             known=[s.id for s in all_svcs],
             # ⭐ 站点声明（供聚合面取 `how_to_open` 原话）+ 写权探针（**不缓存**、每次真问）
             specs=all_svcs,
             authority_probe=runtime.probe_authority)
    # ⭐ spawn dsh **之前那一刻**把 overlay 塞进它的 ``--patch``（端口此时已确定）。
    # ⚠ 走**公开追加口**，不直接改 ``_prepare_hooks``：私有名不许外面碰
    #   （钩子必须先拿到 supervisor 自己 ⇒ 构造参数那条路用不了 ⇒ 故给了这个方法）。
    # ⚠ overlay 的 `serverName` 只用于给 dsh 的工具前缀 ⇒ 取第一个站点名即可
    #   （`make_prepare_hook` 的 `site` 参数正是为这个用途）。
    supervisor.add_prepare_hook(make_prepare_hook(
        runtime, sites[0] if sites else None, lambda: mcp_port))
    #: ⭐⭐ **"软件起来了/没了" ⇒ 工具表跟着变**（2026-10-10）：
    #: ⚠ 不接这两条钩子，就**只有人手动动一下滑块**才会 attach（实测踩到 D3）——
    #: 现象是"软件明明在跑，AI 手里却一个它的工具都没有"。
    #: ⚠ 顺序：ready 时 attach（表里多出它的工具）→ 广播；gone 时 detach（表里变**墓碑**）
    #: → 广播。两者都经 `sync_backends`，幂等（没变就不发通知）。
    supervisor.add_ready_hook(lambda spec, child: runtime.sync_backends())
    supervisor.add_gone_hook(lambda spec, child: runtime.sync_backends())
    return endpoint, runtime, registry


def _warn_if_dsh_port_taken(sites: Sequence[Site]) -> None:
    """dsh 的首选端口已经被占时**响亮报告**（T3：不许"悄悄漂到别的端口"）。

    ## 为什么要这条（2026-10-10 用户实测："极大影响使用"）

    框架把**固定**的首选端口传给 dsh（`DSHSpec.argv()` 的 `--port`）。但上一次没收干净时，
    **旧 dsh 还占着那个端口** ⇒ 新实例只能起在 3082/3083…，而人浏览器里那个 tab/书签
    还是旧实例 ⇒ **"我在看哪一个？"**（旧的那个指向已经消失的聚合端点 ⇒ AI 工具没了）。
    ⇒ 起之前先探一下并**说清楚**：谁占着、本次会漂到哪、怎么清（`python -m mecha --stop`
    现在会扫残留 ✅）。⚠ 只报告、不阻塞（阻塞会把"能起来的世界"也拦住）。
    """
    seen: set[int] = set()
    for site in sites:
        spec = getattr(site, "dsh", None)
        if spec is None:
            continue
        port = int(getattr(spec, "port_preference", 0) or 0)
        if not port or port in seen:
            continue
        seen.add(port)
        if _listening(port):
            print(f"⚠ dsh 的首选端口 {port} 上**已经有人在听**（不是本次要起的那个）："
                  f"浏览器里那个 dsh 可能是**上一轮留下的**。\n"
                  f"  ⇒ 本次 dsh 会起在别的端口（别在旧 tab 里找 AI 面）。\n"
                  f"  ⇒ 要清干净：`python -m mecha --stop`（它会扫残留）或关掉那一轮。",
                  flush=True)


def _request_stop(runtime: Path) -> int:
    """⭐ 请那个**常驻 launcher** 收尾退出（读 ``.page-port`` 打一次 ``POST /shutdown``）。

    ## 为什么必须有它（用户实测问到的缺口）

    无黑框入口下**没有控制台** ⇒ 用户关掉浏览器之后：
    - **看不到**它还在跑；**没有 Ctrl+C**；再双击只会得到"已经有一个在跑"；
    - ⚠ 原来的**唯一**出路是"重新打开面板点『收尾退出』"，或去**任务管理器强杀**
      —— ⚠ 而强杀**跳过收尾**（子进程成孤儿，端口文件留一地）。
    ⇒ 补一条**确定的、从命令行/双击都能走的**停止路径。

    ⚠ 走的是**面板自己的 `POST /shutdown`**（与那个按钮**同一条路**）⇒
    **收尾语义完全一致**（逆序停 child、清端口文件），不是另写一套停法。
    """
    import urllib.error
    import urllib.request

    from .portfile import read_port

    pf = runtime / PAGE_PORT_FILE
    if not pf.is_file():
        print(f"没有在跑的 launcher（找不到 {pf}）")
        return 1
    port = read_port(pf)
    if not port:
        print(f"端口文件读不出端口：{pf}")
        return 1
    req = urllib.request.Request(f"http://127.0.0.1:{port}/shutdown",
                                 method="POST", data=b"")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
    except (urllib.error.URLError, OSError) as exc:
        #: ⭐ **兜底：launcher 已经不在 ⇒ 扫残留**（2026-10-10 用户实测"AI 工具突然没了、
        #: MCP 端口像变了"）。原因不是端口会变，而是**孩子们（含 dsh）还在跑**：
        #: 你浏览器里那个 dsh 指向一个**已经不存在**的聚合端点 ⇒ 掉工具；而它又占着
        #: 首选的 3081 ⇒ 新实例漂到 3082 ⇒ 人看的是旧世界。⇒ 这里把 state.json 里记过、
        #: **现在还活着**的 child 清掉（只清我们自己记过的 pid），并如实报清掉了谁。
        print(f"请求收尾失败（{exc}）：那个 launcher 已经不在了 ⇒ 改为**扫残留**。")
        from .supervise import Supervisor

        sup = Supervisor(log_dir=runtime / "logs", state_path=runtime / "state.json")
        cleaned = sup.sweep_leftovers()
        if cleaned:
            for line in cleaned:
                print(f"  · {line}")
            print(f"扫完：清了 {len(cleaned)} 个残留进程。")
            return 0
        print("没有残留进程（state.json 里记过的 pid 都已退出）⇒ 世界是干净的。")
        return 1
    print(f"已经请 pid 文件里的那个 launcher（面板 :{port}）收尾退出。")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    #: ⭐ **输出必须"立刻可见"**：`print` 到**被重定向的文件**时是**块缓冲**
    #: ⇒ 那行 `mecha：已启动 ⇒ http://…` **会卡到进程退出才出现**
    #: （`.bat`/脚本把 stdout 重定向到日志时就是这么踩的——**实测**：launcher 明明起来了，
    #: 日志里只有聚合端点的日志，**拿不到地址**）。
    #: ⚠ 这与铁律 1（日志必须留、不许 DEVNULL）是**同一件事的两半：留了还要看得见**。
    #: ⚠ 顺带统一编码：Windows 控制台 cp936 会把中文输出打炸。
    #: ⚠ 必须在 `argparse` **之前**（`--help` 的输出同理要可见）。
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(
        prog="python -m mecha",
        description="从 mecha 启动 = 永远是 AI 组：起各软件脊椎 + dsh，并给一个选择/状态页。")
    ap.add_argument("--all", action="store_true",
                    help="跳过选择页：默认全启用（给 .bat 用）")
    ap.add_argument("--root", action="append", default=None,
                    help=f"站点搜索根（可重复）；缺省 ${SITE_ROOTS_ENV} 或 CWD")
    ap.add_argument("--port", type=int, default=0, help="状态页端口（0=自动）")
    #: ⭐ **聚合端点（AI 那条腿）的端口**：`0`＝OS 分配（默认，每次启动都变）。
    #: 给一个固定值 ⇒ ⚠ **占用即响亮失败**（绝不偷偷换：换端口正是"AI 工具突然没了"的根源）。
    #: 固定它的好处：任何**静态配置**的 dsh 永久有效；上一次没收干净留下的旧 dsh
    #: 也能在重连预算内**自己接回**新端点（同一个 URL）。
    ap.add_argument("--aggregate-port", type=int, default=0,
                    help="聚合端点固定端口（0=自动分配；固定则被占即响亮失败）")
    ap.add_argument("--no-browser", action="store_true", help="不要自动开浏览器")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不 spawn")
    ap.add_argument("--stop", action="store_true",
                    help="把在跑的那个常驻 launcher **收尾停掉**（读 .page-port 请求它退出）")
    args = ap.parse_args(list(argv) if argv is not None else None)

    #: ⭐ `--stop`：**先于一切**处理（不 load_sites ⇒ 站点声明不在了也能停；
    #: 不抢单实例锁、不建目录 —— 与 `--dry-run` 同一纪律）。
    if args.stop:
        return _request_stop(Path.cwd() / RUNTIME_DIR_NAME)

    roots = [Path(r) for r in args.root] if args.root else None
    sites = load_sites(roots)
    #: ⭐ dsh 首选端口被占时**响亮报告**（T3）—— 只报告、不阻塞；放在真起之前。
    _warn_if_dsh_port_taken(sites)

    if args.dry_run:
        #: ⚠ **dry-run 必须在"任何副作用"之前返回**（实测抓到的真 bug）：
        #: 原实现先 `acquire_singleton(runtime)`，而那个函数会 `runtime.mkdir(...)`
        #: ⇒ **一次 `--dry-run`（或只在判据里调 `load_sites`）就会在 cwd 下造出
        #: `_mecha_runtime/`** ⇒ 脏工作区（接入仓的判据就地跑一次就多一个目录）。
        #: ⇒ 所以这里**只读地**算计划：**不建目录、不抢单实例锁、不起任何东西**。
        #: ⚠ 连 `Supervisor(...)` 都不许构造 —— 它的 `__init__` 里也有 `log_dir.mkdir`
        #:   （第一版我在这里构造了它 ⇒ dry-run 照样建目录；**判据没抓到，是我读代码抓到的**）。
        plan = plan_startup(sites, [s.id for s in flat_services(sites) if s.default_on])
        print(f"站点（{len(sites)} 个）："
              + "、".join(f"{s.name}[{len(s.services)}]" for s in sites))
        print("启动顺序：" + " → ".join(c.id for c in plan.ordered))
        print("待清理残留：" + (", ".join(i.target for i in plan.stale) or "（无）"))
        return 0

    runtime = _runtime_root()          # ⚠ §1.1 规则 3：不取决于任何站点
    try:
        acquire_singleton(runtime)
    except MechaError as exc:
        print(f"✖ {exc.message}", file=sys.stderr)
        return 2
    # ⭐ §1.1 规则 2/7：服务 id **跨站点**全局唯一（它是工具前缀）
    try:
        assert_service_ids_global_unique(sites)
        merge_dsh(sites)               # ⭐ dsh 全生态一个：站点们共同贡献、身份必须一致
    except MechaError as exc:
        print(f"✖ {exc.message}", file=sys.stderr)
        return 2
    supervisor = Supervisor(log_dir=runtime / "logs", state_path=runtime / "state.json")
    controller = LaunchController(sites=sites, runtime=runtime, supervisor=supervisor)

    # ⭐ 聚合端点：**起在任何 child 之前**（dsh 的 overlay 要指向它），收**全部站点**
    endpoint, agg_runtime, _ = build_aggregate_wiring(
        sites=sites, supervisor=supervisor,
        aggregate_port=int(getattr(args, "aggregate_port", 0) or 0))
    controller.after_start = lambda: agg_runtime.sync_backends()
    controller.agg_runtime = agg_runtime   # 面板的存活滑块走它那条正规路径（含端点同步）

    if args.all:
        enabled = [s.id for s in flat_services(sites) if s.default_on]
        result = controller.start(enabled)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result.get("ok"):
            return 1
        # 起了之后不退出：状态页仍要能看（--all 只是跳过勾选那一步）
    from .providers.status_page import PageOptions, StatusPage
    page = StatusPage(controller, PageOptions(port=args.port, open_browser=not args.no_browser))
    port = page.start()
    #: ⭐ **面板端口落成文件** —— 原来只打印在 stdout 里，而无黑框入口下
    #: **没人看得到 stdout** ⇒ `--stop` / `stop_mecha.pyw` 就**没有确定的找法**。
    #: ⚠ 这与 `.mcp-port` 同族：**端口一律落文件**，别只靠"人记得那个 URL"。
    (runtime / PAGE_PORT_FILE).write_text(str(port), encoding="utf-8")
    print(f"mecha：{'已启动' if args.all else '等待选择'} ⇒ http://127.0.0.1:{port}/")
    try:
        page.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        page.stop()
        controller.shutdown()
        #: ⭐ 谁写的谁清（铁律）：退出时把自己那两个文件收掉，
        #: 否则 `--stop` 会拿一个**陈旧端口**去打，报一句让人摸不着头脑的失败。
        for name in (PAGE_PORT_FILE, _pid_file(runtime).name):
            try:
                (runtime / name).unlink(missing_ok=True)
            except OSError:
                pass
        try:
            endpoint.stop()            # 聚合端点是我们自己起的 ⇒ 也要自己收
        except Exception:              # noqa: BLE001 - 收尾失败不该盖住退出码
            pass
    return 0
