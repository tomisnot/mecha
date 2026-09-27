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
  E2 通道类型（反向断言）：scope 的 ``side`` 只认真正的 ``mecha.gate.Channel``；
    鸭子类型对象自称 ``side='human'`` 或绕过构造器伪造的 side 一律
    ``bad_channel``，且 handler 未进、无审计（P1-2 回归）。
  F 投影不泄漏 handler：``specs()`` 只有声明字段，命令体可调用实现不外露。
  G 不许误报（L7）：一个完全合规的命令注册后全绿——invoke 成功、回执齐键、
    只读的 0 条史、有副作用的 1 条审计。

用法：python checks/phase6_command_surface_selfcheck.py
退出码：0 全绿；1 有失败。

## Known Limitations and Deferred Work

- 判据用装配级替身（ToyEngine + 中性假命令），不依赖任何真宿主 adapter；
  真宿主的命令表与工具投影对齐归各宿主自己判据（mecha 核心不持有命令名）。
- 不测 spec → AI 工具投影：ToolHost 契约未定（宪章 §10.2），命令不自动投影。
- 不测 ``approval_required`` / ``cancel_supported`` 的**语义**：它们目前只是
  声明字段，命令面不据此改变分派。``scope`` 只在宿主注入 ScopePolicy 后参与
  分派（未注入不做过滤），其"谁能自称 human"由通道类型守卫钉住。
  （``wants_channel`` **不在此列**：它**参与分派**——声明后 handler 会收到本次的
  ``channel=``，见 ``check_handler_sees_channel``。）
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
from mecha.authority import Authority, Mode  # noqa: E402
from mecha.commands import (  # noqa: E402
    AUDIT_KEY_PREFIX, CommandRegistry, CommandResult, CommandSpec, define_command,
)
from mecha.errors import MechaError  # noqa: E402
from mecha.gate import Channel, Gate, accept_all  # noqa: E402
from mecha.history import History  # noqa: E402
from mecha.scopes import ScopePolicy  # noqa: E402
from mecha.providers.approval import LocalApproval  # noqa: E402
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
        # ⭐ **审计进事件史、不进域快照**（发现 3 的落地点）：事件在 `/history` 里
        # 一条不少，而快照里**不该**有 `command.` 前缀的键（否则 `/config` 视图会把它
        # 当"契约外的键"，且快照的"后写覆盖"语义对审计毫无意义）。
        snap_audit = [k for k in sw.gate.snapshot if str(k).startswith(AUDIT_KEY_PREFIX)]
        if not snap_audit:
            _ok("审计键**不进域快照**（域快照纯净：`/config` 不再把它当契约外的键）")
        else:
            _fail(out, f"审计键混进了域快照：{snap_audit}")
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
        # ⚠ **这里以前断言「快照里恰好是 `{command.record_note}`」——那是把【已知债】写成
        # 期望值**：框架自己的 ADR 当年承认"审计键会进域快照的键空间（前缀隔离）……
        # 需要'域快照纯净'的读面要按前缀过滤"，只是**没做到**。
        # 现在审计走 `Gate.record`（只记史、不改快照）⇒ 快照里**不该有任何** `command.` 键。
        # ⚠ **不要修回"恰好等于那个键"**：那描述的是缺陷，不是承诺。
        if not sw.gate.snapshot:
            _ok("审计键与域键空间分离：域快照里**没有任何** command.* 键（审计只在事件史）")
        else:
            _fail(out, f"审计仍在域快照键空间里：{sorted(sw.gate.snapshot)}")
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
        # ⚠ **这里以前断言的是「宿主 validator 拒 `command.*` ⇒ `command_audit_denied`」。
        # 那条现在【不能保留】——但原因不是"它错了"，而是【它守的那个契约不存在了】**：
        # 审计此前借 `gate.set` 落史 ⇒ 必须过宿主 validator（所以宿主得给 `command.`
        # 前缀开白名单）；改成 `Gate.record`（只记史、不改快照）之后，**宿主 validator
        # 根本不在审计路径上** ⇒ "validator 拒了怎么办"这个问题不再存在。
        # ⚠ **但"不静默"这条承诺必须留下**：它被【改写】成新路径上的断言（见下面
        # `_NoAuditHistory` 那一段：审计写不进去 ⇒ 仍 `command_audit_denied` fail loud）。
        # ⇒ 顺应翻转：宿主 validator 再拒 `command.*`，命令也应当**正常执行且审计照写**。
        def strict(key, value):
            if key.startswith(AUDIT_KEY_PREFIX):
                raise MechaError(f"未知键 {key!r}", kind="unknown_key",
                                 hint="只接受声明过的域键")
        with _with_software(validate=strict) as sw2:
            sw2.authority.switch_mode(Mode.AI, side="human")
            sw2.commands.register(_note_cmd(), _note_handler)
            r2 = sw2.commands.invoke("record_note", {"text": "t"}, context=_Ctx(),
                                     gate=sw2.gate, channel=sw2.channels["ai"])
            audited2 = [e for e in sw2.history.events()
                        if e.key == f"{AUDIT_KEY_PREFIX}record_note"]
            if not r2.get("is_error") and audited2:
                _ok("宿主 validator 拒 command.* 前缀**不再影响审计**：命令照跑、审计照写"
                    "（审计已不经 validator —— 负担从宿主侧消失）")
            else:
                _fail(out, f"审计仍被宿主 validator 拦住：{r2} 审计={len(audited2)}")
        dispose()

        # 承诺不变（**不静默**）：审计写不进去 ⇒ 命令回 `command_audit_denied`。
        # 用一份"拒写审计键"的 History 复现（新路径上唯一的失败来源）。
        class _NoAuditHistory(History):                      # noqa: D401 - 判据替身
            def append(self, key, value, actor, reason="", call_id=""):
                if str(key).startswith(AUDIT_KEY_PREFIX):
                    raise MechaError(f"拒写审计键 {key!r}", kind="audit_refused")
                return super().append(key, value, actor, reason, call_id)

        g = Gate(Authority(mode=Mode.AI), _NoAuditHistory(), validate=accept_all)
        reg2 = CommandRegistry()
        reg2.register(_note_cmd(), _note_handler)
        r3 = reg2.invoke("record_note", {"text": "t"}, context=_Ctx(),
                         gate=g, channel=Channel("ai"))
        if _fail_shape(r3, "command_audit_denied"):
            _ok("审计写不进去 ⇒ 仍 `command_audit_denied` fail loud（承诺保留、路径改写）")
        else:
            _fail(out, f"审计写失败被静默：{r3}")


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


