"""命令声明 → AI 工具面：**纯投影**（这条缝的契约落点）。

⚠ 框架**只搬不解释**：除了 `NAME_RULES` 那张**明文**惯例表，不解析名字格式、不按值分支、不按名特判。
⚠ **两道闸**（都在框架里、语义不同）：**投影期过滤 = 看不见**（省 AI 去试）；
**运行时 ``ScopePolicy.check`` = 做了也拒**（行为级）。本模块只管前者。
## Known Limitations and Deferred Work

- 只投**声明面**（名字 / 文案 / 参数 / 档位 / scope / 可逆性）：``execute`` **必填**，由调用方给。
- ``render`` 走 ``define_tool`` 的默认实现；**输出形状的呈现**归宿主（本模块不解释值语义）。
- 漂移检查需要**独立来源**（``required_source``）：只给声明时它无从对账 —— 那是有意的
  （框架不猜谁对）。
- 不做 ``project_all``：今天没有消费者（有清单的是项目，不是框架）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from .commands import CommandSpec
from .errors import MechaError
from .scopes import ScopePolicy
from .tools import ToolDefinition, check_tool_name, define_tool

#: 官方改名惯例（**明文规则**，不是猜）：命令面 → 工具面
NAME_RULES: tuple[tuple[str, str], ...] = (("get_", "read_"), ("list_", "query_"))

#: 固定规则：**声明为 ``actor`` 的参数一律不投影**（actor 由通道钉死，不该给模型）
NEVER_PROJECT_PARAMS: tuple[str, ...] = ("actor",)


@dataclass(frozen=True)
class ProjectionOverrides:
    """全是可选；默认全空 = 纯透传。"""

    rename: Mapping[str, str] = field(default_factory=dict)
    description: str | None = None
    omit_params: tuple[str, ...] = ()
    kind: str | None = None
    scope: tuple[str, ...] | None = None
    #: 可逆性：**只从这个口进来**（项目的域知识），框架**不许**从别的字段推
    #: （``cancel_supported`` 是"运行期能不能中途取消"，与"这次改动能不能撤"不等价
    #:  ⇒ 拿 A 冒充 B 就是**假契约**）
    reversible: bool | None = None


def projected_name(name: str, *, overrides: ProjectionOverrides | None = None) -> str:
    """按惯例 + 显式改名表算工具面名字（**只查这两处，不猜**）。"""
    if overrides is not None and name in overrides.rename:
        return overrides.rename[name]
    for old, new in NAME_RULES:
        if name.startswith(old):
            return new + name[len(old):]
    return name


def _declared_required(spec: CommandSpec) -> list[str]:
    req = spec.parameters.get("required")
    if isinstance(req, Sequence) and not isinstance(req, (str, bytes)):
        return [str(x) for x in req]
    return []


def projected_required(spec: CommandSpec, *,
                       overrides: ProjectionOverrides | None = None) -> tuple[str, ...]:
    """投影后**仍然必填**的参数名（剔掉 ``actor`` 与 ``omit_params``）。

    ⚠ 工具面的 ``parameters`` 是属性表、**没有 required 槽位** ⇒ 必填由宿主交给它的
    ``required_source``（线上 ``required`` 归宿主签名层）。这个出口就是为此存在的。
    """
    ov = overrides or ProjectionOverrides()
    omit = set(NEVER_PROJECT_PARAMS) | set(ov.omit_params)
    return tuple(k for k in _declared_required(spec) if k not in omit)


def project(spec: CommandSpec, *, execute: Callable[..., Mapping[str, object]],
            overrides: ProjectionOverrides | None = None,
            policy: ScopePolicy | None = None,
            required_source: Mapping[str, Sequence[str]] | None = None,
            side: str = "ai") -> ToolDefinition | None:
    """把一条 ``CommandSpec`` 投影成 ``ToolDefinition``；**返回 None = 按 policy 不投影**。

    ⚠ ``execute`` **必填、无默认值**：投影的产物**必定可调**，不存在"未接线"的中间态
    （"列得出、调不动"是本项目列过的六大缺陷类之一，抓到它的往往是**另一个仓的判据**
    ⇒ 缝的形状不该邀请它）。忘了给 ⇒ **构造期**就抛（``TypeError``），不是运行时才炸。
    """
    ov = overrides or ProjectionOverrides()
    scope = tuple(ov.scope) if ov.scope is not None else tuple(spec.scope)
    if policy is not None and scope and not all(policy.allows(side, s) for s in scope):
        return None                                   # ⚠ 投影期过滤：看不见（省 AI 去试）
    name = projected_name(spec.name, overrides=ov)
    check_tool_name(name)                             # 改名后仍要过工具面形状律
    declared = _declared_required(spec)
    if required_source is not None and spec.name in required_source:
        from_source = [str(x) for x in required_source[spec.name]]
        if set(from_source) != set(declared):         # ⚠ 漂移硬失败：不静默取一方
            raise MechaError(
                f"声明漂移：{spec.name!r} 的 required 声明 {sorted(declared)} "
                f"与来源 {sorted(from_source)} 不一致",
                kind="capability_drift",
                hint="声明与实现必须同源；改一处、另一处跟着改（别让它静默取一方）",
            )
    omit = set(NEVER_PROJECT_PARAMS) | set(ov.omit_params)
    props = spec.parameters.get("properties")
    props = dict(props) if isinstance(props, Mapping) else {}
    for k in omit:
        props.pop(k, None)
    kept_required = [k for k in declared if k not in omit]
    # ⚠ **两面不同形**：命令面 ``parameters`` 是**完整 JSON Schema**（type/properties/required），
    # 而工具面 ``define_tool`` 要的是**逐键的属性表**（``{参数名: 声明}``；线上按属性表逐键读）
    # ⇒ 投影这一步**必须转对**，不能原样搬运（否则 type/properties/required 会变成三个参数名）。
    params = {k: v for k, v in props.items()}
    return define_tool(
        name=name,
        description=ov.description if ov.description is not None else spec.description,
        parameters=params,
        output_schema=spec.output_schema,
        execute=execute,
        # WARN 这里是**翻译**（kind="write" 能无损翻回 side_effect=True）；凡「翻不回去」的
        # 派生都是**判断** ⇒ 禁止（框架不许替项目判语义）。第三档 read_telemetry 派生不出来，
        # **只能从 overrides 进**。
        kind=ov.kind if ov.kind is not None else ("write" if spec.side_effect else "read"),
        scope=scope,
        reversible=ov.reversible,          # ⚠ 只认 overrides：没给就是 None（未声明）
    )
