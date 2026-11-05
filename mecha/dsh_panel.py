"""兼容导入面：dsh 面板实现已迁入 :mod:`mecha.dsh_adapter.panel`。

保留本模块是为了不打断既有项目、判据和 package-data 路径；新代码应直接使用
``mecha.dsh_adapter.panel``。
"""

from __future__ import annotations

from .dsh_adapter.panel import (
    ASSET_DIR_NAME,
    HOST_PLUGIN_REL,
    MCP_REFRESH_ROUTE,
    PANEL_PLUGIN_ENTRY_ID,
    PANEL_PORT_FILES_ENV,
    PANEL_ROUTE_ENV,
    PANEL_SERVICES_ENV,
    PANEL_SERVICES_ROUTE,
    PATCH_FILENAME,
    TASK_SPECS_ENV,
    asset_dir,
    framework_patch_path,
    framework_plugin_artifact,
    panel_env,
    panel_kind,
    panel_payload,
    panel_port_files,
    panel_services,
    panel_tab_id,
    services_with_monitor,
    task_spec_wire,
    with_panel_contributions,
)

__all__ = [
    "ASSET_DIR_NAME", "HOST_PLUGIN_REL", "MCP_REFRESH_ROUTE",
    "PANEL_PLUGIN_ENTRY_ID", "PANEL_PORT_FILES_ENV", "PANEL_ROUTE_ENV",
    "PANEL_SERVICES_ENV", "PANEL_SERVICES_ROUTE", "PATCH_FILENAME",
    "TASK_SPECS_ENV", "asset_dir", "framework_patch_path",
    "framework_plugin_artifact", "panel_env", "panel_kind", "panel_payload",
    "panel_port_files", "panel_services", "panel_tab_id",
    "services_with_monitor", "task_spec_wire", "with_panel_contributions",
]
