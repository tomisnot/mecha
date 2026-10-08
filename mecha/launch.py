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

from .errors import MechaError
from .site import AutoShutdown, Service, Site, Strategy
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
    """一份**可执行的启动计划**（顺序是显式的，见修正 1）。"""

    site: Site
    #: ⭐ **启动顺序**（拓扑序）：``svc* → aggregator → dsh``。
    #: 本步没有 aggregator，所以就是 ``svc* → dsh``；步骤 2 往中间插，**不改签名**。
    ordered: tuple[ChildSpec, ...]
    #: 待清理的陈旧残留（交给 :meth:`Supervisor.clean_stale`）。
    stale: tuple[StaleItem, ...]
    enabled: tuple[str, ...]


def _port_file_of(svc: Service) -> Path | None:
    name = svc.ready.port_file
    if not name:
        return None
    return (svc.cwd / name) if not Path(name).is_absolute() else Path(name)


def build_children(site: Site, enabled: Sequence[str]) -> tuple[ChildSpec, ...]:
    """把勾中的服务 + dsh 翻成 child 列表（**顺序 = 拓扑序**）。"""
    kids: list[ChildSpec] = []
    for svc in site.services:
        if svc.id in enabled:
            kids.append(svc.child())
    # dsh 最后：它要用聚合端点的 URL（步骤 2）；本步它只用偏好端口。
    dsh = site.dsh
    kids.append(ChildSpec(
        id="dsh", argv=which_or_raise(dsh.argv()), cwd=dsh.cwd, env=dict(dsh.env),
        ready=dsh.ready, stop_timeout=dsh.stop_timeout, data_root=None,
    ))
    return tuple(kids)


def _detect_stale(site: Site,
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
    for svc in site.services:
        pf = _port_file_of(svc)
        if pf is None or not pf.exists():
            continue
        # 端口文件还在，但它记的端口**没人在听** ⇒ 陈旧（这正是 EL 那个假绿的现场）
        from .portfile import read_port
        port = read_port(pf)
        listening = _listening(port) if port else False
        if not listening:
            stale.append(StaleItem(
                kind="port_file", target=str(pf), path=pf,
                why=f"端口文件在、但 {port} 上没人在听 ⇒ 陈旧残留"
                    f"（不清理会让'存在且非空'被误判成就绪）"))
    return tuple(stale)


def _listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def plan_startup(site: Site, enabled: Sequence[str],
                 known_pids: dict[str, int] | None = None) -> StartupPlan:
    """产出一份启动计划（顺序 + 待清理残留 + 勾选）。**不做任何副作用**。"""
    chosen = tuple(s.id for s in site.services if s.id in enabled)
    return StartupPlan(
        site=site,
        ordered=build_children(site, chosen),
        stale=_detect_stale(site, known_pids or {}),
        enabled=chosen,
    )


# --------------------------------------------------------------------------- 单实例


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
                hint="同一份 runtime 只允许一个 supervisor；先收尾那个，或换 --root",
            )
        # 陈旧 pid 文件（进程已死）⇒ 接管，不静默留下"永远锁着"的假象
    pf.write_text(str(os.getpid()), encoding="utf-8")
    return os.getpid()


# --------------------------------------------------------------------------- 状态文件


