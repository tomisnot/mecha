"""Jobs Provider：一次长计算是一个 job（总纲 §5.2⑤ Jobs 行）。

Definition：可轮询 / 可取消 / 断线可找回。local 实现**每次 ``submit`` 新起一个
daemon 线程**（不排队、不设并发上限）——**单一注册表实例**（消 D-21：v1 双
JobRunner 会把另一实例正在跑的 job 翻成 interrupted）。dsh 侧 Provider 注册到
``ctx.jobs``（注入宿主句柄，本包不 import dsh）。

## Known Limitations and Deferred Work

- local 实现**每次 ``submit`` 新起一个 daemon 线程**：既不排队也不限并发——真实
  并发度归项目引擎自己的线程模型（EL 的 SQLite 认线程是它的约束，不焊进库）。
  ⚠ 这条以前写作"单 worker 串行队列"，与实现不符，已按实现改正（台账 A4：文档
  撒谎比代码缺陷更坏；改成串行会动行为且无消费者要求，故改文档）。
- "断线可找回"这里只保证 job 状态在注册表存活期内可查；进程重启后的找回
  需要把 job 元数据落 History，等第二个项目有需求再做（n=1 推迟）。
- 完成唤醒 idle owner 是 dsh ctx.jobs 的语义；local 用轮询 status() 代替。
- ``Job`` 的字段由 job 自己的线程写、其它线程读，**没有** job 级锁；保证是
  "收尾时 ``state`` 最后一次写"（见 ``JobRegistry._run``）⇒ 读到终态即
  ``error``/``result_ref``/``finished_at`` 已定稿。``to_dict()`` 仍不是跨字段
  事务快照（``progress`` 与 ``state`` 可能取自不同瞬间）。
- ``cancel_supported`` 只钉"注册表拒不拒绝取消请求"：它**不保证**声明可取消
  的命令真能在运行中被打断（协作式检查点归宿主；本层不杀线程）。
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

    字段语义**分开**（审查 P2-4）：``error`` 只说"job 本身失败了"，即
    ``state=FAILED`` 的原因；``ref_error`` 只说"可选引用没提取出来"，此时
    job 可能是 ``state=DONE``。消费者用 ``if error:`` 判 job 失败不会被
    引用提取失败误伤——一个字段不许同时表示两件事。

    ``cancel_supported`` 是**从命令声明带过来的事实**（宿主的
    ``CommandSpec.cancel_supported``），不是本层猜的：False 的 job 调
    ``JobRegistry.cancel`` 会被结构化拒绝（``job_not_cancellable``），
    不静默接受一个做不到的取消（第三轮审查：声明必须被消费才算契约）。
    """

    def __init__(self, jid: int, fn: Callable[[ExecutionContext], object], *,
                 command: str = "", cancel_supported: bool = True) -> None:
        self.id = f"job-{jid}"
        self.command = command
        self.cancel_supported = cancel_supported
        self._fn = fn
        self.state = JobState.PENDING
        self.result: object = None
        self.result_ref: Mapping[str, object] | None = None
        self.error: str = ""
        self.ref_error: str = ""
        self.context = ExecutionContext()
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self._result_ref_fn = None
        self._done = threading.Event()

    def status(self) -> dict:
        return self.to_dict()

    def to_dict(self) -> dict:
        out = {"id": self.id, "state": self.state.value,
               "cancel_supported": self.cancel_supported,
               "progress": self.context.progress()}
        if self.command:
            out["command"] = self.command
        if self.result_ref is not None:
            out["result_ref"] = dict(self.result_ref)
        if self.error:
            out["error"] = self.error
        if self.ref_error:
            out["ref_error"] = self.ref_error
        if self.started_at is not None:
            out["started_at"] = self.started_at
        if self.finished_at is not None:
            out["finished_at"] = self.finished_at
        return out

    def wait(self, timeout: float | None = None) -> bool:
        return self._done.wait(timeout)


