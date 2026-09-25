"""MCP 投影 Provider：工具面 → MCP 服务端的宿主传输（extra；核心不 import 本模块）。

``ToolHost`` 契约把工具的**定义**（``tools.py``）与**传输**分开，并声明 dsh/MCP 是
它的另一个 Provider——本模块就是那个一直缺席的远程传输：把 ``ToolRegistry.schemas()``
投影成 mcp SDK 的 ``MCPServer``，把调用收回来经 ``ToolHost.call`` 落到工具本体。

## 三条不变式

1. **工具唯一来源 = ``ToolRegistry.schemas()``**（白名单只出 ``name`` / ``description``
   / ``parameters``）。不新增第二张工具表：``_register_tool`` 从 ``registry.get(name)``
   取回本体，取不到即 fail loud。
2. **调用唯一路径 = ``ToolHost.call(name, args)``**。不直接 ``registry.execute``——
   远端边界那一层的失败归一化（``toolhost_error`` / ``bad_host_result``）必须留在
   路径上。默认 delegate 是 ``LocalToolHost``。
3. **必填项从权威声明派生，不从包装函数签名推**。写工具的 ``execute`` 常被
   ``**kwargs`` 包装，从签名推必填会得到**空集** ⇒ 必填在模型面静默消失（实测
   过的缺陷类）。本模块的优先级是：宿主注入的 ``required_source`` → ``execute``
   闭包签名（未被包装的只读/调度面工具）。命中注入来源时，若闭包签名**确实**暴露了
   命名必填项，两侧必须逐项相等，否则 ``tool_declaration_drift``。

## 为什么必须覆写 SDK 注册项（唯一的私有面访问）

``server.add_tool`` 由函数签名派生 pydantic 入参模型，再由它派生 ``inputSchema``：
类型对，但**声明里的 ``description`` 与真实默认值全丢**，每个属性被加上 ``title``，
可选项变成 ``default: null``——而声明是"模型唯一的用法来源"。所以注册后要把声明
合成的 schema **写回**注册项。该私有面访问**只允许出现在
``_override_declared_schema`` 一个函数里**（SDK 升级只改那一处），取不到注册项时抛
``mcp_sdk_shape_changed`` 并指路。

## 无 ``type`` 声明 ⇒ 注解 ``Any``（**规范，不是可选**）

声明里没写 ``type`` 的参数（语义常常是"任意 JSON 形态"）**不得**被当成 ``string``：
那会让只传数字/数组/对象调用者被 pydantic 拒收。缺 ``type`` 与无法识别的 ``type``
一律注解为 ``Any``（不限制），只对**显式** ``type`` 才做类型注解。

## 不声明 structured output（假契约，不是"顺手补全"）

``output_schema`` **必须**保持不声明。理由不是"dsh 会因此判 invalid"（那条**未经
验证**），而是它是一份**假契约**：SDK 会从 handler 的**合成签名返回注解**派生一个
output schema——声明"这个工具返回 ``{result: string}``"，而工具**实际返回各自领域
回执的 JSON**。发给客户端的是错的契约，且它由**实现痕迹**（合成签名长什么样）派生，
不是任何人的声明。这与"缺 ``type`` 被当成 ``string``"是**同一类**缺陷：实现细节
泄漏成 wire 契约。故合成签名的返回注解**留空**（派生结果为空），并由判据断言
**协议层 ``list_tools()`` 的 wire dump 里没有 ``outputSchema``**——防 SDK 漂移靠
判据，不靠再加一条冗余代码路径。

## Known Limitations and Deferred Work

- 工具是**同步**的：长任务会撞 dsh 侧 ``toolCallTimeoutMs``。本模块不做超时兜底
  （不发明第二套调度）——长任务走 job 类工具（宿主自己的形状）。
- **不做权限判断**：写权归 ``Gate``，而**框架不做可见性分级**（原 ``visibility.py``
  于 2026-09-25 收口轮删除——零消费者）；本层不因"是 AI 调的"
  放宽任何闸门。``CURRENT_CALL_ID`` 只是**归因**。
- 不提供监控面（那是 ``cockpit`` 的事），也不做端口编排（那是宿主启动器的事）。
- **不校验服务名**（dsh 侧要求 ``[A-Za-z0-9_-]{1,32}`` 且跨实例唯一）：那是 dsh 约束，
  本 extra 不 import dsh；宿主自己守（写进"怎么接"的文档，不在这里猜）。
- **不提供"declare 即注册"的 ``ToolHost`` Provider**：曾有一个 ``McpToolHost``（把
  ``declare`` 的声明逐条注册进服务、``call`` 委托 ``LocalToolHost``），但它今天
  **没有外部消费者**（只有判据在用）⇒ 按"每条新增必须有具名消费者"删掉，登记在
  ADR 的收口轮。**升格触发**：出现"Python 侧要经 MCP 调用远程 mecha 工具"的真实
  消费者时再加（那时 ``ToolHost`` 的远程 Provider 才有意义）。
- ⛔ **不得假设 ``MCPServer.call_tool()`` 可在进程内直调**。根因（mcp 2.2 实测）：
  进程内 ``call_tool`` 造的 ``Context`` **没有 ``request_context``**；读它会抛
  ``ValueError("Context is not available outside of a request")``，而
  ``Context.request_id`` 正是读它 ⇒ handler 用 ``getattr(ctx, "request_id", None)``
  取 call_id 时，**``getattr`` 只吞 ``AttributeError``，``ValueError`` 会直接穿出**，
  被 SDK 包成 ``UnexpectedToolError``。
  ⇒ **消费者必须走真实传输链路**（HTTP / streamable-http）；判据 / 测试不得依赖进程内
  直调（本模块 handler 取 call_id 仍保留那个容错写法——它在真链路上是对的）。已裁定
  **不为此加分支持、不加判据**：今天零消费者，而且"成功但 ``call_id`` 为空"的无归因写
  比响亮失败更坏。要测 handler 就按 ``phase12`` 的做法从注册表取 handler 本体直接调。
- ``_override_declared_schema`` 依赖 mcp 2.2 的 ``MCPServer._tool_manager`` 与
  ``Tool.parameters``；SDK 升级破坏时只改那一个函数，不改调用方。
- 本模块顶层 import ``mcp`` ⇒ ``import mecha.providers.mcp`` 需要装 extra
  （``pip install mecha[mcp]``）；``import mecha`` 与核心模块**不**碰它。
"""

