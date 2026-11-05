"""dsh 的声明合并、child 与运行期刷新逻辑。"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path

from ..aggregate import AGGREGATE_SERVER_NAME
from ..errors import MechaError
from ..site import DSH_CHILD_ID, DSHSpec, Service, Site
from ..supervise import ChildSpec, which_or_raise
from .panel import MCP_REFRESH_ROUTE, with_panel_contributions
from .runtime import DEFAULT_TOOL_CALL_TIMEOUT_MS, site_runtime, write_dsh_overlay


def flat_services(sites: Sequence[Site]) -> tuple[Service, ...]:
    return tuple(svc for site in sites for svc in site.services)


def merge_dsh(sites: Sequence[Site]) -> tuple[ChildSpec, DSHSpec]:
    """把各站点的 dsh 声明合并成那一个 dsh；身份冲突响亮失败。"""
    decls = [(s, s.dsh) for s in sites if getattr(s, "dsh", None) is not None]
    if not decls:
        first_svc = flat_services(sites)
        cwd = first_svc[0].cwd if first_svc else Path.cwd()
        dsh = with_panel_contributions(DSHSpec(command=("dsh", "web"), cwd=cwd), sites)
        return dsh_child(dsh), dsh

    head_site, head = decls[0]
    for site, d in decls[1:]:
        for field in ("command", "port_preference", "port_tries", "stop_timeout", "ready"):
            if getattr(d, field) != getattr(head, field):
                raise MechaError(
                    f"两个站点的 dsh 声明不一致：{head_site.name} 与 {site.name} "
                    f"的 `{field}` 不同（{getattr(head, field)!r} vs {getattr(d, field)!r}）",
                    kind="dsh_decl_conflict",
                    hint="dsh 是全生态一个；身份字段必须一致，插件清单才取并集",
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
                    hint="dsh 只有一个进程；同名变量只能有一个值",
                )
            env.setdefault(k, v)
    merged = DSHSpec(
        command=head.command, cwd=head.cwd,
        patches=tuple(patches), plugin_artifacts=tuple(artifacts),
        port_preference=head.port_preference, port_tries=head.port_tries,
        env=env, ready=head.ready, stop_timeout=head.stop_timeout)
    merged = with_panel_contributions(merged, sites)
    return dsh_child(merged), merged


def dsh_child(dsh: DSHSpec) -> ChildSpec:
    return ChildSpec(
        id=DSH_CHILD_ID, argv=which_or_raise(dsh.argv()), cwd=dsh.cwd,
        env=dict(dsh.env), ready=dsh.ready, stop_timeout=dsh.stop_timeout,
        data_root=None,
    )


def _listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def warn_if_port_taken(sites: Sequence[Site]) -> None:
    """dsh 首选端口被占时明确提醒，不阻塞本次启动。"""
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


def make_overlay_refresh(sites: Sequence[Site], mcp_port: int) -> Callable[[], object]:
    """工具表变化时重写 dsh overlay；端口来自已回读的聚合端点。"""
    def refresh() -> object:
        if not sites:
            return None
        return write_dsh_overlay(
            site_runtime(sites[0]), mcp_port=int(mcp_port),
            server_name=AGGREGATE_SERVER_NAME,
            timeout_ms=DEFAULT_TOOL_CALL_TIMEOUT_MS)
    return refresh


def make_client_refresh(sites: Sequence[Site], supervisor: object) -> Callable[[], object]:
    """通过 dsh host 插件的刷新路由重建 MCP 客户端，并验证最终 URL。"""
    def refresh() -> object:
        rows = supervisor.state().get("children", [])  # type: ignore[attr-defined]
        row = next((r for r in rows
                    if r.get("id") == DSH_CHILD_ID
                    and isinstance(r.get("port"), int) and r["port"] > 0), None)
        port = row["port"] if row is not None else None
        if not isinstance(port, int) or port <= 0:
            _, dsh_spec = merge_dsh(sites)
            port = int(dsh_spec.port_preference)
            print(f"[aggregate] WARN supervisor 未回读 DSH 端口，使用启动声明端口 {port}",
                  flush=True)
        url = f"http://127.0.0.1:{port}{MCP_REFRESH_ROUTE}"
        req = urllib.request.Request(
            url, data=b"{}", method="POST",
            headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            print(f"[aggregate] DSH 刷新路由返回 HTTP {resp.status}：{body}", flush=True)
            def verify() -> None:
                for attempt in range(1, 6):
                    time.sleep(1.0)
                    try:
                        with urllib.request.urlopen(
                                urllib.request.Request(url, method="GET"),
                                timeout=1.0) as check:
                            detail = check.read().decode("utf-8", errors="replace")
                        diagnostic = json.loads(detail).get("diagnostic", {})
                        if (diagnostic.get("found")
                                and diagnostic.get("requestedUrl") == diagnostic.get("currentUrl")):
                            print("[aggregate] DSH 刷新验证通过", flush=True)
                            return
                    except (urllib.error.URLError, OSError) as exc:
                        print(f"[aggregate] DSH 刷新验证 #{attempt} 失败：{exc}", flush=True)
                print("[aggregate] WARN DSH 刷新验证未确认", flush=True)
            threading.Thread(target=verify, name="mecha-dsh-refresh-verify",
                             daemon=True).start()
            return True
        except (urllib.error.URLError, OSError) as exc:
            raise RuntimeError(f"DSH 主动刷新路由不可用：{exc}") from exc
    return refresh


__all__ = [
    "dsh_child", "flat_services", "make_client_refresh", "make_overlay_refresh",
    "merge_dsh", "warn_if_port_taken",
]
