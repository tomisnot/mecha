"""Phase 3 装配判据：Provider 层吸收 dsh 成熟件的六条验收。

1. Jobs 单一注册表：双实例并发跑同一批 job 会把对方 RUNNING 的翻成 interrupted
   ——v1 的 D-21。判据：构造"第二个 registry 接管同一 job 集"必须失败（注册表
   实例即所有权，无共享可变状态）。
2. Approval fail closed：无决策器 → 拒绝；决策器抛错 → 拒绝（不静默放行）。
3. Spill 定位符不透明：retrieve 只认 put 返回值；消费者拼路径必须失败。
4. 可见性默认可读（消 D-4）：新动词不声明任何 restriction 即可见；deny 单调、
   撤销后恢复；引用未知动词的 restriction 当场报错（fail loud）。
5. Skill 目录单一来源：未注册先 declare → 红；注册后目录自动含该项（消 16→18 漂移）。
6. CodeAct 逐子调用记日志：每步落 History 一条、actor 来自通道、失败步带教学文本。

每条都有 L7 对偶（不误报用例）。

用法：python checks/phase3_provider_selfcheck.py
退出码：0 全绿；1 有失败。

## Known Limitations and Deferred Work

- dsh 侧 Provider（ctx.jobs/spillStore/approval 注入形态）无真宿主可测，
  其接线判据归 Phase 5；这里测 local 出厂默认 + Definition 语义。
- Jobs 判据测的是"所有权模型"（实例即注册表），不是线程正确性（竞态压测
  超出判据范围，归真实引擎接入时）。
"""

from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mecha.authority import Authority, Mode
from mecha.data_layout import resolve_data_dir
from mecha.errors import MechaError
from mecha.gate import Channel, Gate, accept_all
from mecha.history import History
from mecha.providers.approval import LocalApproval
from mecha.providers.codeact import Action, CodeAct
from mecha.providers.jobs import JobRegistry, JobState
from mecha.providers.skills import SkillCatalog
from mecha.providers.spill import LocalSpillStore
from mecha.surface import Engine, EntryKind, ExecutionContext, Surface
from mecha.visibility import ToolRestriction, VisibilityPolicy


class ToyEngine(Engine):
    def schema(self):
        return {"run_output_required": ["ok", "method"]}

    def run(self, spec, *, context, **opts):
        return {"ok": True, "method": "toy"}

    def summary(self):
        return {"state": "idle"}

    def estimate(self, spec):
        return {"est_sec": 0.1}


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _fail(out: list[str], msg: str) -> None:
    print(f"  ✗ {msg}")
    out.append(msg)


def check_jobs_single_owner(out: list[str]) -> None:
    reg = JobRegistry()
    started = threading.Event()
    release = threading.Event()

    def slow(ctx: ExecutionContext):
        started.set()
        release.wait(5)
        return "done"

    job = reg.submit(slow)
    started.wait(2)
    # 能抓坏：第二个注册表"接管"同一 job —— 结构上不可能（job 不属于注册表
    # 之外的共享存储），构造方式：拿另一个 registry 去 cancel 它
    reg2 = JobRegistry()
    try:
        reg2.cancel(job.id)
        _fail(out, "第二个 JobRegistry 竟能操作别人的 job（D-21 复发）")
    except MechaError as e:
        if e.kind == "unknown_job":
            _ok("job 所有权在单一注册表内（双 runner 构造失败）")
    # 不许误报：原注册表可轮询、可取消
    st = reg.get(job.id).status()
    if st["state"] == JobState.RUNNING.value:
        _ok("原注册表可轮询 RUNNING（不误报）")
    release.set()
    job.wait(5)
    if reg.get(job.id).state is JobState.DONE:
        _ok("job 完成可达 DONE")
    # 取消语义
    started2 = threading.Event()

    def cancellable(ctx: ExecutionContext):
        started2.set()
        while not ctx.cancelled:
            threading.Event().wait(0.05)
        return None

    j3 = reg.submit(cancellable)
    started2.wait(2)
    reg.cancel(j3.id)
    j3.wait(5)
    if reg.get(j3.id).state is JobState.CANCELLED:
        _ok("取消经 ExecutionContext 显式字段生效（消 D-16）")
    else:
        _fail(out, f"取消未生效：{j3.state}")


