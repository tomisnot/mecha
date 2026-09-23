"""Phase 2 装配判据：Surface 瘦身的四条最小判据（总纲 §7 Phase 2）。

1. ``Engine.__abstractmethods__`` ⊆ FRAMEWORK_CALLS（必实现只有框架真会调的）。
2. 注册项 register → disposer 可撤销（dsh AGENTS.md:106 的可逆副作用）。
3. 隐式契约键显式化：schema 未声明 run_output_required 即红；run 回执缺键即红
   （消 D-15）。
4. 只读查询**不经写权门**且**注册即可读**（消 D-4：没有 op 名表判级；
   未注册名报 UnknownOp 带 hint，不是"被判 L3 禁止"）。
5. 不许误报对偶（L7）：合规引擎全绿。

用法：python checks/phase2_surface_selfcheck.py
退出码：0 全绿；1 有失败。

## Known Limitations and Deferred Work

- 判据用装配级替身引擎（ToyEngine），不依赖任何项目 Provider——真实引擎的
  接线判据归 Phase 5（EL 消费配置）。
- 不测 hooks 的调用时机正确性（挂点语义等第一个真钩子出现再补，n=0 不预建）。
"""

from __future__ import annotations

import sys
from abc import abstractmethod
from collections.abc import Mapping
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mecha.errors import MechaError
from mecha.surface import Engine, EntryKind, ExecutionContext, FRAMEWORK_CALLS, Surface


class ToyEngine(Engine):
    """合规替身：4 个必实现全给。"""

    def schema(self) -> Mapping[str, object]:
        return {"run_output_required": ["ok", "method"], "keys": ["power"]}

    def run(self, spec, *, context, **opts) -> Mapping[str, object]:
        if context.cancelled:
            return {"ok": False, "method": "toy", "cancelled": True}
        context.report_progress(1.0, "done")
        return {"ok": True, "method": "toy"}

    def summary(self) -> Mapping[str, object]:
        return {"name": "toy", "state": "idle"}

    def estimate(self, spec) -> Mapping[str, object]:
        return {"est_sec": 1.0}


class LazyEngine(ToyEngine):
    """违规替身：schema 不声明回执契约。"""

    def schema(self) -> Mapping[str, object]:
        return {"keys": ["power"]}


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _fail(out: list[str], msg: str) -> None:
    print(f"  ✗ {msg}")
    out.append(msg)


def check_abstract_subset(out: list[str]) -> None:
    abstracts = set(Engine.__abstractmethods__)
    if abstracts <= set(FRAMEWORK_CALLS) and len(abstracts) == 4:
        _ok(f"必实现 ⊆ 框架真会调名单（{sorted(abstracts)}）")
    else:
        _fail(out, f"必实现 {sorted(abstracts)} 不在名单 {sorted(FRAMEWORK_CALLS)} 内")
    # 能抓坏：往 Definition 加一个框架不调的具名抽象方法，同一判据必须红
    class BloatedABC(Engine):
        @abstractmethod
        def extra(self): ...
    BloatedABC.__abstractmethods__ = frozenset(set(BloatedABC.__abstractmethods__) | {"extra"})
    bloated = set(BloatedABC.__abstractmethods__)
    if bloated <= set(FRAMEWORK_CALLS):
        _fail(out, "膨胀 Definition 未被子集判据抓到（哑守卫）")
    else:
        _ok("构造：往 Definition 塞分派目标 → 子集判据变红")


def check_register_dispose(out: list[str]) -> None:
    s = Surface(ToyEngine())
    seen: list[str] = []

    def my_query(word: str = "") -> str:
        seen.append(word)
        return f"echo:{word}"

    dispose = s.register(EntryKind.QUERY, "echo", my_query, called_by="framework:Surface.query")
    # 能抓坏：重复注册报错
    try:
        s.register(EntryKind.QUERY, "echo", my_query, called_by="framework")
        _fail(out, "重复注册竟通过")
    except MechaError as e:
        if e.kind == "duplicate_entry":
            _ok("重复注册被拒")
    # 注册即可读（消 D-4：没有判级表，新动词默认可用）
    r = s.query("echo", word="hi")
    if r == "echo:hi":
        _ok("未声明于任何名单的只读动词注册即可读（不判 L3）")
    else:
        _fail(out, f"query 分派不符：{r}")
    # disposer 撤销
    dispose()
    try:
        s.query("echo", word="hi")
        _fail(out, "dispose 后仍可调用（disposer 无效）")
    except MechaError as e:
        if e.kind == "unknown_query":
            _ok("disposer 撤销注册（可逆副作用）")
    # called_by 必填（消 D-18：谁调它必须写明）
    try:
        s.register(EntryKind.HOOK, "no_owner", lambda: None, called_by="  ")
        _fail(out, "缺 called_by 的注册竟通过")
    except MechaError as e:
        if e.kind == "bad_registration":
            _ok("注册必须声明 called_by")


def check_output_contract(out: list[str]) -> None:
    # 能抓坏：schema 不声明 run_output_required → Surface.schema 红
    s = Surface(LazyEngine())
    try:
        s.schema()
        _fail(out, "缺回执契约的 schema 竟通过")
    except MechaError as e:
        if e.kind == "missing_output_contract":
            _ok("schema 必须声明 run_output_required（隐式键变显式）")
    # 能抓坏：run 回执缺声明过的键 → 红
    class BrokenRun(ToyEngine):
        def run(self, spec, *, context, **opts):
            return {"ok": True}  # 缺 method
    s2 = Surface(BrokenRun())
    try:
        s2.run({}, context=ExecutionContext())
        _fail(out, "回执缺键竟通过")
    except MechaError as e:
        if e.kind == "bad_run_output":
            _ok("run 回执缺必填键即校验失败（消 D-15）")
    # 不许误报：合规引擎全绿
    s3 = Surface(ToyEngine())
    r = s3.run({}, context=ExecutionContext())
    if r.get("ok") and r.get("method") == "toy":
        _ok("合规引擎 run 通过（不误报）")
    else:
        _fail(out, f"合规 run 结果不符：{r}")
    est = s3.estimate({})
    if "est_sec" in est:
        _ok("estimate 契约（est_sec）可用")


def check_execution_context(out: list[str]) -> None:
    """取消与进度是显式字段（消 D-16）。"""
    ctx = ExecutionContext()
    ctx.report_progress(0.5, "half")
    ctx.request_cancel()
    if ctx.cancelled and ctx.progress() == [(0.5, "half")]:
        _ok("ExecutionContext 显式取消 + 进度字段")
    else:
        _fail(out, f"ExecutionContext 语义不符：{ctx.progress()}")
    try:
        ctx.report_progress(3.0)
        _fail(out, "进度越界竟通过")
    except MechaError as e:
        if e.kind == "bad_progress":
            _ok("进度越界被拒（不误报合法值已验证于上一用例）")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out: list[str] = []
    print("Phase 2 装配判据：")
    check_abstract_subset(out)
    check_register_dispose(out)
    check_output_contract(out)
    check_execution_context(out)
    if out:
        print(f"phase2_surface_selfcheck: FAIL（{len(out)} 条）")
        return 1
    print("phase2_surface_selfcheck: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
