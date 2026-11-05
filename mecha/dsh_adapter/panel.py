"""dsh 监控面板的参数、patch 与环境注入。

这是宿主专用实现；旧的 ``mecha.dsh_panel`` 只保留兼容再导出。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from ..errors import MechaError
from ..site import ExtraPage, MonitorSpec, Service, Site

PANEL_SERVICES_ROUTE = "/mecha/panels"
MCP_REFRESH_ROUTE = "/mecha/mcp/refresh"

PANEL_ROUTE_ENV = "MECHA_PANEL_ROUTE"
PANEL_SERVICES_ENV = "MECHA_PANEL_SERVICES"
PANEL_PORT_FILES_ENV = "MECHA_PANEL_PORT_FILES"
TASK_SPECS_ENV = "MECHA_TASK_SPECS"

ASSET_DIR_NAME = "assets"
PATCH_FILENAME = "mecha-panel.patch.yml"
HOST_PLUGIN_REL = "host-plugin/mecha-panel-host.mjs"
PANEL_PLUGIN_ENTRY_ID = "mecha-panel-host"


def asset_dir() -> Path:
    """框架内 dsh 资产的绝对路径：``mecha/dsh_adapter/assets``。"""
    return Path(__file__).resolve().parent / ASSET_DIR_NAME


def framework_patch_path() -> Path:
    return asset_dir() / PATCH_FILENAME


def framework_plugin_artifact() -> Path:
    return asset_dir() / HOST_PLUGIN_REL


def panel_kind(svc_id: str) -> str:
    return f"mecha:{svc_id}"


def panel_tab_id(svc_id: str) -> str:
    return f"mecha-panel:{svc_id}"


def _extra_page_wire(page: ExtraPage) -> dict:
    return {
        "id": page.id,
        "label": page.label,
        "path": page.path,
        "title": page.title or page.label,
        "emptyText": page.empty_text,
        "unreadableText": page.unreadable_text,
        "note": page.note,
        "columns": [{"label": c.label, "field": c.field} for c in page.columns],
    }


def services_with_monitor(services: Sequence[Service]) -> tuple[Service, ...]:
    return tuple(s for s in services if s.monitor is not None)


def panel_services(services: Sequence[Service]) -> list[dict]:
    out: list[dict] = []
    for svc in services_with_monitor(services):
        spec: MonitorSpec = svc.monitor  # type: ignore[assignment]
        out.append({
            "id": svc.id,
            "kind": panel_kind(svc.id),
            "tabId": panel_tab_id(svc.id),
            "routePath": spec.route_path,
            "portFile": spec.port_file,
            "title": spec.title,
            "extraPages": [_extra_page_wire(p) for p in spec.extra_pages],
        })
    return out


def panel_payload(services: Sequence[Service]) -> dict:
    return {"routePath": PANEL_SERVICES_ROUTE, "services": panel_services(services)}


def panel_port_files(services: Sequence[Service]) -> dict[str, str]:
    out: dict[str, str] = {}
    for svc in services_with_monitor(services):
        p = svc.monitor_port_file()
        if p is not None:
            out[svc.id] = str(Path(p))
    return out


def task_spec_wire(services: Sequence[Service]) -> list[dict]:
    rows: list[dict] = []
    for svc in services:
        for task in getattr(svc, "long_tasks", ()):
            rows.append({
                "tool": f"{svc.id.lower()}_{task.submit_tool}",
                "service": svc.id,
                "status_tool": task.status_tool,
                "cancel_tool": task.cancel_tool,
                "task_id_field": task.task_id_field,
                "label_field": task.label_field,
                "state_field": task.state_field,
                "terminal_states": list(task.terminal_states),
                "wait_field": task.wait_field,
                "wait_value": task.wait_value,
            })
    return rows


def panel_env(services: Sequence[Service]) -> dict[str, str]:
    return {
        PANEL_ROUTE_ENV: PANEL_SERVICES_ROUTE,
        PANEL_SERVICES_ENV: json.dumps(panel_payload(services),
                                       ensure_ascii=False, separators=(",", ":")),
        PANEL_PORT_FILES_ENV: json.dumps(panel_port_files(services),
                                         ensure_ascii=False, separators=(",", ":")),
        TASK_SPECS_ENV: json.dumps(task_spec_wire(services),
                                   ensure_ascii=False, separators=(",", ":")),
    }


def with_panel_contributions(site_dsh, sites: Sequence[Site]):
    """把框架 patch、插件产物与面板变量并进那一个 dsh 声明。"""
    services = tuple(svc for site in sites for svc in site.services)
    env = dict(site_dsh.env)
    for key, value in panel_env(services).items():
        if key in env and env[key] != value:
            raise MechaError(
                f"{key} 是**框架**的变量，站点声明给了不同的值",
                kind="panel_env_reserved",
                hint="监控参数只从 `Service.monitor` 来；删掉 DSHSpec.env 里那个键",
            )
        env[key] = value
    patches = tuple(site_dsh.patches)
    if framework_patch_path() not in patches:
        patches += (framework_patch_path(),)
    artifacts = tuple(site_dsh.plugin_artifacts)
    if framework_plugin_artifact() not in artifacts:
        artifacts += (framework_plugin_artifact(),)
    return replace(site_dsh, patches=patches, plugin_artifacts=artifacts, env=env)


__all__ = [
    "MCP_REFRESH_ROUTE", "PANEL_PLUGIN_ENTRY_ID", "PANEL_PORT_FILES_ENV",
    "PANEL_ROUTE_ENV", "PANEL_SERVICES_ENV", "PANEL_SERVICES_ROUTE",
    "TASK_SPECS_ENV", "asset_dir", "framework_patch_path",
    "framework_plugin_artifact", "panel_env", "panel_kind", "panel_payload",
    "panel_port_files", "panel_services", "panel_tab_id",
    "services_with_monitor", "task_spec_wire", "with_panel_contributions",
]
