"""Phase 7 判据：Job Contract（作业契约）——可轮询/可取消/结果引用。

Job Contract 是第二个宿主（ML Toolbox）逼出的通用机制：训练与调参是长任务，
命令面是同步返回，宿主需要把"一次长命令"提交成后台 job，并在 job 上挂
**不透明结果引用**（``result_ref``），与 ``CommandResult.result_ref`` 同语义。

本判据只测机制，不测任何真宿主的 job 内容：

  A 提交/状态：submit 返回 Job，``state`` 从 pending→running→done，
    ``result`` 保留返回值，``command`` 与 ``result_ref`` 可读。
  B 失败：fn 抛错 → state=failed + error 有类型/消息，result_ref 为空，
    异常不逃出注册表。
  C 取消：fn 在取消后自行退出 → state=cancelled（取消是显式字段，不是杀线程）。
  D 并发写史安全：多个 job 并发 append History，seq 仍严格单调无重复
    （History 的进程内写口互斥；跨进程仍靠写租约）。
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
    check_cancel(out)
    check_concurrent_history(out)
    check_no_false_alarm(out)
    if out:
        print(f"phase7_job_contract_selfcheck: FAIL（{len(out)} 条）")
        return 1
    print("phase7_job_contract_selfcheck: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