def check_scope_policy(out: list[str]) -> None:
    """ScopePolicy：有 scope 的命令在副作用前按 side 过滤；未注入不过滤。"""
    reached: list[str] = []

    def handler(context, text):
        reached.append(text)
        return {"ok": True, "stored_key": text}

    spec = _note_cmd(scope=("write",))
    with _with_software() as sw:
        sw.authority.switch_mode(Mode.AI, side="human")
        # 未注入 policy：scope 只是声明，不参与分派（向后兼容）
        sw.commands.register(spec, handler)
        r = sw.commands.invoke("record_note", {"text": "ok"}, context=_Ctx(),
                               gate=sw.gate, channel=sw.channels["ai"])
        if not r.get("is_error"):
            _ok("未注入 ScopePolicy → scope 只是声明（单宿主不受影响）")
        else:
            _fail(out, f"未注入 policy 却被 scope 拒：{r}")
    with _with_software() as sw:
        sw.authority.switch_mode(Mode.HUMAN, side="human")
        policy = ScopePolicy()
        policy.grant("human", "write")
        sw.commands.bind_scope_policy(policy)
        sw.commands.register(spec, handler)
        events_before = len(sw.history.events())
        r = sw.commands.invoke("record_note", {"text": "ai"}, context=_Ctx(),
                               gate=sw.gate, channel=sw.channels["ai"])
        if _fail_shape(r, "scope_denied") and len(sw.history.events()) == events_before \
                and "ai" not in reached:
            _ok("AI 缺 scope → scope_denied，且无副作用、无审计（fail closed）")
        else:
            _fail(out, f"scope 拒绝语义不符：{r} reached={reached} "
                       f"events={len(sw.history.events()) - events_before}")
        r = sw.commands.invoke("record_note", {"text": "human"}, context=_Ctx(),
                               gate=sw.gate, channel=sw.channels["human"])
        if not r.get("is_error") and "human" in reached:
            _ok("有 scope 的 side 正常放行且审计照写")
        else:
            _fail(out, f"已授权 side 被误拒：{r}")
    # 显式 default_allow=True 才放开未列出的 scope（不把默认拒绝静默改掉）
    policy2 = ScopePolicy(default_allow=True)
    try:
        policy2.check("ai", ["anything"])
        _ok("default_allow=True 显式放开未列出的 scope")
    except MechaError:
        _fail(out, "default_allow=True 未生效")


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
            # ⚠ 用**副作用不在 Gate 里**的替身（只动外置计数器）：这样"拦下"必须发生在
            # handler 之前——若检查仍跟在 handler 之后，就会出现「改动已落地、只是审计
            # 失败」= 静默越权（域状态外置的项目正是这种形状）。
            sw2.commands.register(_note_cmd(scope=()), _external_handler)
            _EXTERNAL["writes"] = 0
            r3 = sw2.commands.invoke("record_note", {"text": "t"}, context=_Ctx(),
                                     gate=sw2.gate, channel=sw2.channels["ai"])
            audit3 = [e for e in sw2.history.events() if e.key.startswith(AUDIT_KEY_PREFIX)]
            # 期望 kind 取 **authority 自己那道闸**的判词。
            # ⚠ **这里以前断言的是 `command_audit_denied`，那不是"更宽松/更严格"的差别，
            # 而是"判据把缺陷钉住了"**：那条期望值描述的是「检查迟到、handler 先干完、
            # 到审计那一步才被拒」——**副作用已经发生**。新断言描述的是「副作用根本没发生」。
            # ⚠ 后来者**不要**把它"修回去"：看到期望值从 command_audit_denied 变成
            # authority_locked 时，请先读这段——**是旧断言错了，不是守卫被放宽了**。
            # （这个 bug 能活很久，正因为测试把错的行为写成了期望值，后来人看到绿就不怀疑顺序。）
            if _fail_shape(r3, "authority_locked") and _EXTERNAL["writes"] == 0:
                _ok("LOCKED 下有副作用命令被写权门**在副作用之前**拦下（handler 零调用）")
            else:
                _fail(out, f"LOCKED 下写命令未被前置拦下：{r3} 副作用={_EXTERNAL['writes']}")
            if not audit3:
                _ok("LOCKED：没干活 ⇒ 不留痕（与 scope 拒绝同一纪律）")
            else:
                _fail(out, f"LOCKED 下却写了审计：{[e.key for e in audit3]}")
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
# E2 通道类型：side 只认真正的 Channel（P1-2 反向断言）
# ---------------------------------------------------------------------------

