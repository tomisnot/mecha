"""Phase 12 判据：MCP 投影 Provider（L6 装配级 + L7 对偶）。

钉住 MCP 工具投影的冻结契约，七组：

  A 必填派生：注入来源优先 / 与签名漂移即红 / 无来源时回退签名 / 必填必须可填
  B ⭐ **无 ``type`` 声明 ⇒ 注解 ``Any``**（致死性）：声明没写 ``type`` 的参数，
    数字 / 数组 / 对象 / 字符串**都必须被接受**；对偶——显式 ``type: "string"``
    的参数**必须**拒收对象（证明这条断言不是哑守卫）
  C 协议层 schema：``properties`` 逐字、无 pydantic ``title``、``required`` 恒出现、
    真实默认值、描述非空、**wire 上没有 ``outputSchema``**（唯一的防漂移断言）
  D 服务说明体积：合规放行 / 超限当场 raise（含多字节字符按 UTF-8 字节算）
  E 调用唯一路径：handler 必须经 ``ToolHost.call``（spy 证明），**不**直调
    ``ToolRegistry.execute``
  F 失败归一化：``is_error=True`` + 纯 JSON 正文（无 ``Error executing tool`` 前缀）；
    成功回执是 JSON 字符串
  G 端点生命周期：``build_server`` 纯构造 / ``port=0`` 回读真实端口 / ``/mcp`` 路径 /
    端口文件两条清理语义 / ``stop`` 幂等 / 重复 ``start`` fail loud

用法：python checks/phase12_mcp_projection_selfcheck.py
退出码：0 全绿；1 有失败（**含缺 mcp extra**——缺真依赖即红，不假绿）。

## Known Limitations and Deferred Work

- **需要 mcp extra**（``pip install mecha[mcp]``）：本模块顶层 import ``mcp``，
  故缺依赖时本判据**报红**而不是跳过——与 phase5 缺 EL 即红同一纪律（缺真依赖
  假绿比红更危险）。核心模块与 ``import mecha`` 不需要它。
- 不跑真 dsh 客户端：``mcp__<server>__<tool>`` 的公开名映射、``serverName`` 约束、
  ``maxInstructionBytes`` 的真实拒绝行为都属宿主侧，活体验收（真 dsh mount）覆盖。
- 不测真 HTTP 往返（streamable-http 握手）：那需要异步客户端且属 SDK 自身契约；
  本判据测"服务能 listen 且端口可发现"，协议往返归宿主判据。
- 判据里读了两处**模块私有面**：``_make_handler`` / ``_override_declared_schema``
  的产物（经 SDK 注册表取 handler）。这是**刻意**的——要证明"调用真的经 ToolHost"
  与"正文真的逐字透传"，必须拿到 handler 本体；SDK 内部形状变化时本判据会以
  明确消息报红，而不是静默跳过。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:                                                     # 缺 extra 即红（不假绿）
    from mcp.server.mcpserver.utilities.func_metadata import func_metadata
    from pydantic import ValidationError
except ImportError as _exc:                              # pragma: no cover
    print(f"  ✗ 缺 mcp extra（{_exc}）：pip install mecha[mcp] 后重跑")
    print("phase12_mcp_projection_selfcheck: FAIL（缺真依赖，不假绿）")
    raise SystemExit(1)

from mecha.errors import MechaError  # noqa: E402
from mecha.portfile import read_port  # noqa: E402
from mecha.providers.mcp import (  # noqa: E402
    DEFAULT_MAX_INSTRUCTION_BYTES, MCP_PATH, McpEndpoint,
    build_mcp_server, check_instructions, resolve_required, signature_required,
    synthesize_signature, tool_input_schema,
)
from mecha.providers.mcp import _make_handler  # noqa: E402  (判据刻意读私有面，见 Limitations)
from mecha.tools import ToolRegistry, define_tool  # noqa: E402

INSTRUCTIONS = "先看有哪些动作，再按名字调用；结果用编号引用，大对象不内联返回。"


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _fail(out: list[str], msg: str) -> None:
    print(f"  ✗ {msg}")
    out.append(msg)


def _raises(fn, kind: str) -> bool:
    try:
        fn()
    except MechaError as e:
        return e.kind == kind
    return False


def _tool(name, parameters, execute, *, description="做一个中性的检查用动作"):
    return define_tool(name=name, description=description, parameters=parameters,
                       output_schema={"type": "object", "required": ["ok"]},
                       execute=execute)


def _registry(*tools) -> ToolRegistry:
    reg = ToolRegistry()
    for tool in tools:
        reg.register(tool)
    return reg


class _SpyHost:
    """只记录调用的 ToolHost：证明 handler 走的是传输 seam 而不是直调注册表。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call(self, name, args=None):
        self.calls.append((name, dict(args or {})))
        return {"is_error": False, "value": {"echo": dict(args or {})}, "content": []}

    def declare(self, schemas) -> None:
        pass

    def schemas(self) -> list:
        return []

    def teardown(self) -> None:
        pass


