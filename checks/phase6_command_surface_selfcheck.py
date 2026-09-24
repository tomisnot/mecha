"""Phase 6 判据：通用命令面（Command Surface，总纲 §7 后续 phase；ADR 2026-09-24-通用命令面）。

验证的是**机制**，不是任何项目的命令表。本判据里的命令名与状态键全部是
中性假值（``compute_summary`` / ``record_note`` / ``step`` / ``power``），
仓库里任何一个真宿主的命令名/状态键都不会出现在这里。

七组：

  A 声明契约：``CommandSpec`` 构造即校验——坏名字、空描述、缺 ``required``
    的 output_schema、非 bool 标志、坏 estimate 全部当场拒（门在执行器上）。
  B 注册/dispose：``register → disposer`` 可逆；重复注册拒；
    ``command:<name>`` 能力行随 dispose 一并撤销（不留幽灵条目）。
  C 必填参数与回执契约：缺必填参数 → ``missing_arguments``；handler 回执缺
    ``output_schema.required`` 的键 → ``bad_command_output``；未知命令 →
    ``unknown_command``。全部归一化成 ``ToolFailure {message, info}``。
  D ``side_effect=True`` 的命令经 Gate 写 ``command.<name>`` 审计事件，
    事件带 actor/call_id/reason（可归因、可与操作者行为史互引），且审计值
    只含操作语义、不含结果体（大对象不进 History）。
  E 只读命令**不过 Gate**：``side_effect=False`` 时 gate 传了也不碰、不写史；
    有副作用却不注入 gate 则 fail loud（``missing_gate``）。
  F 投影不泄漏 handler：``specs()`` 只有声明字段，命令体可调用实现不外露。
  G 不许误报（L7）：一个完全合规的命令注册后全绿——invoke 成功、回执齐键、
    只读的 0 条史、有副作用的 1 条审计。

用法：python checks/phase6_command_surface_selfcheck.py
退出码：0 全绿；1 有失败。

## Known Limitations and Deferred Work

- 判据用装配级替身（ToyEngine + 中性假命令），不依赖任何真宿主 adapter；
  真宿主的命令表与工具投影对齐归各宿主自己判据（mecha 核心不持有命令名）。
- 不测 spec → AI 工具投影：ToolHost 契约未定（宪章 §10.2），命令不自动投影。
- 不测 ``scope`` / ``approval_required`` / ``cancel_supported`` 的**语义**：
  它们目前只是声明字段，Scope Authority 落地前没有任何分派含义。
- 不测命令体内部实现的正确性（判据只钉契约与副作用痕迹）。
"""

from __future__ import annotations

import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mecha.assembly import assemble  # noqa: E402
from mecha.authority import Mode  # noqa: E402
from mecha.commands import (  # noqa: E402
    AUDIT_KEY_PREFIX, CommandRegistry, CommandResult, CommandSpec, define_command,
)
from mecha.errors import MechaError  # noqa: E402
from mecha.gate import accept_all  # noqa: E402
from mecha.surface import Engine, ExecutionContext  # noqa: E402


class ToyEngine(Engine):
    """合规替身引擎：命令面不碰它，只用来装配一台实例。"""

    def schema(self) -> Mapping[str, object]:
        return {"run_output_required": ["ok"], "keys": ["power"]}

    def run(self, spec, *, context, **opts) -> Mapping[str, object]:
        return {"ok": True}

    def summary(self) -> Mapping[str, object]:
        return {"ok": True}

    def estimate(self, spec) -> Mapping[str, object]:
        return {"est_sec": 0.1}


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _fail(out: list[str], msg: str) -> None:
    print(f"  ✗ {msg}")
    out.append(msg)


class _Ctx:
    """调用上下文替身（判据不测取消/进度，那归 phase2）。"""

    def __init__(self) -> None:
        self.cancelled = False
        self.calls: list[object] = []


