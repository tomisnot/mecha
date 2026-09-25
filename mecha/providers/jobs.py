"""Jobs Provider：一次长计算是一个 job（总纲 §5.2⑤ Jobs 行）。

Definition：可轮询 / 可取消 / 断线可找回。local 实现用单线程串行队列——
**单一注册表实例**（消 D-21：v1 双 JobRunner 会把另一实例正在跑的 job 翻成
interrupted）。dsh 侧 Provider 注册到 ``ctx.jobs``（注入宿主句柄，本包不 import dsh）。

## Known Limitations and Deferred Work

- local 实现是**单 worker 串行**（线程），不是并发池——真实并发度归项目引擎
  自己的线程模型（EL 的 SQLite 认线程是它的约束，不焊进库）。
- "断线可找回"这里只保证 job 状态在注册表存活期内可查；进程重启后的找回
  需要把 job 元数据落 History，等第二个项目有需求再做（n=1 推迟）。
- 完成唤醒 idle owner 是 dsh ctx.jobs 的语义；local 用轮询 status() 代替。
- ``Job`` 的字段由 job 自己的线程写、其它线程读，**没有** job 级锁；保证是
  "收尾时 ``state`` 最后一次写"（见 ``JobRegistry._run``）⇒ 读到终态即
  ``error``/``result_ref``/``finished_at`` 已定稿。``to_dict()`` 仍不是跨字段
  事务快照（``progress`` 与 ``state`` 可能取自不同瞬间）。
"""

from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Callable, Mapping
from enum import Enum

from ..errors import MechaError
from ..surface import ExecutionContext


class JobState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Job:
    """一个后台 job 的句柄（Job Contract 的单位）。

    ``result_ref`` 与命令面 ``CommandResult.result_ref`` 同语义：**不透明的
    宿主结果引用**（对 ML 是 ``{"kind": "ml_command", "run_ids": [...]}``），
    由宿主经 ``result_ref_fn`` 从 job 结果里提取。Job 自身不解释它。
    """

    def __init__(self, jid: int, fn: Callable[[ExecutionContext], object], *,
                 command: str = "") -> None:
        self.id = f"job-{jid}"
        self.command = command
        self._fn = fn
        self.state = JobState.PENDING
        self.result: object = None
        self.result_ref: Mapping[str, object] | None = None
        self.error: str = ""
        self.context = ExecutionContext()
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self._result_ref_fn = None
        self._done = threading.Event()

    def status(self) -> dict:
        return self.to_dict()

    def to_dict(self) -> dict:
        out = {"id": self.id, "state": self.state.value,
               "progress": self.context.progress()}
        if self.command:
            out["command"] = self.command
        if self.result_ref is not None:
            out["result_ref"] = dict(self.result_ref)
        if self.error:
            out["error"] = self.error
        if self.started_at is not None:
            out["started_at"] = self.started_at
        if self.finished_at is not None:
            out["finished_at"] = self.finished_at
        return out

    def wait(self, timeout: float | None = None) -> bool:
        return self._done.wait(timeout)


class JobRegistry:
    """单一 job 注册表（local 实现）。"""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def submit(self, fn: Callable[[ExecutionContext], object], *,
               command: str = "",
               result_ref_fn: Callable[[object], Mapping[str, object] | None]
               | None = None) -> Job:
        """提交一个后台 job。

        ``result_ref_fn`` 从 job 结果提取不透明结果引用；提取自身抛错归
        job 的 ``error``（只影响引用，不影响 job 结果——引用是可选的）。
        """
        with self._lock:
            job = Job(next(self._ids), fn, command=command)
            job._result_ref_fn = result_ref_fn
            self._jobs[job.id] = job
        t = threading.Thread(target=self._run, args=(job,), daemon=True)
        t.start()
        return job

    def _run(self, job: Job) -> None:
        """跑 job 并收尾。

        **终态与引用同源**（ADR：失败/取消的 job 不伪造 ``result_ref``）：
        ``result_ref`` 只在 ``state=DONE`` 这一条路径上提取，cancelled/failed
        一律留空——取消态携带成功态引用就是真值混用（第三轮审查 P1-3）。
        ``state`` 的终态是收尾的**最后一次写**（在 ``finished_at`` /
        ``result_ref`` / ``error`` 都定稿之后、``_done.set()`` 之前）：轮询者
        看到 ``done`` 时引用已定稿，``wait()`` 返回时终态一定已落地
        （审查 P2-3）。
        """
        job.state = JobState.RUNNING
        job.started_at = time.time()
        terminal = JobState.FAILED          # BaseException 中断也不留未绑定名
        try:
            cancelled = job.context.cancelled
            if not cancelled:
                job.result = job._fn(job.context)
                # 取消是协作式的，函数可能没查 ctx.cancelled 就跑完了：
                # 返回值照留（真发生过），但不给它挂引用。
                cancelled = job.context.cancelled
            if cancelled:
                terminal = JobState.CANCELLED
            else:
                ref_fn = getattr(job, "_result_ref_fn", None)
                if ref_fn is not None:
                    try:
                        ref = ref_fn(job.result)
                        if ref is not None:
                            job.result_ref = dict(ref)
                    except Exception as e:  # noqa: BLE001 - 引用失败不改 job 结果
                        job.error = f"result_ref 提取失败：{type(e).__name__}: {e}"
                terminal = JobState.DONE
        except Exception as e:  # noqa: BLE001 - 记录后归为 failed，不外抛
            job.error = f"{type(e).__name__}: {e}"
            terminal = JobState.FAILED
        finally:
            job.finished_at = time.time()
            job.state = terminal
            job._done.set()

    def get(self, jid: str) -> Job:
        """按 id 取 job。读侧也进锁：``dict.get`` 在 CPython 下原子是**实现
        细节**，JobRegistry 的线程安全契约不该建立在解释器实现上（审查 P1-1）。"""
        with self._lock:
            job = self._jobs.get(jid)
        if job is None:
            raise MechaError(f"job {jid!r} 不存在", kind="unknown_job",
                             hint="用 submit 返回的 Job.id")
        return job

    def wait(self, jid: str, timeout: float | None = None) -> dict:
        """等待 job 结束并返回状态（local 实现是轮询替代 dsh 的唤醒语义）。"""
        job = self.get(jid)
        job.wait(timeout)
        return job.to_dict()

    def cancel(self, jid: str) -> None:
        self.get(jid).context.request_cancel()

    def list(self) -> list[dict]:
        """列出全部 job 状态（轮询面）。

        先在锁内取**快照**再投影：``self._jobs.values()`` 的迭代碰上并发
        ``submit`` 会抛 ``RuntimeError: dictionary changed size during
        iteration``（审查 P1-1 实测 1.5s 内 25–35 次）。持锁只覆盖取快照，
        ``to_dict()`` 在锁外做——不把投影开销带进临界区。
        """
        with self._lock:
            snapshot = list(self._jobs.values())
        return [j.to_dict() for j in snapshot]