def _handler_of(server, name):
    """从 SDK 注册表取回已注册的 handler 本体（判据刻意读私有面，见 Limitations）。"""
    manager = getattr(server, "_tool_manager", None)
    tool = None if manager is None else manager.get_tool(name)
    if tool is None:
        raise MechaError(f"SDK 注册表里取不到 {name!r}", kind="mcp_sdk_shape_changed",
                         hint="判据依赖 ToolManager.get_tool；SDK 形状变了")
    return tool.fn


def _listed_tools(server) -> list:
    """``list_tools()``（同步或协程都接住），返回 SDK 的 Tool 对象列表。"""
    result = server.list_tools()
    if inspect.isawaitable(result):
        result = asyncio.run(result)
    return list(result)


# --------------------------------------------------------------------------
# A 必填派生
# --------------------------------------------------------------------------

def check_required(out: list[str]) -> None:
    def _wrapped(**kwargs):
        return {"ok": True}

    wrapped = _tool("do_thing", {"alpha": {"type": "string"}}, _wrapped)
    if signature_required(wrapped) == []:
        _ok("对偶（不许误报）：**kwargs 包装器的签名推出空集——这正是"
            "'必填不能只靠签名'的事实依据")
    else:
        _fail(out, f"包装器签名推出非空集：{signature_required(wrapped)}")

    class _Source:
        def required_for(self, name, tool):
            return ["alpha"] if name == "do_thing" else None

    if resolve_required("do_thing", wrapped, _Source()) == ["alpha"]:
        _ok("注入来源优先：包装器（签名空集）下必填来自 required_source")
    else:
        _fail(out, "注入来源未被采用（必填会静默消失）")

    if resolve_required("do_thing", wrapped, None) == []:
        _ok("无注入来源时回退闭包签名（只读/调度面工具的路径）")
    else:
        _fail(out, "回退签名失败")

    named = _tool("read_widget", {"alpha": {"type": "string"}}, lambda alpha: {"ok": True})

    class _WrongSource:
        def required_for(self, name, tool):
            return ["beta"]

    if _raises(lambda: resolve_required("read_widget", named, _WrongSource()),
               "tool_declaration_drift"):
        _ok("⭐ 签名非空且与注入来源不一致 ⇒ tool_declaration_drift（fail loud）")
    else:
        _fail(out, "两处必填不一致未被拒（会静默按一处走）")

    if resolve_required("read_widget", named, None) == ["alpha"]:
        _ok("对偶（不许误报）：未被包装的工具按签名派生正确")
    else:
        _fail(out, f"命名参数工具签名派生错：{resolve_required('read_widget', named, None)}")

    schema = {"name": "do_thing", "description": "d", "parameters": {"alpha": {}}}
    if _raises(lambda: tool_input_schema("do_thing", schema,
                                        declared_required=["ghost"]),
               "tool_declaration_drift"):
        _ok("必填项不在 properties 里 ⇒ tool_declaration_drift（不许产出填不出来的字段）")
    else:
        _fail(out, "不可填的必填项未被拒")


# --------------------------------------------------------------------------
# B 无 type 声明 ⇒ Any（致死性 + 对偶）
# --------------------------------------------------------------------------