from __future__ import annotations

import inspect
import json
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult, TextContent

from ..errors import MechaError, ToolFailure
from ..portfile import clear_port_file, write_port_file
from ..toolhost import LocalToolHost, ToolHost
from ..tools import CURRENT_CALL_ID, ToolDefinition, ToolRegistry

__all__ = [
    "DEFAULT_MAX_INSTRUCTION_BYTES",
    "MCP_PATH",
    "McpEndpoint",
    "RequiredSource",
    "build_mcp_server",
    "check_instructions",
    "resolve_required",
    "signature_required",
    "synthesize_signature",
    "tool_input_schema",
]

#: streamable-http 的挂载路径（dsh 内置客户端连 ``http://host:port/mcp``）。
MCP_PATH = "/mcp"

#: 服务说明的 UTF-8 字节上限（dsh 侧 ``maxInstructionBytes`` 默认值）。
#: 超出会让 dsh **连接失败**——所以要在构造期就拦，而不是等连不上。
DEFAULT_MAX_INSTRUCTION_BYTES = 32768

#: JSON Schema ``type`` → Python 注解。**只用于显式 ``type``**；缺失/未知一律
#: ``Any``（见模块 docstring 的规范条）。
_PY_TYPES: dict[str, type] = {
    "string": str, "integer": int, "number": float,
    "boolean": bool, "object": dict, "array": list,
}


def _is_name_sequence(value: object) -> bool:
    """是不是"键名序列"（非空、非字符串、非 bytes 的序列）。"""
    return (isinstance(value, Sequence) and not isinstance(value, (str, bytes))
            and bool(value))


