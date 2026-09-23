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
"""

from __future__ import annotations

import itertools
import threading
from collections.abc import Callable
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
    """一个后台 job 的句柄。"""

    def __init__(self, jid: int, fn: Callable[[ExecutionContext], object]) -> None:
        self.id = f"job-{jid}"
        self._fn = fn
        self.state = JobState.PENDING
        self.result: object = None
        self.error: str = ""
        self.context = ExecutionContext()
        self._done = threading.Event()

    def status(self) -> dict:
        return {"id": self.id, "state": self.state.value,
                "progress": self.context.progress()}

    def wait(self, timeout: float | None = None) -> bool:
        return self._done.wait(timeout)


class JobRegistry:
    """单一 job 注册表（local 实现）。"""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def submit(self, fn: Callable[[ExecutionContext], object]) -> Job:
        with self._lock:
            job = Job(next(self._ids), fn)
            self._jobs[job.id] = job
        t = threading.Thread(target=self._run, args=(job,), daemon=True)
        t.start()
        return job

    def _run(self, job: Job) -> None:
        job.state = JobState.RUNNING
        try:
            if job.context.cancelled:
                job.state = JobState.CANCELLED
            else:
                job.result = job._fn(job.context)
                job.state = (JobState.CANCELLED if job.context.cancelled
                             else JobState.DONE)
        except Exception as e:  # noqa: BLE001 - 记录后归为 failed，不外抛
            job.error = f"{type(e).__name__}: {e}"
            job.state = JobState.FAILED
        finally:
            job._done.set()

    def get(self, jid: str) -> Job:
        job = self._jobs.get(jid)
        if job is None:
            raise MechaError(f"job {jid!r} 不存在", kind="unknown_job",
                             hint="用 submit 返回的 Job.id")
        return job

    def cancel(self, jid: str) -> None:
        self.get(jid).context.request_cancel()

    def list(self) -> list[dict]:
        return [j.status() for j in self._jobs.values()]
