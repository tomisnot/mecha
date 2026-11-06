"""Codex 宿主适配器：**托管形态**（与 dsh 同构）。

## 与首版的区别（2026-11-05）

首版是"外部 MCP 模式"：mecha 只写一份隔离 ``CODEX_HOME`` 配置，不 spawn 任何进程，
由用户自己在终端开 codex。那样 ``managed=False``、``child()`` 返回 ``None``——
滑块只能表达"这一轮用谁"，永远不会有 pid。

现在改成**托管**：supervisor 直接起

    codex app-server --listen ws://127.0.0.1:<port>
                     --ws-auth capability-token --ws-token-file <abs>

它是常驻服务（实测本机 0.162.1：监听 TCP，自带 ``/readyz``、``/healthz``），
于是 dsh 那一套原封不动适用：有 pid、有就绪判据、能起停、崩了标红。

## 两件事必须同时成立，否则"起来了"是假的

1. **端口必须 spawn 前就定下来**（``--listen`` 是我们给的）。codex 不像 dsh／各家软件
   那样自己回读端口再写文件 ⇒ 由 ``ensure_ports()`` 先挑好写文件，就绪判据用
   ``ReadySpec(port=...)`` 直接探 ``/readyz``（见 ``supervise.ReadySpec.port``）。
2. **登录态**。隔离 home 里没有 ``auth.json`` 就是 "Not logged in"（实测），TUI 与
   这个 app-server 都一样。⇒ ``ensure_auth()`` 从 ``~/.codex/auth.json`` **只复制凭据**
   （不复制会话与历史），让隔离 home 免登录可用。

## 界面

浏览器不能直连 app-server：它给带 ``Origin`` 头的握手回 **403**（本机实测）。
⇒ 宿主 ready 之后由 :meth:`install` 挂的 ready hook 起一个 **本地代理**（vendor 的
``webui/``，来自 ``@ai-nd-co/codex-web-remote`` 0.2.0），代理负责剥 Origin、注入 token、
按白名单放行 RPC；浏览器只跟代理说话。宿主 gone 时把它一起停。
"""

from __future__ import annotations

import webbrowser
from collections.abc import Callable, Mapping
from pathlib import Path

from ..errors import MechaError
from ..host_adapter import HostPlan, HostPreparation, HostWiring, RefreshResult
from ..portfile import read_port
from ..supervise import ChildSpec, ReadySpec, which_or_raise
from .config import (CODEX_CHILD_ID, CODEX_UI_CHILD_ID, MCP_SERVER_NAME,
                     DEFAULT_TOOL_TIMEOUT_SEC, ensure_auth, ensure_ports,
                     ensure_ws_token, paths_for, write_preparation)
from .tasks import ledger_path

#: vendor 进来的界面入口（相对本文件）。缺了 ⇒ 响亮失败，别让宿主"半好"。
WEBUI_ENTRY = Path(__file__).resolve().parent / "webui" / "bin" / "codex-web-remote.mjs"