def _declared_parameters(schema: Mapping[str, object]) -> dict:
    """``schemas()`` 项里的参数声明映射（缺省空映射，不猜）。"""
    parameters = schema.get("parameters")
    return dict(parameters) if isinstance(parameters, Mapping) else {}


def _py_annotation(declaration: object) -> object:
    """参数声明的 Python 注解。**缺 ``type`` ⇒ ``Any``**（不得默认成 ``str``）。"""
    if not isinstance(declaration, Mapping):
        return Any
    json_type = declaration.get("type")
    if json_type is None or isinstance(json_type, (list, tuple, dict)):
        return Any                      # 未声明 / 联合类型：不限制（保守放行）
    return _PY_TYPES.get(str(json_type), Any)


def _optional(annotation: object) -> object:
    """可选项注解：``Any`` 保持 ``Any``（它本就接受 ``null``），其余并上 ``None``。"""
    return Any if annotation is Any else annotation | None  # type: ignore[operator]


def _signature_required(execute: object) -> list[str]:
    """闭包签名里的必填项（无默认值 = 必填）。

    只认 ``POSITIONAL_OR_KEYWORD`` / ``KEYWORD_ONLY``；``**kwargs`` 包装器在这里
    会得到**空集**——那正是"不能只靠签名"的原因，不是本函数的缺陷。
    """
    if execute is None:
        return []
    if not callable(execute):
        raise MechaError(
            f"工具本体不可调用（{type(execute).__name__}）",
            kind="bad_tool_definition",
            hint="ToolDefinition.execute 必须是可调用对象（define_tool 保证这一点）",
        )
    try:
        signature = inspect.signature(execute)
    except (TypeError, ValueError):     # pragma: no cover - 内建函数等
        return []
    return [p.name for p in signature.parameters.values()
            if p.default is inspect.Parameter.empty
            and p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                           inspect.Parameter.KEYWORD_ONLY)]


def signature_required(tool: ToolDefinition) -> list[str]:
    """出厂回退：从工具本体的闭包签名派生必填项。"""
    return _signature_required(tool.execute)


def _declaration_defaulted(parameters: Mapping[str, object]) -> set[str]:
    """``defaulted`` 的**唯一权威来源**：声明里 ``default`` 非 ``None`` 的参数名。

    三分规则（缺一即错）：

    - 声明有 ``default`` 且**非** ``None`` ⇒ 进集合（收到显式 ``null`` 时丢键）；
    - 声明有 ``default`` 且**为** ``None`` ⇒ **不**进集合（显式 ``null`` 原样传递
      ——那是"清空"语义的唯一表达方式）；
    - 声明**没有** ``default`` 键 ⇒ 视同真默认 ``None`` ⇒ 同样**不**进集合。

    ⛔ **刻意不读 ``tool_def.execute`` 的签名**：写工具体常被
    ``def wrapped(**kwargs)`` 包一层，**内层闭包的真默认被包装遮死** ⇒ 签名侧恒为
    空集。对"包装宿主"它必然漏，且漏得静默（显式 ``null`` 以 ``None`` 到达工具体；
    今天无害只因各工具体用真值判断，改成 ``is not None`` 就立刻错）。权威收回声明
    ——这与本仓既有教条一致（**声明 → 机械投影**，投影不靠实现细节反推）。
    """
    return {str(name) for name, decl in parameters.items()
            if isinstance(decl, Mapping) and decl.get("default") is not None}


@runtime_checkable
class RequiredSource(Protocol):
    """必填项的注入来源（宿主侧策略：如从命令声明面取）。

    ``required_for(name, tool)`` 返回 ``None`` = "我不知道，请回退闭包签名"；
    返回列表 = 权威答案（此时与闭包签名不一致即 ``tool_declaration_drift``）。
    """

    def required_for(self, name: str,
                     tool: ToolDefinition) -> list[str] | None: ...


