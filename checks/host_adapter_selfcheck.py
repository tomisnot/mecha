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
                                 write_preparation,
                                 parse_post_tool_use, permission_response_json,
                                 read_records)
from mecha.codex_adapter.hook_entry import (handle_permission_request,  # noqa: E402
                                             handle_post_tool_use)
from mecha.codex_adapter.config import (ensure_ports,  # noqa: E402
                                        ensure_ws_token)
from mecha.codex_adapter.host import paths_for  # noqa: E402
from mecha.codex_adapter.tasks import ledger_path  # noqa: E402
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


def _plan_at(root: Path, kind: str = "codex"):
    return make_host_plan(AIHostKind(kind), (_site(),), root,
                          aggregate_port=3089,
                          aggregate_url="http://127.0.0.1:3089/mcp")


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
    adapter = select_host_adapter("codex")
    _check("Codex 是**托管**宿主（managed=True，child_id=codex）",
           adapter.managed is True and adapter.child_id == "codex")
    with tempfile.TemporaryDirectory(prefix="codex-child-") as td:
        root = Path(td)
        paths = paths_for(root)
        paths.home.mkdir(parents=True, exist_ok=True)
        port, ui_port = ensure_ports(paths)
        ensure_ws_token(paths)
        child = adapter.child(_plan_at(root))
        _check("child() 给的是 app-server：固定端口 + /readyz 就绪判据",
               child is not None and child.id == "codex"
               and "--listen" in child.argv
               and any(f"ws://127.0.0.1:{port}" == a for a in child.argv)
               and child.ready is not None and child.ready.port == port
               and (child.ready.probe or "").endswith("/readyz"), str(child))
        ui = adapter.ui_child(_plan_at(root))
        _check("界面 child：connect-only 连本机 app-server，端口是 UI 端口",
               ui.id == "codex-ui" and "--upstream" in ui.argv
               and any(f"ws://127.0.0.1:{port}" == a for a in ui.argv)
               and ui.ready is not None and ui.ready.port == ui_port)


def check_startup_plan() -> None:
    site = _site()
    codex_plan = plan_startup([site], ["DEMO"], host_kind="codex")
    none_plan = plan_startup([site], ["DEMO"], host_kind="none")
    _check("codex 启动计划是 软件 → codex（不再有 dsh）",
           [c.id for c in codex_plan.ordered] == ["DEMO", "codex"]
           and all(c.id != "dsh" for c in codex_plan.ordered),
           str([c.id for c in codex_plan.ordered]))
    _check("none 启动计划不产生 AI 宿主 child",
           [c.id for c in none_plan.ordered] == ["DEMO"])
    _check("build_children 走适配器，宿主 child 由适配器给",
           [c.id for c in build_children([site], ["DEMO"], "codex")] == ["DEMO", "codex"])


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
        adapter.prepare_start(plan)
        prep = adapter.prepare(plan)
        paths = paths_for(Path(td))
        _check("Codex 配置落在隔离 CODEX_HOME",
               prep.config_path == paths.config
               and paths.config.is_file()
               and str(paths.home) in prep.command)
        text = paths.config.read_text(encoding="utf-8")
        _check("配置包含 MCP server、AGENTS 与启动说明",
               "[mcp_servers.mecha]" in text
               and paths.agents.is_file() and paths.start.is_file())
        _check("启动说明使用 CODEX_HOME，不修改全局配置",
               "CODEX_HOME=" in paths.start.read_text(encoding="utf-8")
               and "~/.codex" not in paths.start.read_text(encoding="utf-8"))
        _check("配置生成 PostToolUse 与 PermissionRequest Hook",
               "[[hooks.PostToolUse]]" in text
               and "[[hooks.PermissionRequest]]" in text
               and "mecha.codex_adapter.hook_entry" in text)
        _check("Hook 指向隔离任务账",
               "codex-home" in text and "tasks.jsonl" in text)
        refreshed = adapter.refresh_catalog(plan, 7)
        _check("工具表刷新写 revision 并明确要求重连",
               refreshed.reconnect_required
               and paths.catalog_revision.read_text(encoding="utf-8").strip() == "7")
        caps = adapter.capabilities(plan)
        _check("托管宿主能力声明：managed + child_id=codex + 界面地址",
               caps["external"] is False and caps["managed"] is True
               and caps["child_id"] == "codex"
               and str(caps.get("ui_url", "")).startswith("http://127.0.0.1:")
               and caps["task_ledger"] == str(ledger_path(Path(td))))
        handle_post_tool_use({
            "hook_event_name": "PostToolUse",
            "session_id": "s", "turn_id": "t",
            "tool_name": "mcp__mecha__demo_submit",
            "tool_use_id": "u", "tool_input": {},
            "tool_response": {"value": {"text": '{"job_id": "job-1"}'}},
        }, ledger_path(Path(td)))
        handle_permission_request({
            "hook_event_name": "PermissionRequest",
            "session_id": "s", "turn_id": "t",
            "tool_name": "mcp__mecha__demo_write", "tool_input": {},
        }, ledger_path(Path(td)))
        records = read_records(ledger_path(Path(td)))
        _check("PostToolUse 把 job_id 写入任务账",
               any(r.get("kind") == "task.submitted" and r.get("task_id") == "job-1"
                   for r in records))
        _check("PermissionRequest 只审计，不伪造 decision",
               any(r.get("kind") == "permission.requested" for r in records))


