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
  **没有外部消费者**（只有判据在用）⇒ 按"每条新增必须有消费"删掉，登记在
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
- ⭐ **工具表热同步包了 SDK 的两个公开类方法**（2026-10-10，用户裁决的方案 B）：
  ``declare_tools_list_changed`` 读一次 ``MCPServer._lowlevel_server`` 并给它的
  ``create_initialization_options`` 包一层（SDK 没给"声明能力"的构造参数）；
  ``install_connection_tracking`` 给 ``Connection.for_loop`` / ``.from_envelope``
  各包一层（SDK 没有"连接建立"的公开回调 ⇒ 这是拿活连接唯一不重写传输层的办法）。
  ⚠ **代价**：SDK 升级可能让包装失效（现象：AI 工具表又停在连接那一刻）。
  ⇒ 判据 ``checks/aggregate_tools_hot_sync_selfcheck.py`` 钉住"包装在位 + 真收到通知"，
  SDK 一改就红；替换点写在这两个函数头上（SDK 若开放官方回调就换掉）。
- 本模块顶层 import ``mcp`` ⇒ ``import mecha.providers.mcp`` 需要装 extra
  （``pip install mecha[mcp]``）；``import mecha`` 与核心模块**不**碰它。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from mcp.server.lowlevel.server import NotificationOptions
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
    实现（每一份新增都要有消费；一条冗余置空今天没有）。
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


def _brief(payload: object, *, limit: int = 200) -> str:
    """把回执**截断**成一行人话：原样透传顶层键（模型据此自我纠正），但不 dump 整包。

    ``limit`` 是对**模型可见面**的体积守卫：非标准信封可能带着大 blob，
    整包塞进错误消息会把上下文挤爆（而错误消息本身要能读）。
    """
    try:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:                       # noqa: BLE001 - 不可 JSON 化也要如实说
        text = repr(payload)
    return text if len(text) <= limit else text[:limit] + "…"


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
        if "value" not in result:
            # ⚠ **非标准信封 ⇒ 响亮失败，别默认吞**。旧行为**更坏**：既不是失败信封、
            # 又没有 `value` ⇒ 取 `result.get("value")` = `None` ⇒ 返回 `"null"` 且
            # `is_error` 缺省为假 ⇒ **模型看到「成功、内容是 null」**，而能力其实说了
            # `ok: false`；顶层 `status`/`date` 全被丢掉 ⇒ 模型**没法自我纠正**。
            # （L4：错误消息是给 AI 看的界面。）⇒ 这里把信封**截断透传**进消息。
            return _error_result(ToolFailure(
                f"工具回执不是标准信封（既没有 value，也不是 is_error+error）："
                f"{_brief(result)}",
                kind="bad_envelope",
                hint="宿主回执只有两种合法形状：成功 {value: …}，"
                     "失败 {is_error: true, error: {message, info}}；"
                     "看到 {ok: false, …} 这类自定义信封，请在**宿主侧**翻译成其中之一",
                suggest="LocalToolHost 的回执就是标准形状；自定义 ToolHost 请对齐它",
            ).to_failure())
        return json.dumps(result.get("value"), ensure_ascii=False, default=str)

    _handler.__name__ = tool_name
    return _handler


def _register_tool(server: MCPServer, registry: ToolRegistry,
                   schema: Mapping[str, object], *, toolhost: ToolHost | None = None,
                   required_source: RequiredSource | None = None) -> None:
    """把一条 ``schemas()`` 投影注册成 MCP 工具（三步：合成 → 注册 → 覆写）。

    ⚠ ``toolhost=None`` ⇒ **现场建一个** ``LocalToolHost(registry)``（与
    :func:`build_mcp_server` 同一条规则）。⭐ 2026-10-10 实测踩到的真 bug：热同步的对账
    路径把 ``self._toolhost``（聚合端点上是 ``None``）直接传了进来 ⇒ handler 闭包捕获
    ``toolhost=None`` ⇒ 调它时 ``AttributeError: 'NoneType' object has no attribute 'call'``
    ⇒ **被重新注册过的工具（含墓碑）全废**，而且现象是 SDK 那句通用
    ``Error executing tool X``（墓碑该有的"怎么重开"全丢）。
    """
    name = str(schema["name"])
    tool = registry.get(name)
    if tool is None:
        raise MechaError(
            f"schemas() 声明了工具 {name!r}，注册表里却取不到本体",
            kind="tool_declaration_drift",
            hint="schemas() 与 get() 必须来自同一张注册表（不允许第二张工具表）",
        )
    host = toolhost if toolhost is not None else LocalToolHost(registry)
    required = resolve_required(name, tool, required_source)
    input_schema, defaulted = tool_input_schema(
        name, schema, execute=tool.execute, declared_required=required)
    handler = _make_handler(host, name, defaulted)
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