def resolve_required(name: str, tool: ToolDefinition,
                     required_source: RequiredSource | None = None) -> list[str]:
    """按优先级求一条工具的必填项：注入来源 → 闭包签名。

    命中注入来源时做**漂移检查**：闭包签名若**确实**暴露了命名必填项（未被包装的
    工具），两侧必须逐项相等；签名推出空集是包装器下的预期状态，不比对（这是
    "必填不能只靠签名"与"签名可用时不许自相矛盾"两条的合取）。
    """
    from_source: list[str] | None = None
    if required_source is not None:
        raw = required_source.required_for(name, tool)
        if raw is not None:
            from_source = [str(k) for k in raw]
    if from_source is None:
        return signature_required(tool)
    by_signature = signature_required(tool)
    if by_signature and by_signature != from_source:
        raise MechaError(
            f"工具 {name!r} 的必填项两处声明不一致："
            f"签名 {by_signature} vs 注入来源 {from_source}",
            kind="tool_declaration_drift",
            hint="必填的权威来源是注入的 required_source；包装成 **kwargs 后签名"
                 "推出空集是预期的，但签名**非空**时两侧必须相等",
            suggest="改宿主侧的必填声明，或去掉包装让签名与声明一致",
        )
    return from_source


def tool_input_schema(name: str, schema: Mapping[str, object], *,
                      execute: object = None,
                      declared_required: list[str] | None = None
                      ) -> tuple[dict, set[str]]:
    """合成协议层 ``inputSchema`` 与 ``defaulted`` 集合。

    ``properties`` 是**声明逐字**（含 ``type`` / ``description`` / ``default``），
    键序即声明序；``required`` **恒出现**（空则为 ``[]``——JSON Schema 里与省略
    等价，但形状稳定、判据可逐字段断言）。

    ``defaulted`` **只读声明**（见 :func:`_declaration_defaulted` 的三分规则），
    **不读** ``execute`` 的签名：包装器会遮死内层真默认，签名侧恒为空集。

    必填项里任何一个不在 ``properties`` 里 ⇒ ``tool_declaration_drift``
    （不静默产出一个模型永远填不出来的字段）。
    """
    parameters = _declared_parameters(schema)
    if declared_required is not None:
        required = [str(k) for k in declared_required]
    else:
        required = _signature_required(execute)
    undeclared = [n for n in required if n not in parameters]
    if undeclared:
        raise MechaError(
            f"工具 {name!r} 的必填项 {undeclared} 不在参数声明里",
            kind="tool_declaration_drift",
            hint="required 里的名字必须都能在声明（properties）里找到",
            suggest="在工具的 parameters 声明里补上这些参数",
        )
    defaulted = _declaration_defaulted(parameters)
    properties = {key: (dict(decl) if isinstance(decl, Mapping)
                        else {"type": "string"})
                  for key, decl in parameters.items()}
    return {"type": "object", "properties": properties, "required": required}, defaulted


def synthesize_signature(schema: Mapping[str, object], required: list[str], *,
                         default_none_for_optional: bool = True,
                         annotate: bool = True) -> inspect.Signature:
    """给 handler 合成"真实命名参数"签名（MCP 从签名建 pydantic 入参模型）。

    ``**kwargs`` 会让 SDK 派生出一个名为 ``kwargs`` 的必填字符串字段（错），所以
    必须换成真实参数名。必填不给默认值 ⇒ pydantic 层同样强制；可选给
    ``default=None`` ⇒ 客户端送显式 ``null`` 也能过校验（再由 handler 的垫片丢弃）。

    ⚠ **返回注解刻意留空**（不是 ``str``）：SDK 会**从返回注解派生一个
    ``outputSchema``** 并放进 ``tools/list``——实测（mcp 2.2）``return_annotation=str``
    会产出 ``{"properties": {"result": …}, "required": ["result"], …}``，而声明
    structured output 会让 dsh 侧把回执契约改成要求 ``content + structuredContent``。
    留空 ⇒ 派生结果为空 ⇒ wire 上不出现 ``outputSchema``。这与
    ``_override_declared_schema`` 里的显式置空是同一条不变式的两道保险。

    ``annotate=False`` / ``default_none_for_optional=False`` 只用于复刻"另一家的
    现状"做对账，**不是**出厂默认。
    """
    parameters = _declared_parameters(schema)
    params: list[inspect.Parameter] = []
    for key, decl in parameters.items():
        annotation = _py_annotation(decl) if annotate else inspect.Parameter.empty
        if key in required:
            params.append(inspect.Parameter(
                key, inspect.Parameter.KEYWORD_ONLY, annotation=annotation))
        else:
            default = None if default_none_for_optional else inspect.Parameter.empty
            params.append(inspect.Parameter(
                key, inspect.Parameter.KEYWORD_ONLY,
                annotation=_optional(annotation) if annotate
                else inspect.Parameter.empty, default=default))
    return inspect.Signature(params, return_annotation=inspect.Signature.empty)


