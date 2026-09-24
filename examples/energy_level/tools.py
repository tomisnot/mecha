"""EL 的 AI 工具面：把能力包装成 dsh 原生形态的工具（Phase 4 判据的落地）。

命名守 Phase 4 守卫（动词_宾语、无视图名、无前缀）；每个 description 只含
任务概念（L4），不含框架/实现词汇；output.schema 显式声明 required 键。

工具经 Gate 写、经 Surface 跑——**没有一条路径绕过写入门**（洞1/洞2 的
Phase 5 侧证：AI 拿到的就是这些工具，物理演化只有 run_sim→Surface.run 一条）。

## Known Limitations and Deferred Work

- 工具函数是纯闭包工厂（build_tools 返回注册进宿主 ctx.tools 的形状）；真
  dsh defineTool 适配在部署接线时做，这里形状已对齐（ToolRegistry.register 可直挂）。
- run_sim 同步返回摘要（不含大数组 pops）；要完整演化数据走 artifact 指针，
  等 Jobs/Spill 的 dsh Provider 接线后再开（避免现在塞一个没人消费的大回执）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Callable

from mecha.authority import Authority, Mode
from mecha.errors import MechaError
from mecha.gate import Channel, Gate
from mecha.history import History
from mecha.providers.codeact import Action, CodeAct
from mecha.providers.jobs import JobRegistry, JobState
from mecha.surface import EntryKind, ExecutionContext, Surface
from mecha.tools import CURRENT_CALL_ID, ToolRegistry, define_tool

from .engine import ElEngine

# —— 任务概念词汇（description 只准用这些，禁框架内部词）——
# 每个工具的 execute 闭包捕获 (gate, surface, engine, channel)。

#: run_actions 声明的只读查询名（**单一来源**：description 由它生成、Surface
#: QUERY 注册由它驱动、判据按它逐个验真——审查②a：声明与能力面不许两套账）。
#: CodeAct 内置 op（estimate/summary/run/schema）不在此列，见 providers/codeact。
#: get_waves 是纯查询项（②b：读不占顶层工具，走 Surface QUERY），注册时走独立
#: 函数而非复用工具 execute。
DOMAIN_QUERY_OPS = ("read_config", "query_params", "preview_run", "query_levels",
                    "get_waves")

#: set_drive 的 atom 参数 → 写回的配置键（②b，ADR 2026-09-23-EL驱动手感）。
_WAVE_KEY = {"single": "dynamic.waves_single",
             "A": "dynamic.waves_A", "B": "dynamic.waves_B"}


def build_tool_registry(engine: ElEngine, gate: Gate, surface: Surface,
                        authority: Authority, channel: Channel,
                        jobs: JobRegistry) -> ToolRegistry:
    """装配 EL 的工具面。channel 决定 actor（AI 侧恒为 'ai'，改不了）。

    jobs 必填（显式装配，不默认建）——**单一注册表实例**由宿主在装配点建好
    传进来（消 D-21：两处各建 runner 会互相误翻 interrupted）。
    """
    reg = ToolRegistry()
    schema = engine.schema()
    settable = list(schema["settable_keys"])

    def _set(key: str, value):
        # call_id 从请求作用域读（审查 2.7）：hub 的 MCP 层在 execute 前 set，
        # 把这次工具调用与 dsh 行为史钉在一起（总纲 §5.2②互引）。
        gate.set(channel, key, value, reason="ai tool",
                 call_id=CURRENT_CALL_ID.get())
        return {"ok": True, "key": key, "applied": True}

    # ---- set_config ----
    reg.register(define_tool(
        name="set_config",
        description=(
            "设置一个配置键的值。键必须是可写配置键（见 query_params 的清单）。"
            "改引擎内部常量会被拒。设置后立即可被 estimate_run/preview_run/run_sim 读到。"
        ),
        parameters={"key": {"type": "string", "description": "可写配置键，如 dynamic.t_max"},
                    "value": {"description": "新值（任意 JSON 形态），类型须匹配该键（见 query_params）"}},
        output_schema={"type": "object", "required": ["ok", "key"]},
        execute=lambda key, value: _set(key, value),
        banned_words=("gate", "choke", "读回面", "session", "channel"),
    ))

    # ---- read_config ----
    reg.register(define_tool(
        name="read_config",
        description="读取当前所有已设置的配置键与其值（快照）。用于确认改动是否生效。",
        parameters={},
        output_schema={"type": "object", "required": ["ok", "config"]},
        execute=lambda: {"ok": True, "config": gate.snapshot},
        banned_words=("gate", "snapshot 内部", "journal"),
    ))

    # ---- query_params ----
    reg.register(define_tool(
        name="query_params",
        description=(
            "列出所有可写配置键的元数据：类型、单位、值域、枚举、说明。"
            "写 set_config 前先查这里，避免类型/枚举错。"
        ),
        parameters={},
        output_schema={"type": "object", "required": ["ok", "keys"]},
        execute=lambda: {
            "ok": True,
            "keys": {k: {kk: vv for kk, vv in v.items()
                         if kk in ("type", "unit", "lo", "hi", "enum", "desc", "default")}
                     for k, v in dict(schema["config"]).items()
                     if k in settable}
            | {k: {kk: vv for kk, vv in v.items()
                   if kk in ("type", "unit", "lo", "hi", "desc")}
               for k, v in dict(schema["solve"]).items()},
        },
        banned_words=("registry", "scan", "AST"),
    ))

    # ---- estimate_run ----
    reg.register(define_tool(
        name="estimate_run",
        description=(
            "预估按当前配置跑一次要花多久、规模多大（不真正演化）。"
            "先看它决定要不要调小基组或时长。"
        ),
        parameters={},
        output_schema={"type": "object", "required": ["ok", "est_sec"]},
        execute=lambda: {"ok": True, **surface.estimate(gate.snapshot)},
        banned_words=("cost.py", "标定", "backend"),
    ))

    # ---- preview_run ----
    reg.register(define_tool(
        name="preview_run",
        description=(
            "预览当前配置会驱动哪些跃迁、基组够不够（不演化）。"
            "若报缺组，先补 groups.list 再 run_sim，否则结果失真。"
        ),
        parameters={},
        output_schema={"type": "object", "required": ["ok", "basis_enough"]},
        execute=lambda: {"ok": True, **engine.preview(gate.snapshot)},
        banned_words=("collect_transitions", "build_states"),
    ))

    # ---- run_sim ----
    reg.register(define_tool(
        name="run_sim",
        description=(
            "按当前配置真正跑一次演化，返回终态布居摘要（top 态 + 平均布居）。"
            "跑前建议先 estimate_run 看耗时、preview_run 看基组是否完整。"
        ),
        parameters={"cancel_after_sec": {"type": "number",
                    "description": "可选：超过此秒数自动请求取消"}},
        output_schema={"type": "object", "required": ["ok", "method"]},
        execute=lambda cancel_after_sec=None: _run_sim(
            surface, gate, cancel_after_sec),
        banned_words=("evolve_run", "progress_arr", "engine_kwargs"),
    ))

    # ---- query_levels ----
    reg.register(define_tool(
        name="query_levels",
        description=(
            "列出当前基组解析出的量子态（label + 身份键 + 能量 MHz）。"
            "设初态 dynamic.psi_single 用的身份键就从这里取，别猜索引。"
        ),
        parameters={},
        output_schema={"type": "object", "required": ["ok", "n_states", "states"]},
        execute=lambda: {"ok": True, **engine.list_levels(gate.snapshot)},
        banned_words=("base_sublevels", "make_levels"),
    ))

    # ---- submit_job / read_job / cancel_job（异步长任务，审查 §1.2①）----
    # 单一注册表实例由宿主传入（消 D-21）；read_job 合一"查单个/列全部"
    # ——枚举是投影不是工具（命名守卫禁 list_/get_ 前缀）。
    reg.register(define_tool(
        name="submit_job",
        description=(
            "把当前配置提交为后台演化任务，立即返回任务号。长任务（两体/多体、"
            "大基组）用它代替 run_sim——不必让调用方干等。用 read_job 轮询进度，"
            "done 后 result 即 run_sim 同款摘要。提交时刻的配置被固定，之后的"
            "改动不影响在飞任务。"
        ),
        parameters={},
        output_schema={"type": "object", "required": ["ok", "job_id"]},
        execute=lambda: _submit_job(jobs, surface, gate),
        banned_words=("evolve_run", "队列", "worker"),
    ))
    reg.register(define_tool(
        name="read_job",
        description=(
            "查后台任务状态：给 job_id 查单个（done 时含 result 摘要，failed "
            "时含 error）；不给 job_id 返回全部任务的状态清单。"
        ),
        parameters={"job_id": {"type": "string",
                    "description": "submit_job 返回的任务号；省略则列全部"}},
        output_schema={"type": "object", "required": ["ok"]},
        execute=lambda job_id=None: _read_job(jobs, job_id),
        banned_words=("registry", "轮询"),
    ))
    reg.register(define_tool(
        name="cancel_job",
        description=(
            "请求取消后台任务（在下一个计算步边界生效；已结束的取消无效果但"
            "不报错，状态如实返回）。任务号不存在会被拒。"
        ),
        parameters={"job_id": {"type": "string", "description": "要取消的任务号"}},
        output_schema={"type": "object", "required": ["ok", "job_id"]},
        execute=lambda job_id: _cancel_job(jobs, job_id),
        banned_words=("线程", "interrupt"),
    ))

    # ---- set_drive（②b：驱动手感，ADR 2026-09-23-EL驱动手感）----
    # 换算在 ElEngine.drive_wave（引擎线程，cache 亲和）；这里只"读现有波 →
    # 追加/替换 → 经 gate.set 写回单键"——门不旁路，与其余写工具同一条路。
    reg.register(define_tool(
        name="set_drive",
        description=(
            "给一对量子态之间加一个驱动场（比手搓波形清单省事）。src/tgt 用 "
            "query_levels 返回的身份键。强度二选一：rabi_mhz（拉比频率，物理"
            "自然语言，会自动换算成光强）或 intensity_mwcm2（直接给光强）。"
            "时长用 pulse='pi'/'pi2'（按拉比频率自动定时长）或 duration_ns 显式"
            "给。偏振 pol 取 π/σ+/σ-。默认追加到现有驱动清单，mode='replace' "
            "则整组替换。两体演化用 atom='A'/'B' 分别设两侧驱动。设完可 run_sim 演化。"
        ),
        parameters={
            "src": {"description": "源态身份键（query_levels 的 key）"},
            "tgt": {"description": "目标态身份键（query_levels 的 key）"},
            "rabi_mhz": {"type": "number", "description": "拉比频率 MHz（与 intensity_mwcm2 二选一）"},
            "intensity_mwcm2": {"type": "number", "description": "光强 mW/cm²（与 rabi_mhz 二选一）"},
            "pol": {"type": "string", "description": "偏振：π / σ+ / σ-（默认 π）"},
            "pulse": {"type": "string", "description": "脉冲形状 pi / pi2（自动定时长）；省略则用 duration_ns"},
            "duration_ns": {"type": "number", "description": "脉冲时长 ns（pulse 省略时必填）"},
            "detuning_mhz": {"type": "number", "description": "相对共振的失谐 MHz（默认 0）"},
            "mode": {"type": "string", "description": "append（默认追加）/ replace（整组替换）"},
            "atom": {"type": "string", "description": "单体 single（默认）；两体 A / B"},
            "name": {"type": "string", "description": "波形名（默认 drive；多条波时用可辨的名字）"},
        },
        output_schema={"type": "object", "required": ["ok", "key", "wave"]},
        execute=lambda src, tgt, rabi_mhz=None, intensity_mwcm2=None,
        pol="π", pulse="", duration_ns=None, detuning_mhz=0.0,
        mode="append", atom="single", name="drive": _set_drive(
            engine, gate, channel, src, tgt, pol=pol, rabi_mhz=rabi_mhz,
            intensity_mwcm2=intensity_mwcm2, pulse=pulse,
            duration_ns=duration_ns, detuning_mhz=detuning_mhz, mode=mode,
            atom=atom, name=name),
        banned_words=("gate", "dipole", "dme", "serialize", "wave_list 结构"),
    ))

    # ---- run_actions（CodeAct 编排，审查 §1.2②：机制已建、消费方接上）----
    # 只读查询 op 的真身 = Surface 的 QUERY 注册项（审查②a：description 里
    # "或已注册的只读查询名"必须为真）。注册驱动自 DOMAIN_QUERY_OPS 单一来源，
    # 分派直接复用上面各工具已注册的 execute（一份实现两处用，不造第二真值）。
    # get_waves 是纯查询项（②b：读不占顶层工具），无对应工具，注册独立闭包。
    for _qn in DOMAIN_QUERY_OPS:
        if _qn == "get_waves":
            surface.register(EntryKind.QUERY, _qn,
                             lambda atom="single": _get_waves(gate, atom),
                             called_by="framework:CodeAct.run_actions")
            continue
        _tool = reg.get(_qn)
        surface.register(EntryKind.QUERY, _qn, _tool.execute,
                         called_by="framework:CodeAct.run_actions")

    reg.register(define_tool(
        name="run_actions",
        description=(
            "一次提交一串动作按序执行（省往返）。每步是 {op, args}：op 可为 "
            "estimate（探规模）、summary（读引擎概况）、run（跑一次演化，args "
            "带 spec），或只读查询名（" + " / ".join(DOMAIN_QUERY_OPS) + "）。"
            "任一步失败即停在该步并如实返回失败原因，之前的步不回滚。"
            "每步都留可追溯记录。"
        ),
        parameters={"actions": {"type": "array",
                    "description": "动作列表，每项 {\"op\": 名称, \"args\": 对象}"},
                    "spec": {"type": "object",
                             "description": "run/estimate 缺省 spec 时用当前配置"}},
        output_schema={"type": "object", "required": ["ok", "steps", "stopped_at"]},
        execute=lambda actions, spec=None: _run_actions(
            surface, gate, channel, actions, spec),
        banned_words=("eval", "解释器", "dispatch"),
    ))

    return reg


def _run_actions(surface: Surface, gate: Gate,
                 channel: Channel, actions, spec=None):
    """把一串结构化动作交给 CodeAct 编排（逐子调用记日志，可归因）。

    **审计落一份进程内临时 History，不混进域状态史**（总纲 §5.2②：域状态史
    与操作者行为史是两份资产）。若把 `codeact.N` 写进域史，Gate 重启折叠快照
    会把这些审计事件当域状态吃进去 ⇒ read_config 出噪声、存档 load 被 validate
    当场拒。操作者行为史归宿主（dsh 按 call_id 记），这里逐步随回执如实返回。
    """
    if not isinstance(actions, (list, tuple)):
        raise MechaError("actions 必须是列表", kind="bad_action",
                         hint='形如 [{"op": "estimate"}, {"op": "run"}]')
    acts = []
    for a in actions:
        if not isinstance(a, Mapping) or "op" not in a:
            raise MechaError(f"动作项 {a!r} 缺 op", kind="bad_action",
                             hint='每项须是 {"op": 名称, "args": 对象}')
        args = dict(a.get("args") or {})
        if a["op"] in ("run", "estimate") and "spec" not in args:
            args["spec"] = dict(spec) if spec else dict(gate.snapshot)
        acts.append(Action(op=str(a["op"]), args=args))
    actor = channel.actor                      # 恒为 'ai'（通道钉死，改不了）
    audit = History()                          # 临时审计史（不进域史，见 docstring）
    codeact = CodeAct(surface, audit, lambda: actor)
    res = codeact.run(acts, call_id=CURRENT_CALL_ID.get())
    return {"ok": res.stopped_at == -1,
            "steps": [{"index": s.index, "op": s.op, "ok": s.ok,
                       "detail": s.detail} for s in res.steps],
            "outputs": res.outputs,
            "stopped_at": res.stopped_at}


def _submit_job(jobs: JobRegistry, surface: Surface, gate: Gate):
    snap = dict(gate.snapshot)     # 提交时刻固定配置（在飞任务不受后续改动影响）

    def _work(ctx: ExecutionContext):
        return surface.run(snap, context=ctx)

    job = jobs.submit(_work)
    return {"ok": True, "job_id": job.id, "state": job.state.value}


def _read_job(jobs: JobRegistry, job_id):
    if job_id:
        job = jobs.get(job_id)     # 未知任务号抛 unknown_job（fail loud）
        out = {"ok": True, **job.status()}
        if job.state is JobState.DONE:
            out["result"] = job.result
        elif job.state is JobState.FAILED:
            out["error"] = job.error
        return out
    return {"ok": True, "jobs": jobs.list()}


def _cancel_job(jobs: JobRegistry, job_id):
    jobs.cancel(job_id)            # 未知任务号抛 unknown_job
    return {"ok": True, "job_id": job_id,
            "state": jobs.get(job_id).state.value}


def _run_sim(surface: Surface, gate: Gate, cancel_after_sec):
    ctx = ExecutionContext()
    timer = None
    if cancel_after_sec:
        import threading
        timer = threading.Timer(float(cancel_after_sec), ctx.request_cancel)
        timer.start()
    try:
        result = surface.run(gate.snapshot, context=ctx)
    finally:
        if timer is not None:
            timer.cancel()
    return result


def _set_drive(engine: ElEngine, gate: Gate, channel: Channel, src, tgt, **kw):
    """set_drive：换算在引擎线程（cache 亲和），写回经 gate（门不旁路）。

    atom 决定写哪个键；append 读现有波再追加、replace 整组替换。换算失败
    （禁戒跃迁 / 身份键不在基组 / 强度时长没给全）抛 InvalidSpec，工具层
    如实回错（L4 可教学）。
    """
    atom = kw.pop("atom", "single")
    mode = kw.pop("mode", "append")
    key = _WAVE_KEY.get(atom)
    if key is None:
        raise MechaError(f"atom={atom!r} 非法", kind="bad_drive",
                         hint="atom 取 single / A / B")
    wave = engine.drive_wave(gate.snapshot, src=src, tgt=tgt, **kw)
    cur = list(gate.snapshot.get(key) or [])
    waves = ([dict(w) for w in cur] if mode == "append" else []) + [wave]
    gate.set(channel, key, waves, reason="ai set_drive",
             call_id=CURRENT_CALL_ID.get())
    return {"ok": True, "key": key, "wave": wave, "n_waves": len(waves)}


def _get_waves(gate: Gate, atom: str = "single"):
    """get_waves：读某原子侧的驱动波摘要（不内联大数组）。

    纯读快照里的 wave_list，逐波回 name/频率/光强/偏振/包络形状/时长。要完整
    演化数据走 run_sim 回执，要原始波形对象是 GUI 的事——查询面只给可概览的摘要。
    """
    key = _WAVE_KEY.get(atom)
    if key is None:
        raise MechaError(f"atom={atom!r} 非法", kind="bad_drive",
                         hint="atom 取 single / A / B")
    out = []
    for w in (gate.snapshot.get(key) or []):
        env = w.get("env")
        shape = {"kind": env[0], "duration_ns": (env[4] if len(env) > 4 else None)} \
            if isinstance(env, (list, tuple)) and env else {"kind": w.get("type")}
        out.append({"name": w.get("name"),
                    "freq_mhz": w.get("freq_mhz"),
                    "intensity_mwcm2": w.get("intensity", w.get("intensity_mwcm2")),
                    "pol": w.get("pol"), "offset_ns": w.get("offset_ns"),
                    "envelope": shape})
    return {"ok": True, "atom": atom, "key": key, "waves": out}
