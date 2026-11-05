"""Codex 的隔离配置生成。

依据 Codex ``codex-rs/config/src/mcp_types.rs``：streamable HTTP 由 ``url``
触发，内联 ``bearer_token`` 被 ``mcp_edit.rs`` 拒绝，工具调用预算用
``tool_timeout_sec``。这里不写用户全局 ``~/.codex/config.toml``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..errors import MechaError

MCP_SERVER_NAME = "mecha"
CONFIG_FILENAME = "config.toml"
START_FILENAME = "codex-start.txt"
AGENTS_FILENAME = "AGENTS.md"
CATALOG_REVISION_FILENAME = "catalog-revision"
DEFAULT_TOOL_TIMEOUT_SEC = 600.0
DEFAULT_STARTUP_TIMEOUT_SEC = 30.0
DENIED_TOOLS = ("set_write_authority", "open_manual_surface")


@dataclass(frozen=True)
class CodexPaths:
    home: Path
    config: Path
    start: Path
    agents: Path
    catalog_revision: Path


def paths_for(runtime_root: Path) -> CodexPaths:
    home = Path(runtime_root) / "codex-home"
    return CodexPaths(
        home=home,
        config=home / CONFIG_FILENAME,
        start=home / START_FILENAME,
        agents=home / AGENTS_FILENAME,
        catalog_revision=home / CATALOG_REVISION_FILENAME,
    )


def _toml_string(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _toml_array(values: tuple[str, ...] | list[str]) -> str:
    return "[" + ", ".join(_toml_string(v) for v in values) + "]"


def config_text(*, aggregate_url: str,
                server_name: str = MCP_SERVER_NAME,
                tool_timeout_sec: float = DEFAULT_TOOL_TIMEOUT_SEC,
                startup_timeout_sec: float = DEFAULT_STARTUP_TIMEOUT_SEC,
                disabled_tools: tuple[str, ...] = DENIED_TOOLS) -> str:
    """生成一份 Codex 可解析的 MCP server 配置。"""
    if not aggregate_url.startswith(("http://", "https://")):
        raise MechaError("聚合端点 URL 不合法", kind="bad_codex_mcp_url",
                         hint="url 必须来自 McpEndpoint.start() 的回读端口")
    if tool_timeout_sec <= 0 or startup_timeout_sec <= 0:
        raise MechaError("Codex MCP 超时必须为正数", kind="bad_codex_timeout")
    lines = [
        f"[mcp_servers.{server_name}]",
        f"url = {_toml_string(aggregate_url)}",
        "required = true",
        "startup_readiness = \"connection\"",
        f"startup_timeout_sec = {float(startup_timeout_sec):g}",
        f"tool_timeout_sec = {float(tool_timeout_sec):g}",
    ]
    if disabled_tools:
        lines.append(f"disabled_tools = {_toml_array(disabled_tools)}")
    return "\n".join(lines) + "\n"


def agents_text() -> str:
    return (
        "# mecha 接入说明\n\n"
        "领域状态、运行记录和结果文件属于软件本身。修改项目状态时只使用 mecha 聚合 MCP 工具；\n"
        "不要通过 shell、Python API 或直接文件写入修改项目数据目录、journal 或数据库。\n"
        "写操作被拒绝时先调用 read_write_authority 查询当前写权，再按错误提示处理。\n"
    )


def start_text(paths: CodexPaths) -> str:
    return (
        "CODEX_HOME=" + str(paths.home) + " codex\n"
        "# Windows PowerShell:\n"
        "# $env:CODEX_HOME='" + str(paths.home) + "'; codex\n"
    )


def write_preparation(runtime_root: Path, *, aggregate_url: str,
                      server_name: str = MCP_SERVER_NAME,
                      tool_timeout_sec: float = DEFAULT_TOOL_TIMEOUT_SEC,
                      startup_timeout_sec: float = DEFAULT_STARTUP_TIMEOUT_SEC
                      ) -> tuple[CodexPaths, str]:
    """写隔离配置并返回路径与启动命令；重复执行按当前端口覆盖。"""
    paths = paths_for(runtime_root)
    paths.home.mkdir(parents=True, exist_ok=True)
    config = config_text(aggregate_url=aggregate_url, server_name=server_name,
                         tool_timeout_sec=tool_timeout_sec,
                         startup_timeout_sec=startup_timeout_sec)
    paths.config.write_text(config, encoding="utf-8")
    paths.agents.write_text(agents_text(), encoding="utf-8")
    paths.start.write_text(start_text(paths), encoding="utf-8")
    paths.catalog_revision.write_text("0\n", encoding="utf-8")
    command = f"CODEX_HOME={paths.home} codex"
    return paths, command


__all__ = [
    "AGENTS_FILENAME", "CATALOG_REVISION_FILENAME", "CONFIG_FILENAME",
    "DEFAULT_STARTUP_TIMEOUT_SEC", "DEFAULT_TOOL_TIMEOUT_SEC", "DENIED_TOOLS",
    "MCP_SERVER_NAME", "START_FILENAME", "CodexPaths", "agents_text",
    "config_text", "paths_for", "start_text", "write_preparation",
]