def check_instructions(text: str, *,
                       limit: int = DEFAULT_MAX_INSTRUCTION_BYTES) -> None:
    """服务说明的体积守卫：UTF-8 字节超限当场 raise（可教学，不静默）。

    超限的后果是 dsh **连接失败**——报错离病因很远，所以在构造期就拦。
    """
    if not isinstance(text, str):
        raise MechaError(
            f"服务说明必须是字符串，收到 {type(text).__name__}",
            kind="bad_instructions",
            hint="instructions 是模型可见的服务说明文本",
        )
    size = len(text.encode("utf-8"))
    if size > limit:
        raise MechaError(
            f"服务说明 {size} 字节超过上限 {limit} 字节（UTF-8）",
            kind="instructions_too_long",
            hint="超出会让宿主客户端拒绝连接（maxInstructionBytes 默认 32768）；"
                 "服务说明只写任务相关概念，篇幅按句数收敛",
            suggest="删掉实现词汇与流程复述，只留'先看什么、结果怎么引用'",
        )


def _override_declared_schema(server: MCPServer, name: str,
                              input_schema: dict) -> None:
    """把声明合成的 ``inputSchema`` 写回 SDK 注册项。

    ⭐ **本模块唯一的 SDK 私有面访问点**（SDK 升级只改这一个函数）。``list_tools``
    用注册项的 ``parameters`` 作为协议层 ``input_schema``，故覆写即生效；pydantic
    派生值会丢掉声明里的 ``description`` 与真实默认值，不能直接用。

    这里**只**覆写 ``parameters``：``output_schema`` 的"不声明"由合成签名的返回
    注解留空实现（见模块 docstring），wire 层由判据断言——同一条不变式不做两份
    实现（每一份新增都要有具名消费者；一条冗余置空今天没有）。
    """
    manager = getattr(server, "_tool_manager", None)
    tool = None if manager is None else manager.get_tool(name)
    if tool is None:
        raise MechaError(
            f"刚注册的工具 {name!r} 在 SDK 注册表里找不到",
            kind="mcp_sdk_shape_changed",
            hint="mcp SDK 的 MCPServer 内部结构变了（_tool_manager.get_tool）",
            suggest="只改本模块的 _override_declared_schema 这一处垫片，别改调用方",
        )
    tool.parameters = input_schema


def _error_result(error: Mapping[str, object]) -> CallToolResult:
    """领域失败的**唯一**返回形状：``is_error=True`` + 结构化 JSON 正文。

    实测（mcp 2.2）：``raise ToolError(json)`` 会给正文加上
    ``Error executing tool …`` 前缀（模型读到散文而非 ``kind``）；返回错误文本则
    ``is_error=False``，成为"成功文本里写着错误"的假成功。所以只能返回
    ``CallToolResult(is_error=True)``——它对 SDK 是逐字透传。
    """
    payload = {"ok": False, "error": dict(error)}
    return CallToolResult(
        content=[TextContent(
            type="text",
            text=json.dumps(payload, ensure_ascii=False, default=str))],
        is_error=True,
    )


