"""Phase 7 判据：Job Contract（作业契约）——可轮询/可取消/结果引用。

Job Contract 是第二个宿主（ML Toolbox）逼出的通用机制：训练与调参是长任务，
命令面是同步返回，宿主需要把"一次长命令"提交成后台 job，并在 job 上挂
**不透明结果引用**（``result_ref``），与 ``CommandResult.result_ref`` 同语义。

本判据只测机制，不测任何真宿主的 job 内容：

  A 提交/状态：submit 返回 Job，``state`` 从 pending→running→done，
    ``result`` 保留返回值，``command`` 与 ``result_ref`` 可读。
  B 失败：fn 抛错 → state=failed + error 有类型/消息，result_ref 为空，
    异常不逃出注册表。**引用提取失败不算 job 失败**：``result_ref_fn`` 抛错
    ⇒ state=done、result 完整、result_ref 为空、原因进独立字段 ``ref_error``，
    而 ``error`` 保持空（真值不许一字段两义，审查 P2-4）。
  C 取消：fn 在取消后自行退出 → state=cancelled（取消是显式字段，不是杀线程）；
    取消的 job **不写 result_ref**——哪怕函数忽略 ``ctx.cancelled`` 照样跑完
    （终态与引用同源，ADR「失败/取消的 job 不伪造引用」）。
  D 并发写史安全：多个 job 并发 append History，seq 仍严格单调无重复
    （History 的进程内写口互斥；跨进程仍靠写租约）；并发 submit 下反复
    ``list()`` 零异常（读侧取锁快照，P1-1 回归）。
  E 不许误报（L7）：合规 job 全绿；list/status 形状稳定。

用法：python checks/phase7_job_contract_selfcheck.py
退出码：0 全绿；1 有失败。

## Known Limitations and Deferred Work

- 不测进程重启后的 job 找回（需要 job 元数据落 History；等第二个宿主真实
  要跨重启时再做，ADR 里登记）。
- 不测真实并发池/优先级/GPU 队列：local 仍是"每 job 一个 daemon 线程"，
  并发度归宿主自己的线程模型。
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mecha.assembly import assemble  # noqa: E402
from mecha.gate import accept_all  # noqa: E402
from mecha.providers.jobs import JobState  # noqa: E402
from mecha.surface import Engine  # noqa: E402


class ToyEngine(Engine):
    def schema(self):
        return {"run_output_required": ["ok"], "keys": []}

    def run(self, spec, *, context, **opts):
        return {"ok": True}

    def summary(self):
        return {"ok": True}

    def estimate(self, spec):
        return {"est_sec": 0.1}


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _fail(out: list[str], msg: str) -> None:
    print(f"  ✗ {msg}")
    out.append(msg)


def _with_software():
    td = tempfile.mkdtemp(prefix="phase7-")
    return assemble(root=Path(td), data_dir_name="p7data", engine=ToyEngine(),
                    summarizer=lambda evs: {}, validate=accept_all, project="toyhost")


def check_submit_and_ref(out: list[str]) -> None:
    with _with_software() as sw:
        job = sw.jobs.submit(
            lambda ctx: {"ok": True, "value": 42},
            command="compute_summary",
            result_ref_fn=lambda res: {"kind": "toy_result", "id": res["value"]})
        if not job.wait(5):
            _fail(out, "job 未在 5s 内结束")
            return
        d = job.to_dict()
        if d["state"] == JobState.DONE.value and job.result["value"] == 42:
            _ok("submit → done，result 保留返回值")
        else:
            _fail(out, f"job 状态/结果不符：{d}")
        if d.get("command") == "compute_summary" \
                and d.get("result_ref") == {"kind": "toy_result", "id": 42}:
            _ok("command 与 result_ref 进 job 状态（可轮询、可互引）")
        else:
            _fail(out, f"command/result_ref 缺失或错：{d}")
        if any(j["id"] == job.id for j in sw.jobs.list()):
            _ok("list() 能看到 job（注册表单一来源）")
        else:
            _fail(out, "list() 缺 job")


def check_failure(out: list[str]) -> None:
    with _with_software() as sw:
        def boom(ctx):
            raise RuntimeError("engine exploded")

        job = sw.jobs.submit(boom, command="boom", result_ref_fn=lambda r: {"x": 1})
        if not job.wait(5):
            _fail(out, "失败 job 未结束")
            return
        d = job.to_dict()
        if d["state"] == JobState.FAILED.value and "RuntimeError" in d.get("error", ""):
            _ok("fn 抛错 → failed + 可读 error（异常不逃出注册表）")
        else:
            _fail(out, f"失败归因不符：{d}")
        if d.get("result_ref") is None:
            _ok("失败 job 无 result_ref（不伪造引用）")
        else:
            _fail(out, f"失败 job 竟有 ref：{d}")


def check_ref_extraction_error(out: list[str]) -> None:
    """P2-4 回归：引用提取失败是**独立字段**，不许改 job 结果/状态/``error``。

    构造：fn 正常返回，``result_ref_fn`` 抛错。job 本身是成功的
    （``state=done``、``result`` 完整），失败的只是"可选引用"这一步。
    所以：``result_ref is None``、``ref_error`` 非空且可读、``error == ""``。
    先跑一条**同一 fn、引用正常**的对照片，证明 ``result_ref is None`` 不是
    因为引用机制整体坏了（防断言空转）。
    """
    with _with_software() as sw:
        payload = {"ok": True, "value": 7}

        # 对照片：同一条 fn + 能用的 ref_fn ⇒ 引用必须提取出来
        good = sw.jobs.submit(lambda ctx: dict(payload), command="ref_ok",
                              result_ref_fn=lambda res: {"kind": "toy_result",
                                                         "id": res["value"]})
        if good.wait(5) and good.result_ref == {"kind": "toy_result", "id": 7}:
            _ok("对照片：能提取的引用照旧提取（防断言空转）")
        else:
            _fail(out, f"对照片不符：{good.to_dict()}")

        def boom_ref(res):
            raise ValueError("ref extractor exploded")

        job = sw.jobs.submit(lambda ctx: dict(payload), command="ref_fault",
                             result_ref_fn=boom_ref)
        if not job.wait(5):
            _fail(out, "引用提取失败的 job 未结束")
            return
        d = job.to_dict()
        if job.state is JobState.DONE and job.result == payload:
            _ok("result_ref_fn 抛错不改 job 结果/状态（state=done）")
        else:
            _fail(out, f"引用提取失败污染了 job 结果/状态：{d}")
        if job.result_ref is None and "result_ref" not in d:
            _ok("引用提取失败 ⇒ result_ref 为空（不伪造引用）")
        else:
            _fail(out, f"引用提取失败竟带 ref：{d}")
        if "ValueError" in job.ref_error and d.get("ref_error") == job.ref_error:
            _ok("失败原因进独立字段 ref_error（可轮询读到）")
        else:
            _fail(out, f"ref_error 缺失/不可读：{d}")
        if job.error == "" and "error" not in d:
            _ok("error 只表示 job 失败 ⇒ 此处保持空（语义不混用）")
        else:
            _fail(out, f"error 被引用提取失败污染：{d}")


def check_cancel(out: list[str]) -> None:
    with _with_software() as sw:
        started = threading.Event()

        def long_task(ctx):
            started.set()
            for _ in range(500):
                if ctx.cancelled:
                    return {"ok": False, "cancelled": True}
                time.sleep(0.01)
            return {"ok": True}

        job = sw.jobs.submit(long_task, command="long_task")
        started.wait(2)
        sw.jobs.cancel(job.id)
        if not job.wait(5):
            _fail(out, "取消后 job 未结束")
            return
        if job.state is JobState.CANCELLED:
            _ok("cancel → cancelled（取消是显式字段，job 自行退出）")
        else:
            _fail(out, f"取消状态不符：{job.to_dict()}")


def check_concurrent_history(out: list[str]) -> None:
    with _with_software() as sw:
        n = 6
        gate = threading.Barrier(n)

        def writer(i):
            def fn(ctx):
                gate.wait(2)
                for k in range(20):
                    sw.history.append(f"key.{i}", k, f"actor{i}", "concurrency")
                return i
            return fn

        jobs = [sw.jobs.submit(writer(i), command=f"writer_{i}") for i in range(n)]
        for j in jobs:
            if not j.wait(10):
                _fail(out, f"并发 job {j.id} 未结束")
                return
        events = sw.history.events()
        seqs = [e.seq for e in events]
        if len(seqs) == n * 20 and seqs == sorted(set(seqs)):
            _ok("并发 job 写史：seq 严格单调无重复（History 进程内写口互斥）")
        else:
            _fail(out, f"并发写史 seq 损坏：{len(seqs)} 条，"
                       f"唯一 {len(set(seqs))}，单调={seqs == sorted(seqs)}")


def check_cancel_has_no_ref(out: list[str]) -> None:
    """P1-3 回归：取消的 job 不得带 result_ref（哪怕函数跑完了）。

    构造：fn **故意不看** ``ctx.cancelled``，并且返回前等一个信号——保证
    cancel 一定发生在 fn 返回之前（不是靠 sleep 赌时序），于是终态必然是
    cancelled，而 fn 也确实跑完了。这种"取消后照样跑完"的 job 若还挂引用，
    就是取消态携带成功态引用（ADR 明文禁止）。
    """
    with _with_software() as sw:
        started = threading.Event()
        release = threading.Event()

        def runs_to_completion(ctx):
            started.set()
            release.wait(5)                 # 故意忽略 ctx.cancelled
            return {"ok": True, "run_ids": ["r-1"]}

        def ref_fn(res):
            return {"kind": "toy_result", "run_ids": res["run_ids"]}

        # 对照组（同一条 ref_fn，不取消）：证明引用本身是能提取的，
        # 下面的 result_ref is None 不是因为 ref 机制坏了（不许放宽成"可能为 None"）。
        good = sw.jobs.submit(runs_to_completion, command="cancel_target",
                             result_ref_fn=ref_fn)
        started.wait(2)
        release.set()
        good.wait(5)
        if good.state is JobState.DONE \
                and good.result_ref == {"kind": "toy_result", "run_ids": ["r-1"]}:
            _ok("未取消的同类 job 照旧带 result_ref（对照片，防断言空转）")
        else:
            _fail(out, f"对照片不符：{good.to_dict()}")

        started.clear()
        release.clear()
        job = sw.jobs.submit(runs_to_completion, command="cancel_target",
                             result_ref_fn=ref_fn)
        started.wait(2)
        sw.jobs.cancel(job.id)              # fn 仍阻塞在 release 上 ⇒ 取消必先到
        release.set()                       # 放行：fn 跑完，但 job 已被取消
        if not job.wait(5):
            _fail(out, "取消的 job 未结束")
            return
        d = job.to_dict()
        if job.state is JobState.CANCELLED:
            _ok("忽略 cancelled 的函数跑完 ⇒ 终态仍是 cancelled")
        else:
            _fail(out, f"终态不是 cancelled：{d}")
        if job.result_ref is None and "result_ref" not in d:
            _ok("取消的 job 无 result_ref（不伪造引用；结果照留但不挂引用）")
        else:
            _fail(out, f"取消的 job 竟带 ref：{d}")


def check_list_under_concurrent_submit(out: list[str]) -> None:
    """P1-1 回归：并发 submit 时反复 list() 不得抛 RuntimeError。

    有界：submit 次数固定（2×300），reader 到点即停，不写成 1.5s 长跑。
    未加锁的 ``self._jobs.values()`` 迭代在这个量级下必然抛
    ``dictionary changed size during iteration``（已用修复前实现双击验证）。
    """
    with _with_software() as sw:
        errors: list[str] = []
        stop = threading.Event()
        deadline = time.time() + 10.0
        per_submitter = 300

        def reader():
            while not stop.is_set() and time.time() < deadline:
                try:
                    sw.jobs.list()
                except Exception as e:  # noqa: BLE001 - 抓的就是并发迭代异常
                    errors.append(f"{type(e).__name__}: {e}")

        def submitter():
            for _ in range(per_submitter):
                if stop.is_set():
                    return
                sw.jobs.submit(lambda ctx: None, command="poll_target")

        readers = [threading.Thread(target=reader, daemon=True) for _ in range(3)]
        subs = [threading.Thread(target=submitter, daemon=True) for _ in range(2)]
        for t in readers + subs:
            t.start()
        for t in subs:
            t.join(20)
        stop.set()
        for t in readers:
            t.join(5)
        listed = sw.jobs.list()
        if errors:
            _fail(out, f"并发 submit 时 list() 抛异常 {len(errors)} 次，"
                       f"例：{errors[0]}")
        elif len(listed) < per_submitter * 2:
            _fail(out, f"list() 少 job：{len(listed)} < {per_submitter * 2}")
        elif len({j["id"] for j in listed}) != len(listed):
            _fail(out, "list() 出现重复 id（快照不是注册表的投影）")
        else:
            _ok(f"并发 submit（{per_submitter * 2} 个）下 list() 零异常，"
                f"快照完整（{len(listed)} 条）")


def check_no_false_alarm(out: list[str]) -> None:
    with _with_software() as sw:
        ok_job = sw.jobs.submit(lambda ctx: "ok", command="noop")
        if ok_job.wait(5) and ok_job.to_dict()["state"] == JobState.DONE.value:
            _ok("合规 job 全绿（不误报）")
        else:
            _fail(out, f"合规 job 被误报：{ok_job.to_dict()}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out: list[str] = []
    print("Phase 7 作业契约判据：")
    check_submit_and_ref(out)
    check_failure(out)
    check_ref_extraction_error(out)
    check_cancel(out)
    check_cancel_has_no_ref(out)
    check_list_under_concurrent_submit(out)
    check_concurrent_history(out)
    check_no_false_alarm(out)
    if out:
        print(f"phase7_job_contract_selfcheck: FAIL（{len(out)} 条）")
        return 1
    print("phase7_job_contract_selfcheck: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