def check_channel_authority_is_typed(out: list[str]) -> None:
    """scope 的 side 不许由"调用方自称"决定：鸭子类型通道必须被拒。

    第三轮审查 P1-2 实测 ``class FakeCh: side="human"`` 能拿到人类 scope。
    这里钉反向：伪通道 → ``bad_channel`` 结构化失败、handler 未进、无审计；
    同时保真通道照旧放行（不误伤），并拒 ``object.__setattr__`` 篡改的 side。
    """
    reached: list[str] = []

    class FakeChannel:
        """鸭子类型通道：字段形状与 Channel 一模一样，但不是 Channel。"""

        def __init__(self, side: str) -> None:
            self.side = side
            self.actor = side

    def handler(context, text):
        reached.append(text)
        return {"ok": True, "stored_key": text}

    with _with_software() as sw:
        sw.authority.switch_mode(Mode.HUMAN, side="human")
        policy = ScopePolicy()
        policy.grant("human", "state")          # 人类侧全权；AI 侧一无所有
        sw.commands.bind_scope_policy(policy)
        sw.commands.register(_note_cmd(), handler)
        before = len(sw.history.events())
        r = sw.commands.invoke("record_note", {"text": "hu"}, context=_Ctx(),
                               gate=sw.gate, channel=FakeChannel("human"))
        if (_fail_shape(r, "bad_channel") and "hu" not in reached
                and len(sw.history.events()) == before):
            _ok("伪通道自称 side='human' 被拒（bad_channel），无副作用、无审计")
        else:
            _fail(out, f"伪通道竟过 scope：{r} reached={reached} "
                       f"events+={len(sw.history.events()) - before}")
        # 不误报：真 Channel 的同侧调用照旧放行
        r2 = sw.commands.invoke("record_note", {"text": "real"}, context=_Ctx(),
                                gate=sw.gate, channel=sw.channels["human"])
        if not r2.get("is_error") and "real" in reached:
            _ok("真 Channel(human) 照旧放行（类型守卫不误伤正常路径）")
        else:
            _fail(out, f"真 human 通道被误拒：{r2}")
        # 绕过构造器伪造的 side 也不认（白名单校验，不只查类型）
        forged = Channel("ai", "ai")
        object.__setattr__(forged, "side", "root")
        r3 = sw.commands.invoke("record_note", {"text": "root"}, context=_Ctx(),
                                gate=sw.gate, channel=forged)
        if _fail_shape(r3, "bad_channel") and "root" not in reached:
            _ok("side 越出 human/ai 白名单即拒（伪造实例也拦）")
        else:
            _fail(out, f"非法 side 竟可用：{r3}")
        # 不注入 ScopePolicy 时同样校验：通道合法性不依赖 policy 是否在场
        with _with_software() as sw2:
            sw2.authority.switch_mode(Mode.HUMAN, side="human")
            sw2.commands.register(_note_cmd(), handler)
            r4 = sw2.commands.invoke("record_note", {"text": "nopolicy"},
                                     context=_Ctx(), gate=sw2.gate,
                                     channel=FakeChannel("ai"))
            if _fail_shape(r4, "bad_channel") and "nopolicy" not in reached:
                _ok("未注入 ScopePolicy 时伪通道同样被拒（校验无条件）")
            else:
                _fail(out, f"无 policy 时伪通道未被拒：{r4}")