def _make_handler(toolhost: ToolHost, tool_name: str,
                  defaulted: set[str]) -> Callable[..., object]:
    """一个工具的 handler：归一化入参 → 经 ToolHost 调用 → 结构化回执。

    ``ctx`` 由 SDK 按**注解**注入（``Context`` 必须模块级导入，否则
    ``get_type_hints`` 解析不到、call_id 恒为空），且不在合成签名里 ⇒ 不是模型
    可见参数。
    """
    def _handler(ctx: Context = None, **kwargs):
        # 垫片：可选参数的真实默认值非 None 时，丢弃宿主 pydantic 填进来的显式
        # null，让真默认生效（否则"说明书说默认 X，但客户端必须显式传"）。
        args = {k: v for k, v in kwargs.items()
                if not (v is None and k in defaulted)}
        # ``getattr(..., None)`` 是**刻意**的容错写法，别"修"：真链路（HTTP /
        # streamable-http）上 ``ctx.request_context`` 存在、``request_id`` 正常可得。
        # 而进程内直调时 ``Context.request_context`` 缺失会抛 ``ValueError``
        # （**不是** ``AttributeError``）⇒ ``getattr`` 的默认值吞不住，会穿出去被
        # SDK 包成 ``UnexpectedToolError``。那是"进程内直调不可用"的表现（见模块
        # Known Limitations），不是这里的缺陷。已裁定：**不为此加分支持、不加判据**
        # ——"成功但 ``call_id`` 为空"的无归因写比响亮失败更坏。
        request_id = None if ctx is None else getattr(ctx, "request_id", None)
        token = CURRENT_CALL_ID.set("" if request_id is None else str(request_id))
        try:
            result = toolhost.call(tool_name, args)
        finally:
            CURRENT_CALL_ID.reset(token)
        if not isinstance(result, Mapping):
            return _error_result(ToolFailure(
                f"工具宿主回执不是映射（{type(result).__name__}）",
                kind="bad_host_result",
                hint="宿主 call 必须返回与 ToolRegistry.execute 同构的回执",
            ).to_failure())
        if result.get("is_error"):
            failure = result.get("error")
            if not isinstance(failure, Mapping):
                failure = ToolFailure("工具失败", kind="unknown_failure").to_failure()
            return _error_result(failure)
        return json.dumps(result.get("value"), ensure_ascii=False, default=str)

    _handler.__name__ = tool_name
    return _handler


def _register_tool(server: MCPServer, registry: ToolRegistry,
                   schema: Mapping[str, object], *, toolhost: ToolHost,
                   required_source: RequiredSource | None = None) -> None:
    """把一条 ``schemas()`` 投影注册成 MCP 工具（三步：合成 → 注册 → 覆写）。"""
    name = str(schema["name"])
    tool = registry.get(name)
    if tool is None:
        raise MechaError(
            f"schemas() 声明了工具 {name!r}，注册表里却取不到本体",
            kind="tool_declaration_drift",
            hint="schemas() 与 get() 必须来自同一张注册表（不允许第二张工具表）",
        )
    required = resolve_required(name, tool, required_source)
    input_schema, defaulted = tool_input_schema(
        name, schema, execute=tool.execute, declared_required=required)
    handler = _make_handler(toolhost, name, defaulted)
    handler.__signature__ = synthesize_signature(schema, required)  # type: ignore[attr-defined]
    server.add_tool(handler, name=name,
                    description=str(schema.get("description") or ""))
    _override_declared_schema(server, name, input_schema)