def check_approval_fail_closed(out: list[str]) -> None:
    # 能抓坏：无决策器 → 拒绝
    a = LocalApproval()
    d = a.request("ai", "set", "k=v")
    if not d.allowed:
        _ok("无审批通道 fail closed（拒绝而非放行）")
    else:
        _fail(out, "无决策器竟放行")

    # 能抓坏：决策器抛错 → 拒绝
    def boom(actor, op, detail):
        raise RuntimeError("审批 UI 崩了")
    d2 = LocalApproval(boom).request("ai", "set")
    if not d2.allowed and "异常" in d2.reason:
        _ok("决策器异常按拒绝处理（不静默放行）")
    else:
        _fail(out, f"决策器异常未 fail closed：{d2}")

    # 不许误报：给决策器 → one-shot 同意生效（且不留记忆）
    calls: list[tuple[str, str]] = []

    def decider(actor, op, detail):
        calls.append((actor, op))
        return actor == "human"
    ap = LocalApproval(decider)
    if ap.request("human", "set").allowed and not ap.request("ai", "set").allowed:
        _ok("one-shot 决策按 actor 区分（无 allow-always 残留）")
    else:
        _fail(out, "one-shot 决策语义不符")
    if len(calls) == 2:
        _ok("每次请求都重新问（无规则记忆）")


def check_spill_opaque(out: list[str]) -> None:
    with tempfile.TemporaryDirectory() as td:
        layout = resolve_data_dir(Path(td), "projdata")
        store = LocalSpillStore(layout, threshold_bytes=10)
        # 不许误报：小载荷不 spill
        if store.should_spill("tiny"):
            _fail(out, "小载荷被判需 spill（阈值失效）")
        else:
            _ok("阈值以下不 spill")
        big = "x" * 100
        loc = store.put(big)
        if store.retrieve(loc) == big:
            _ok("locator 取回一致")
        else:
            _fail(out, "spill 取回不符")
        # 能抓坏：消费者拼路径（假设本地布局）必须失败
        if loc.startswith("$") and str(layout.data_dir) not in loc:
            _ok("定位符不透明（不含路径）")
        else:
            _fail(out, f"定位符泄漏路径：{loc}")
        try:
            store.retrieve("$nonexistent")
            _fail(out, "假定位符竟取回成功")
        except MechaError as e:
            if e.kind == "bad_locator":
                _ok("未知定位符拒绝 + 教学 hint")


def check_visibility_default_readable(out: list[str]) -> None:
    pol = VisibilityPolicy()
    pol.declare("my_query")
    pol.declare("write_setting")
    # 能抓坏的反面（L7）：未声明 restriction 的新动词默认可读（消 D-4）
    if pol.is_visible("my_query"):
        _ok("未声明的只读动词默认可见（不判 L3）")
    else:
        _fail(out, "新动词被默认拒绝（D-4 复发）")
    # deny 单调 + disposer 恢复
    dispose = pol.restrict(ToolRestriction(deny=["write_setting"]))
    if not pol.is_visible("write_setting") and pol.is_visible("my_query"):
        _ok("deny 只缩减可见集")
    else:
        _fail(out, "deny 语义不符")
    dispose()
    if pol.is_visible("write_setting"):
        _ok("restriction disposer 撤销恢复可见（可逆副作用）")
    else:
        _fail(out, "撤销 restriction 未恢复可见性")
    # 交集：两层 restriction 取交
    pol.restrict(ToolRestriction(allow=["my_query", "write_setting"]))
    pol.restrict(ToolRestriction(deny=["my_query"]))
    if pol.visible() == ["write_setting"]:
        _ok("多层 restriction 取交集")
    else:
        _fail(out, f"交集不符：{pol.visible()}")
    # 能抓坏：引用未知动词的 restriction 当场报错（fail loud at load）
    try:
        pol.restrict(ToolRestriction(deny=["typo_verb"]))
        _fail(out, "拼错的动词名静默通过")
    except ValueError:
        _ok("未知动词名的 restriction 当场报错")


