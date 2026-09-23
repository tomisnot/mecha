"""AI 适配器的工具形态层（总纲 §7 Phase 4；形态同构，不是功能齐全）。

工具注册进宿主的 ``ctx.tools``（Phase 5 接线），**不另起 MCP server**
（ADR 另起-MCP-server）。本模块定义"像自己的手脚"的**形状**：

- 声明：``define_tool`` 契约 + ``output.schema`` 显式声明（消 D-15 的四个
  隐式键——回执必填键在 schema 里，不在 spec 散文里）。
- 命名：snake_case、动词_宾语，**禁止项目前缀**（``mecha_`` 这类要收——
  工具已在自己的命名空间里，前缀是实现词汇）。
- 描述文体：只含任务相关概念，**不含 UI/transport/实现词汇**（守卫词表在
  checks 侧，模型可见文本逐字钉住是 L4）。
- 错误：对齐 ``ToolFailure {message, info}``（MechaError.to_failure 即此形状）。
- 投影：``schemas()`` 白名单——只有 name/description/parameters 进模型请求，
  execute/output/render 绝不泄漏（消 D-25：不维护手写 TOOL_NAMES）。

## Known Limitations and Deferred Work

- 不 import dsh：宿主注入时把 ToolDefinition 适配成 defineTool 形状是 Phase 5
  的接线工作；这里保证形状与不变式（白名单投影、单调拒绝）在库内成立。
- ``present_call``/``present_result``（UI 展示投影）未实现：Monitor 读面够用，
  宿主卡片需求等真实 UI 出现再加（n=0 不建）。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from .errors import MechaError, ToolFailure
from .roles import Disposer

#: schemas() 白名单（dsh ToolSchema 的模型可见字段）。
MODEL_VISIBLE_FIELDS = ("name", "description", "parameters")

_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)+$")

#: 库自身包名。工具注册进宿主命名空间后不该再重复它（``mecha_run`` 是把
#: 实现结构写进模型可见文本）。这是库的自有名，不是消费项目的领域值。
OWN_PACKAGE = "mecha"


def check_tool_name(name: str, *, banned_prefixes: tuple[str, ...] = (OWN_PACKAGE,)) -> None:
    """命名守卫：snake_case 动词_宾语；禁项目前缀。违规当场报错（fail loud）。"""
    if not _NAME_RE.match(name):
        raise MechaError(
            f"工具名 {name!r} 不合命名律",
            kind="bad_tool_name",
            hint="必须 snake_case 且至少两段（动词_宾语，如 set_config / run_sim）",
        )
    first = name.split("_")[0]
    if first in ("get", "list"):
        raise MechaError(
            f"工具名 {name!r} 以 {first}_ 开头：视图/枚举不是工具，是 schema 的投影",
            kind="bad_tool_name",
            hint="把只读枚举并进 describe/schema 类工具，或注册为 query 项（Surface.register）",
        )
    for p in banned_prefixes:
        if name.startswith(p + "_"):
            raise MechaError(
                f"工具名 {name!r} 带项目前缀 {p!r}",
                kind="bad_tool_name",
                hint="工具已在自己的命名空间内；前缀是把实现结构写进模型可见文本",
            )


@dataclass(frozen=True)
class ToolOutput:
    """canonical 输出声明：schema + 纯投影（dsh ToolOutputDefinition 同形）。"""

    schema: Mapping[str, object]
    render: Callable[[Mapping[str, object], Mapping[str, object]], list[Mapping[str, object]]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: Mapping[str, object]
    output: ToolOutput
    execute: Callable[..., Mapping[str, object]] = field(repr=False, default=None)  # type: ignore[assignment]


def define_tool(
    *,
    name: str,
    description: str,
    parameters: Mapping[str, object],
    output_schema: Mapping[str, object],
    execute: Callable[..., Mapping[str, object]],
    render: Callable[[Mapping[str, object], Mapping[str, object]], list[Mapping[str, object]]] | None = None,
    banned_prefixes: tuple[str, ...] | None = None,
    banned_words: tuple[str, ...] = (),
) -> ToolDefinition:
    """构造即校验：坏形状的工具根本注册不出来（门在执行器上）。"""
    check_tool_name(name, banned_prefixes=(OWN_PACKAGE,) if banned_prefixes is None else banned_prefixes)
    if not description.strip():
        raise MechaError(
            f"工具 {name!r} 没有 description",
            kind="bad_description",
            hint="description 是模型唯一的用法来源（Phase 4 判据的前提）",
        )
    low = description.lower()
    for w in banned_words:
        if w.lower() in low:
            raise MechaError(
                f"工具 {name!r} 的 description 含实现词汇 {w!r}",
                kind="bad_description",
                hint="模型可见文本只含任务相关概念，不含 UI/transport/内部结构词汇",
            )
    if not isinstance(output_schema, Mapping) or "required" not in output_schema:
        raise MechaError(
            f"工具 {name!r} 的 output.schema 缺 required 声明",
            kind="bad_output_schema",
            hint="回执必填键必须显式声明（消 D-15：隐式契约键变显式）",
        )

    def _render(args, value):
        return [{"type": "text", "text": str(value)}]

    return ToolDefinition(
        name=name, description=description, parameters=dict(parameters),
        output=ToolOutput(schema=dict(output_schema), render=render or _render),
        execute=execute,
    )


class ToolRegistry:
    """工具注册表：注册返回 disposer；schemas() 白名单投影。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> Disposer:
        if tool.name in self._tools:
            raise MechaError(
                f"工具 {tool.name!r} 重复注册",
                kind="duplicate_tool",
                hint="同名先 dispose 再注册",
            )
        self._tools[tool.name] = tool

        def dispose() -> None:
            self._tools.pop(tool.name, None)

        return dispose

    def schemas(self) -> list[dict]:
        """模型可见投影：只有白名单字段（不维护手写名单，消 D-25）。"""
        return [
            {f: getattr(t, f) for f in MODEL_VISIBLE_FIELDS}
            for t in sorted(self._tools.values(), key=lambda x: x.name)
        ]

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def execute(self, name: str, args: Mapping[str, object]) -> dict:
        """执行 + 回执校验 + 失败归一化（ToolFailure {message, info}）。"""
        tool = self._tools.get(name)
        if tool is None:
            return {"is_error": True,
                    "error": ToolFailure(
                        f"未知工具 {name!r}", kind="unknown_tool",
                        hint="可见工具见 schemas() 投影",
                    ).to_failure()}
        try:
            value = dict(tool.execute(**dict(args)))
        except MechaError as e:
            return {"is_error": True, "error": e.to_failure()}
        missing = [k for k in tool.output.schema.get("required", []) if k not in value]
        if missing:
            return {"is_error": True,
                    "error": ToolFailure(
                        f"工具 {name!r} 回执缺必填键 {missing}",
                        kind="bad_tool_output",
                        hint="回执契约由 output.schema.required 声明",
                    ).to_failure()}
        return {"is_error": False, "value": value,
                "content": tool.output.render(dict(args), value)}
