"""EL Engine Provider：把 src.core.protocol 包装成 v2 的 4 必实现。

领域现实与框架契约的两处适配（ADR phase5-el-provider 已登记）：
- estimate 单体无 est_sec ⇒ 补保守粗估（锚点：6 态/50 点/5ns ≈ 2.4 s 实测）。
- result_summary 无 method 键 ⇒ 从 run['method'] 补进回执。

**线程亲和**（EL 的 ARC cache 是 SQLite，认建它的线程）：所有 protocol 调用
都汇流到建 cache 的专属引擎线程（单线程 executor）。调用方线程（GUI worker /
MCP anyio worker / 主线程）随便换，cache 永不跨线程。

## Known Limitations and Deferred Work

- 取消是尽力而为：监视线程把 EL progress_arr 的取消槽置 1；引擎不响应时跑完
  为止（同进程无法强杀）。
- run_sim 同步执行；长运行的 job 化（ctx.jobs 接线）待真实部署授权后进行。
- validate 只到"类型/枚举/值域/wave_list 结构"层；物理合理性（基组够不够）
  由 preview/check_run 表达，不重复造第二份判据。
- check_run 的非致命问题（如空演化）作为 warnings 如实带出而非硬拦——与 EL
  自身语义一致（GUI 也照跑）；硬拦会造出生产路径不存在的输入（假绿判据）。
- 引擎线程串行执行所有调用：两个并发 run 排队而非并行（EL 单计算本来就是重
  活；真并发需求出现时按 ADR 流程加 job 队列，不在此预建）。
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
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


class CacheProxy:
    """ARC cache 的线程亲和代理：任意线程的属性调用都汇流到引擎线程执行。

    单进程"人机同一道门"必需：GUI 代码把 cache 当普通对象用（`cache.E_mhz(...)`、
    传给 build_states），但 ARC 的 SQLite 连接认建它的线程（EL B-1①）。当 MCP
    服务器与 GUI 同进程、各自在 anyio/Qt 线程上碰同一个 cache 时，代理把每个方法
    调用 submit 到引擎池线程 ⇒ 连接永不被跨线程触碰。引擎无池（own_thread=False）
    时直通原对象（调用方自己保证亲和）。
    """

    __slots__ = ("_cache", "_route")

    def __init__(self, cache, route) -> None:
        object.__setattr__(self, "_cache", cache)
        object.__setattr__(self, "_route", route)

    def __getattr__(self, name):
        attr = getattr(self._cache, name)
        if callable(attr):
            def _routed(*args, **kwargs):
                return self._route(attr, *args, **kwargs)
            return _routed
        return attr


class ElEngine(Engine):
    """4 必实现 + 领域查询口（list_levels/preview/check 供工具层用）。

    **线程亲和**（ARC cache 是 SQLite，认建它的线程，EL 的 B-1① 约束）两种模式：

    - ``own_thread=True``（Hub 默认）：cache 建在专属引擎线程上，所有 protocol
      调用经单 worker executor 汇流到它——调用方线程（MCP anyio worker）随便换。
    - ``own_thread=False``（GUI 内嵌）：cache 建在**构造线程**（GUI 主线程），
      不建池、调用就地执行；调用方自己保证亲和（GUI 的 cache 直用与 build_run
      在主线程，evolve 在 worker——与 v1 的 prepare/execute 分线程契约同构）。
    """

    def __init__(self, el_root: str | Path, *, atom: str = "Rubidium87",
                 own_thread: bool = True) -> None:
        self._atom = atom
        self._own_thread = own_thread
        if own_thread:
            self._pool = ThreadPoolExecutor(max_workers=1,
                                            thread_name_prefix="el-engine")
            self._protocol = self._pool.submit(import_el, el_root).result()
            self._cache = self._pool.submit(self._protocol.make_cache, atom).result()
            self._schema = self._pool.submit(self._build_schema).result()
        else:
            self._pool = None
            self._protocol = import_el(el_root)
            self._cache = self._protocol.make_cache(atom)
            self._schema = self._build_schema()

    def _on_engine(self, fn, *args, **kwargs):
        """把一次 protocol 调用汇流到引擎线程（own_thread=False 时就地执行）。"""
        if self._pool is None:
            return fn(*args, **kwargs)
        return self._pool.submit(fn, *args, **kwargs).result()

    def _build_schema(self) -> Mapping[str, object]:
        p = self._protocol
        return {
            "run_output_required": ["ok", "method"],
            "estimate_output_required": ["est_sec"],
            "config": p.config_schema(),
            "solve": p.solve_schema(),
            "engine": p.engine_params(),          # 只读族（readonly=True）
            "settable_keys": p.settable_keys(),
        }

    # ---- 必实现 ①：一处声明的 schema（构造期物化，全部派生自 protocol）----

    def schema(self) -> Mapping[str, object]:
        return self._schema

    # ---- 必实现 ②：run = build → check → evolve → summary ----

    def run(self, spec: Mapping[str, object], *, context: ExecutionContext,
            **opts: object) -> Mapping[str, object]:
        snap = dict(spec)
        # EL 的 progress_arr 三槽约定在**这里**适配（约定不外泄给框架）：
        # [0]=进度 [1]=取消标志 [2]=阶段。取消监视线程独立于引擎线程翻标志位。
        # stop 事件让 watcher 在 run 正常结束时也能退出（外部深研 P0：
        # 只等 cancel_event 的旧写法每次成功 run 泄漏一个等待线程）。
        progress_arr = [0.0, 0, ""]
        stop = threading.Event()
        watcher = threading.Thread(
            target=self._watch_cancel, args=(context, progress_arr, stop),
            daemon=True)
        watcher.start()
        try:
            return self._on_engine(self._run_on_engine, snap, progress_arr)
        finally:
            stop.set()
            watcher.join(timeout=1.0)
            context.report_progress(1.0, "evolve done")

    def _run_on_engine(self, snap: dict, progress_arr: list) -> Mapping[str, object]:
        """build → check → evolve → summary，全程在引擎线程（cache 亲和）。"""
        p = self._protocol
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
        res = p.evolve_run(run, progress_arr=progress_arr, cache=self._cache)
        summ = dict(p.result_summary(run, res))
        summ["method"] = method
        if not summ.get("ok"):
            summ["hint"] = "演化无结果（可能被取消或引擎契约破坏）；可先 estimate_run 探规模"
        if problems:
            summ["warnings"] = problems  # 非致命问题（如改道说明）如实带出
        return summ

    @staticmethod
    def _watch_cancel(context: ExecutionContext, arr: list,
                      stop: threading.Event) -> None:
        """轮询取消：cancel_event 置位 → 翻标志退出；run 结束（stop 置位）→ 退出。

        两条退出路径都收敛（不泄漏等待线程）；run 已结束才看到 stop 时翻
        arr[1] 无意义，故 cancel 分支优先于 stop 分支无冲突。
        """
        while not stop.is_set():
            if context.cancel_event.wait(0.05):
                arr[1] = 1
                return

    # ---- 必实现 ③：summary（全部取 EL 的公开声明面）----

    def summary(self) -> Mapping[str, object]:
        methods = self._schema["config"].get(          # type: ignore[union-attr]
            "dynamic.method", {}).get("enum") or []
        return {"engine": "energy-level", "atom": self._atom,
                "cache_ready": True, "methods": sorted(map(str, methods))}

    # ---- 必实现 ④：estimate（补框架必填 est_sec，粗估口径写进 note）----

    def estimate(self, spec: Mapping[str, object]) -> Mapping[str, object]:
        out = dict(self._on_engine(self._protocol.estimate, self._cache, dict(spec)))
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

    # ---- 领域查询口（工具层经 Surface 注册项消费；都汇流到引擎线程）----

    def list_levels(self, snap: Mapping[str, object]) -> Mapping[str, object]:
        return dict(self._on_engine(self._protocol.list_levels, self._cache, dict(snap)))

    def preview(self, snap: Mapping[str, object]) -> Mapping[str, object]:
        return dict(self._on_engine(self._protocol.preview, self._cache, dict(snap)))

    # ---- 驱动手感（②b：set_drive 的物理换算，全程引擎线程，cache 亲和）----

    def drive_wave(self, snap: Mapping[str, object], **kw: object) -> Mapping:
        """把"按 Rabi/光强设一个驱动波"翻译成一条 wave_list dict（不写、只算）。

        换算要碰 ARC cache（dme），故汇流引擎线程。工具层拿到 dict 后经
        `gate.set` 写回单键——门不旁路（ADR 2026-09-23-EL驱动手感）。
        """
        return self._on_engine(self._drive_wave, dict(snap), **kw)

    def _drive_wave(self, snap: dict, *, src, tgt, pol="π",
                    rabi_mhz=None, intensity_mwcm2=None, pulse="",
                    detuning_mhz=0.0, duration_ns=None, edge_ns=None,
                    name="drive", offset_ns=0.0) -> dict:
        # 延迟 import（同 import_el：路径注入后才碰 EL 领域层，库不猜路径）
        from src.core import atomic_levels as AL
        from src.core import static_atom as SA
        p = self._protocol
        if (rabi_mhz is None) == (intensity_mwcm2 is None):
            raise InvalidSpec("rabi_mhz 与 intensity_mwcm2 恰给其一",
                              kind="bad_drive",
                              hint="按 Rabi 频率给 rabi_mhz，或按光强给 intensity_mwcm2")
        polq = {"π": 0, "σ+": 1, "σ-": -1}
        if pol not in polq:
            raise InvalidSpec(f"pol={pol!r} 不支持按 Rabi 换算",
                              kind="bad_drive",
                              hint="π/σ+/σ- 可给 rabi_mhz；椭圆偏请直给 intensity_mwcm2")
        groups = p._runspec.groups_from_params(snap)
        if not groups:
            raise InvalidSpec("基组为空，无法定位跃迁", kind="bad_drive",
                              hint="先 set_config groups.list 再 set_drive")
        states = p._runspec.build_states(self._cache, groups,
                                         snap.get("field.B_gauss"),
                                         snap.get("field.E_vpcm"))
        ia, ib = AL.find_state_index(states, src), AL.find_state_index(states, tgt)
        if ia < 0 or ib < 0:
            raise InvalidSpec("src/tgt 身份键不在当前基组", kind="bad_drive",
                              hint="身份键取自 query_levels，别猜索引")
        a, b = states[ia], states[ib]
        d = abs(SA.dipole_amplitude(self._cache, a, b, polq[pol]))
        if rabi_mhz is not None:
            if d <= 0:
                raise InvalidSpec(f"{a['label']}→{b['label']} {pol} 偶极为零（禁戒跃迁）",
                                  kind="bad_drive", hint="换偏振或态；禁戒跃迁无 Rabi")
            inten = SA.intensity_mwcm2_from_rabi(d, float(rabi_mhz), 1.0)
            om = float(rabi_mhz)
        else:
            inten = float(intensity_mwcm2)
            om = SA.rabi_mhz(d, SA.field_e0_sq(inten), 1.0)
        if pulse in ("pi", "pi2"):
            if om <= 0:
                raise InvalidSpec("脉冲时长需非零 Rabi", kind="bad_drive",
                                  hint="给 rabi_mhz，或改用 duration_ns")
            dur = (500.0 if pulse == "pi" else 250.0) / om
        elif duration_ns is not None:
            dur = float(duration_ns)
        else:
            raise InvalidSpec("需 pulse(pi/pi2) 或 duration_ns 定时长",
                              kind="bad_drive",
                              hint="π/π2 脉冲用 pulse；任意时长用 duration_ns")
        edge = float(edge_ns) if edge_ns is not None else round(dur * 0.1, 6)
        freq = abs(float(b["E_mhz"]) - float(a["E_mhz"])) + float(detuning_mhz)
        return {"name": name or "drive", "freq_mhz": freq, "intensity": inten,
                "pol": pol, "offset_ns": float(offset_ns), "elliptical": None,
                "env": ("square", 0.0, edge, 1.0, dur)}


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
            raise UnknownKey.typo(
                key, settable,
                kind="unknown_key",
                hint=f"可写键共 {len(settable)} 个（config+solve，不含 engine.*）；"
                     "完整清单 query_params",
            )
        # None = "清空回默认"（GUI 的"留空=默认/跟随模板"语义，v1 既有）：
        # 下游消费方（mb_get、runspec 的 `or` 回落）一律把快照里的 None 当未设。
        if value is None:
            return
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
        if tname == "atom_list":
            # 多体 atoms 的真契约（g/r 四元组 / pos 三元组 / Ω 数值）——委托
            # EL core（schema_mb），与 wave_list 同角色：类型级校验在门上。
            p.validate_atom_list(value, err=_gate_err)
        lo, hi = rec.get("lo"), rec.get("hi")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if lo is not None and value < lo:
                raise _gate_err(f"{key}={value} 低于下限 {lo}")
            if hi is not None and value > hi:
                raise _gate_err(f"{key}={value} 高于上限 {hi}")

    return validate