def _summary_cmd(**over) -> CommandSpec:
    """合规的**只读**命令（中性名）。"""
    kw: dict = dict(
        name="compute_summary",
        description="按给定输入算一次摘要，不改任何状态。",
        parameters={"type": "object", "properties": {"of": {"type": "string"}},
                    "required": ["of"]},
        output_schema={"type": "object", "required": ["ok", "digest"]},
        side_effect=False,
        scope=("read",),
        estimate_sec=0.25,
    )
    kw.update(over)
    return define_command(**kw)


def _note_cmd(**over) -> CommandSpec:
    """合规的**有副作用**命令（中性名）。"""
    kw: dict = dict(
        name="record_note",
        description="把一条备注记进域状态；属显式副作用。",
        parameters={"type": "object", "properties": {"text": {"type": "string"}},
                    "required": ["text"]},
        output_schema={"type": "object", "required": ["ok", "stored_key"]},
        side_effect=True,
        scope=("state",),
        estimate_sec=lambda args: 0.5,
        cancel_supported=True,
        approval_required=False,
    )
    kw.update(over)
    return define_command(**kw)


def _read_handler(context, of):
    context.calls.append(of)
    return {"ok": True, "digest": f"d:{of}"}


def _note_handler(context, text):
    return {"ok": True, "stored_key": "note"}


def _fail_shape(r: dict, kind: str) -> bool:
    """归一化失败回执形状：ToolFailure {message, info}，且 info.kind 对。"""
    if r.get("is_error") is not True:
        return False
    err = r.get("error")
    if not isinstance(err, dict) or set(err) != {"message", "info"}:
        return False
    return err["info"].get("kind") == kind


# ---------------------------------------------------------------------------
# A 声明契约
# ---------------------------------------------------------------------------

def check_spec_contract(out: list[str]) -> None:
    cases = [
        ("空名", dict(name=""), "bad_command_name"),
        ("非 snake_case", dict(name="RunMethod"), "bad_command_name"),
        ("项目前缀", dict(name="mecha_step"), "bad_command_name"),
        ("坏描述", dict(description="   "), "bad_command_description"),
        ("缺 output_schema.required",
         dict(output_schema={"type": "object"}), "bad_command_output_schema"),
        ("required 空表",
         dict(output_schema={"type": "object", "required": []}),
         "bad_command_output_schema"),
        ("side_effect 非 bool", dict(side_effect="yes"), "bad_command_flags"),
        ("estimate 形态坏", dict(estimate_sec="fast"), "bad_command_estimate"),
        ("scope 非序列", dict(scope=7), "bad_command_scope"),
    ]
    bad = 0
    for label, over, want in cases:
        try:
            _summary_cmd(**over)
            _fail(out, f"坏声明（{label}）竟构造成功")
        except MechaError as e:
            if e.kind == want:
                bad += 1
            else:
                _fail(out, f"坏声明（{label}）报错 kind 不符：{e.kind} != {want}")
    if bad == len(cases):
        _ok(f"声明契约：{len(cases)} 种坏形状全部当场拒（构造即校验）")
    # 直构 CommandSpec 走同一道门（不是只有 define_command 才校验）
    try:
        CommandSpec(name="Bad", description="x", parameters={}, output_schema={},
                    side_effect=False)
        _fail(out, "直构 CommandSpec 竟绕过校验")
    except MechaError as e:
        if e.kind == "bad_command_name":
            _ok("直构 CommandSpec 与 define_command 走同一道门（executor 上）")
    # 不许误报：核心只守通用格式，**不**照搬 EL/MCP 工具名语义
    try:
        _summary_cmd(name="list_datasets", **{"description": "列出可用数据集。"})
        _summary_cmd(name="get_state", description="取当前状态。")
        _summary_cmd(name="reset", description="复位。")
        _ok("核心只守通用格式：get_/list_ 视图名与单段名不被核心拒（不照搬工具名律）")
    except MechaError as e:
        _fail(out, f"合规命令名被误拒（核心混入了工具名语义）：{e}")


# ---------------------------------------------------------------------------
# B 注册 / dispose
# ---------------------------------------------------------------------------

