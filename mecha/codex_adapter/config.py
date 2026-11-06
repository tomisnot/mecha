"""Codex 的隔离配置生成。

依据 Codex ``codex-rs/config/src/mcp_types.rs``：streamable HTTP 由 ``url``
触发，内联 ``bearer_token`` 被 ``mcp_edit.rs`` 拒绝，工具调用预算用
``tool_timeout_sec``。这里不写用户全局 ``~/.codex/config.toml``。
"""

from __future__ import annotations

import json
import os
import secrets
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ..errors import MechaError
from ..portfile import pick_free_port, read_port
from .tasks import TASK_LEDGER_FILENAME

MCP_SERVER_NAME = "mecha"
CONFIG_FILENAME = "config.toml"
START_FILENAME = "codex-start.txt"
AGENTS_FILENAME = "AGENTS.md"
CATALOG_REVISION_FILENAME = "catalog-revision"
DEFAULT_TOOL_TIMEOUT_SEC = 600.0
DEFAULT_STARTUP_TIMEOUT_SEC = 30.0
DENIED_TOOLS = ("set_write_authority", "open_manual_surface")

#: 托管形态（2026-11-05 起）：codex 不再是"只写配置的外部宿主"，而是由 supervisor
#: 起一个 ``codex app-server`` 常驻进程 —— 与 dsh 同构。
CODEX_CHILD_ID = "codex"
#: 浏览器界面那个进程（vendor 进来的第三方 Web UI + 代理）。
CODEX_UI_CHILD_ID = "codex-ui"
#: 端口文件与 token：都放在隔离 CODEX_HOME 里（谁写的谁清，跟着运行目录走）。
APP_SERVER_PORT_FILENAME = "app-server-port"
UI_PORT_FILENAME = "ui-port"
WS_TOKEN_FILENAME = "ws-token"
AUTH_FILENAME = "auth.json"
ENV_FILENAME = ".env"


@dataclass(frozen=True)
class CodexPaths:
    home: Path
    config: Path
    start: Path
    agents: Path
    catalog_revision: Path
    #: 托管形态新增：app-server 端口 / UI 端口 / ws token / 登录凭据
    app_server_port: Path
    ui_port: Path
    ws_token: Path
    auth: Path
    #: 用户 `~/.codex/.env` 的副本（代理配置常放这儿；没有就跳过）
    env: Path


def paths_for(runtime_root: Path) -> CodexPaths:
    home = Path(runtime_root) / "codex-home"
    return CodexPaths(
        home=home,
        config=home / CONFIG_FILENAME,
        start=home / START_FILENAME,
        agents=home / AGENTS_FILENAME,
        catalog_revision=home / CATALOG_REVISION_FILENAME,
        app_server_port=home / APP_SERVER_PORT_FILENAME,
        ui_port=home / UI_PORT_FILENAME,
        ws_token=home / WS_TOKEN_FILENAME,
        auth=home / AUTH_FILENAME,
        env=home / ENV_FILENAME,
    )


