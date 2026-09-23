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
from mecha.surface import ExecutionContext, Surface
from mecha.tools import ToolRegistry, define_tool

from .engine import ElEngine

# —— 任务概念词汇（description 只准用这些，禁框架内部词）——
# 每个工具的 execute 闭包捕获 (gate, surface, engine, channel)。


def build_tool_registry(engine: ElEngine, gate: Gate, surface: Surface,
                        authority: Authority, channel: Channel) -> ToolRegistry:
    """装配 EL 的工具面。channel 决定 actor（AI 侧恒为 'ai'，改不了）。"""
    reg = ToolRegistry()
    schema = engine.schema()
    settable = list(schema["settable_keys"])

    def _set(key: str, value):
        gate.set(channel, key, value, reason="ai tool")
        return {"ok": True, "key": key, "applied": True}

    # ---- set_config ----
    reg.register(define_tool(
        name="set_config",
        description=(
            "设置一个配置键的值。键必须是可写配置键（见 query_params 的清单）。"
            "改引擎内部常量会被拒。设置后立即可被 estimate_run/preview_run/run_sim 读到。"
        ),
        parameters={"key": {"type": "string", "description": "可写配置键，如 dynamic.t_max"},
                    "value": {"type": "json", "description": "新值，类型须匹配该键"}},
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

    return reg


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