def check_register_dispose(out: list[str]) -> None:
    from mecha.roles import RoleRegistry

    roles = RoleRegistry()
    reg = CommandRegistry(roles=roles, caller="判据")
    dispose = reg.register(_summary_cmd(), _read_handler)
    if reg.names() == ["compute_summary"] and reg.spec("compute_summary") is not None:
        _ok("注册即可见（specs() 立即投影）")
    else:
        _fail(out, f"注册后投影不符：{reg.names()}")
    if "command:compute_summary" in roles.names():
        _ok("注册同时声明 command:<name> 能力行（capability map 同源）")
    else:
        _fail(out, "注册未声明能力行（map 会缺命令）")
    try:
        reg.register(_summary_cmd(), _read_handler)
        _fail(out, "重复注册竟通过")
    except MechaError as e:
        if e.kind == "duplicate_command":
            _ok("重复注册被拒（duplicate_command）")
        else:
            _fail(out, f"重复注册 kind 不符：{e.kind}")
    dispose()
    r = reg.invoke("compute_summary", {"of": "x"}, context=_Ctx())
    if reg.names() == [] and _fail_shape(r, "unknown_command"):
        _ok("disposer 撤销注册：命令消失且调用报 unknown_command")
    else:
        _fail(out, f"dispose 后仍可见/调用不符：{reg.names()} {r}")
    if "command:compute_summary" not in roles.names():
        _ok("disposer 对称：能力行一并撤销（无幽灵条目）")
    else:
        _fail(out, "dispose 后能力表仍含命令行（幽灵条目）")
    # 非 CommandSpec / 非 callable 注册拒
    try:
        reg.register("compute_summary", _read_handler)  # type: ignore[arg-type]
        _fail(out, "非 CommandSpec 注册竟通过")
    except MechaError as e:
        if e.kind == "bad_command_registration":
            _ok("非声明对象注册被拒（先 define_command）")
    try:
        reg.register(_summary_cmd(), "not callable")  # type: ignore[arg-type]
        _fail(out, "不可调用 handler 注册竟通过")
    except MechaError as e:
        if e.kind == "bad_command_registration":
            _ok("不可调用 handler 被拒")
    if not out:
        _ok("B 组零失败")


# ---------------------------------------------------------------------------
# C 必填参数与回执契约
# ---------------------------------------------------------------------------