def check_skill_catalog_single_source(out: list[str]) -> None:
    s = Surface(ToyEngine())
    cat = SkillCatalog(s)
    # 能抓坏：未注册先 declare → 红（目录只有一个来源）
    try:
        cat.declare("ghost", "没注册就想进目录")
        _fail(out, "未注册动词进了目录")
    except ValueError:
        _ok("declare 必须先 register（单一来源）")
    # 不许误报：注册 → 目录自动含；dispose → 目录自动不含（漂移在结构上不可能）
    dispose = s.register(EntryKind.QUERY, "get_power", lambda: 42, called_by="framework")
    cat.declare("get_power", "查询当前功率", model_invocable=True, user_invocable=False)
    names = [e.name for e in cat.catalog(model_facing=True)]
    if names == ["get_power"]:
        _ok("目录从注册项生成（注册即可见）")
    else:
        _fail(out, f"目录生成不符：{names}")
    user_names = [e.name for e in cat.catalog(model_facing=False)]
    if user_names == []:
        _ok("四象限：仅模型可见项不进人类目录")
    else:
        _fail(out, f"四象限投影不符：{user_names}")
    dispose()
    if [e.name for e in cat.catalog()] == []:
        _ok("dispose 后目录同步收缩（无手抄残留）")
    else:
        _fail(out, "dispose 后目录仍含旧项（漂移源）")


def check_codeact_logs_each_subcall(out: list[str]) -> None:
    with tempfile.TemporaryDirectory() as td:
        layout = resolve_data_dir(Path(td), "d")
        hist = History()
        auth = Authority(Mode.AI)
        gate = Gate(auth, hist, validate=accept_all)
        s = Surface(ToyEngine())
        s.register(EntryKind.QUERY, "echo", lambda word="": f"E:{word}", called_by="framework")
        act = CodeAct(s, hist, actor_provider=lambda: "ai")
        r = act.run([
            Action("echo", {"word": "hi"}),
            Action("run", {"spec": {"mode": "x"}}),
            Action("no_such_op"),
        ], call_id="c1")
        # 逐子调用记日志（Phase 3 补的债）
        steps = [e for e in hist.events() if e.key.startswith("codeact.")]
        if len(steps) == 3:
            _ok("每子调用一条 History 记录（含失败步）")
        else:
            _fail(out, f"记日志条数不符：{[e.key for e in steps]}")
        if all(e.actor == "ai" and e.call_id == "c1" for e in steps):
            _ok("子调用日志带通道 actor + call_id（可归因互引）")
        else:
            _fail(out, "子调用日志归因缺失")
        detail2 = steps[2].value["detail"] if len(steps) > 2 else ""
        if r.stopped_at == 2 and "不在能力面" in detail2 and "提示：" in detail2:
            _ok("失败步带教学文本（message + hint 进日志）")
        else:
            _fail(out, f"失败步无教学文本：{detail2!r}")
        if r.outputs[0] == "E:hi" and r.outputs[1].get("ok"):
            _ok("成功步输出正常（不误报）")
        else:
            _fail(out, f"成功步输出不符：{r.outputs}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out: list[str] = []
    print("Phase 3 装配判据：")
    check_jobs_single_owner(out)
    check_approval_fail_closed(out)
    check_spill_opaque(out)
    check_visibility_default_readable(out)
    check_skill_catalog_single_source(out)
    check_codeact_logs_each_subcall(out)
    if out:
        print(f"phase3_provider_selfcheck: FAIL（{len(out)} 条）")
        return 1
    print("phase3_provider_selfcheck: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