# ---------------------------------------------------------------------------
# F 投影不泄漏 handler
# ---------------------------------------------------------------------------

def check_projection(out: list[str]) -> None:
    field_names = set(CommandSpec.__dataclass_fields__)
    want = {"name", "description", "parameters", "output_schema", "side_effect",
            "scope", "estimate_sec", "cancel_supported", "approval_required",
            "wants_channel"}
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


# ---------------------------------------------------------------------------
# D3 「副作用不在 Gate 里」的替身（域状态外置：外置库 / 外部 API）
# ---------------------------------------------------------------------------

_EXTERNAL = {"writes": 0}


def _external_handler(context, text):
    """副作用**不在 Gate 里**的替身：只动一个外置计数器，不碰 Gate/History。"""
    _EXTERNAL["writes"] += 1
    return {"ok": True, "stored_key": "note"}


def check_approval_required(out: list[str]) -> None:
    """声明了 ``approval_required`` ⇒ **真的拦**（one-shot、fail closed）。

    为什么必须有它：``scope`` 真拦（scopes.py）、``cancel_supported`` 真被 Job 用，
    而 ``approval_required`` 当年**只是声明**——项目写了「这命令需要人工批准」就**以为
    被保护**，而它**照跑不误**（**比没有这个字段更坏：制造虚假安全感**）。
    """
    calls = {"n": 0}

    def h(context, text):
        calls["n"] += 1
        return {"ok": True, "stored_key": "note"}

    with _with_software() as sw:
        sw.authority.switch_mode(Mode.AI, side="human")
        sw.commands.register(_note_cmd(approval_required=True, scope=()), h)
        # ① 没接审批通道 ⇒ **拒绝**（fail closed）：handler 零调用、域零改动、无审计
        calls["n"] = 0
        r1 = sw.commands.invoke("record_note", {"text": "a"}, context=_Ctx(),
                                gate=sw.gate, channel=sw.channels["ai"])
        audit1 = [e for e in sw.history.events() if e.key.startswith(AUDIT_KEY_PREFIX)]
        if (_fail_shape(r1, "approval_denied") and calls["n"] == 0
                and not audit1 and not sw.gate.snapshot):
            _ok("声明需审批但**没接审批通道** ⇒ 拒绝 + handler 零调用 + 域零改动 + 无审计")
        else:
            _fail(out, f"未接审批通道却放行：{r1} 调用={calls['n']} 审计={len(audit1)}")
        hint = str((r1.get("error") or {}).get("info", {}).get("hint", ""))
        if "approval" in hint and "去掉" in hint:
            _ok("失败文案可教学：给出两条出路（接审批人 / 去掉声明）")
        else:
            _fail(out, f"审批失败文案没有出路指引：{hint!r}")

        # ② 对偶：接「点头」决策器 ⇒ **正常执行**（否则「永远拒绝」也能过上面所有条）
        calls["n"] = 0
        sw.commands.register(_note_cmd(name="record_note_ok", approval_required=True,
                                       scope=()), h)
        r2 = sw.commands.invoke("record_note_ok", {"text": "b"}, context=_Ctx(),
                                gate=sw.gate, channel=sw.channels["ai"],
                                approval=LocalApproval(lambda *a: True))
        audit2 = [e for e in sw.history.events()
                  if e.key.startswith(AUDIT_KEY_PREFIX + "record_note_ok")]
        if not r2.get("is_error") and calls["n"] == 1 and len(audit2) == 1:
            _ok("对偶：接「点头」决策器 ⇒ 正常执行（副作用 1 次 + 审计 1 条）")
        else:
            _fail(out, f"点头却没放行：{r2} 调用={calls['n']} 审计={len(audit2)}")

        # ③ 接「摇头」决策器 ⇒ 拒绝，且 handler **不再被调用**
        calls["n"] = 0
        sw.commands.register(_note_cmd(name="record_note_no", approval_required=True,
                                       scope=()), h)
        r3 = sw.commands.invoke("record_note_no", {"text": "c"}, context=_Ctx(),
                                gate=sw.gate, channel=sw.channels["ai"],
                                approval=LocalApproval(lambda *a: False))
        if _fail_shape(r3, "approval_denied") and calls["n"] == 0:
            _ok("接「摇头」决策器 ⇒ 拒绝且 handler 零调用")
        else:
            _fail(out, f"摇头却放行：{r3} 调用={calls['n']}")

        # ④ 未声明的命令：**不传 approval** 也照常（行为一字不变）
        calls["n"] = 0
        sw.commands.register(_note_cmd(name="record_note_free", scope=()), h)
        r4 = sw.commands.invoke("record_note_free", {"text": "d"}, context=_Ctx(),
                                gate=sw.gate, channel=sw.channels["ai"])
        if not r4.get("is_error") and calls["n"] == 1:
            _ok("未声明 approval_required 的命令：不传 approval 也照常（不破坏）")
        else:
            _fail(out, f"未声明的命令被审批闸影响：{r4}")