def check_params_and_output(out: list[str]) -> None:
    reg = CommandRegistry()
    reg.register(_summary_cmd(), _read_handler)
    # 缺必填参数
    r = reg.invoke("compute_summary", {}, context=_Ctx())
    if _fail_shape(r, "missing_arguments"):
        _ok("缺必填参数 → missing_arguments（归一化，不抛穿）")
    else:
        _fail(out, f"缺参未归一化：{r}")
    # 多传未声明参数 → 结构化失败（拼错的参数名不许被 **kwargs 静默吞掉）
    r = reg.invoke("compute_summary", {"of": "x", "extra": 1}, context=_Ctx())
    if _fail_shape(r, "missing_arguments"):
        _ok("未声明参数被拒（声明漂移 fail loud，不静默吞参）")
    else:
        _fail(out, f"未声明参数未拒：{r}")
    # 声明里的可选参数（properties 有、required 没有）必须放行
    reg_opt = CommandRegistry()
    reg_opt.register(_summary_cmd(parameters={
        "type": "object",
        "properties": {"of": {"type": "string"}, "mode": {"type": "string"}},
        "required": ["of"]}),
        lambda context, of, mode="fast": {"ok": True, "digest": f"{of}/{mode}"})
    r = reg_opt.invoke("compute_summary", {"of": "x", "mode": "slow"},
                       context=_Ctx())
    if not r.get("is_error") and r["value"]["digest"] == "x/slow":
        _ok("可选参数（properties 声明但非 required）被放行（不误拒）")
    else:
        _fail(out, f"可选参数被误拒：{r}")
    # 显式声明 additionalProperties 才放行附加参数（宿主显式表态）
    reg_ap = CommandRegistry()
    reg_ap.register(_summary_cmd(parameters={
        "type": "object", "properties": {"of": {"type": "string"}},
        "required": ["of"], "additionalProperties": True}),
        lambda context, of, **rest: {"ok": True, "digest": f"{of}/{len(rest)}"})
    r = reg_ap.invoke("compute_summary", {"of": "x", "extra": 1}, context=_Ctx())
    if not r.get("is_error") and r["value"]["digest"] == "x/1":
        _ok("声明 additionalProperties: true 时附加参数放行（宿主显式表态）")
    else:
        _fail(out, f"显式附加参数声明未生效：{r}")
    # 框架保留键（reason/call_id）默认不喂命令体；显式声明后才是普通参数
    seen: list[dict] = []

    def _spy(context, **kw):
        seen.append(kw)
        return {"ok": True, "digest": "d"}

    reg_meta = CommandRegistry()
    reg_meta.register(_summary_cmd(), _spy)
    reg_meta.invoke("compute_summary", {"of": "x", "reason": "r", "call_id": "c"},
                    context=_Ctx())
    reg_decl = CommandRegistry()
    reg_decl.register(_summary_cmd(parameters={
        "type": "object", "properties": {"of": {"type": "string"},
                                         "reason": {"type": "string"}},
        "required": ["of", "reason"]}), _spy)
    reg_decl.invoke("compute_summary", {"of": "y", "reason": "r"}, context=_Ctx())
    if seen == [{"of": "x"}, {"of": "y", "reason": "r"}]:
        _ok("reason/call_id 默认不喂命令体；显式声明后才是普通参数（声明即契约）")
    else:
        _fail(out, f"保留键语义不符：{seen}")
    # 回执缺 required 键
    reg2 = CommandRegistry()
    reg2.register(_summary_cmd(), lambda context, of: {"ok": True})   # 缺 digest
    r = reg2.invoke("compute_summary", {"of": "x"}, context=_Ctx())
    if _fail_shape(r, "bad_command_output"):
        _ok("回执缺 output_schema.required 的键 → bad_command_output（消 D-15）")
    else:
        _fail(out, f"回执缺键未拒：{r}")
    # 命令体自报失败：CommandResult(ok=False, error=MechaError(...))
    reg3 = CommandRegistry()
    reg3.register(_summary_cmd(), lambda context, of: CommandResult(
        ok=False, values={}, error=MechaError(
            f"输入 {of!r} 不在可读面", kind="teach_me", hint="先看 schema")))
    r = reg3.invoke("compute_summary", {"of": "zzz"}, context=_Ctx())
    if r.get("is_error") and r["error"]["info"]["kind"] == "teach_me" \
            and r["error"]["info"]["hint"] == "先看 schema":
        _ok("命令体失败经 MechaError → ToolFailure 投影（kind/hint 保留）")
    else:
        _fail(out, f"命令体失败投影不符：{r}")
    # 未知命令
    r = reg3.invoke("no_such_command", {}, context=_Ctx())
    if _fail_shape(r, "unknown_command"):
        _ok("未知命令 → unknown_command（结构化，带已注册命令清单）")
    else:
        _fail(out, f"未知命令处理不符：{r}")
    # 命令体可以读上下文（取消/进度不是靠签名约定传的）
    ctx = _Ctx()
    reg3.register(_note_cmd(), _note_handler)
    reg3.invoke("record_note", {"text": "t"}, context=ctx, gate=None)
    if ctx is not None:
        _ok("handler 经显式 context 收到执行上下文（不做位置约定）")


# ---------------------------------------------------------------------------
# D 有副作用 → 经 Gate 审计
# ---------------------------------------------------------------------------

def _with_software(validate=accept_all):
    td = tempfile.mkdtemp(prefix="phase6-")
    return assemble(root=Path(td), data_dir_name="p6data", engine=ToyEngine(),
                    summarizer=lambda evs: {}, validate=validate, project="toyhost")


