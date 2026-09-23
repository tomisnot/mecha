"""EL Engine Provider：把 src.core.protocol 包装成 v2 的 4 必实现。

领域现实与框架契约的两处适配（ADR phase5-el-provider 已登记）：
- estimate 单体无 est_sec ⇒ 补保守粗估（锚点：6 态/50 点/5ns ≈ 2.4 s 实测）。
- result_summary 无 method 键 ⇒ 从 run['method'] 补进回执。

## Known Limitations and Deferred Work

- 取消是尽力而为：监视线程把 EL progress_arr 的取消槽置 1；引擎不响应时跑完
  为止（同进程无法强杀）。
- run_sim 同步执行；长运行的 job 化（ctx.jobs 接线）待真实部署授权后进行。
- validate 只到"类型/枚举/值域/wave_list 结构"层；物理合理性（基组够不够）
  由 preview/check_run 表达，不重复造第二份判据。
- check_run 的非致命问题（如空演化）作为 warnings 如实带出而非硬拦——与 EL
  自身语义一致（GUI 也照跑）；硬拦会造出生产路径不存在的输入（假绿判据）。
"""

from __future__ import annotations

import sys
import threading
import uuid
from collections.abc import Mapping
from pathlib import Path

from mecha.errors import GateDenied, InvalidSpec, MechaError, UnknownKey
from mecha.surface import Engine, ExecutionContext


def import_el(el_root: str | Path):
    """按注入路径 import EL 的纯领域层（绝不碰 v1 的 src.adapter）。"""
    root = Path(el_root)
    if not (root / "src" / "core" / "protocol.py").exists():
        raise MechaError(
            f"EL 根路径无效：{root}（缺 src/core/protocol.py）",
            kind="bad_el_root",
            hint="构造 Provider 时显式注入 EL 项目根；库不猜路径",
        )
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from src.core import protocol  # noqa: PLC0415 —— 动态注入路径后延迟 import
    return protocol


class ElEngine(Engine):
    """4 必实现 + 领域查询口（list_levels/preview/check 供工具层用）。"""

    def __init__(self, el_root: str | Path, *, atom: str = "Rubidium87") -> None:
        self._protocol = import_el(el_root)
        self._atom = atom
        self._cache = self._protocol.make_cache(atom)

    # ---- 必实现 ①：一处声明的 schema（全部派生自 protocol，不手抄）----

    def schema(self) -> Mapping[str, object]:
        p = self._protocol
        return {
            "run_output_required": ["ok", "method"],
            "estimate_output_required": ["est_sec"],
            "config": p.config_schema(),
            "solve": p.solve_schema(),
            "engine": p.engine_params(),          # 只读族（readonly=True）
            "settable_keys": p.settable_keys(),
        }

    # ---- 必实现 ②：run = build → check → evolve → summary ----

    def run(self, spec: Mapping[str, object], *, context: ExecutionContext,
            **opts: object) -> Mapping[str, object]:
        p = self._protocol
        snap = dict(spec)
        try:
            run = p.build_run(self._cache, snap)
        except ValueError as e:
            raise InvalidSpec(
                f"配置无法组装成运行规格：{e}",
                kind="build_failed",
                hint="先 query_params 看必填键；groups.list/dynamic.t_max/npts 是骨架",
                suggest="estimate_run 可无副作用地探一探配置",
            ) from None
        method = str(run.get("method", ""))
        problems = p.check_run(run)
        if context.cancelled:
            return {"ok": False, "method": method, "cancelled": True,
                    "problems": [], "hint": "执行前已取消"}
        # EL 的 progress_arr 三槽约定在**这里**适配（约定不外泄给框架）：
        # [0]=进度 [1]=取消标志 [2]=阶段
        progress_arr = [0.0, 0, ""]
        watcher = threading.Thread(
            target=self._watch_cancel, args=(context, progress_arr), daemon=True)
        watcher.start()
        try:
            res = p.evolve_run(run, progress_arr=progress_arr, cache=self._cache)
        finally:
            context.report_progress(1.0, "evolve done")
        summ = dict(p.result_summary(run, res))
        summ["method"] = method
        if not summ.get("ok"):
            summ["hint"] = "演化无结果（可能被取消或引擎契约破坏）；可先 estimate_run 探规模"
        if problems:
            summ["warnings"] = problems  # 非致命问题（如改道说明）如实带出
        return summ

    @staticmethod
    def _watch_cancel(context: ExecutionContext, arr: list) -> None:
        while not context.cancel_event.wait(0.05):
            pass
        arr[1] = 1

    # ---- 必实现 ③：summary ----

    def summary(self) -> Mapping[str, object]:
        return {"engine": "energy-level", "atom": self._atom,
                "cache_ready": True,
                "methods": sorted({m for (m, _) in
                                   self._protocol._runspec._ENGINE_TABLE})}

    # ---- 必实现 ④：estimate（补框架必填 est_sec，粗估口径写进 note）----

    def estimate(self, spec: Mapping[str, object]) -> Mapping[str, object]:
        out = dict(self._protocol.estimate(self._cache, dict(spec)))
        if "est_sec" not in out:
            n = int(out.get("n_states") or 0)
            t_max = float(spec.get("dynamic.t_max") or 0)
            npts = int(spec.get("dynamic.npts") or 0)
            if n and t_max and npts:
                # 锚点：6 态/50 点/5 ns ≈ 2.4 s（2026-09-23 实测），线性外推粗估
                out["est_sec"] = round(0.4 * n * (npts / 50.0) * (t_max / 5.0), 1)
                out["est_note"] = "单体粗估（实测锚点线性外推）；两体用 cost.py 标定"
            else:
                out["est_sec"] = 30.0
                out["est_note"] = "缺 t_max/npts/n_states——给保守上限 30 s"
        return out

    # ---- 领域查询口（工具层经 Surface 注册项消费）----

    def list_levels(self, snap: Mapping[str, object]) -> Mapping[str, object]:
        return dict(self._protocol.list_levels(self._cache, dict(snap)))

    def preview(self, snap: Mapping[str, object]) -> Mapping[str, object]:
        return dict(self._protocol.preview(self._cache, dict(snap)))