def _check_toolhost_reach(registry: ToolRegistry, toolhost: ToolHost) -> None:
    """便宜且确定的一档守卫：**工具列得出来、却调不动**。

    端点暴露的工具来自 ``registry.schemas()``，而每次调用都经 ``toolhost.call``。
    两者若不是同一张注册表，就会出现"``tools/list`` 看着正常、调用全报未知工具"
    ——EL 真踩过（2026-09-25）：把 ``assemble()`` 建出的**空** ``sw.tools``（经
    ``sw.toolhost``）传了进来，于是工具面列得出、调不动，且 journal 不落盘。

    ⛔ **只查"非空 vs 零"这一档**，不做集合严格相等：``toolhost`` 暴露的可能是
    暴露面的**子集**（例如只报它本地能处理的那部分、其余经远端转发），那是合法
    设计，集合相等会把合法设计判红。而"端点多于零、toolhost 恰好零"没有任何合法
    解释——它就是绑错了注册表。查这一档既便宜又确定。
    """
    exposed = registry.schemas()
    if not exposed:
        return
    try:
        reachable = toolhost.schemas()
    except Exception:            # noqa: BLE001
        # 取不到就"不知道"，**不**把宿主自己的异常转成这条守卫的结论（那是另一件事，
        # 会在真正的调用路径上响亮失败）。本守卫只判"零"这一档。
        return
    if reachable:
        return
    raise MechaError(
        f"工具列得出来、调不动：端点暴露 {len(exposed)} 个工具，"
        f"而 toolhost 暴露 0 个",
        kind="toolhost_registry_mismatch",
        hint="多半是 toolhost 绑的不是同一个注册表（EL 踩过：把 assemble() 建出的"
             "空 sw.tools 传了进来）——tools/list 看起来正常，调用会全部报未知工具",
        suggest="不传 toolhost，让框架缺省用 LocalToolHost(端点暴露的那个注册表)",
    )


def build_mcp_server(registry: ToolRegistry, *, server_name: str,
                     instructions: str, toolhost: ToolHost | None = None,
                     required_source: RequiredSource | None = None) -> MCPServer:
    """按当前工具面**纯构造** MCP 服务（不起线程、不碰端口；判据可直接调）。

    ``toolhost=None`` ⇒ 用出厂 ``LocalToolHost(registry)``（进程内直调）。
    服务名与说明由宿主给（框架不设领域默认值）；说明的体积在构造期就校验。

    构造期还做一次**绑表核对**（:func:`_check_toolhost_reach`）：显式给的
    ``toolhost`` 若一个工具都暴露不出来、而端点却有工具 ⇒ 当场响亮报错。这一档
    只在**服务起来之前**查，所以误配不会变成"跑起来但调不动"的静默故障。

    这里**不再**复校 ``registry.schemas()`` 的白名单形状：``schemas()`` 本身就是
    从 ``MODEL_VISIBLE_FIELDS`` 机械投影出来的唯一来源，再校验一遍是并行实现
    （且要跨模块 import 私有函数）。声明形状的守卫留在 ``ToolHost.declare`` 那一处。
    """
    check_instructions(instructions)
    if toolhost is not None:
        _check_toolhost_reach(registry, toolhost)
    server = MCPServer(name=server_name, instructions=instructions)
    host = toolhost if toolhost is not None else LocalToolHost(registry)
    for schema in registry.schemas():
        _register_tool(server, registry, schema, toolhost=host,
                       required_source=required_source)
    return server