def check_any_annotation(out: list[str]) -> None:
    untyped = {"name": "set_widget", "description": "d",
               "parameters": {"value": {"description": "任意形态"}}}
    sig = synthesize_signature(untyped, ["value"])
    if sig.parameters["value"].annotation is Any:
        _ok("⭐ 无 type 声明 ⇒ 注解 Any（**不得**默认成 str）")
    else:
        _fail(out, f"无 type 声明被注解成 {sig.parameters['value'].annotation!r}"
                   f"（会拒收一切非字符串值）")

    spy = _SpyHost()
    handler = _make_handler(spy, "set_widget", set())
    handler.__signature__ = sig
    model = func_metadata(handler).arg_model
    accepted = []
    for payload in (3, 2.5, [1, 2], {"a": 1}, "s", True):
        try:
            model.model_validate({"value": payload})
            accepted.append(payload)
        except ValidationError:
            pass
    if len(accepted) == 6:
        _ok("⭐ 致死性：数字 / 浮点 / 数组 / 对象 / 字符串 / 布尔**全部被接受**")
    else:
        _fail(out, f"无 type 的参数拒收了合法值（只接受 {accepted}）——"
                   f"日常的'传数字'会直接坏")

    typed = {"name": "set_widget", "description": "d",
             "parameters": {"value": {"type": "string"}}}
    sig2 = synthesize_signature(typed, ["value"])
    if sig2.parameters["value"].annotation is str:
        _ok("对偶：显式 type: string ⇒ 注解 str")
    else:
        _fail(out, f"显式 string 未注解成 str：{sig2.parameters['value'].annotation!r}")
    handler2 = _make_handler(spy, "set_widget", set())
    handler2.__signature__ = sig2
    model2 = func_metadata(handler2).arg_model
    rejected = False
    try:
        model2.model_validate({"value": {"a": 1}})
    except ValidationError:
        rejected = True
    if rejected:
        _ok("对偶（不许误报）：显式 string 的参数**确实**拒收对象——守卫不哑")
    else:
        _fail(out, "显式 string 竟接受对象：上一条断言是哑的")

    unknown = {"name": "set_widget", "description": "d",
               "parameters": {"value": {"type": "quantum"}}}
    if synthesize_signature(unknown, ["value"]).parameters["value"].annotation is Any:
        _ok("无法识别的 type ⇒ Any（保守放行，不误拒）")
    else:
        _fail(out, "未知 type 未回退 Any")


# --------------------------------------------------------------------------
# C 协议层 schema
# --------------------------------------------------------------------------

def check_wire_schema(out: list[str]) -> None:
    def _echo(value, mode="fast"):
        return {"ok": True, "value": value, "mode": mode}

    reg = _registry(_tool("do_echo", {"value": {"description": "任意形态"},
                                      "mode": {"type": "string", "default": "fast",
                                               "description": "模式"}}, _echo))
    server = build_mcp_server(reg, server_name="checkbox",
                              instructions=INSTRUCTIONS)
    tools = _listed_tools(server)
    if [t.name for t in tools] == ["do_echo"]:
        _ok("工具枚举来自 schemas()（名字逐字，不手抄）")
    else:
        _fail(out, f"工具枚举错：{[t.name for t in tools]}")

    tool = tools[0]
    schema = tool.input_schema
    if schema.get("type") == "object":
        _ok("inputSchema.type == object")
    else:
        _fail(out, f"inputSchema 形状错：{schema}")
    if list(schema.get("properties", {})) == ["value", "mode"]:
        _ok("properties 键序 == 声明序（顺序是契约）")
    else:
        _fail(out, f"properties 键序错：{list(schema.get('properties', {}))}")
    if "value" in schema["properties"] and "type" not in schema["properties"]["value"]:
        _ok("无 type 的声明在 wire 上**逐字**保留（不替宿主补 type）")
    else:
        _fail(out, f"声明未逐字透出：{schema['properties'].get('value')}")
    if schema["properties"]["mode"].get("default") == "fast":
        _ok("可选参数带**声明里的真实默认值**（不是 null）")
    else:
        _fail(out, f"默认值丢失：{schema['properties']['mode']}")
    if "title" not in json.dumps(schema):
        _ok("无 pydantic 派生的 title（覆写垫片生效）")
    else:
        _fail(out, "schema 里出现 title：声明覆写没生效")
    if schema.get("required") == ["value"]:
        _ok("required 从权威声明派生（['value']）")
    else:
        _fail(out, f"required 错：{schema.get('required')}")
    if tool.description:
        _ok("description 非空（模型唯一的用法来源）")
    else:
        _fail(out, "description 为空")
    wired = tool.model_dump(by_alias=True, exclude_none=True)
    if "outputSchema" not in wired:
        _ok("⭐ 不声明 structured output：**wire 上没有 outputSchema**。SDK 会从合成"
            "签名的返回注解派生一个假契约（声称工具返回 {result: string}），返回注解"
            "留空即派生为空；**本条断言是唯一的防漂移守卫**（不再加冗余代码路径）")
    else:
        _fail(out, "wire 上出现由实现痕迹派生的**假契约** outputSchema："
                   f"{json.dumps(wired.get('outputSchema'))[:120]}")

    empty = build_mcp_server(_registry(_tool("do_nothing", {}, lambda: {"ok": True})),
                             server_name="checkbox", instructions=INSTRUCTIONS)
    if _listed_tools(empty)[0].input_schema.get("required") == []:
        _ok("对偶：无必填时 required 恒出现且为 []（形状稳定、可逐字段断言）")
    else:
        _fail(out, f"空 required 未恒出现：{_listed_tools(empty)[0].input_schema}")


