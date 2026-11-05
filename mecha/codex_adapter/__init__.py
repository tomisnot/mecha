"""Codex 宿主适配层：配置、Hooks wire 与外部 MCP 适配器。"""

from .config import MCP_SERVER_NAME, config_text, paths_for, write_preparation
from .hooks import (bare_tool_name, parse_post_tool_use, permission_response,
                    permission_response_json)
from .host import CodexHostAdapter

__all__ = [
    "CodexHostAdapter", "MCP_SERVER_NAME", "bare_tool_name", "config_text",
    "parse_post_tool_use", "paths_for", "permission_response",
    "permission_response_json", "write_preparation",
]
