"""Command Surface：通用命令注册与调用机制（additive；第二个宿主逼出的机制）。

**为什么单有 ``Engine.run`` 不够**（ADR 2026-09-24-通用命令面）：

- ``Engine.run`` 是**一个**操作，吃一个不透明 ``spec``、回一个不透明映射。
  「一次演化」「一次拟合」都能塞进去，但塞不出**逐命令的参数契约与回执契约**：
  哪个参数必填、回执必含哪些键，只能靠宿主散文或判据钉，使用者看不到。
- ``Engine.run`` 的必实现判据（``FRAMEWORK_CALLS``）明确要求 Definition 只放
  框架真会调的方法。把「注册一个命令」做成新的必实现 = 具名 ABC 方法膨胀
  （rejected/2026-09-23-具名-ABC-方法膨胀）。命令是**分派目标**，不是框架自己
  会调的方法 ⇒ 它必须是注册项，机制住本模块。
- ``Surface.query`` 只解决只读分派（D-4：查询不经写权门）。有副作用、要审批、
  要估时、要取消的命令没有归宿。

本模块只写**机制**，不写任何宿主的命令名、状态键、指标名：

- ``CommandSpec``：声明（frozen）——名字 / 描述 / 参数 schema / 回执 schema
  （必含 ``required``，消 D-15 的隐式契约键）/ 是否有副作用 / scope /
  估时 / 可否取消 / 是否要审批。**声明即投影**：handler 另存注册表内部，
  所以投影面天然不含可调用实现。
- ``CommandResult``：命令体回执（``ok`` / ``values`` / ``error``）。错误形状
  沿用 ``MechaError``，跨边界投影沿用 ``ToolFailure {message, info}``。
- ``CommandRegistry``：``register(spec, handler) → disposer`` 与
  ``invoke(name, args, *, context, gate=None, channel=None) → dict``。

三条纪律：

1. **只读命令不得强制过 Gate**（D-4 同款：不存在"只读动词被判不可逆"）。
   ``side_effect=False`` 的 invoke 不碰 gate 参数，传了也不用。
2. **有副作用的命令经 Gate 写审计事件**，键为 ``command.<name>``。审计值只放
   操作语义（命令名 / ok / 回执键 / 错误 kind / scope），**不放结果体**——大
   对象由宿主自己存，History 只留引用（总纲 §5.2②，宪章 §8）。
3. **框架不替宿主表态**：``scope`` / ``approval_required`` / ``cancel_supported``
   是声明字段，本模块**不解释**它们的领域含义，也不据此改变分派；谁消费由
   宿主与 Consumer 决定（分类留宿主，机制进核心）。

``invoke`` 回执形状（与 ``ToolRegistry.execute`` 同构，宿主适配层可共用一套
归一化）：``{"is_error": False, "value": {...}}`` 或
``{"is_error": True, "error": ToolFailure.to_failure()}``。

## Known Limitations and Deferred Work

- 命令**不自动投影成 AI 工具**：工具投影是另一个 seam（ToolHost 契约未定，
  见宪章 §10.2）。本模块只保证 spec 是可投影的 frozen 数据；把 spec 适配成
  ``tools.define_tool`` 是宿主适配层的活，n=1 不预建。
- ``estimate_sec`` 只做"声明存在 + 调用形状对"的守卫，**不校验估算准不准**
  （那是宿主域语义，机械不可判）。
- Gate 审计键 ``command.<name>`` 要求宿主的 ``validate`` 接受该前缀；宿主
  validator 若拒绝未知键，审计写入会 fail loud（``command_audit_denied``），
  框架不替宿主放行——这是刻意的，见 ADR 的 Consequences。
- ``scope`` / ``approval_required`` / ``cancel_supported`` 目前**只是声明**：
  Scope 化 Authority（宪章 §10.3）与 Job 契约（§10.4）落地前不参与分派。
- 不内置并发控制：同命令并发 invoke 的串行化归宿主 Job Provider（n=1 无此压力）。
- 不校验 handler 形参与 ``parameters`` 声明的**逐字一致**：只校验调用时**传进来
  的键**与声明吻合（缺必填键或多未声明键都拒）。handler 多收一个声明过的参数
  属于宿主自己的事，机械对账（``command_signature_names`` 已备好读签名的手柄）
  等第二个宿主的真实压力，n=1 不建判据。
- 参数契约**默认严格**（未声明的键一律拒，除非声明 ``additionalProperties:
  true``）：宿主若习惯用 ``**kwargs`` 吞参数，会在这里被拦下。这是刻意的——静默
  吞参会把"拼错参数名"变成"命令悄悄少做一件事"。
- 审计事件的**值形状**是本地约定（``command``/``ok``/``side_effect``/``scope``/
  ``result_keys``/``error_kind``/``error_message``），尚未版本化：等它成为跨版本
  读取面时按 ``format_version`` 制度显式登记。
"""

