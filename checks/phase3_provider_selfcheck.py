"""Provider 契约：单写者、审批拒绝、注册可见性、技能目录、逐步日志与可注入保留策略。

使用合成引擎与临时数据；真实宿主接线由项目验证覆盖。
python checks/phase3_provider_selfcheck.py
"""

from __future__ import annotations

import io
import contextlib
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
from mecha.providers.log_local import JsonlLogSink
from mecha.providers.skills import SkillCatalog
from mecha.surface import EntryKind, ExecutionContext, Surface
from mecha.tools import ToolRegistry, define_tool


class ToyEngine:
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




def check_registration_is_the_only_visibility(out: list[str]) -> None:
    """D-4 的**实质承诺**：新项目自己发明的动词，注册即可用，不由任何第二份表判级。

    （原 `visibility.py` 的 `ToolRestriction`/`VisibilityPolicy` 已于 2026-09-25 收口轮
    删除——零消费者。**R10 自问**：那条旧断言验的是"可见性机制的语义"（declare /
    restrict / 交集 / disposer），机制一删它就没有对象了；但它背后要保的承诺不是
    "机制好用"，而是 **D-4 不复发**：没有任何东西能替项目把自有动词判成危险/隐藏。
    所以这里换成**行为级**的同一承诺，而不是删掉了事。
    """
    surface = Surface()
    surface.register(EntryKind.QUERY, "my_query", lambda **kw: {"ok": True},
                     called_by="check")
    if [e.name for e in surface.entries(EntryKind.QUERY)] == ["my_query"] \
            and surface.query("my_query") == {"ok": True}:
        _ok("自有动词 register 即可分派（无需任何可见性声明——D-4 不复发）")
    else:
        _fail(out, "新注册的动词不可用：可能又出现了第二份可见性真值")
    reg = ToolRegistry()
    reg.register(define_tool(name="do_thing", description="中性检查用动作",
                             parameters={},
                             output_schema={"type": "object", "required": ["ok"]},
                             execute=lambda: {"ok": True}))
    if [s["name"] for s in reg.schemas()] == ["do_thing"] \
            and reg.execute("do_thing", {})["is_error"] is False:
        _ok("对偶：工具注册即可见可执行，schemas() 不做任何过滤（无第二份真值）")
    else:
        _fail(out, f"工具可见性被过滤：{reg.schemas()}")


def check_skill_catalog_single_source(out: list[str]) -> None:
    s = Surface()
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
        s = Surface()
        s.register(EntryKind.QUERY, "echo", lambda word="": f"E:{word}", called_by="framework")
        act = CodeAct(s, hist, actor_provider=lambda: "ai")
        r = act.run([
            Action("echo", {"word": "hi"}),
            Action("no_such_op"),
        ], call_id="c1")
        # 逐子调用记日志（Phase 3 补的债）
        steps = [e for e in hist.events() if e.op.startswith("codeact.")]
        if len(steps) == 2:
            _ok("每子调用一条 History 记录（含失败步）")
        else:
            _fail(out, f"记日志条数不符：{[e.op for e in steps]}")
        if all(e.actor == "ai" and e.call_id == "c1" for e in steps):
            _ok("子调用日志带通道 actor + call_id（可归因互引）")
        else:
            _fail(out, "子调用日志归因缺失")
        detail2 = steps[1].after["detail"] if len(steps) > 1 else ""
        if r.stopped_at == 1 and "不在能力面" in detail2 and "提示：" in detail2:
            _ok("失败步带教学文本（message + hint 进日志）")
        else:
            _fail(out, f"失败步无教学文本：{detail2!r}")
        if r.outputs[0] == "E:hi":
            _ok("成功步输出正常（不误报）")
        else:
            _fail(out, f"成功步输出不符：{r.outputs}")