class McpEndpoint:
    """在**权威进程后台线程**里跑 MCP 服务；起完把实际端口写进端口文件。

    用 ``streamable_http_app`` + ``uvicorn.Server``（不是 ``uvicorn.run`` /
    ``MCPServer.run``——后两者内部自己 ``asyncio.run``，拿不到句柄 ⇒ 无法进程内
    干净停止）。就绪判据是 ``uvicorn.Server.started``（不是"线程起来了"），
    然后**回读真实 bind 端口**——``port=0`` 时那是唯一可靠来源，且没有
    "先 bind 再 close 再复用"那种预选端口的 TOCTOU 窗口。
    """

    def __init__(self, registry: ToolRegistry, *, server_name: str,
                 instructions: str, toolhost: ToolHost | None = None,
                 host: str = "127.0.0.1", port: int = 0,
                 port_file: str | Path | None = None,
                 required_source: RequiredSource | None = None,
                 log: Callable[[str], None] | None = None) -> None:
        check_instructions(instructions)
        self._registry = registry
        self._server_name = server_name
        self._instructions = instructions
        self._toolhost = toolhost
        self._required_source = required_source
        self._host = str(host)
        self._want_port = int(port)
        self._port_file = Path(port_file) if port_file is not None else None
        self._log = log or (lambda _m: None)
        self._server: MCPServer | None = None
        self._uvicorn = None
        self._thread: threading.Thread | None = None
        self._port: int | None = None

    # ---- 事实面 ----

    @property
    def port(self) -> int | None:
        return self._port

    @property
    def url(self) -> str:
        """``""`` 或 ``http://host:port/mcp``。"""
        return "" if self._port is None else "http://%s:%d%s" % (
            self._host, self._port, MCP_PATH)

    @property
    def server(self) -> MCPServer | None:
        """已构造的 MCP 服务（判据/调试用；**不是**第二条调用通道）。"""
        return self._server

    # ---- 构造与生命周期 ----

    def build_server(self) -> MCPServer:
        """纯构造（幂等）：不起线程、不碰端口、不写发现文件。"""
        if self._server is None:
            self._server = build_mcp_server(
                self._registry, server_name=self._server_name,
                instructions=self._instructions, toolhost=self._toolhost,
                required_source=self._required_source)
        return self._server

    def start(self, timeout: float = 30.0) -> int:
        """起服务并返回**实际**端口；失败抛错（不静默留一个连不上的端点）。

        顺序：清陈旧端口文件 → 构造服务 → 起线程 → 等 ``started`` → 回读端口 →
        写端口文件。
        """
        if self._thread is not None:
            raise MechaError(
                "MCP 服务已启动",
                kind="mcp_already_started",
                hint="一个 McpEndpoint 只 start 一次；要重起先 stop()",
            )
        import uvicorn                                  # 惰性：纯构造不需要 ASGI 栈

        clear_port_file(self._port_file)
        server = self.build_server()
        app = server.streamable_http_app(streamable_http_path=MCP_PATH,
                                        host=self._host)
        config = uvicorn.Config(app, host=self._host, port=self._want_port,
                                log_level="warning")
        self._uvicorn = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._uvicorn.run, daemon=True,
                                        name="mecha-mcp")
        self._thread.start()
        self._port = self._await_ready(timeout)
        write_port_file(self._port_file, self._port)
        self._log("[mecha-mcp] %s（工具面 %s）"
                  % (self.url, " ".join(s["name"] for s in self._registry.schemas())))
        return self._port

    def _await_ready(self, timeout: float) -> int:
        """等 uvicorn 真正 listen（不是等线程起来）；失败当场报清楚。"""
        deadline = time.time() + max(1.0, float(timeout))
        while time.time() < deadline:
            if getattr(self._uvicorn, "started", False):
                return self._bound_port()
            if self._thread is not None and not self._thread.is_alive():
                raise MechaError(
                    f"MCP 服务线程退出，未能 listen {self._host}:{self._want_port}",
                    kind="mcp_start_failed",
                    hint="端口被占 / 依赖缺失 / ASGI 栈启动即失败；栈见上一条日志",
                    suggest="换端口（port=0 让 OS 分配）后重试",
                )
            time.sleep(0.05)
        raise MechaError(
            f"等 MCP 服务 listen 超时（{timeout}s）",
            kind="mcp_start_timeout",
            hint="uvicorn 未在限期内 started=True",
        )

    def _bound_port(self) -> int:
        """回读真正 bind 到的端口（``port=0`` 时这是唯一可靠来源）。"""
        for srv in getattr(self._uvicorn, "servers", None) or []:
            for sock in getattr(srv, "sockets", None) or []:
                try:
                    return int(sock.getsockname()[1])
                except OSError:                          # pragma: no cover - 平台差异
                    continue
        return self._want_port

    def stop(self, timeout: float = 10.0) -> None:
        """干净退出（幂等）：置 ``should_exit`` → join → 按内容清端口文件。"""
        if self._uvicorn is not None:
            self._uvicorn.should_exit = True
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(max(0.1, float(timeout)))
        clear_port_file(self._port_file, self._port)
        self._port = None

    def __enter__(self) -> "McpEndpoint":
        self.start()
        return self

    def __exit__(self, *exc) -> bool:
        self.stop()
        return False