from __future__ import annotations

import inspect
import numbers
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from .errors import MechaError, ToolFailure, UnknownOp
from .roles import Disposer, Role, RoleRegistry

#: 命令名守卫：snake_case、非空、无项目前缀。
#:
#: **刻意不照搬 EL/MCP 的工具名语义**（``tools.check_tool_name`` 禁 ``get_``/
#: ``list_`` 视图名、要求"动词_宾语"两段）：命令是宿主能力的名字，不是模型
#: 可见工具名——宿主叫 ``list_datasets`` / ``get_state`` / ``reset`` 都是合法
#: 的命令，只是投影成工具时由 ToolHost 适配层决定怎么呈现。核心只守**通用
#: 格式**（可读、可拼、非空、无项目前缀），单段命令名也放行（``reset`` 是
#: 完整语义，逼它改叫 ``reset_state`` 是核心替宿主定分类）。
_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")

#: 库自身包名：命令名带 ``mecha_`` 前缀是把实现结构写进宿主可见文本。
OWN_PACKAGE = "mecha"

#: 命令审计事件的键前缀（Gate 键空间里的保留前缀；宿主 validator 需接受它）。
AUDIT_KEY_PREFIX = "command."

#: 框架保留的调用元数据键：由 ``invoke`` 抽出来喂 Gate 审计，**不传给命令体**。
#: 命令体若要自己用，必须在 ``parameters`` 里显式声明（声明即契约）。
RESERVED_ARGS = ("reason", "call_id")


def _is_name_sequence(value: object) -> bool:
    """是不是"键名序列"（非空、非字符串、非 bytes 的序列）。"""
    return (isinstance(value, Sequence) and not isinstance(value, (str, bytes))
            and bool(value))


def check_command_name(name: str, *,
                       banned_prefixes: tuple[str, ...] = (OWN_PACKAGE,)) -> None:
    """通用命名守卫：snake_case、非空、无项目前缀。违规即报错。

    不管"动词_宾语"这条更强的命名律，也不禁 ``get_``/``list_`` 前缀——那是
    工具投影（模型可见面）的文体要求，不是命令面的通用格式要求。
    """
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise MechaError(
            f"命令名 {name!r} 不合命名律",
            kind="bad_command_name",
            hint="必须非空 snake_case（小写字母/数字/下划线，字母开头，"
                 "不许首尾或连续下划线），如 run_method / prepare_dataset / reset",
        )
    for p in banned_prefixes:
        if name.startswith(p + "_"):
            raise MechaError(
                f"命令名 {name!r} 带项目前缀 {p!r}",
                kind="bad_command_name",
                hint="命令已在宿主自己的命名空间内；前缀是把实现结构写进可见文本",
            )


def _check_output_schema(name: str, output_schema: object) -> None:
    """回执契约守卫：output_schema 必须含非空 required 键名序列（消 D-15）。"""
    if not isinstance(output_schema, Mapping) or "required" not in output_schema:
        raise MechaError(
            f"命令 {name!r} 的 output_schema 缺 required 声明",
            kind="bad_command_output_schema",
            hint="回执必填键必须显式声明（消 D-15：隐式契约键变显式）",
        )
    if not _is_name_sequence(output_schema["required"]):
        raise MechaError(
            f"命令 {name!r} 的 output_schema.required 必须是非空键名序列",
            kind="bad_command_output_schema",
            hint='output_schema={"type": "object", "required": ["ok", ...]}',
        )