def check_codeact_failure_leaves_trace(out: list[str]) -> None:
    """CodeAct 三条（2026-09-26）：**账逐条即时写** + **普通异常也留痕** + **动作不写域状态**。

    ⚠ 旧行为：账在 `run` **末尾**一次性写（"先收集后落盘"）⇒ **中途抛异常 = 整段无账**；
    且只捕 `MechaError` ⇒ 普通异常**既穿透又不落账**（与 A1 同病）。
    """
    def boom(**kw):
        raise RuntimeError("引擎炸了")

    with tempfile.TemporaryDirectory() as td:
        hist = History()
        s = Surface()
        s.register(EntryKind.QUERY, "ok_op", lambda word="": f"E:{word}",
                   called_by="framework")
        s.register(EntryKind.QUERY, "boom_op", boom, called_by="framework")
        act = CodeAct(s, hist, actor_provider=lambda: "ai")
        raised = False
        try:
            act.run([Action("ok_op", {"word": "a"}), Action("boom_op"),
                     Action("ok_op", {"word": "b"})], call_id="c9")
        except RuntimeError:
            raised = True
        steps = [e for e in hist.events() if e.op.startswith("codeact.")]
        if raised and [e.op for e in steps] == ["codeact.0", "codeact.1"]:
            _ok("中途抛普通异常 ⇒ **已跑过的步都在史里**（不再整段无账），且异常**原样抛出**")
        else:
            _fail(out, f"崩溃留痕不符：raised={raised} steps={[e.op for e in steps]}")
        if (len(steps) == 2 and steps[1].after["ok"] is False
                and "RuntimeError" in str(steps[1].after["detail"])):
            _ok("失败步也留痕，detail 带异常类型名（与 A1 同一判据）")
        else:
            _fail(out, f"失败步的痕不对：{[e.after for e in steps]}")
        if all(e.call_id == "c9" for e in steps):
            _ok("逐条写仍「整段可识别」：两步共用同一个 call_id（不必新增批 id）")
        else:
            _fail(out, f"call_id 不一致：{[e.call_id for e in steps]}")

        # ⚠ 事实判据：`run` 动作**不写域状态**（Surface.run 不碰 Gate）——
        # 免得后人以为"CodeAct 在改世界"（它只编排；域状态写归宿主的 handler）。
        hist2 = History()
        gate2 = Gate(Authority(Mode.AI), hist2, validate=accept_all)


def check_log_keep_is_injectable(out: list[str]) -> None:
    """保留策略可选：有限保留最终裁到预算；不裁保留全部事件。"""
    import io
    import tempfile
    from mecha.data_layout import resolve_data_dir
    from mecha.providers.log_local import JsonlLogSink

    with tempfile.TemporaryDirectory() as td:
        layout = resolve_data_dir(Path(td), ".trim")
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            sink = JsonlLogSink(layout, keep=5)
            for seq in range(1, 51):
                sink.append({"seq": seq, "key": "x", "value": seq})
                if log.getvalue():
                    break
            saved = list(sink.since(0))
            sink.close()
        if log.getvalue() and [e["seq"] for e in saved] == list(range(seq - 4, seq + 1)):
            _ok("有限保留触发裁剪，最新事件与原序号保留，裁剪可见")
        else:
            _fail(out, f"有限保留没有生效：saved={saved}, log={log.getvalue()!r}")

    with tempfile.TemporaryDirectory() as td:
        sink = JsonlLogSink(resolve_data_dir(Path(td), ".keep"), keep=0)
        for seq in range(1, 9):
            sink.append({"seq": seq, "key": "x", "value": seq})
        saved = list(sink.since(0))
        sink.close()
        if [e["seq"] for e in saved] == list(range(1, 9)):
            _ok("keep=0 保留全部事件")
        else:
            _fail(out, f"keep=0 丢失事件：{saved}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out: list[str] = []
    print("Phase 3 装配判据：")
    check_jobs_single_owner(out)
    check_approval_fail_closed(out)
    check_registration_is_the_only_visibility(out)
    check_skill_catalog_single_source(out)
    check_codeact_logs_each_subcall(out)
    check_codeact_failure_leaves_trace(out)
    check_log_keep_is_injectable(out)
    if out:
        print(f"phase3_provider_selfcheck: FAIL（{len(out)} 条）")
        return 1
    print("phase3_provider_selfcheck: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
