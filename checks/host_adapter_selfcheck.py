"""宿主适配层判据：目录隔离、Codex 配置与启动计划。

不依赖 Codex 可执行文件；只验证 mecha 生成的契约与现有 dsh 兼容面。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mecha.aggregate_runtime import overlay_text  # noqa: E402
from mecha.codex_adapter import (CodexHostAdapter, config_text,  # noqa: E402
                                 parse_post_tool_use, permission_response_json)
from mecha.codex_adapter.host import paths_for  # noqa: E402
from mecha.dsh_adapter.runtime import overlay_text as dsh_overlay_text  # noqa: E402
from mecha.dsh_adapter.runtime import with_overlay_patch  # noqa: E402
from mecha.host_adapter import AIHostKind, make_host_plan, select_host_adapter  # noqa: E402
from mecha.launch import build_children, plan_startup  # noqa: E402
from mecha.site import ReadySpec, Service, Site  # noqa: E402
from mecha.supervise import ChildSpec  # noqa: E402

FAILS: list[str] = []


def _check(name: str, cond: bool, extra: object = "") -> None:
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"  ← {extra}"))
    if not cond:
        FAILS.append(name)


def _site() -> Site:
    svc = Service(
        id="DEMO", brief="demo", argv=(sys.executable, "-c", "pass"),
        cwd=Path.cwd(), ready=ReadySpec(port_file=".p"),
        data_root=Path.cwd(),
    )
    return Site(name="demo-site", services=(svc,))


def check_adapter_selection() -> None:
    plan = make_host_plan(AIHostKind.CODEX, (_site(),), Path.cwd(),
                          aggregate_port=3089,
                          aggregate_url="http://127.0.0.1:3089/mcp")
    _check("dsh/codex/none 三种适配器可选",
           (select_host_adapter("dsh").name,
            select_host_adapter("codex").name,
            select_host_adapter("none").name) == ("dsh", "codex", "none"))
    _check("HostPlan 保留站点、服务与回读端口",
           plan.kind is AIHostKind.CODEX and plan.services[0].id == "DEMO"
           and plan.aggregate_port == 3089)
    _check("Codex 是外部宿主，不产生 child",
           select_host_adapter("codex").child(plan) is None
           and select_host_adapter("codex").managed is False)


def check_startup_plan() -> None:
    site = _site()
    codex_plan = plan_startup([site], ["DEMO"], host_kind="codex")
    none_plan = plan_startup([site], ["DEMO"], host_kind="none")
    _check("codex 启动计划只有软件 child，没有 dsh",
           [c.id for c in codex_plan.ordered] == ["DEMO"]
           and all(c.id != "dsh" for c in codex_plan.ordered))
    _check("none 启动计划同样不产生 AI 宿主 child",
           [c.id for c in none_plan.ordered] == ["DEMO"])
    _check("build_children 走适配器，不在 launch 内写宿主分支",
           [c.id for c in build_children([site], ["DEMO"], "codex")] == ["DEMO"])


def check_codex_config() -> None:
    text = config_text(aggregate_url="http://127.0.0.1:3089/mcp")
    _check("Codex 配置使用 url 触发 streamable HTTP",
           'url = "http://127.0.0.1:3089/mcp"' in text
           and "transport" not in text)
    _check("Codex 配置要求聚合端点启动时必需",
           "required = true" in text and 'startup_readiness = "connection"' in text)
    _check("Codex 工具预算与 mecha 的 600000ms 对齐",
           "tool_timeout_sec = 600" in text)
    _check("Codex 配置不写内联 bearer_token",
           "bearer_token" not in text)
    _check("控制工具不在默认暴露列表之外",
           "set_write_authority" not in text.split("disabled_tools")[0])
    bad = False
    try:
        config_text(aggregate_url="file:///tmp/x")
    except Exception:
        bad = True
    _check("非法聚合 URL 被拒（不生成假地址）", bad)


def check_codex_prepare() -> None:
    adapter = CodexHostAdapter()
    with tempfile.TemporaryDirectory(prefix="codex-adapter-") as td:
        plan = make_host_plan(AIHostKind.CODEX, (_site(),), Path(td),
                              aggregate_port=3089,
                              aggregate_url="http://127.0.0.1:3089/mcp")
        prep = adapter.prepare(plan)
        paths = paths_for(Path(td))
        _check("Codex 配置落在隔离 CODEX_HOME",
               prep.config_path == paths.config
               and paths.config.is_file()
               and str(paths.home) in prep.command)
        _check("配置包含 MCP server、AGENTS 与启动说明",
               "[mcp_servers.mecha]" in paths.config.read_text(encoding="utf-8")
               and paths.agents.is_file() and paths.start.is_file())
        _check("启动说明使用 CODEX_HOME，不修改全局配置",
               "CODEX_HOME=" in paths.start.read_text(encoding="utf-8")
               and "~/.codex" not in paths.start.read_text(encoding="utf-8"))
        refreshed = adapter.refresh_catalog(plan, 7)
        _check("工具表刷新写 revision 并明确要求重连",
               refreshed.reconnect_required
               and paths.catalog_revision.read_text(encoding="utf-8").strip() == "7")
        caps = adapter.capabilities(plan)
        _check("外部宿主能力声明不伪造 managed child",
               caps["external"] is True and caps["managed"] is False
               and caps["child_id"] is None)


def check_hooks_and_compat() -> None:
    event = parse_post_tool_use({
        "hook_event_name": "PostToolUse",
        "session_id": "s", "turn_id": "t", "tool_name": "mcp__mecha__demo_run",
        "tool_use_id": "u", "tool_input": {"x": 1}, "tool_response": {"ok": True},
    })
    _check("PostToolUse 输入字段按 Codex snake_case 读取",
           event.tool_name == "mcp__mecha__demo_run"
           and event.tool_response == {"ok": True})
    deny = json.loads(permission_response_json(allow=False, message="blocked"))
    _check("PermissionRequest 输出是嵌套 behavior=deny",
           deny["hookSpecificOutput"]["hookEventName"] == "PermissionRequest"
           and deny["hookSpecificOutput"]["decision"]["behavior"] == "deny")
    child = ChildSpec(id="dsh", argv=("dsh", "web"), cwd=Path.cwd())
    patched = with_overlay_patch(child, Path("overlay.yml"))
    _check("overlay patch 仍以成对 --patch 插入",
           patched.argv[-2:] == ("--patch", "overlay.yml"))
    _check("旧 aggregate_runtime.overlay_text 与新 dsh_adapter 实现同源",
           overlay_text(mcp_port=3089, server_name="mecha")
           == dsh_overlay_text(mcp_port=3089, server_name="mecha"))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("宿主适配层判据：")
    check_adapter_selection()
    check_startup_plan()
    check_codex_config()
    check_codex_prepare()
    check_hooks_and_compat()
    if FAILS:
        print(f"host_adapter_selfcheck: FAIL（{len(FAILS)} 条）")
        return 1
    print("host_adapter_selfcheck: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