def _runtime_root(site: Site) -> Path:
    """运行期根：**启动根**下的约定目录（不放各软件自己的数据根里——那是它们的）。"""
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
        return st

    def services(self) -> tuple[Service, ...]:
        """**全部已声明**的软件（⚠ 与"勾过的"无关：面板**始终**展示全部）。"""
        return tuple(self.sites[0].services)

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
        """
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

    def plan(self, enabled: Sequence[str]) -> StartupPlan:
        site = self.sites[0]
        return plan_startup(site, enabled, self._known_pids())

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
        site = self.sites[0]
        try:
            plan = plan_startup(site, enabled, self._known_pids())
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


def build_aggregate_wiring(*, site: Site, supervisor: Supervisor,
                           services: Sequence[Service] | None = None):
    """把**聚合端点**接起来，返回 ``(endpoint, runtime, registry_facade)``。

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
    from .aggregate import Aggregator
    from .aggregate_runtime import AggregateRuntime, make_prepare_hook
    from .providers.mcp import McpEndpoint
    from .providers.mcp_client import Backend

    agg = Aggregator(server_name=site.name)

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

    registry = _LiveRegistry()
    endpoint = McpEndpoint(registry, server_name=site.name,
                           instructions=f"{site.name} 的聚合端点（各软件一条脊椎合在这里）")
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

    runtime = AggregateRuntime(supervisor=supervisor,
                               services=tuple(services or site.services),
                               aggregator=agg, backend_factory=backend_factory)
    # ⭐ 铁律 7：进程事实**只有一个来源**（supervisor）；``known`` 交出**声明里全部** id
    #   （否则"存在但没跑"会被误报成"压根没有这个软件"）。
    agg.wire(state_fn=supervisor.state,
             open_fn=runtime.open_service, close_fn=runtime.close_service,
             known=[s.id for s in (services or site.services)])
    # ⭐ spawn dsh **之前那一刻**把 overlay 塞进它的 ``--patch``（端口此时已确定）。
    # ⚠ 走**公开追加口**，不直接改 ``_prepare_hooks``：私有名不许外面碰
    #   （钩子必须先拿到 supervisor 自己 ⇒ 构造参数那条路用不了 ⇒ 故给了这个方法）。
    supervisor.add_prepare_hook(make_prepare_hook(runtime, site, lambda: mcp_port))
    return endpoint, runtime, registry


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m mecha",
        description="从 mecha 启动 = 永远是 AI 组：起各软件脊椎 + dsh，并给一个选择/状态页。")
    ap.add_argument("--all", action="store_true",
                    help="跳过选择页：默认全启用（给 .bat 用）")
    ap.add_argument("--root", action="append", default=None,
                    help=f"站点搜索根（可重复）；缺省 ${SITE_ROOTS_ENV} 或 CWD")
    ap.add_argument("--port", type=int, default=0, help="状态页端口（0=自动）")
    ap.add_argument("--no-browser", action="store_true", help="不要自动开浏览器")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不 spawn")
    args = ap.parse_args(list(argv) if argv is not None else None)

    roots = [Path(r) for r in args.root] if args.root else None
    sites = load_sites(roots)

    if args.dry_run:
        #: ⚠ **dry-run 必须在"任何副作用"之前返回**（实测抓到的真 bug）：
        #: 原实现先 `acquire_singleton(runtime)`，而那个函数会 `runtime.mkdir(...)`
        #: ⇒ **一次 `--dry-run`（或只在判据里调 `load_sites`）就会在 cwd 下造出
        #: `_mecha_runtime/`** ⇒ 脏工作区（接入仓的判据就地跑一次就多一个目录）。
        #: ⇒ 所以这里**只读地**算计划：**不建目录、不抢单实例锁、不起任何东西**。
        #: ⚠ 连 `Supervisor(...)` 都不许构造 —— 它的 `__init__` 里也有 `log_dir.mkdir`
        #:   （第一版我在这里构造了它 ⇒ dry-run 照样建目录；**判据没抓到，是我读代码抓到的**）。
        plan = plan_startup(sites[0], [s.id for s in sites[0].services if s.default_on])
        print(f"站点：{sites[0].name}")
        print("启动顺序：" + " → ".join(c.id for c in plan.ordered))
        print("待清理残留：" + (", ".join(i.target for i in plan.stale) or "（无）"))
        return 0

    runtime = _runtime_root(sites[0])
    try:
        acquire_singleton(runtime)
    except MechaError as exc:
        print(f"✖ {exc.message}", file=sys.stderr)
        return 2
    supervisor = Supervisor(log_dir=runtime / "logs", state_path=runtime / "state.json")
    controller = LaunchController(sites=sites, runtime=runtime, supervisor=supervisor)

    # ⭐ 聚合端点：**起在任何 child 之前**（dsh 的 overlay 要指向它）
    endpoint, agg_runtime, _ = build_aggregate_wiring(site=sites[0], supervisor=supervisor)
    controller.after_start = lambda: agg_runtime.sync_backends()
    controller.agg_runtime = agg_runtime   # 面板的存活滑块走它那条正规路径（含端点同步）

    if args.all:
        enabled = [s.id for s in sites[0].services if s.default_on]
        result = controller.start(enabled)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result.get("ok"):
            return 1
        # 起了之后不退出：状态页仍要能看（--all 只是跳过勾选那一步）
    from .providers.status_page import PageOptions, StatusPage
    page = StatusPage(controller, PageOptions(port=args.port, open_browser=not args.no_browser))
    port = page.start()
    print(f"mecha：{'已启动' if args.all else '等待选择'} ⇒ http://127.0.0.1:{port}/")
    try:
        page.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        page.stop()
        controller.shutdown()
        try:
            endpoint.stop()            # 聚合端点是我们自己起的 ⇒ 也要自己收
        except Exception:              # noqa: BLE001 - 收尾失败不该盖住退出码
            pass
    return 0
