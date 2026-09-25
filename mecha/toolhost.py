"""ToolHost 契约：工具**定义**与宿主**传输**分成两个 seam。

``tools.py`` 管"工具长什么形状"（定义：命名/声明/回执契约/白名单投影的唯一
来源），本模块管"定义怎么交到宿主手里、调用怎么回来"（传输）。两者分开的
直接后果：**dsh/MCP 只是一个 Provider**——``declare`` 里注册进 ``ctx.tools``
的是 dsh 侧实现，``call`` 里走宿主回程的也是 dsh 侧实现，本包一行都不 import
它。核心不认任何宿主词，因而也不在核心手抄工具名单（``schemas()`` 是唯一
来源，谁要名单谁来取）。

- ``declare(schemas)``：把工具声明交给宿主。local 出厂实现**只校验**（合规
  schema 形状 + 白名单字段）然后什么都不做；dsh/MCP 实现把每项注册进
  ``ctx.tools``。
- ``call(name, args)``：执行一次调用，把任何结果**归一化**成与
  ``ToolRegistry.execute`` 同构的回执（``{"is_error", ...}``）。宿主句柄坏掉
  / 返回畸形结果 / 意外异常，一律转成结构化 ``ToolFailure {message, info}``，
  **不抛穿**——模型只该看见能纠正的失败，栈留给宿主日志（L4）。
- ``schemas()``：模型可见投影，只出白名单字段（``execute``/``output``/
  ``render`` 绝不泄漏）。
- ``teardown()``：可逆释放，**幂等**（宿主关停/重装工具面时可能被调多次）。

## Known Limitations and Deferred Work

- 未实现 dsh/MCP Provider（本包不 import dsh）。**触发**：出现可注入
  ``ctx.tools`` 的真 dsh 宿主进程，且宿主需要工具面 opt-in 接线。
- 未实现"传输层权限检查"：可见性过滤归 ``visibility.py``，写权归 Gate——
  ToolHost 只是搬运，不新增第二套权限（加了就是 v1 的第二道门）。
- ``declare`` 不做注册表身份核对（local 实现拿到的 schemas 可能不是本实例
  registry 的产出）。**触发**：出现两个 registry 片交错的真实宿主。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from .errors import MechaError, ToolFailure
from .tools import MODEL_VISIBLE_FIELDS

#: ``LocalToolHost`` 与派生实现的 provider 标签。
LOCAL_HOST = "local"

#: ``declare`` 的 schema 形状守卫：字段名与 ``tools.MODEL_VISIBLE_FIELDS`` 同源，
#: 这里只判"给过来的东西是不是白名单形状"，不解释取值内容。
_DECLARED_FIELDS = MODEL_VISIBLE_FIELDS


@runtime_checkable
class ToolHost(Protocol):
    """宿主传输面：定义交出去、调用收回来。dsh/MCP 与 local 是它的 Provider。"""

    def declare(self, schemas) -> None: ...

    def call(self, name: str, args: Mapping[str, object]) -> Mapping[str, object]: ...

    def schemas(self) -> list[dict]: ...

    def teardown(self) -> None: ...


def _failure(message: str, kind: str, hint: str = "") -> dict:
    """结构化失败的单一构造点（形状与 ``ToolRegistry.execute`` 同构）。"""
    return {"is_error": True, "error": ToolFailure(message, kind=kind, hint=hint).to_failure()}


def _validate_declaration(schemas) -> list[dict]:
    """校验一批待声明的 schema，返回白名单投影。

    ⭐ **选择：拒收，不静默过滤**（ADR `ToolHost契约`）。出现白名单外字段
    （``execute``/``output``/``render`` 或任何别的东西）当场
    ``MechaError(kind="bad_tool_declaration")``——silent filter 会把"谁手工
    拼了 schema"这个真事实吞掉，而它正是能让执行器/可调用对象泄进模型请求的
    那条路。少字段比透传对象安全，但"少字段"和"对象差点透传"是两件事，前者
    该报错（L3：不要把可疑当正常）。
    """
    if isinstance(schemas, Mapping) or isinstance(schemas, (str, bytes)):
        raise MechaError(
            f"declare 收到 {type(schemas).__name__}，要一批 schema 项",
            kind="bad_tool_declaration",
            hint="传 schemas() 那样的列表（每项是含 name/description/parameters 的映射）",
        )
    try:
        items = list(schemas)
    except TypeError as e:
        raise MechaError(
            f"declare 收到不可迭代的 {type(schemas).__name__}：{e}",
            kind="bad_tool_declaration",
            hint="传 schemas() 那样的列表（每项是含 name/description/parameters 的映射）",
        ) from None
    out: list[dict] = []
    for i, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise MechaError(
                f"第 {i} 项不是映射（{type(item).__name__}）：是工具定义对象吗？",
                kind="bad_tool_declaration",
                hint="只交模型可见投影（schemas() 的产出），不交 ToolDefinition 本体",
            )
        extra = sorted(k for k in item if k not in _DECLARED_FIELDS)
        if extra:
            raise MechaError(
                f"第 {i} 项含白名单外字段 {extra}",
                kind="bad_tool_declaration",
                hint="模型可见字段只有 name/description/parameters（投影唯一来源是 schemas()）",
            )
        missing = [f for f in _DECLARED_FIELDS if f not in item]
        if missing:
            raise MechaError(
                f"第 {i} 项缺字段 {missing}",
                kind="bad_tool_declaration",
                hint="声明必须给全 name/description/parameters",
            )
        if not isinstance(item["description"], str) or not item["description"].strip():
            raise MechaError(
                f"第 {i} 项的 description 不是非空字符串",
                kind="bad_tool_declaration",
                hint="description 是模型唯一的用法来源",
            )
        if not isinstance(item["parameters"], Mapping):
            raise MechaError(
                f"第 {i} 项的 parameters 不是映射（{type(item['parameters']).__name__}）",
                kind="bad_tool_declaration",
                hint="parameters 是 JSON Schema 风格的对象声明",
            )
        out.append({f: item[f] for f in _DECLARED_FIELDS})
    return out


def _host_result(result, *, name: str, label: str) -> dict:
    """归一化宿主回执：坏形状 → ``bad_host_result``，**绝不把畸形结果当值透传**。

    只判形状，不解释内容（内容是工具的领域真值，核心不许读）。判据：
    ``is_error`` 必须是真布尔；错误支必有 ``error.{message,info}``；
    成功支必有 ``value`` 与 ``content``。
    """
    if not isinstance(result, Mapping):
        return _failure(
            f"宿主 {label!r} 对工具 {name!r} 的调用结果不是映射"
            f"（{type(result).__name__}）",
            "bad_host_result",
            "宿主 call 必须返回与 ToolRegistry.execute 同构的回执",
        )
    flag = result.get("is_error")
    if not isinstance(flag, bool):
        return _failure(
            f"宿主 {label!r} 的回执缺布尔 is_error：{sorted(result)!r}",
            "bad_host_result",
            "回执形状见 ToolRegistry.execute（is_error 真布尔）",
        )
    if flag:
        err = result.get("error")
        if not isinstance(err, Mapping) or "message" not in err or "info" not in err:
            return _failure(
                f"宿主 {label!r} 的错误回执不是 ToolFailure {{message, info}} 形状："
                f"{err!r}",
                "bad_host_result",
                "错误回执用 MechaError.to_failure() 投影",
            )
        return dict(result)
    if "value" not in result or "content" not in result:
        return _failure(
            f"宿主 {label!r} 的成功回执缺 value/content：{sorted(result)!r}",
            "bad_host_result",
            "成功回执 = canonical value + 渲染 content",
        )
    return dict(result)


class LocalToolHost:
    """出厂 local Provider：定义归 ``ToolRegistry``，传输是进程内直调。

    ``declare`` 校验后无事可做（工具已经在同一个进程的注册表里）；``call``
    委托 ``tools.execute``（回执契约与失败归一化在那一处），本类只补宿主边界
    那一层保险——宿主句柄坏掉/回执畸形照样不抛穿；``schemas`` 委托投影；
    ``teardown`` 幂等空操作（没有外部注册要撤）。
    """

    def __init__(self, registry, *, name: str = LOCAL_HOST) -> None:
        self._registry = registry
        self._name = name
        self._teardowns = 0

    @property
    def name(self) -> str:
        """provider 标签（进教学 hint 用，缺省 ``local``）。"""
        return self._name

    @property
    def registry(self):
        """被托管的注册表（定义面唯一来源；装配点接线用）。"""
        return self._registry

    def declare(self, schemas) -> None:
        """校验并交出声明。local 无外部注册面 ⇒ 校验即全部动作（幂等）。"""
        _validate_declaration(schemas)

    def call(self, name: str, args: Mapping[str, object] | None = None) -> dict:
        """执行一次调用；任何异常/畸形结果都归一化，不抛穿（L4）。"""
        try:
            result = self._registry.execute(name, dict(args or {}))
        except Exception as e:  # noqa: BLE001 - 宿主边界的最后一道保险
            return _failure(
                f"宿主 {self._name!r} 执行工具 {name!r} 时异常："
                f"{type(e).__name__}: {e}",
                "toolhost_error",
                "宿主实现自身的问题；回执已如实报告，栈见宿主日志",
            )
        return _host_result(result, name=name, label=self._name)

    def schemas(self) -> list[dict]:
        """模型可见投影（白名单再投影一层，保证本类自身契约成立）。"""
        return [{f: s.get(f) for f in MODEL_VISIBLE_FIELDS}
                for s in self._registry.schemas()]

    def teardown(self) -> None:
        """可逆释放：没有外部注册要撤 ⇒ 空操作，计数只为自证幂等。"""
        self._teardowns += 1