class JobRegistry:
    """单一 job 注册表（local 实现）。

    线程安全边界：``_lock`` 只保护 ``_jobs`` 这张表——``submit``（写）、
    ``get``/``list``/``cancel``（读）都经它，所以并发 submit 下轮询/取句柄
    不会被字典迭代撕裂。锁内**不做回调、不做 IO、不读 Job 字段**（只取快照），
    因此不存在重入或锁序问题；``Job`` 自身的字段由 job 线程写（见模块
    Known Limitations 的收尾顺序保证）。
    """

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def submit(self, fn: Callable[[ExecutionContext], object], *,
               command: str = "",
               cancel_supported: bool = True,
               result_ref_fn: Callable[[object], Mapping[str, object] | None]
               | None = None) -> Job:
        """提交一个后台 job。

        ``result_ref_fn`` 从 job 结果提取不透明结果引用；提取自身抛错写 job 的
        ``ref_error``（只影响可选引用，不影响 job 结果与 ``error``——后者只说
        job 失败）。

        ``cancel_supported`` 由宿主从该命令的声明面取来（本层不认识命令面，
        只保管这个事实）：False ⇒ 该 job 的 ``cancel`` 被结构化拒绝。默认
        True 保持旧调用零改动。
        """
        with self._lock:
            job = Job(next(self._ids), fn, command=command,
                      cancel_supported=cancel_supported)
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
        ``result_ref`` / ``error`` / ``ref_error`` 都定稿之后、``_done.set()``
        之前）：轮询者看到 ``done`` 时引用已定稿，``wait()`` 返回时终态一定
        已落地（审查 P2-3）。

        两条失败路径**分开**：``_fn`` 抛错 ⇒ ``state=FAILED`` + ``error``；
        ``result_ref_fn`` 抛错 ⇒ ``state=DONE`` + ``ref_error``（job 没失败）。
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
                        # 独立字段：job 本身成功了（state=DONE），失败的只是
                        # "可选引用"这一步。写进 ``error`` 会让消费者用
                        # ``if error:`` 把成功的 job 判成失败（审查 P2-4）。
                        job.ref_error = f"result_ref 提取失败：{type(e).__name__}: {e}"
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

    def cancel(self, jid: str) -> dict:
        """请求取消。**返回真话**——不是一个什么都不告诉调用方的 ``None``。

        ⚠ 为什么改：旧实现只调 ``context.request_cancel()``、**什么都不返回** ⇒
        **调用方只能自己编**——下游就编出过「**取消已登记**」这种话；而对**已经跑完**
        的 job 说"已登记/已取消"是**谎话**（AI 会以为它停了）。**一个什么都不告诉你的
        接口，就是在邀请调用方自己编。**

        返回 ``{"job_id", "state_before", "terminal", "cancel_requested"}``：

        * ``terminal=True``（已 done / failed / cancelled）⇒ ``cancel_requested=False``
          ——**取消没有被请求**（本来就是终态，没什么可取消的），调用方**不能**说"已取消"；
        * 否则确实置位了取消标志 ⇒ ``cancel_requested=True``。⚠ 取消是**协作式**的：
          函数可能没查 ``ctx.cancelled`` 就跑完 ⇒ **真正的终态由 ``status()``/``wait()``
          说了算**，不看这个返回值。

        取句柄经 ``get``（持锁读），真正的取消是 ``context.request_cancel()``——
        ``threading.Event`` 自身线程安全，不需要注册表锁；注册表锁只保护 ``_jobs``
        这张表。

        ``cancel_supported=False`` 的 job **拒收**取消：抛
        ``MechaError(kind="job_not_cancellable")``，且**不**置位
        ``context.cancel_event``——静默接受一个做不到的取消会让调用方以为
        "已经停了"，是最坏的一类失败（第三轮审查：声明不被消费就只是注释）。
        """
        job = self.get(jid)
        if not job.cancel_supported:
            where = f"命令 {job.command!r} 的" if job.command else "该 job 的"
            raise MechaError(
                f"job {jid!r} 不可取消：{where}声明是 cancel_supported=False",
                kind="job_not_cancellable",
                hint="这个 job 没有取消点（中断不安全或内部不可打断）；"
                     "取消请求已被拒绝，它的取消标志未被置位",
                suggest="用 wait/status 轮询到终态再读结果，不要反复重试 cancel",
            )
        before = job.state
        terminal = before in (JobState.DONE, JobState.FAILED, JobState.CANCELLED)
        if not terminal:
            job.context.request_cancel()
        return {"job_id": jid, "state_before": before.value,
                "terminal": terminal, "cancel_requested": not terminal}

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
