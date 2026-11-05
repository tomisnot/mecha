"""dsh 宿主适配层：所有 dsh 专用的 Python 接线都住在这里。"""

from .host import DshHostAdapter
from .launch import (dsh_child, make_client_refresh, make_overlay_refresh,
                     merge_dsh, warn_if_port_taken)
from .panel import MCP_REFRESH_ROUTE, with_panel_contributions
from .runtime import (DEFAULT_TOOL_CALL_TIMEOUT_MS, make_prepare_hook,
                      overlay_text, site_runtime, with_overlay_patch,
                      write_dsh_overlay)

__all__ = [
    "DEFAULT_TOOL_CALL_TIMEOUT_MS", "DshHostAdapter", "MCP_REFRESH_ROUTE",
    "dsh_child", "make_client_refresh", "make_overlay_refresh",
    "make_prepare_hook", "merge_dsh", "overlay_text", "site_runtime",
    "with_overlay_patch", "with_panel_contributions", "warn_if_port_taken",
    "write_dsh_overlay",
]