def _toml_string(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _toml_array(values: tuple[str, ...] | list[str]) -> str:
    return "[" + ", ".join(_toml_string(v) for v in values) + "]"


#: provider 表里**不属于传输层**的键：隔离环境不该被它们牵连（`notify` 会拉起 GUI 程序）。
_NON_TRANSPORT_KEYS = {"notify"}


def extract_transport(user_config: Path) -> str:
    """从用户自己的 `~/.codex/config.toml` 里抠出"传输层"那段。

    为什么需要它（实测）：codex 默认**先试 WSS**，失败 5 次才回退 HTTPS —— 这就是那个
    "Reconnecting 1/5 … 5/5"。用户在自己的 config 里写了
    ``model_provider = "openai_http"`` + ``supports_websockets = false`` 把 WSS 关掉，
    但**隔离 home 的 config 是我们生成的**，不继承他这段 ⇒ 重连 5/5 又回来了。

    只搬两样东西：顶层 ``model_provider = "..."`` 一行，以及对应的
    ``[model_providers.<name>]`` 表（原样保留它的键值，含 base_url / wire_api /
    supports_websockets）。⚠ 表里的 ``notify`` 不搬 —— 那不是传输设置，
    而是一个会拉起外部程序的钩子。

    ⚠ 用文本抠而不是解析 TOML：Python 3.10 没有 ``tomllib``，而这里要的是**原样搬运**。
    抠不到（文件不在 / 没有 model_provider）⇒ 返回空串，配置回到默认行为。
    """
    path = Path(user_config)
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""

    provider = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            break                                  # 顶层键只出现在第一个表头之前
        if stripped.startswith("model_provider") and "=" in stripped:
            provider = stripped.split("=", 1)[1].strip().strip('"').strip("'")
            break
    if not provider:
        return ""

    header = f"[model_providers.{provider}]"
    body: list[str] = []
    inside = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            inside = stripped == header
            if inside:
                body.append(stripped)
            continue
        if not inside:
            continue
        key = stripped.split("=", 1)[0].strip() if "=" in stripped else ""
        if key in _NON_TRANSPORT_KEYS:
            continue
        if not stripped:
            continue                               # 表内空行只是排版，删掉更干净
        body.append(line.rstrip())
    if len(body) <= 1:                             # 只有表头 ⇒ 说明没这段
        return ""
    return ("model_provider = " + _toml_string(provider) + "\n\n"
            + "\n".join(body) + "\n")


def config_text(*, aggregate_url: str,
                server_name: str = MCP_SERVER_NAME,
                tool_timeout_sec: float = DEFAULT_TOOL_TIMEOUT_SEC,
                startup_timeout_sec: float = DEFAULT_STARTUP_TIMEOUT_SEC,
                disabled_tools: tuple[str, ...] = DENIED_TOOLS,
                hooks: str = "",
                transport: str = "") -> str:
    """生成一份 Codex 可解析的配置（MCP server + 继承来的传输层）。"""
    if not aggregate_url.startswith(("http://", "https://")):
        raise MechaError("聚合端点 URL 不合法", kind="bad_codex_mcp_url",
                         hint="url 必须来自 McpEndpoint.start() 的回读端口")
    if tool_timeout_sec <= 0 or startup_timeout_sec <= 0:
        raise MechaError("Codex MCP 超时必须为正数", kind="bad_codex_timeout")
    lines: list[str] = []
    if transport.strip():
        lines.append(transport.strip())
        lines.append("")
    lines += [
        f"[mcp_servers.{server_name}]",
        f"url = {_toml_string(aggregate_url)}",
        "required = true",
        "startup_readiness = \"connection\"",
        f"startup_timeout_sec = {float(startup_timeout_sec):g}",
        f"tool_timeout_sec = {float(tool_timeout_sec):g}",
    ]
    if disabled_tools:
        lines.append(f"disabled_tools = {_toml_array(disabled_tools)}")
    if hooks:
        lines.append("")
        lines.append(hooks.rstrip())
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


def _hook_command(mode: str, ledger: Path) -> tuple[str, str]:
    argv = [sys.executable, "-m", "mecha.codex_adapter.hook_entry", mode,
            "--ledger", str(ledger)]
    posix = shlex.join(argv)
    windows = subprocess.list2cmdline(argv)
    return posix, windows


def hooks_text(*, ledger: Path) -> str:
    """生成 PostToolUse 任务账与 PermissionRequest 审计 Hook。"""
    post, post_win = _hook_command("post-tool-use", ledger)
    perm, perm_win = _hook_command("permission-request", ledger)
    return "\n".join([
        "[[hooks.PostToolUse]]",
        'matcher = "^mcp__mecha__.*"',
        "",
        "[[hooks.PostToolUse.hooks]]",
        'type = "command"',
        f"command = {_toml_string(post)}",
        f"command_windows = {_toml_string(post_win)}",
        "timeout = 10",
        "",
        "[[hooks.PermissionRequest]]",
        'matcher = ".*"',
        "",
        "[[hooks.PermissionRequest.hooks]]",
        'type = "command"',
        f"command = {_toml_string(perm)}",
        f"command_windows = {_toml_string(perm_win)}",
        "timeout = 5",
        "",
    ])


def write_preparation(runtime_root: Path, *, aggregate_url: str,
                      server_name: str = MCP_SERVER_NAME,
                      tool_timeout_sec: float = DEFAULT_TOOL_TIMEOUT_SEC,
                      startup_timeout_sec: float = DEFAULT_STARTUP_TIMEOUT_SEC,
                      user_config: Path | None = None
                      ) -> tuple[CodexPaths, str]:
    """写隔离配置并返回路径与启动命令；重复执行按当前端口覆盖。

    ⭐ **继承用户自己的传输层设置**（`model_provider` + 对应 provider 表）：
    用户若在 `~/.codex/config.toml` 里关掉了 WebSocket（`supports_websockets = false`），
    隔离 home 必须跟着关，否则 codex 又回到"先试 WSS、失败 5 次再回退"那条路上。
    ⚠ 这是**读**用户配置，不修改它。
    """
    paths = paths_for(runtime_root)
    paths.home.mkdir(parents=True, exist_ok=True)
    ledger = paths.home / TASK_LEDGER_FILENAME
    source = (Path(user_config) if user_config is not None
              else (Path.home() / ".codex" / CONFIG_FILENAME))
    config = config_text(aggregate_url=aggregate_url, server_name=server_name,
                         tool_timeout_sec=tool_timeout_sec,
                         startup_timeout_sec=startup_timeout_sec,
                         hooks=hooks_text(ledger=ledger),
                         transport=extract_transport(source))
    paths.config.write_text(config, encoding="utf-8")
    paths.agents.write_text(agents_text(), encoding="utf-8")
    paths.start.write_text(start_text(paths), encoding="utf-8")
    paths.catalog_revision.write_text("0\n", encoding="utf-8")
    command = f"CODEX_HOME={paths.home} codex"
    return paths, command


# --------------------------------------------------------------- 托管形态的运行期准备


def _write_port(path: Path, port: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{int(port)}\n", encoding="utf-8")


def _pick_excluding(taken: set[int]) -> int:
    for _ in range(20):
        port = pick_free_port()
        if port not in taken:
            return port
    raise MechaError("挑不到空闲端口（连着 20 次都撞上同一批）",
                     kind="no_free_port", hint="端口被大量占用，稍后重试或手工清一下")


def ensure_ports(paths: CodexPaths) -> tuple[int, int]:
    """把 app-server 与 UI 两个端口**先定下来**（幂等，返回 ``(app_server, ui)``）。

    ⚠ 为什么必须"先定"：`codex app-server` 不写端口文件，端口是启动者给的
    （``--listen ws://127.0.0.1:<port>``）。dsh 那种"进程自己回读再写文件"的判据
    在这里不成立 ⇒ 我们先把选好的端口落成文件，supervisor 用
    ``ReadySpec(port=...)`` 直接探活（见 ``supervise.ReadySpec.port``）。

    ⚠ ``pick_free_port`` 有 TOCTOU（挑到 ≠ 一定抢得到）：真被抢了，表现是
    ``/readyz`` 一直不通 ⇒ 就绪超时并**如实报**，不假设"挑到了就一定能用"。
    """
    port = read_port(paths.app_server_port) or 0
    ui = read_port(paths.ui_port) or 0
    taken: set[int] = set()
    if port:
        taken.add(port)
    if ui and ui != port:
        taken.add(ui)
    if not port:
        port = _pick_excluding(taken)
        _write_port(paths.app_server_port, port)
        taken.add(port)
    if not ui or ui == port:
        ui = _pick_excluding(taken)
        _write_port(paths.ui_port, ui)
    return int(port), int(ui)


def ensure_ws_token(paths: CodexPaths) -> Path:
    """capability token：**只在不存在时生成**；app-server 与代理读同一个文件。"""
    if not paths.ws_token.is_file():
        paths.ws_token.parent.mkdir(parents=True, exist_ok=True)
        paths.ws_token.write_text(secrets.token_hex(32), encoding="utf-8")
    return paths.ws_token


def ensure_auth(paths: CodexPaths, *, source: Path | None = None) -> str:
    """把**已登录**的凭据复制进隔离 home；返回 ``copied`` / ``exists`` / ``missing``。

    ⚠ 只复制 ``auth.json``（凭据），**不复制**会话、历史与配置 —— 那些是用户自己的。
    ⚠ 它是真凭据：目标在 ``_mecha_runtime/`` 下（不入仓），但也不该被随手拷走。
    ⚠ 源不存在（用户没登录过）⇒ 返回 ``missing``，**不抛**：让页面如实显示"未登录"，
    总比启动时炸在别人看不懂的地方好。
    """
    if paths.auth.is_file():
        return "exists"
    src = Path(source) if source is not None else (Path.home() / ".codex" / AUTH_FILENAME)
    if not src.is_file():
        return "missing"
    paths.home.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, paths.auth)
    return "copied"


def ensure_env(paths: CodexPaths, *, source: Path | None = None) -> str:
    """把用户 `~/.codex/.env` 复制进隔离 home；返回 ``copied`` / ``exists`` / ``missing``。

    ⚠ 这份文件通常只有代理配置（HTTP(S)_PROXY / NO_PROXY）。**不存在就跳过**，
    不报错 —— 没有代理的人不该被这件事挡住。
    ⚠ 只复制凭据与代理设置，不改用户的文件。
    """
    if paths.env.is_file():
        return "exists"
    src = Path(source) if source is not None else (Path.home() / ".codex" / ENV_FILENAME)
    if not src.is_file():
        return "missing"
    paths.home.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, paths.env)
    return "copied"


__all__ = [
    "AGENTS_FILENAME", "APP_SERVER_PORT_FILENAME", "AUTH_FILENAME", "ENV_FILENAME",
    "CATALOG_REVISION_FILENAME", "CODEX_CHILD_ID", "CODEX_UI_CHILD_ID",
    "CONFIG_FILENAME",
    "DEFAULT_STARTUP_TIMEOUT_SEC", "DEFAULT_TOOL_TIMEOUT_SEC", "DENIED_TOOLS",
    "MCP_SERVER_NAME", "START_FILENAME", "UI_PORT_FILENAME", "WS_TOKEN_FILENAME",
    "CodexPaths", "agents_text",
    "config_text", "ensure_auth", "ensure_env", "ensure_ports", "ensure_ws_token",
    "extract_transport",
    "hooks_text", "paths_for", "start_text",
    "write_preparation",
]