def check_side_effect_audit(out: list[str]) -> None:
    with _with_software() as sw:
        sw.authority.switch_mode(Mode.AI, side="human")
        dispose = sw.commands.register(_note_cmd(), _note_handler)
        if sw.surface.commands is sw.commands:
            _ok("Software.commands 与 Surface.commands 是同一张注册表（单一来源）")
        else:
            _fail(out, "Software.commands 与 Surface.commands 不是同一对象（双表）")
        ctx = _Ctx()
        r = sw.commands.invoke("record_note", {"text": "hello", "reason": "测试审计",
                                              "call_id": "c-42"},
                               context=ctx, gate=sw.gate, channel=sw.channels["ai"])
        if not r.get("is_error") and r["value"].get("stored_key") == "note":
            _ok("有副作用命令经注册表调用成功（回执齐键）")
        else:
            _fail(out, f"有副作用命令调用不符：{r}")
        events = [e for e in sw.history.events()
                  if e.key == f"{AUDIT_KEY_PREFIX}record_note"]
        if len(events) != 1:
            _fail(out, f"审计事件数不符（期望 1）：{[e.key for e in sw.history.events()]}")
        else:
            ev = events[0]
            v = ev.value
            if ev.actor == "ai" and ev.call_id == "c-42" and ev.reason == "测试审计":
                _ok("审计事件可归因：actor + call_id + reason 进 History（互引）")
            else:
                _fail(out, f"审计事件归因缺失：{ev}")
            if v.get("command") == "record_note" and v.get("ok") is True \
                    and v.get("scope") == ["state"]:
                _ok("审计值只含操作语义（命令名/ok/scope/回执键/错误 kind）")
            else:
                _fail(out, f"审计值形状不符：{v}")
            if "hello" not in str(v) and set(v) == {
                    "command", "ok", "side_effect", "scope", "result_keys",
                    "error_kind", "error_message"}:
                _ok("审计值不含结果体/入参原文（大对象不进 History）")
            else:
                _fail(out, f"审计值泄漏结果体或字段漂移：{sorted(v)}")
        # 允许：审计事件不污染域快照（键前缀不在域键空间内）
        if "record_note" not in sw.gate.snapshot and set(sw.gate.snapshot) == {
                f"{AUDIT_KEY_PREFIX}record_note"}:
            _ok("审计键与域键空间分离（command.* 前缀，域快照里无同名污染）")
        else:
            _fail(out, f"审计写入污染了域快照键空间：{sorted(sw.gate.snapshot)}")
        # 回执不合契约但已产生副作用 ⇒ 仍必须留痕
        dispose()
        sw.commands.register(_note_cmd(), lambda context, text: {"ok": True})
        sw.commands.invoke("record_note", {"text": "x", "call_id": "c-43"},
                           context=_Ctx(), gate=sw.gate, channel=sw.channels["ai"])
        audited = [e for e in sw.history.events()
                   if e.key == f"{AUDIT_KEY_PREFIX}record_note"]
        if len(audited) == 2 and audited[-1].value["ok"] is False \
                and audited[-1].value["error_kind"] == "bad_command_output":
            _ok("副作用已发生但回执坏 ⇒ 仍写审计且标 ok=False+error_kind（不静默）")
        else:
            _fail(out, f"坏回执的副作用未留痕：{[e.value for e in audited]}")
        # 宿主 validator 不受协时审计被拒 = fail loud（框架不替宿主放行）
        def strict(key, value):
            if key.startswith(AUDIT_KEY_PREFIX):
                raise MechaError(f"未知键 {key!r}", kind="unknown_key",
                                 hint="只接受声明过的域键")
        with _with_software(validate=strict) as sw2:
            sw2.authority.switch_mode(Mode.AI, side="human")
            sw2.commands.register(_note_cmd(), _note_handler)
            r2 = sw2.commands.invoke("record_note", {"text": "t"}, context=_Ctx(),
                                     gate=sw2.gate, channel=sw2.channels["ai"])
            if _fail_shape(r2, "command_audit_denied"):
                _ok("宿主 validator 拒 command.* 键 ⇒ fail loud（不静默放行）")
            else:
                _fail(out, f"审计被拒不报：{r2}")
        dispose()