# ============================================================================
# ⭐ 工具表**热同步**（2026-10-10 用户裁决："按最正规的方式来"）
#
# 三件事缺一不可（少一件，AI 的工具表就永远停在"连接那一刻"）：
#   ① **表真的会变**：``MCPServer`` 支持 ``add_tool``/``remove_tool``，但**没人调它**
#      ⇒ 端点发出去的永远是 ``start()`` 那一刻的快照（实测：``Aggregator`` 里有该软件的
#      **全部**工具，端点却只发那几个元工具）。⇒ :meth:`McpEndpoint.publish_tools` 做
#      **幂等对账**（多退少补、描述变了的重铺）。
#   ② **声明能力**：客户端 SDK **只在服务端声明 ``capabilities.tools.listChanged`` 时**
#      才挂通知处理器（SDK 原话 "silently skipped"）⇒ :func:`declare_tools_list_changed`。
#   ③ **服务端主动发**：``Connection.send_tool_list_changed()``（best-effort；没有独立
#      SSE 通道就**静默丢弃**）⇒ :meth:`McpEndpoint.notify_tools_changed`。
#
# ⚠ **为什么不"在工具调用里顺手发"**（用户明确禁止）：发送只发生在
#   ``AggregateRuntime.sync_backends()``（软件就绪/退出那一刻），不在任何请求里。
# ============================================================================


def _on_this_loop(loop: Any) -> bool:
    """当前线程是不是就跑在 ``loop`` 上（决定是"直接跑"还是"投过去"）。"""
    try:
        return asyncio.get_running_loop() is loop
    except RuntimeError:
        return False


def _schema_digest(schema: Mapping[str, object]) -> str:
    """一条工具投影的指纹（描述变了 ⇒ 要重注册：墓碑语义就靠它）。"""
    return json.dumps(schema, sort_keys=True, default=str)


def declare_tools_list_changed(server: MCPServer) -> None:
    """⭐ ② 让端点在 ``initialize`` 里**声明** ``capabilities.tools.listChanged = true``。

    ## 为什么非声明不可（实测抓到的真因）

    dsh 用的 TS 客户端 SDK 里，**只有服务端声明了这条能力**，客户端才去挂
    ``notifications/tools/list_changed`` 的处理器——SDK 源码原话：
    *"servers are silently skipped if the server doesn't advertise the corresponding
    listChanged capability"*。⇒ 不声明，我们发出去的通知会被**静默忽略**
    （现象会像"发了没用"，比不发更难查）。

    ⚠ 实测本模块原先声明的是 ``tools = list_changed=False``：因为
    ``MCPServer(name=…, instructions=…)`` 没传 ``NotificationOptions``，而
    ``streamable_http_app()`` 的签名里**也没有**这个参数 ⇒ 只能在这里注入。

    ## 注入点为什么是这一处

    SDK 的 ``ServerRunner`` 在 initialize 时调
    ``server.create_initialization_options()``（``mcp/server/runner.py:431``，
    且 streamable 路径**不传** ``init_options``：``streamable_http_manager.py:245``）
    ⇒ 把那个**公开方法**在**这一个实例**上包一层即可。
    ⚠ ``server._lowlevel_server`` 是 SDK 未提供公开访问口的属性（本仓唯一的私有读，
    见 Known Limitations）。

    ## ⚠ 能力声明必须与"真会发"成对

    只有**聚合端点**装它。控制端点（``software_control``）**不发**通知 ⇒ 不许声明，
    否则是对客户端的**假声明**。
    """
    low = server._lowlevel_server                      # noqa: SLF001 - 见 docstring
    original = low.create_initialization_options

    def with_tools_changed(notification_options: NotificationOptions | None = None,
                           experimental_capabilities: dict | None = None,
                           extensions: dict | None = None):
        base = notification_options or NotificationOptions()
        merged = NotificationOptions(prompts_changed=base.prompts_changed,
                                     resources_changed=base.resources_changed,
                                     tools_changed=True)
        return original(notification_options=merged,
                        experimental_capabilities=experimental_capabilities,
                        extensions=extensions)

    low.create_initialization_options = with_tools_changed      # type: ignore[method-assign]