def check_codex_capabilities_before_endpoint() -> None:
    """控制台可能在聚合端点回读端口之前就渲染，`capabilities` 必须不抛。

    实测来源：`--all --ai-host codex` 起步时页面 POST 渲染，`state()` 调
    `capabilities()`，旧实现让 `MechaError` 穿过 ⇒ launcher 日志里刷出
    「处理请求时异常」，页面 500。硬校验应只留在 `prepare()`。
    """
    adapter = CodexHostAdapter()
    with tempfile.TemporaryDirectory(prefix="codex-caps-") as td:
        plan = make_host_plan(AIHostKind.CODEX, (_site(),), Path(td))
        try:
            caps = adapter.capabilities(plan)
            raised = ""
        except Exception as exc:                            # noqa: BLE001 - 就是要看它抛不抛
            caps, raised = {}, f"{type(exc).__name__}: {exc}"
        _check("聚合端点未就绪时 capabilities 不抛（页面不能 500）",
               not raised, raised)
        _check("未就绪时 aggregate_url 如实为空，不编造地址",
               caps.get("aggregate_url") == "", str(caps.get("aggregate_url")))
        try:
            adapter.prepare(plan)
            refused = False
        except Exception:                                   # noqa: BLE001
            refused = True
        _check("但真正写配置时仍然响亮拒绝（硬校验不后退）", refused)


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


def check_transport_inheritance() -> None:
    """用户的"传输层"设置必须结婚隔离 home —— 否则"重连 5/5"会回来。

    背景（实测）：codex 默认先试 WSS，失败 5 次才回退 HTTPS，界面上就是
    `Reconnecting 1/5 … 5/5`。用户在自己的 `~/.codex/config.toml` 里用
    `model_provider = "openai_http"` + `supports_websockets = false` 关掉了它，
    但隔离 home 的 config 是我们生成的 ⇒ 必须**继承**这段。
    """
    with tempfile.TemporaryDirectory(prefix="codex-transport-") as td:
        root = Path(td)
        user = root / "user-config.toml"
        user.write_text(
            """model_provider = "openai_http"
model = "gpt-6-astra"

[model_providers.openai_http]
name = "OpenAI HTTP"
wire_api = "responses"
supports_websockets = false
notify = ["C:/some/gui.exe", "turn-ended"]

[desktop]
followUpQueueMode = "steer"
""",
            encoding="utf-8")
        paths, _ = write_preparation(root / "run", aggregate_url="http://127.0.0.1:3089/mcp",
                                     user_config=user)
        text = paths.config.read_text(encoding="utf-8")
        _check("继承传输层：model_provider + supports_websockets=false 都在",
               'model_provider = "openai_http"' in text
               and "supports_websockets = false" in text, text[:200])
        _check("顶层键排在第一个表头之前（TOML 硬要求）",
               text.index("model_provider") < text.index("[mcp_servers.mecha]"))
        _check("`notify` 这种非传输设置**不**带进隔离环境", "notify" not in text)
        _check("用户别的表（如 [desktop]）也不带", "[desktop]" not in text)

        plain = root / "plain.toml"
        plain.write_text("""model = "gpt-6-astra"
""", encoding="utf-8")
        paths2, _ = write_preparation(root / "run2", aggregate_url="http://127.0.0.1:3089/mcp",
                                      user_config=plain)
        text2 = paths2.config.read_text(encoding="utf-8")
        _check("用户没设 model_provider ⇒ 不注入，回到默认行为",
               "model_providers" not in text2 and "model_provider" not in text2)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("宿主适配层判据：")
    check_adapter_selection()
    check_startup_plan()
    check_codex_config()
    check_codex_prepare()
    check_codex_capabilities_before_endpoint()
    check_hooks_and_compat()
    check_transport_inheritance()
    if FAILS:
        print(f"host_adapter_selfcheck: FAIL（{len(FAILS)} 条）")
        return 1
    print("host_adapter_selfcheck: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