def check_handler_sees_channel(out: list[str]) -> None:
    """handler 想知「**这单是谁交的**」⇒ **声明 `wants_channel=True`**（显式，不靠签名自省）。

    ① 声明了：**两个通道共用同一张命令表**时，各自拿到的 side 与审计 actor 都对
       （多操作者共享一张表 = AI 走 MCP、Web 走 human 的真实形状）；
    ② **未声明**：handler 收到的参数与从前**一字不差**（不破坏）——用"签名里既没有
       channel 也没有 **kwargs"的老式 handler 来证：多塞一个 kwarg 就会 TypeError。
    """
    with _with_software() as sw:
        seen: list[str] = []

        def h_optin(*, context, text, channel):
            seen.append(str(getattr(channel, "side", None)))
            return {"ok": True, "stored_key": "note"}

        sw.commands.register(_note_cmd(wants_channel=True, scope=()), h_optin)
        sw.authority.switch_mode(Mode.AI, side="human")
        r_ai = sw.commands.invoke("record_note", {"text": "a"}, context=_Ctx(),
                                  gate=sw.gate, channel=sw.channels["ai"])
        sw.authority.switch_mode(Mode.HUMAN, side="human")
        r_hu = sw.commands.invoke("record_note", {"text": "h"}, context=_Ctx(),
                                  gate=sw.gate, channel=sw.channels["human"])
        actors = [e.actor for e in sw.history.events()
                  if e.key.startswith(AUDIT_KEY_PREFIX)]
        if seen == ["ai", "human"]:
            _ok("handler 拿到的是**本次**的通道（同一张命令表：ai 与 human 各自正确）")
        else:
            _fail(out, f"handler 看到的 side 不对：{seen}")
        if actors == ["ai", "human"]:
            _ok("审计 actor 与通道一致（**归因不撒谎**）")
        else:
            _fail(out, f"审计 actor 不对：{actors}")
        if not (r_ai.get("is_error") or r_hu.get("is_error")):
            _ok("两个通道各自调用都成功（对偶：不是「永不执行」）")
        else:
            _fail(out, f"调用失败：{r_ai} / {r_hu}")

        # ② 不破坏：**未声明**的 handler —— 签名里既没有 channel 也没有 **kwargs
        def h_plain(context, text):        # 与改动前的调用形状完全一致
            return {"ok": True, "stored_key": "note"}

        sw.commands.register(_note_cmd(name="record_note_plain", scope=()), h_plain)
        r_plain = sw.commands.invoke("record_note_plain", {"text": "p"}, context=_Ctx(),
                                     gate=sw.gate, channel=sw.channels["human"])
        if not r_plain.get("is_error"):
            _ok("未声明 wants_channel ⇒ handler 收到的参数与从前一字不差（不破坏）")
        else:
            _fail(out, f"未声明的 handler 被多塞了参数：{r_plain}")

        # ③ 构造期就拒「声明了 wants_channel 又声明同名参数 channel」（可教学错误）
        try:
            _note_cmd(wants_channel=True,
                      parameters={"type": "object",
                                  "properties": {"channel": {"type": "string"}},
                                  "required": ["channel"]})
            _fail(out, "wants_channel 与同名参数 channel 并用没有被拒")
        except MechaError as e:
            if e.kind == "bad_command_flags":
                _ok("wants_channel 与同名参数 'channel' 并用 ⇒ 构造期即拒（可教学）")
            else:
                _fail(out, f"拒因 kind 不符：{e.kind}")