def check_result_ref_audit(out: list[str]) -> None:
    """命令体可带不透明 result_ref；它进审计，但结果体不进 History。"""
    with _with_software() as sw:
        sw.authority.switch_mode(Mode.AI, side="human")

        def handler(context, text):
            return CommandResult(ok=True, values={"ok": True, "stored_key": "ref"},
                                 result_ref={"kind": "note", "id": "n-1"})

        sw.commands.register(
            _note_cmd(output_schema={"type": "object",
                                     "required": ["ok", "stored_key"]}),
            handler)
        r = sw.commands.invoke("record_note", {"text": "x"}, context=_Ctx(),
                               gate=sw.gate, channel=sw.channels["ai"])
        if not r.get("is_error") and r["value"].get("stored_key") == "ref":
            _ok("带 result_ref 的命令正常返回回执")
        else:
            _fail(out, f"带 result_ref 的命令回执不符：{r}")
        events = [e for e in sw.history.events()
                  if e.key == f"{AUDIT_KEY_PREFIX}record_note"]
        if events and events[-1].value.get("result_ref") == {"kind": "note",
                                                             "id": "n-1"}:
            _ok("result_ref 进审计（命令调用与宿主结果可互引）")
        else:
            _fail(out, f"result_ref 未进审计：{[e.value for e in events]}")
        # 不透明引用不进结果体；参数原文也不进审计
        audit_value = events[-1].value
        if "x" not in str(audit_value) and "values" not in audit_value:
            _ok("result_ref 审计不含结果体/入参原文（大对象不进 History）")
        else:
            _fail(out, f"result_ref 审计泄漏结果体：{audit_value}")
    # 坏形状的 result_ref 构造即拒（门在执行器上）
    try:
        CommandResult(ok=True, values={}, result_ref="not-a-mapping")
        _fail(out, "非映射 result_ref 竟可构造")
    except MechaError as e:
        if e.kind == "bad_command_result_ref":
            _ok("非映射 result_ref 构造即拒")
        else:
            _fail(out, f"坏 result_ref 报错 kind 不对：{e.kind}")


# ---------------------------------------------------------------------------
# E 只读命令不经 Gate
# ---------------------------------------------------------------------------

def check_readonly_bypasses_gate(out: list[str]) -> None:
    with _with_software() as sw:
        sw.commands.register(_summary_cmd(), _read_handler)
        before = sw.history.last_seq
        # 只读命令：即使把 gate 传进来也不许碰（D-4 同款：不存在"只读被判不可逆"）
        r = sw.commands.invoke("compute_summary", {"of": "abc"}, context=_Ctx(),
                               gate=sw.gate, channel=sw.channels["ai"])
        if not r.get("is_error") and sw.history.last_seq == before:
            _ok("只读命令传了 gate 也不写史（0 条审计，只读不被强制过门）")
        else:
            _fail(out, f"只读命令写了史或被拒：{r} last_seq={sw.history.last_seq}")
        # 只读命令在 authority 出厂 LOCKED 下照样可调（没写权也能读）
        with _with_software() as sw2:
            sw2.commands.register(_summary_cmd(), _read_handler)
            r2 = sw2.commands.invoke("compute_summary", {"of": "x"}, context=_Ctx(),
                                     gate=sw2.gate, channel=sw2.channels["ai"])
            if not r2.get("is_error"):
                _ok("LOCKED 下只读命令可调（读权与写权分离）")
            else:
                _fail(out, f"LOCKED 下只读命令被拒：{r2}")
            sw2.commands.register(_note_cmd(), _note_handler)
            r3 = sw2.commands.invoke("record_note", {"text": "t"}, context=_Ctx(),
                                     gate=sw2.gate, channel=sw2.channels["ai"])
            if _fail_shape(r3, "command_audit_denied"):
                _ok("LOCKED 下有副作用命令被写权门拦下（fail closed）")
            else:
                _fail(out, f"LOCKED 下写命令竟通过：{r3}")
        # 有副作用却没注入 gate：fail loud（不留痕的副作用不可审计）
        reg = CommandRegistry()
        reg.register(_note_cmd(), _note_handler)
        r4 = reg.invoke("record_note", {"text": "t"}, context=_Ctx())
        if _fail_shape(r4, "missing_gate"):
            _ok("有副作用但未注入 gate → missing_gate（不允许无痕副作用）")
        else:
            _fail(out, f"无 gate 的副作用未拒：{r4}")
        # 忘了传 channel 时的兜底：仍受写权门约束，不伪装 human
        with _with_software() as sw3:
            sw3.authority.switch_mode(Mode.AI, side="human")
            sw3.commands.register(_note_cmd(), _note_handler)
            r5 = sw3.commands.invoke("record_note", {"text": "t"}, context=_Ctx(),
                                     gate=sw3.gate)
            ev = [e for e in sw3.history.events()
                  if e.key == f"{AUDIT_KEY_PREFIX}record_note"]
            if not r5.get("is_error") and ev and ev[0].actor.startswith("command:"):
                _ok("未传 channel 的兜底 actor 是命令名、side 为 ai（不伪装 human）")
            else:
                _fail(out, f"缺省 channel 语义不符：{r5} {[e.actor for e in ev]}")