class ConnectionTracker:
    """⭐ ③ 的"活连接"登记表（**只服务聚合端点**；launcher 里只有一个端点 ⇒ 单例）。

    ## 为什么要包 SDK 的工厂（方案 B，用户裁决）

    MCP Python SDK 在 streamable-HTTP 这条路上**没有**"连接建立/关闭"的公开回调：

    - ``ServerRequestContext.session`` 只在**请求处理期**可达，而且**每请求新建**
      （实测：4 次请求 = 4 个会话对象）；
    - ``Connection`` 由 SDK 在 ``serve_loop`` 内部创建，**不挂在任何我们拿得到的地方**
      （``StreamableHTTPServerTransport`` 上没有 session/connection 属性）；
    - streamable 路径的 lifespan 是**管理器级一次**，不是每会话。

    ⇒ 想拿"活连接"，只能包它的**两个公开工厂**：
    ``for_loop``（loop 模式，**有**独立 SSE back-channel）与
    ``from_envelope``（2026-era 单请求信封 / 无状态，**没有** back-channel ⇒ 登记了也发不出，
    发送那一步会按 ``has_standalone_channel()`` 跳过并留日志）。

    ⚠ **这是有意的代价**：SDK 升级可能让包装失效 ⇒ 判据
    ``checks/aggregate_tools_hot_sync_selfcheck.py`` 钉住"包装在位 + 真收到通知"。

    ⚠ 存的是 ``(conn, loop)``：**改表与发通知都要在端点自己的事件循环上做**，
    否则会和并发的 ``tools/list`` 抢工具表；跨线程则走
    ``asyncio.run_coroutine_threadsafe``。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conns: dict[int, tuple[Any, Any]] = {}

    def register(self, conn: Any, loop: Any) -> None:
        """登记一条连接（按对象身份去重 ⇒ **一连接一条**，不会随请求数膨胀）。"""
        with self._lock:
            self._conns[id(conn)] = (conn, loop)

    def snapshot(self) -> list[tuple[int, Any, Any]]:
        with self._lock:
            return [(key, conn, loop) for key, (conn, loop) in self._conns.items()]

    def drop(self, key: int) -> None:
        with self._lock:
            self._conns.pop(key, None)

    def count(self) -> int:
        with self._lock:
            return len(self._conns)


#: 进程内**唯一**那份登记表（launcher 单实例锁保证只有一个聚合端点）。
_TRACKER: ConnectionTracker | None = None


def install_connection_tracking() -> ConnectionTracker:
    """装（或复用）活连接登记表：把 ``Connection`` 的两个公开工厂包一层。幂等。"""
    global _TRACKER
    if _TRACKER is not None:
        return _TRACKER
    from mcp.server.connection import Connection

    tracker = ConnectionTracker()
    original_for_loop = Connection.for_loop.__func__            # type: ignore[attr-defined]
    original_from_envelope = Connection.from_envelope.__func__   # type: ignore[attr-defined]

    def _wrap(original) -> classmethod:
        def call(cls, *args, **kwargs):
            conn = original(cls, *args, **kwargs)
            try:
                tracker.register(conn, asyncio.get_running_loop())
            except RuntimeError:
                pass          # 不在事件循环里（正常路径不会走到）
            return conn
        return classmethod(call)

    Connection.for_loop = _wrap(original_for_loop)               # type: ignore[assignment]
    Connection.from_envelope = _wrap(original_from_envelope)     # type: ignore[assignment]
    _TRACKER = tracker
    return tracker


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
                 log: Callable[[str], None] | None = None,
                 track_connections: bool = False) -> None:
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
        #: ⭐ 活表对账用：**我们注册过什么**（name → 投影指纹）。⚠ 它不是第二份真值：
        #: 真值永远是喂进来的那个 ``ToolRegistry``，这里只记"上一次铺成什么样"，
        #: 判据 ``aggregate_tools_hot_sync_selfcheck`` 还钉着"名字集合恒等于 registry"。
        self._declared: dict[str, str] = {}
        #: ⭐ 只在**聚合端点**上开：登记活连接 + 声明 ``tools.listChanged``。
        self._track = bool(track_connections)
        self._tracker: ConnectionTracker | None = None
        self._publish_lock = threading.Lock()

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
        """纯构造（幂等）：不起线程、不碰端口、不写发现文件。

        ⭐ 2026-10-10：构造时同时做两件与"热同步"有关的事（**只在聚合端点上**）：
        ① 记下"这次铺了哪些工具"（``_declared``，供后续对账）；
        ② 装活连接登记表 + **声明** ``tools.listChanged``（否则客户端不挂通知处理器）。
        """
        if self._server is None:
            self._server = build_mcp_server(
                self._registry, server_name=self._server_name,
                instructions=self._instructions, toolhost=self._toolhost,
                required_source=self._required_source)
            self._declared = {str(s["name"]): _schema_digest(s)
                              for s in self._registry.schemas()}
            if self._track:
                self._tracker = install_connection_tracking()
                declare_tools_list_changed(self._server)
        return self._server

    # ---- ⭐ 工具表热同步（① 对账 + ③ 广播）----

    def publish_tools(self, registry: ToolRegistry) -> bool:
        """① 把**活工具表**对账到 ``registry``；有变化则 ③ 广播。返回"是否变了"。

        ⚠ **顺序不能反**：先改表、再通知——客户端收到通知会**立刻** ``tools/list``，
        那时必须已经能看到新表。

        ⚠ **在哪跑**：有活连接时，改表也投到**端点自己的事件循环**上跑（``tools/list``
        在同一个循环里 ⇒ 不会一边遍历一边被改）；一条连接都没有时直接在调用线程改
        （没有客户端就**没有并发读**）。
        """
        self.build_server()
        loop = None
        if self._tracker is not None:
            snap = self._tracker.snapshot()
            loop = snap[0][2] if snap else None
        with self._publish_lock:
            if loop is not None and not _on_this_loop(loop):
                changed = asyncio.run_coroutine_threadsafe(
                    self._reconcile(registry), loop).result(10.0)
            else:
                #: ⚠ 这里**不能**调 `_reconcile`（它是协程）：没有活连接时直接在
                #: 当前线程改表，走同一个同步实现。
                changed = self._reconcile_now(registry)
        if changed:
            self.notify_tools_changed()
        return changed

    async def _reconcile(self, registry: ToolRegistry) -> bool:
        """``_reconcile_now`` 的协程外壳（投到端点自己的事件循环上跑）。"""
        return self._reconcile_now(registry)

    def _reconcile_now(self, registry: ToolRegistry) -> bool:
        """同步对账：多退少补、描述变了的重铺（墓碑靠这个）。"""
        server = self.build_server()
        want = {str(s["name"]): s for s in registry.schemas()}
        changed = False
        for name in sorted(set(self._declared) - set(want)):
            server.remove_tool(name)
            self._declared.pop(name, None)
            changed = True
        for name, schema in sorted(want.items()):
            digest = _schema_digest(schema)
            if self._declared.get(name) == digest:
                continue
            if name in self._declared:
                server.remove_tool(name)              # 同名替换：描述可能变了（墓碑）
            #: ⚠ `toolhost=self._toolhost` 可能是 None（聚合端点就是）——那一条由
            #: `_register_tool` 现场补 `LocalToolHost(registry)`，**不许把 None 传进去**
            #: （传了 ⇒ handler 一调就 AttributeError，见该函数的 docstring）。
            _register_tool(server, registry, schema, toolhost=self._toolhost,
                           required_source=self._required_source)
            self._declared[name] = digest
            changed = True
        if changed:
            self._log("[mecha-mcp] 工具表已更新：%d 个" % len(self._declared))
        return changed

    def notify_tools_changed(self) -> int:
        """③ 给**每个活连接**发一次 ``tools/list_changed``（服务端主动，不在请求里）。

        ⚠ ``Connection.notify`` 是 **best-effort**：没有独立 SSE 通道时它会**静默丢弃**
        ⇒ 这里先问 ``has_standalone_channel()``，不可发就**我们自己 INFO 记一笔**
        （不让"没发出去"变成看不见的事）。发送抛错 ⇒ 剔除该连接并留日志。
        """
        if self._tracker is None:
            return 0
        sent = 0
        for key, conn, loop in self._tracker.snapshot():
            try:
                #: ⚠ 实测踩到：``has_standalone_channel`` 在本版 SDK 里是 **property**
                #: （不是方法）——当函数调会抛 ``TypeError: 'bool' object is not callable``，
                #: 而它被下面的 except 收成"发送失败 ⇒ 剔除连接" ⇒ **把活连接误删**。
                #: ⇒ 两种形状都接（属性优先，可调用则调）。
                has_channel = conn.has_standalone_channel
                if callable(has_channel):
                    has_channel = has_channel()
                if not has_channel:
                    self._log("[mecha-mcp] 跳过通知：该连接没有独立 SSE 通道"
                              "（2026-era 单请求信封/无状态那条路天生收不到推送）")
                    continue
                coro = conn.send_tool_list_changed()
                if _on_this_loop(loop):
                    loop.create_task(coro)            # 已在循环上 ⇒ 不可阻塞等待
                else:
                    asyncio.run_coroutine_threadsafe(coro, loop).result(5.0)
                sent += 1
            except Exception as exc:                  # noqa: BLE001 - 一条连接坏了不拖垮别的
                self._log(f"[mecha-mcp] 通知发送失败（剔除该连接）：{type(exc).__name__}: {exc}")
                self._tracker.drop(key)
        if sent:
            self._log(f"[mecha-mcp] 已通知 {sent} 个连接：工具表变了")
        return sent

    def connection_count(self) -> int:
        """活连接条数（判据用：钉住"一连接一条、不随请求数膨胀"）。"""
        return 0 if self._tracker is None else self._tracker.count()

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