# --------------------------------------------------------------------------
# D 服务说明体积
# --------------------------------------------------------------------------

def check_instruction_limit(out: list[str]) -> None:
    check_instructions(INSTRUCTIONS)
    _ok("合规服务说明放行")
    long_ascii = "a" * (DEFAULT_MAX_INSTRUCTION_BYTES + 1)
    if _raises(lambda: check_instructions(long_ascii), "instructions_too_long"):
        _ok("超限（ASCII）当场 raise instructions_too_long（不等宿主连不上）")
    else:
        _fail(out, "超限说明未被拒")
    multibyte = "机" * (DEFAULT_MAX_INSTRUCTION_BYTES // 3 + 1)
    if _raises(lambda: check_instructions(multibyte), "instructions_too_long"):
        _ok("按 **UTF-8 字节**算（多字节字符不被当 1 字节）")
    else:
        _fail(out, "多字节说明未按字节数拦截")
    if not _raises(lambda: check_instructions("机" * 10), "instructions_too_long"):
        _ok("对偶（不许误报）：短多字节说明放行")
    else:
        _fail(out, "短说明被误拒")


# --------------------------------------------------------------------------
# E 调用唯一路径 / F 失败归一化
# --------------------------------------------------------------------------

def check_call_path_and_failure(out: list[str]) -> None:
    def _explode(**kwargs):
        raise AssertionError("注册表 execute 不该被 MCP 层直接调用")

    spy = _SpyHost()
    reg = _registry(_tool("do_thing", {"alpha": {"type": "string"}}, _explode))
    server = build_mcp_server(reg, server_name="checkbox",
                              instructions=INSTRUCTIONS, toolhost=spy)
    handler = _handler_of(server, "do_thing")
    result = handler(ctx=None, alpha="x")
    if spy.calls == [("do_thing", {"alpha": "x"})]:
        _ok("⭐ handler 经 ToolHost.call 调用（spy 收到；注册表 execute 未被调用）")
    else:
        _fail(out, f"调用未走传输 seam：{spy.calls}")
    if json.loads(result) == {"echo": {"alpha": "x"}}:
        _ok("成功回执 = 值的 JSON 字符串（单个 text block 的内容）")
    else:
        _fail(out, f"成功回执形状错：{result!r}")

    def _reject(**_kwargs):
        raise MechaError("写权处于 LOCKED", kind="authority_locked",
                         hint="先由人类侧切到目标模式")

    reg2 = _registry(_tool("do_thing", {}, _reject))
    server2 = build_mcp_server(reg2, server_name="checkbox",
                               instructions=INSTRUCTIONS)
    body = _handler_of(server2, "do_thing")(ctx=None)
    if getattr(body, "is_error", None) is True:
        _ok("失败回执 is_error=True（CallToolResult 逐字透传）")
    else:
        _fail(out, f"失败回执未标 is_error：{body!r}")
    text = body.content[0].text
    payload = json.loads(text)
    if (set(payload) == {"ok", "error"} and payload["ok"] is False
            and set(payload["error"]) == {"message", "info"}
            and payload["error"]["info"]["kind"] == "authority_locked"
            and "Error executing tool" not in text):
        _ok("失败正文是纯 JSON（无 'Error executing tool' 前缀），"
            "形状 {ok:false, error:{message, info}} 且 kind 原样透出")
    else:
        _fail(out, f"失败正文形状错：{text!r}")

    def _echo(value):
        return {"ok": True, "value": value}

    reg4 = _registry(_tool("do_thing", {"value": {"type": "string"}}, _echo))
    server4 = build_mcp_server(reg4, server_name="checkbox",
                               instructions=INSTRUCTIONS)
    body4 = _handler_of(server4, "do_thing")(ctx=None)
    if (getattr(body4, "is_error", None) is True
            and json.loads(body4.content[0].text)["error"]["info"]["kind"]
            == "missing_arguments"):
        _ok("对偶：工具层判定的缺必填同样是结构化失败（不是裸异常 / 不是 500）")
    else:
        _fail(out, f"缺必填未归一化成 missing_arguments：{body4!r}")

    def _inner(value, mode="inner-default", cleared=None, absent=None):
        return {"ok": True, "got": {"value": value, "mode": mode,
                                    "cleared": cleared, "absent": absent}}

    def _wrapper(**kwargs):
        return _inner(**kwargs)

    declared = {"value": {"type": "string"},
                "mode": {"type": "string", "default": "declared-default"},
                "cleared": {"type": "string", "default": None},
                "absent": {"type": "string"}}
    reg3 = _registry(_tool("do_thing", declared, _wrapper))
    server3 = build_mcp_server(reg3, server_name="checkbox",
                               instructions=INSTRUCTIONS)
    got = json.loads(_handler_of(server3, "do_thing")(
        ctx=None, value="x", mode=None, cleared=None, absent=None))
    want = {"value": "x", "mode": "inner-default", "cleared": None,
            "absent": None}
    if got.get("got") == want:
        _ok("⭐ 默认值垫片三分（**唯一权威 = 声明**）：声明 default 非 None 的参数"
            "收到显式 null ⇒ 丢键（内层真默认生效）；声明 default 为 None / 无 "
            "default 键 ⇒ null 原样保留")
    else:
        _fail(out, f"三分规则错：{got.get('got')!r}（期望 {want!r}）")
    if "mode" not in json.dumps(got):
        _fail(out, "哑断言：mode 根本没进回执，上面那条测不出东西")
    else:
        _ok("对偶（不许误报）：断言非哑——回执里确实带 mode 字段可供判定")


# --------------------------------------------------------------------------
# G 端点生命周期
# --------------------------------------------------------------------------

def check_endpoint(out: list[str]) -> None:
    reg = _registry(_tool("do_thing", {}, lambda: {"ok": True}))
    with tempfile.TemporaryDirectory(prefix="mcp-g-") as td:
        port_file = Path(td) / ".check-port"
        port_file.write_text("1", encoding="utf-8")      # 陈旧文件
        ep = McpEndpoint(reg, server_name="checkbox",
                         instructions=INSTRUCTIONS, port=0, port_file=port_file)
        built = ep.build_server()
        if ep.server is built and ep.port is None and ep.url == "":
            _ok("build_server() 纯构造（幂等、不起线程、不碰端口；url 未启动即空串）")
        else:
            _fail(out, "build_server 不是纯构造")
        port = ep.start()
        try:
            if port > 0 and read_port(port_file) == port and ep.url.endswith(MCP_PATH):
                _ok("start 清陈旧文件后回读**真实**端口并写发现文件；url 以 /mcp 结尾")
            else:
                _fail(out, f"start 端口/发现文件错：{port} {read_port(port_file)} {ep.url}")
            if _raises(lambda: ep.start(), "mcp_already_started"):
                _ok("重复 start fail loud（不静默起第二个服务）")
            else:
                _fail(out, "重复 start 未被拒")
        finally:
            ep.stop()
        ep.stop()                                        # 幂等
        if ep.port is None and read_port(port_file) is None:
            _ok("stop 幂等 + 按内容清发现文件 + port 复位")
        else:
            _fail(out, f"stop 收尾错：{ep.port} {read_port(port_file)}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out: list[str] = []
    print("Phase 12 判据（MCP 投影 Provider）：")
    check_required(out)
    check_any_annotation(out)
    check_wire_schema(out)
    check_instruction_limit(out)
    check_call_path_and_failure(out)
    check_endpoint(out)
    if out:
        print(f"phase12_mcp_projection_selfcheck: FAIL（{len(out)} 条）")
        return 1
    print("phase12_mcp_projection_selfcheck: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