@dataclass(frozen=True)
class CommandSpec:
    """命令声明（frozen）。**声明即投影**：handler 不在本结构里。

    ``parameters`` / ``output_schema`` 是 JSON-schema 风格映射；后者**必须**
    含 ``required``（回执必填键显式化，消 D-15）。本模块不解释 schema 语义
    （校验器归宿主），只保证契约字段存在且形状可判。

    构造即校验（``__post_init__``）：坏形状的声明根本造不出来——手写
    ``CommandSpec(...)`` 与走 ``define_command`` 走同一道门。
    """

    name: str
    description: str
    parameters: Mapping[str, object]
    output_schema: Mapping[str, object]
    side_effect: bool
    scope: tuple[str, ...] = ()
    estimate_sec: float | Callable[..., float] = 0.0
    cancel_supported: bool = False
    approval_required: bool = False

    def __post_init__(self) -> None:
        check_command_name(self.name)
        if not isinstance(self.description, str) or not self.description.strip():
            raise MechaError(
                f"命令 {self.name!r} 没有 description",
                kind="bad_command_description",
                hint="description 是使用者唯一的用法来源；空描述等于没有声明",
            )
        if not isinstance(self.parameters, Mapping):
            raise MechaError(
                f"命令 {self.name!r} 的 parameters 不是映射",
                kind="bad_command_parameters",
                hint="parameters 是 JSON-schema 风格映射：{参数名: {type, ...}}",
            )
        _check_output_schema(self.name, self.output_schema)
        if isinstance(self.scope, (str, bytes)) or not isinstance(self.scope, Sequence):
            raise MechaError(
                f"命令 {self.name!r} 的 scope 必须是字符串序列",
                kind="bad_command_scope",
                hint="scope 是声明字段（如 ('state',)）；框架不解释其领域含义",
            )
        if not (isinstance(self.estimate_sec, numbers.Real)
                or callable(self.estimate_sec)):
            raise MechaError(
                f"命令 {self.name!r} 的 estimate_sec 既不是数也不可调用",
                kind="bad_command_estimate",
                hint="estimate_sec 是秒数（float）或接收本次参数的 callable",
            )
        for flag in ("side_effect", "cancel_supported", "approval_required"):
            if not isinstance(getattr(self, flag), bool):
                raise MechaError(
                    f"命令 {self.name!r} 的 {flag} 必须是 bool",
                    kind="bad_command_flags",
                    hint="副作用/可取消/需审批是显式声明，不是注释约定",
                )


@dataclass
class CommandResult:
    """命令体回执：``ok`` 为假时 ``error`` 必须给 ``MechaError``（fail loud）。

    ``result_ref`` 是**不透明**的宿主结果引用（如 ``{"kind": "ml_run",
    "run_id": "..."}``）：核心只检查它是映射，不解释内容；它随审计事件进
    History，让"命令调用"与"宿主结果"可互引，而结果体仍留宿主侧。
    """

    ok: bool
    values: Mapping[str, object]
    error: MechaError | None = None
    result_ref: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if self.result_ref is not None and not isinstance(self.result_ref, Mapping):
            raise MechaError(
                f"CommandResult.result_ref 需要映射或 None，收到 "
                f"{type(self.result_ref).__name__}",
                kind="bad_command_result_ref",
                hint="result_ref 是宿主结果的不透明引用（如 run_id/locator）",
            )


def define_command(
    *,
    name: str,
    description: str,
    parameters: Mapping[str, object],
    output_schema: Mapping[str, object],
    side_effect: bool,
    scope: Sequence[str] = (),
    estimate_sec: float | Callable[..., float] = 0.0,
    cancel_supported: bool = False,
    approval_required: bool = False,
    banned_prefixes: tuple[str, ...] | None = None,
) -> CommandSpec:
    """构造即校验的具名入口（门在 ``CommandSpec.__post_init__`` 上）。"""
    if banned_prefixes is not None:
        check_command_name(name, banned_prefixes=banned_prefixes)
    return CommandSpec(
        name=name, description=description, parameters=parameters,
        output_schema=output_schema, side_effect=side_effect, scope=scope,  # type: ignore[arg-type]
        estimate_sec=estimate_sec, cancel_supported=cancel_supported,
        approval_required=approval_required,
    )