class CodexHostAdapter:
    name = "codex"
    managed = True
    child_id: str | None = CODEX_CHILD_ID
    #: 界面那个 child 的 id（控制器据此判断"界面在不在跑"；dsh/none 没有）。
    ui_child_id: str | None = CODEX_UI_CHILD_ID

    def _url(self, plan: HostPlan) -> str:
        if plan.aggregate_url:
            return plan.aggregate_url
        if plan.aggregate_port > 0:
            return f"http://127.0.0.1:{plan.aggregate_port}/mcp"
        raise MechaError("聚合端点尚未就绪，不能生成 Codex MCP 配置",
                         kind="codex_aggregate_port_unknown",
                         hint="McpEndpoint.start() 必须先回读真实端口")

    # ---- 启动前准备（端口 / token / 登录态）----
    def prepare_start(self, plan: HostPlan) -> dict:
        """**真启动之前**的副作用：隔离 home、登录态、ws token、两个端口。

        ⚠ 与 :meth:`prepare` 分开：``prepare`` 写的是 MCP 配置（要聚合端点地址），
        这里准备的是"进程能不能起来"的前提。两者都在 spawn 之前完成。
        ⚠ dry-run 不走这条路（由 ``LaunchController.start`` 调用），不会脏工作区。
        """
        paths = paths_for(plan.runtime_root)
        paths.home.mkdir(parents=True, exist_ok=True)
        auth = ensure_auth(paths)
        ensure_ws_token(paths)
        app_port, ui_port = ensure_ports(paths)
        return {"auth": auth, "app_server_port": app_port, "ui_port": ui_port}

    # ---- 进程 ----
    def child(self, plan: HostPlan) -> ChildSpec | None:
        paths = paths_for(plan.runtime_root)
        port = read_port(paths.app_server_port) or 0
        argv = which_or_raise((
            "codex", "app-server",
            "--listen", f"ws://127.0.0.1:{port}",
            "--ws-auth", "capability-token",
            "--ws-token-file", str(paths.ws_token),
        ))
        return ChildSpec(
            id=CODEX_CHILD_ID, argv=argv, cwd=paths.home,
            env={"CODEX_HOME": str(paths.home)},
            #: ⚠ 固定端口 + HTTP 探活：它自己不写端口文件，端口是我们给的。
            ready=ReadySpec(port=port,
                            probe="http://127.0.0.1:{port}/readyz",
                            timeout_s=60.0),
            stop_timeout=8.0,
        )

    def ui_child(self, plan: HostPlan) -> ChildSpec:
        """浏览器界面那个进程（本地代理 + 静态前端）。"""
        if not WEBUI_ENTRY.is_file():
            raise MechaError("Codex 界面资产缺失（vendor 的 webui 不在包里）",
                             kind="codex_webui_missing",
                             hint=f"应存在：{WEBUI_ENTRY}")
        paths = paths_for(plan.runtime_root)
        app_port = read_port(paths.app_server_port) or 0
        ui_port = read_port(paths.ui_port) or 0
        argv = which_or_raise((
            "node", str(WEBUI_ENTRY),
            #: ⭐ connect-only：给了 --upstream 就**绝不**自己拉起 codex
            #: （生存域留在 supervisor 手里，这是选这个包的主要原因）。
            "--upstream", f"ws://127.0.0.1:{app_port}",
            #: token 只给代理（服务端注入上游握手），浏览器永远看不到它。
            "--token-file", str(paths.ws_token),
            "--port", str(ui_port), "--host", "127.0.0.1",
        ))
        return ChildSpec(
            id=CODEX_UI_CHILD_ID, argv=argv, cwd=WEBUI_ENTRY.parent.parent,
            ready=ReadySpec(port=ui_port,
                            probe="http://127.0.0.1:{port}/proxy/healthz",
                            timeout_s=45.0),
            stop_timeout=5.0,
        )

    def spec(self, plan: HostPlan) -> object | None:
        return None

    def validate(self, plan: HostPlan) -> None:
        """启动前只校验声明；实际 URL 在聚合端点回读端口后再由 prepare 校验。"""
        return None

    def warn(self, plan: HostPlan) -> None:
        print("[codex] 托管模式：由 supervisor 起 codex app-server（与 dsh 同构），"
              "界面走本地代理", flush=True)

    def prepare(self, plan: HostPlan) -> HostPreparation:
        paths, command = write_preparation(
            plan.runtime_root, aggregate_url=self._url(plan),
            server_name=MCP_SERVER_NAME,
            tool_timeout_sec=DEFAULT_TOOL_TIMEOUT_SEC)
        notes = (
            "Codex 使用独立 CODEX_HOME，未修改用户全局配置",
            f"MCP 端点：{self._url(plan)}",
            "工具表变化后必须重连 Codex MCP 客户端，不依赖 list_changed 通知",
        )
        for note in notes:
            print(f"[codex] {note}", flush=True)
        return HostPreparation(
            config_path=paths.config, command=command, notes=notes,
            files=(paths.config, paths.start, paths.agents,
                   paths.catalog_revision))

    def install(self, plan: HostPlan, wiring: HostWiring) -> None:
        """挂两个钩子：宿主 ready ⇒ 起界面；宿主 gone ⇒ 停界面。

        ⚠ 为什么用钩子而不是"一次起两个 child"：界面依赖 app-server 的端口**已经能连**，
        而端口是 app-server 起来之后才有意义的。放在 ready 之后起，顺序天然正确；
        gone 时一起停，保证"关掉宿主不会留一个空壳界面"。
        """
        sup = wiring.supervisor

        def on_ready(spec: object, child: object) -> None:
            if str(getattr(spec, "id", "")) != CODEX_CHILD_ID:
                return
            try:
                sup.start(self.ui_child(plan))
            except MechaError as exc:
                #: 界面起不来**不该把宿主拖红**：AI 那条腿仍然是通的。
                print(f"[codex] 界面起不来（宿主照常可用）：{exc.message}", flush=True)
                return
            #: ⭐ 照 dsh 的做法（`StatusPage.start` 里那三行）：界面起来就开浏览器，
            #: 打不开不算失败（用户还能从控制台的链接点进去）。
            if wiring.open_browser:
                ui_port = read_port(paths_for(plan.runtime_root).ui_port) or 0
                if ui_port:
                    try:
                        webbrowser.open(f"http://127.0.0.1:{ui_port}/")
                    except Exception:                  # noqa: BLE001 - 开不了浏览器不是故障
                        pass

        def on_gone(spec: object, child: object) -> None:
            if str(getattr(spec, "id", "")) != CODEX_CHILD_ID:
                return
            try:
                if CODEX_UI_CHILD_ID in sup.state_ids():
                    sup.stop(CODEX_UI_CHILD_ID)
            except MechaError as exc:
                print(f"[codex] 界面收尾失败：{exc.message}", flush=True)

        sup.add_ready_hook(on_ready)
        sup.add_gone_hook(on_gone)

    def overlay_refresh(self, plan: HostPlan,
                        wiring: HostWiring) -> Callable[[], object] | None:
        return None

    def client_refresh(self, plan: HostPlan,
                       wiring: HostWiring) -> Callable[[], object] | None:
        return None

    def refresh_catalog(self, plan: HostPlan, revision: int) -> RefreshResult:
        paths = paths_for(plan.runtime_root)
        paths.home.mkdir(parents=True, exist_ok=True)
        paths.catalog_revision.write_text(f"{int(revision)}\n", encoding="utf-8")
        return RefreshResult(
            True,
            "Codex 外部会话需重连 MCP 客户端；当前工具表版本已写入 catalog-revision",
            int(revision))

    def capabilities(self, plan: HostPlan) -> Mapping[str, object]:
        paths = paths_for(plan.runtime_root)
        #: ⚠ 只读自省：控制台可能在聚合端点回读端口之前就渲染，那时 URL 还不存在。
        #: 硬抛会让页面 500（实测过）。真正的硬校验留在 `prepare()`。
        try:
            aggregate_url = self._url(plan)
        except MechaError:
            aggregate_url = ""
        ui_port = read_port(paths.ui_port) or 0
        return {"label": self.name, "managed": True, "child_id": CODEX_CHILD_ID,
                "external": False, "config_path": str(paths.config),
                "start_file": str(paths.start),
                "task_ledger": str(ledger_path(plan.runtime_root)),
                "aggregate_url": aggregate_url,
                #: 界面地址（人点的链接）：只有端口定了才有。
                "ui_url": f"http://127.0.0.1:{ui_port}/" if ui_port else "",
                "app_server_port": read_port(paths.app_server_port) or 0}

    def shutdown(self, plan: HostPlan) -> None:
        return None


__all__ = ["CodexHostAdapter", "WEBUI_ENTRY"]