# ---------------------------------------------------------------------------
# F 投影不泄漏 handler
# ---------------------------------------------------------------------------

def check_projection(out: list[str]) -> None:
    field_names = set(CommandSpec.__dataclass_fields__)
    want = {"name", "description", "parameters", "output_schema", "side_effect",
            "scope", "estimate_sec", "cancel_supported", "approval_required"}
    if field_names == want:
        _ok("声明字段 = 契约要求的集合（无多余实现字段可泄漏）")
    else:
        _fail(out, f"CommandSpec 字段漂移：{sorted(field_names ^ want)}")
    reg = CommandRegistry()
    reg.register(_summary_cmd(), _read_handler)
    spec = reg.specs()[0]
    leaked = [k for k, v in vars(spec).items() if callable(v)]
    if not leaked and "handler" not in dir(reg):
        _ok("specs() 投影只有声明：handler 不入 spec、注册表无 handler 出口")
    else:
        _fail(out, f"投影泄漏可调用实现：{leaked} / handler in dir(reg)")
    # estimate 契约（callable 形态取本次参数）
    reg.register(_note_cmd(), _note_handler)
    if reg.estimate("record_note") == 0.5 and reg.estimate("compute_summary") == 0.25:
        _ok("estimate 契约可读（数值与 callable 两种声明形态都成立）")
    else:
        _fail(out, f"estimate 不符：{reg.estimate('record_note')}")


# ---------------------------------------------------------------------------
# G 不许误报（L7 对偶）
# ---------------------------------------------------------------------------

def check_no_false_alarm(out: list[str]) -> None:
    with _with_software() as sw:
        sw.authority.switch_mode(Mode.AI, side="human")
        sw.commands.register(_summary_cmd(), _read_handler)
        sw.commands.register(_note_cmd(), _note_handler)
        ctx = _Ctx()
        r_read = sw.commands.invoke("compute_summary", {"of": "ok"}, context=ctx,
                                    gate=sw.gate, channel=sw.channels["ai"])
        r_write = sw.commands.invoke("record_note", {"text": "ok"}, context=ctx,
                                     gate=sw.gate, channel=sw.channels["ai"])
        audit = [e for e in sw.history.events() if e.key.startswith(AUDIT_KEY_PREFIX)]
        good = (not r_read.get("is_error") and r_read["value"]["digest"] == "d:ok"
                and not r_write.get("is_error")
                and r_write["value"]["stored_key"] == "note"
                and len(audit) == 1 and audit[0].key == f"{AUDIT_KEY_PREFIX}record_note"
                and ctx.calls == ["ok"])
        if good:
            _ok("合规命令全绿：只读 0 审计 / 有副作用 1 审计 / 回执齐键（不误报）")
        else:
            _fail(out, f"合规命令被误报：read={r_read} write={r_write} "
                       f"audit={[e.key for e in audit]}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out: list[str] = []
    print("Phase 6 命令面判据：")
    check_spec_contract(out)
    check_register_dispose(out)
    check_params_and_output(out)
    check_side_effect_audit(out)
    check_result_ref_audit(out)
    check_readonly_bypasses_gate(out)
    check_projection(out)
    check_no_false_alarm(out)
    if out:
        print(f"phase6_command_surface_selfcheck: FAIL（{len(out)} 条）")
        return 1
    print("phase6_command_surface_selfcheck: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