class CommandRegistry:
    """命令注册表：``register(spec, handler) → disposer``；``invoke`` 统一分派。

    注册返回 disposer（可逆副作用，dsh AGENTS.md:106）；同名重复注册即报错。
    若装配时给了 ``roles``，注册会同时声明 ``command:<name>`` 能力行并在
    dispose 时撤销（capability map 不留幽灵条目——与 Surface.register 同款纪律）。
    """

    def __init__(self, *, roles: RoleRegistry | None = None,
                 caller: str = "CommandRegistry.invoke") -> None:
        self._roles = roles
        self._caller = caller
        self._specs: dict[str, CommandSpec] = {}
        self._handlers: dict[str, Callable[..., object]] = {}

    # ---- 注册 ----

    def register(self, spec: CommandSpec, handler: Callable[..., object]) -> Disposer:
        if not isinstance(spec, CommandSpec):
            raise MechaError(
                f"注册命令必须给 CommandSpec，收到 {type(spec).__name__}",
                kind="bad_command_registration",
                hint="用 define_command(...) 构造声明（构造即校验）",
            )
        if not callable(handler):
            raise MechaError(
                f"命令 {spec.name!r} 的 handler 不可调用",
                kind="bad_command_registration",
                hint="handler 接收 (context=..., **声明参数) 并返回映射或 CommandResult",
            )
        if spec.name in self._specs:
            raise MechaError(
                f"命令 {spec.name!r} 重复注册",
                kind="duplicate_command",
                hint="同名先 dispose 再 register",
            )
        self._specs[spec.name] = spec
        self._handlers[spec.name] = handler
        dispose_role: Disposer | None = None
        if self._roles is not None:
            dispose_role = self._roles.register(
                f"command:{spec.name}", Role.CONSUMER, consumer=self._caller)

        def dispose() -> None:
            self._specs.pop(spec.name, None)
            self._handlers.pop(spec.name, None)
            if dispose_role is not None:
                dispose_role()

        return dispose

    # ---- 投影 ----

    def specs(self) -> list[CommandSpec]:
        """投影：只有声明，handler 绝不外露（同 tools.schemas 的白名单纪律）。"""
        return [self._specs[n] for n in sorted(self._specs)]

    def spec(self, name: str) -> CommandSpec | None:
        return self._specs.get(name)

    def names(self) -> list[str]:
        return sorted(self._specs)

    # ---- 调用 ----

    def estimate(self, name: str, args: Mapping[str, object] | None = None) -> float:
        """契约先行：取声明估时。callable 形态接收本次参数（不做域语义校验）。

        与 ``invoke`` 的不对称是刻意的：``invoke`` 把失败归一化成回执（跨边界
        面），``estimate`` 是库内查询面，未知命令与坏估算**直接抛** ``MechaError``
        （fail loud，与 ``Surface.query`` 同款）。
        """
        spec = self._require(name)
        est = spec.estimate_sec
        value = est(dict(args or {})) if callable(est) else est
        if not isinstance(value, numbers.Real):
            raise MechaError(
                f"命令 {name!r} 的 estimate_sec 返回 {type(value).__name__}，不是数",
                kind="bad_command_estimate",
                hint="估算必须回秒数（float）",
            )
        return float(value)

    def invoke(self, name: str, args: Mapping[str, object], *, context: object,
               gate: object | None = None,
               channel: object | None = None) -> dict:
        """统一调用口：契约校验 → 命令体 → 回执契约 →（有副作用则）Gate 审计。

        返回归一化回执（``is_error`` / ``value`` / ``error``）。任何失败都以
        ``ToolFailure`` 形状回来，不抛穿（宿主适配层只见一种形状）。

        参数纪律：``args`` 必须与 ``spec.parameters`` **双向吻合**——声明的必填
        键缺一即拒，未声明的键多一也拒（除非 ``parameters.additionalProperties``
        显式为真）。后者抓的是"声明漂移"：拼错的参数名不该被 handler 的
        ``**kwargs`` 静默吞掉。``RESERVED_ARGS``（reason / call_id）是框架抽给
        Gate 的调用元数据，命令体若要自己用必须在 ``parameters`` 里显式声明。
        """
        spec = self._specs.get(name)
        if spec is None:
            return _failure(UnknownOp(
                f"未知命令 {name!r}",
                kind="unknown_command",
                hint="已注册命令见 CommandRegistry.specs()；新命令先 register",
                suggest="、".join(self.names()) or "",
            ))
        meta = {k: args.get(k) for k in RESERVED_ARGS if k in args}
        declared = spec.parameters.get("required", [])
        declared_set = set(declared) if _is_name_sequence(declared) else set()
        body_args = {k: v for k, v in args.items()
                     if k not in RESERVED_ARGS or k in declared_set}
        try:
            self._check_args(spec, body_args)
            result = self._call_handler(spec, body_args, context)
        except MechaError as e:
            # 缺参/参数不符/命令体自报失败：命令体未产出回执 ⇒ 无审计可写。
            return _failure(e)
        error = None if result.ok else (result.error or MechaError(
            f"命令 {name!r} 回执 ok=False 却没给 error",
            kind="bad_command_result",
            hint="CommandResult(ok=False, values={}, error=MechaError(...))",
        ))
        payload = dict(result.values)
        if error is None:
            error = _output_error(spec, payload)
        # 审计在回执契约判定之后、返回之前：有副作用的命令哪怕回执不合契约，
        # 也已经动过宿主状态 ⇒ 必须留痕（否则最该被审计的失败反而无痕）。
        audit_error = self._audit(spec, gate, channel, meta, payload, error,
                                  result.result_ref)
        if error is not None:
            return _failure(error, payload)
        if audit_error is not None:
            return _failure(audit_error)
        return {"is_error": False, "value": payload}

    # ---- 内部 ----

    def _require(self, name: str) -> CommandSpec:
        spec = self._specs.get(name)
        if spec is None:
            raise UnknownOp(
                f"未知命令 {name!r}",
                kind="unknown_command",
                hint="已注册命令见 CommandRegistry.specs()",
            )
        return spec

    def _check_args(self, spec: CommandSpec, args: Mapping[str, object]) -> None:
        """参数契约守卫（fail loud，先于命令体）：必填键缺一即拒；多键也拒。

        ``"required"`` 缺失时才从 ``properties`` 的键取必填集；两者都无声明
        则退化为"不约束必填"，但**仍禁多余键**（除非 ``additionalProperties``
        显式为真——那是宿主显式表态"这个命令吃任意附加参数"）。
        """
        if not isinstance(args, Mapping):
            raise MechaError(
                f"命令 {spec.name!r} 的参数不是映射",
                kind="bad_command_arguments",
                hint="invoke(name, {参数名: 值}, ...)",
            )
        declared = spec.parameters.get("required")
        if not _is_name_sequence(declared):
            props = spec.parameters.get("properties")
            declared = list(props) if isinstance(props, Mapping) else []
        required = [str(k) for k in declared]
        missing = [k for k in required if k not in args]
        if missing:
            raise MechaError(
                f"命令 {spec.name!r} 缺必填参数 {missing}",
                kind="missing_arguments",
                hint="必填参数见该命令 parameters.required 声明",
            )
        if spec.parameters.get("additionalProperties") is True:
            return
        props = spec.parameters.get("properties")
        allowed = set(props) if isinstance(props, Mapping) else set(required)
        extra = [k for k in args if k not in allowed]
        if extra:
            raise MechaError(
                f"命令 {spec.name!r} 收到未声明的参数 {extra}",
                kind="missing_arguments",
                hint="参数名必须与 parameters 声明逐字吻合；"
                     "吃任意附加参数请在声明里写 additionalProperties: true",
            )

    def _call_handler(self, spec: CommandSpec, args: Mapping[str, object],
                      context: object) -> CommandResult:
        """调命令体并归一化：裸 TypeError 不许逃（接管报告 P1-16 同款）。"""
        handler = self._handlers[spec.name]
        try:
            raw = handler(context=context, **dict(args))
        except MechaError:
            raise
        except TypeError as e:
            raise MechaError(
                f"命令 {spec.name!r} 参数不符：{e}",
                kind="missing_arguments",
                hint="必填参数见该命令 parameters 声明；handler 只接收声明过的参数",
            ) from e
        if isinstance(raw, CommandResult):
            if not isinstance(raw.values, Mapping):
                raise MechaError(
                    f"命令 {spec.name!r} 的 CommandResult.values 不是映射",
                    kind="bad_command_result",
                    hint="values 是回执键值映射，键必须覆盖 output_schema.required",
                )
            return raw
        if isinstance(raw, Mapping):
            return CommandResult(ok=True, values=dict(raw))
        raise MechaError(
            f"命令 {spec.name!r} 的 handler 返回 {type(raw).__name__}",
            kind="bad_command_result",
            hint="handler 必须返回映射（回执键值）或 CommandResult",
        )

    def _audit(self, spec: CommandSpec, gate: object | None,
               channel: object | None, meta: Mapping[str, object],
               payload: Mapping[str, object], error: MechaError | None,
               result_ref: Mapping[str, object] | None = None
               ) -> MechaError | None:
        """有副作用的命令经 Gate 写 ``command.<name>`` 审计事件。

        只读命令**不碰 Gate**（gate 传了也不用，D-4 同款）。未注入 gate 时
        有副作用的命令直接拒（做了副作用却不留痕 = 不可审计）。
        """
        if not spec.side_effect:
            return None
        if gate is None:
            return MechaError(
                f"命令 {spec.name!r} 声明有副作用，但 invoke 未注入 gate",
                kind="missing_gate",
                hint="有副作用的命令必须经 Gate 留审计：invoke(..., gate=sw.gate, "
                     "channel=sw.channels['ai'])",
            )
        reason = str(meta.get("reason", "") or f"command {spec.name}")
        call_id = str(meta.get("call_id", "") or "")
        record = {
            "command": spec.name,
            "ok": error is None,
            "side_effect": True,
            "scope": list(spec.scope),
            "result_keys": sorted(payload),
            "error_kind": error.kind if error is not None else "",
            "error_message": error.message if error is not None else "",
        }
        if result_ref is not None:
            # 不透明引用：核心不解释，只保证可 JSON 化（值由宿主负责）。
            record["result_ref"] = dict(result_ref)
        try:
            gate.set(channel or _default_channel(spec.name),   # type: ignore[attr-defined]
                     f"{AUDIT_KEY_PREFIX}{spec.name}", record, reason, call_id)
        except MechaError as e:
            return MechaError(
                f"命令 {spec.name!r} 的审计事件被 Gate 拒绝：{e.message}",
                kind="command_audit_denied",
                hint="宿主 validator 需接受 'command.' 前缀的审计键；"
                     "或先由人类通道开闸（authority）",
                suggest=e.suggest,
            )
        return None