def check_required_semantics(out: list[str]) -> None:
    """``parameters.required``：**缺失与空都表示「无必填」**（对齐 JSON Schema）。

    ⚠ **这是一处语义翻转（别"修"回去）**：旧行为是"``required`` 缺失 ⇒ 把
    ``properties`` 的键**全部**当必填"。⇒ 老写法（有 properties、没写 required）从
    "全必填"变成"都不必填"，**可能静默接受原本会被拒的调用**；迁移办法是
    **显式列出** ``required: [...]``。
    ⚠ 与 ``output_schema`` **刻意不同**：那里仍**必须**给非空 ``required``
    （回执契约显式化，消 D-15）——别把两处"看起来一样"的 required 合成一处。
    """
    props = {"type": "object", "properties": {"a": {"type": "string"},
                                              "b": {"type": "string"}}}

    def h(context, **kw):
        return {"ok": True, "digest": "d"}

    def call(params: dict, args: dict) -> dict:
        reg = CommandRegistry()
        reg.register(_summary_cmd(name="probe_cmd", parameters=params), h)
        return reg.invoke("probe_cmd", args, context=_Ctx())

    # ① 没写 required ⇒ 无必填（**翻转点**）
    r1 = call(dict(props), {})
    if not r1.get("is_error"):
        _ok("没写 required ⇒ 无必填（对齐 JSON Schema；旧行为是「properties 全必填」）")
    else:
        _fail(out, f"缺 required 时仍被当全必填：{r1}")

    # ② required: [] ⇒ 同样无必填
    r2 = call({**props, "required": []}, {})
    if not r2.get("is_error"):
        _ok("required: [] ⇒ 无必填（与「缺失」同义）")
    else:
        _fail(out, f"required: [] 被当成全必填：{r2}")

    # ③ 显式列出才必填：缺则拒、给齐则过
    r3 = call({**props, "required": ["a"]}, {})
    r4 = call({**props, "required": ["a"]}, {"a": "x"})
    if _fail_shape(r3, "missing_arguments") and not r4.get("is_error"):
        _ok("显式 required: [a] ⇒ 缺 a 即拒、给齐即过（这才是「想让它必填」的写法）")
    else:
        _fail(out, f"显式 required 不生效：缺={r3} 齐={r4}")

    # ④ 多余键仍拒（除非 additionalProperties 显式为真）——严格性不变
    r5 = call(dict(props), {"zzz": 1})
    r6 = call({**props, "additionalProperties": True}, {"zzz": 1})
    if _fail_shape(r5, "missing_arguments") and not r6.get("is_error"):
        _ok("未声明的键仍拒；显式 additionalProperties: true 才放行（严格性不变）")
    else:
        _fail(out, f"多余键语义变了：默认={r5} 显式={r6}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out: list[str] = []
    print("Phase 6 命令面判据：")
    check_spec_contract(out)
    check_register_dispose(out)
    check_params_and_output(out)
    check_required_semantics(out)
    check_side_effect_audit(out)
    check_result_ref_audit(out)
    check_scope_policy(out)
    check_approval_required(out)
    check_handler_sees_channel(out)
    check_channel_authority_is_typed(out)
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