def make_validator(engine: ElEngine):
    """Gate 的 validate：键可写性 + 类型/枚举/值域/wave_list，报错即教学（L4）。"""
    p = engine._protocol
    schema = dict(p.config_schema())
    schema.update(p.solve_schema())
    engine_params = p.engine_params()
    settable = set(p.settable_keys())
    sample = ", ".join(sorted(settable)[:6])

    def _gate_err(msg: str) -> GateDenied:
        return GateDenied(msg, kind="bad_value",
                          hint="值不合法；query_params 看每个键的类型/单位/值域")

    def validate(key: str, value: object) -> None:
        if key in engine_params:
            raise UnknownKey(
                f"engine.{key} 是只读引擎常量",
                kind="readonly_key",
                hint="engine.* 不可运行级覆写；调精度/成本用 solve.* 键",
                suggest=f"可写键示例：{sample}（完整清单 query_params）",
            )
        if key not in settable:
            raise UnknownKey(
                f"键 {key!r} 不在可写清单",
                kind="unknown_key",
                hint=f"可写键共 {len(settable)} 个（config+solve，不含 engine.*）",
                suggest=f"示例：{sample}；完整清单用 query_params",
            )
        rec = schema.get(key) or {}
        tname = rec.get("type")
        enum = rec.get("enum")
        if enum is not None and value not in enum:
            raise GateDenied(
                f"{key}={value!r} 不在枚举 {enum}",
                kind="bad_enum",
                hint=f"合法取值就是 {enum}",
            )
        if tname == "int" and (isinstance(value, bool) or not isinstance(value, int)):
            raise _gate_err(f"{key} 须是整数，收到 {type(value).__name__}")
        if tname == "float" and (isinstance(value, bool)
                                 or not isinstance(value, (int, float))):
            raise _gate_err(f"{key} 须是数值，收到 {type(value).__name__}")
        if tname == "str" and not isinstance(value, str):
            raise _gate_err(f"{key} 须是字符串")
        if tname == "list" and not isinstance(value, (list, tuple)):
            raise _gate_err(f"{key} 须是列表")
        if tname == "dict" and not isinstance(value, Mapping):
            raise _gate_err(f"{key} 须是映射")
        if tname == "wave_list":
            p.validate_wave_list(value, err=_gate_err)
        lo, hi = rec.get("lo"), rec.get("hi")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if lo is not None and value < lo:
                raise _gate_err(f"{key}={value} 低于下限 {lo}")
            if hi is not None and value > hi:
                raise _gate_err(f"{key}={value} 高于上限 {hi}")

    return validate