def _default_channel(name: str) -> object:
    """未显式给 channel 时的兜底：actor 取命令名，side 钉死 'ai'（最低写权）。

    刻意不猜 'human'：AI 不得借默认值拿到人类闸（硬纪律 4）。宿主应显式传
    ``Channel``（如 ``sw.channels['ai']``）；这里的兜底只为让"忘了传通道"
    的调用仍受写权门约束，而不是绕过它。
    """
    from .gate import Channel
    return Channel(f"command:{name}", "ai")


def _output_error(spec: CommandSpec, values: Mapping[str, object]) -> MechaError | None:
    """回执契约守卫：output_schema.required 的键缺一即拒（消 D-15）。"""
    required = list(spec.output_schema.get("required", []))
    missing = [k for k in required if k not in values]
    if missing:
        return MechaError(
            f"命令 {spec.name!r} 回执缺必填键 {missing}",
            kind="bad_command_output",
            hint="回执契约由 output_schema.required 声明（消 D-15）",
        )
    return None


def _failure(error: MechaError,
             payload: Mapping[str, object] | None = None) -> dict:
    """MechaError → ``ToolFailure {message, info}`` 形状的归一化失败回执。

    若命令体已产出回执（``payload``），失败回执里额外带 ``value``：宿主
    回执的引用字段（如 run_ids）不能在失败路径丢失，否则对账断链。
    """
    out = {"is_error": True, "error": ToolFailure(
        message=error.message, kind=error.kind, hint=error.hint,
        suggest=error.suggest).to_failure()}
    if payload is not None:
        out["value"] = dict(payload)
    return out


def command_signature_names(handler: Callable[..., object]) -> tuple[list[str], bool]:
    """读 handler 的形参名与是否吃 ``**kwargs``（供宿主侧声明对齐检查用）。

    handler 形状不合（内建函数等无签名可读）时返回 ``([], True)``——即"不做
    额外约束"，把判断留给运行时 TypeError 归一化，而不是在这里猜。
    """
    try:
        sig = inspect.signature(handler)
    except (TypeError, ValueError):
        return [], True
    names: list[str] = []
    var_kw = False
    for p in sig.parameters.values():
        if p.kind is inspect.Parameter.VAR_KEYWORD:
            var_kw = True
        elif p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        inspect.Parameter.KEYWORD_ONLY):
            names.append(p.name)
    return names, var_kw
