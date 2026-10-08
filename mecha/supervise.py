"""Supervisor：一台软件里**所有子进程的唯一管理者**（spawn / 就绪 / 重启 / 收尾）。

## 为什么它存在（替代两家的 launcher）

EL 与 ML 各有一份 launcher，四条实测缺陷**逐字同源**；本模块把那段机制**收成一份**，
并把四条各自**根治**（每条都配判据，见 ``checks/launch_selfcheck.py``）：

1. **不许开黑框**：两家都用 ``CREATE_NEW_CONSOLE``（EL ``launcher.py:42`` / ML ``:100``）
   ⇒ AI 模式弹 4 个黑框。这里恒用 ``CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP``：
   前者不开窗，后者给"整组信号"用的**新进程组**（两家都**没有**它）。
2. **就绪必须真探活**：EL ``_wait_mcp_port`` 只判"端口文件存在且非空" ⇒ 实测把
   **264.5 小时前、没人在听**的残留判成"就绪"（假绿）。这里先记**spawn 前的
   ``(port, mtime)`` 快照**，要求"文件消失过、或内容/mtime 变了"，**再真探活**
   （HTTP 或 TCP），超时**响亮失败**。
3. **停要连整棵树**：只 terminate 直系子进程会留下持端口的孤儿（ML ``_kill_tree``
   的注释记着实测：``dsh.CMD→cmd.exe→node``）。这里先礼（``terminate``）→ 等**真退出**
   → 超时才杀树（Windows ``taskkill /T /F``，POSIX ``killpg``）。
4. **端口必须回读**：ML 把 ``pick_dsh_port()`` 的**预选值**写进运行期描述符
   （``:762``）——预选与实际绑定可能在窗口期分叉。本模块**只认回读值**：
   子进程写出的端口文件里是什么，``state()["port"]`` 就是什么；写不出就**不报端口**。

## 术语沿用 OTP（不造新词）
``ChildSpec.restart`` 用 ``permanent`` / ``transient`` / ``temporary``；
``stop`` 的语义等同 OTP 的 ``terminate_child``（**停进程、留 spec**）。

## Known Limitations and Deferred Work

- **不含 aggregator**：本步只做"起一组子进程 + 看状态"。``significant`` 与
  ``Strategy`` 在这里**只被如实记录与上报**，不驱动编排（步骤 2 才接）。
- 就绪探测是**轮询**（默认 0.2s 间隔）：不做事件驱动——子进程是任意程序，
  没有可靠的"我已就绪"事件通道，轮询是唯一普适做法。
- 重启退避是**线性**（``period_s`` 窗口内数次数，超限即停）：不引入指数退避——
  没有实测依据说明需要（n=1 不预建）。
- ``env`` 与父环境**合并**：不做"完全替换"语义。理由：项目声明通常只想加几个变量；
  真要完全隔离应显式传一份完整环境（那时父环境本来也不该泄漏进去，属部署问题）。
"""

from __future__ import annotations

import ctypes
import os
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .errors import MechaError
from .portfile import read_port

RestartPolicy = Literal["permanent", "transient", "temporary"]
ChildState = Literal["stopped", "starting", "ready", "running", "unhealthy", "exited"]

#: 就绪轮询间隔（秒）。子进程是任意程序 ⇒ 只能轮询（见模块 Known Limitations）。
_POLL_S = 0.2
#: 单个日志文件的大小上限；超过就轮转（**绝不 DEVNULL**：没有日志的失败查不动）。
_LOG_MAX_BYTES = 1 << 20
#: 保留几个历史日志文件（.1/.2/...）。
_LOG_KEEP = 3


@dataclass(frozen=True)
class ReadySpec:
    """一条 child 的"就绪"判据。

    ⚠ **三条要一起看**（缺一条就会重现 EL 那个假绿）：
    判据 = **端口文件发生过变化**（相对 spawn 前的快照）**且** 真探活通过。
    """

    #: 相对项目根的端口文件名（如 ``.mcp-port``）；``None`` ⇒ 不看端口文件。
    port_file: str | None = None
    #: 形如 ``http://127.0.0.1:{port}/health``；``{port}`` 用**回读**到的端口填。
    probe: str | None = None
    #: 对回读到的端口做 TCP 连通性探测。
    tcp: bool = False
    timeout_s: float = 90.0


@dataclass(frozen=True)
class StaleItem:
    """上次留下的陈旧残留（孤儿进程 / 陈旧端口文件）。

    它必须能被 ``Supervisor.clean_stale()`` **执行**掉、并**出现在状态里**——
    "清掉了哪个孤儿"只写进日志就没了，等于没有可核对的账（实测教训：
    EL 的 ``.mcp-port`` 曾静躺 **264.5 小时**、还被人当成"就绪"）。
    """

    #: ``"orphan"``（活着的孤儿进程）或 ``"port_file"``（陈旧端口文件）。
    kind: Literal["orphan", "port_file"]
    #: 人可读标识（orphan 给 ``"<id> pid=1234"``；port_file 给路径）。
    target: str
    #: 为什么判它是残留（写进状态页，让人能复核这个判断）。
    why: str
    #: orphan 的 pid（port_file 为 None）。
    pid: int | None = None
    #: port_file 的路径（orphan 为 None）。
    path: Path | None = None


@dataclass(frozen=True)
class ChildSpec:
    """要托管的子进程。``id`` 全局唯一，同时是日志名（``runtime/logs/<id>.log``）。"""

    id: str
    argv: tuple[str, ...]
    cwd: Path
    #: 与父环境**合并**（同名以这里为准）。
    env: Mapping[str, str] = field(default_factory=dict)
    ready: ReadySpec | None = None
    #: OTP 的三档：permanent=总重启 / transient=非正常退出才重启 / temporary=从不重启。
    restart: RestartPolicy = "permanent"
    #: 供步骤 2 的 aggregator / auto_shutdown 用；本步只如实记录并上报。
    significant: bool = False
    #: 先礼后兵：``terminate`` 后等这么久，超时才杀树。
    stop_timeout: float = 8.0
    #: 该 child 要持的**写租约根**（``write_lease`` 按数据根独占）。
    #: ⚠ 它让"**同一数据根同一时刻只许一个 child 在跑**"可**机械校验**：
    #: 软件本体与它的 human adapter（``…:manual``）同根 ⇒ **互斥**，这正是
    #: "切手动 = 换一条腿"而不是"两条腿同时活"的表达（修正 4）。
    data_root: Path | None = None


def _now() -> float:
    return time.monotonic()


def pid_alive(pid: int) -> bool:
    """探活一个 pid。**Windows 上不许用 ``os.kill(pid, 0)``**。

    ⚠ 为什么：Windows 上 ``os.kill(pid, 0)`` 对非 0 信号会直接
    ``TerminateProcess``（把要探的进程**杀掉**）——ML 的 ``pid_alive`` 注释记着同一实测。
    ⇒ 这里用 ``OpenProcess`` + ``GetExitCodeProcess == STILL_ACTIVE``。
    """
    if not pid or pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True          # 存在但没权限（仍算活着）
        return True
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32           # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _spawn_flags() -> int:
    """spawn 用的 creationflags：**不开窗** + **新进程组**。

    ⚠ 两家旧 launcher 用的是 ``CREATE_NEW_CONSOLE``（开窗！）且没有进程组 ⇒
    AI 模式 4 个黑框、且"整组一起停"没有依据。这两个旗标是本模块的第一条铁律。
    """
    if os.name != "nt":
        return 0
    return (subprocess.CREATE_NO_WINDOW                     # type: ignore[attr-defined]
            | subprocess.CREATE_NEW_PROCESS_GROUP)          # type: ignore[attr-defined]


# --------------------------------------------------------------------------- Win32 Job
#
# ⚠ 为什么需要 Job Object（而不是只靠 taskkill /T）：
# ``taskkill /PID <p> /T`` 是**沿父 PID 走子树**的 ⇒ **父一退出它就找不到那棵树**。
# 实测（本仓判据第 ⑦ 条）：先礼（terminate）成功、父退出之后，孙进程**照样活着**
# 并继续占着端口——正是 ML 注释里那个 "dsh.CMD→cmd.exe→node 留下孤儿"。
# Job 的做法是把"整棵树一起死"变成**内核保证**：进程句柄一关，
# ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` 让系统把 job 里**剩下的所有**进程都杀掉。

if os.name == "nt":
    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _JobObjectExtendedLimitInformation = 9
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_TERMINATE = 0x0001

    def _make_kill_on_close_job(pid: int) -> int | None:
        """把 ``pid`` 放进一个 "关掉就杀光" 的 job，返回 job 句柄（失败返回 None）。

        ⚠ **自己的进程可能已在别的 job 里**（CI/容器常见）⇒ 嵌套 job 要 Win8+；
        失败就返回 None，让调用方退回 ``taskkill`` 路径（不假装成功）。
        """
        k32 = ctypes.windll.kernel32                  # type: ignore[attr-defined]
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(job, _JobObjectExtendedLimitInformation,
                                           ctypes.byref(info), ctypes.sizeof(info)):
            k32.CloseHandle(job)
            return None
        handle = k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, int(pid))
        if not handle:
            k32.CloseHandle(job)
            return None
        try:
            if not k32.AssignProcessToJobObject(job, handle):
                k32.CloseHandle(job)
                return None
        finally:
            k32.CloseHandle(handle)
        return job

    def _close_job(job: int | None) -> None:
        """关 job 句柄 ⇒ ``KILL_ON_JOB_CLOSE`` 生效，job 里剩下的进程全被杀。"""
        if job:
            ctypes.windll.kernel32.CloseHandle(job)   # type: ignore[attr-defined]
else:
    def _make_kill_on_close_job(pid: int) -> int | None:
        return None                                   # POSIX 用进程组（killpg），不需要 job

    def _close_job(job: int | None) -> None:
        return None


class _RollingLog:
    """把一个流泵进 ``<dir>/<id>.log``，超过上限就轮转（``.1``/``.2``/…）。

    ⚠ **绝不 DEVNULL**：旧 launcher 把子进程输出丢掉，出事后没有任何线索。
    轮转是为了不让一个长跑进程把盘写满（上限与保留数见模块常量）。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def _rotate_if_needed(self) -> None:
        try:
            if self.path.stat().st_size < _LOG_MAX_BYTES:
                return
        except OSError:
            return
        for i in range(_LOG_KEEP - 1, 0, -1):
            src = self.path.with_suffix(self.path.suffix + f".{i}")
            if src.exists():
                try:
                    src.replace(self.path.with_suffix(self.path.suffix + f".{i + 1}"))
                except OSError:
                    pass
        try:
            self.path.replace(self.path.with_suffix(self.path.suffix + ".1"))
        except OSError:
            pass

    def write(self, chunk: bytes | str) -> None:
        if not chunk:
            return
        data = chunk.encode("utf-8", "replace") if isinstance(chunk, str) else chunk
        with self._lock:
            self._rotate_if_needed()
            try:
                with self.path.open("ab") as fh:
                    fh.write(data)
            except OSError:
                pass          # 日志写不进去不该拖垮进程管理（但绝不因此 DEVNULL 掉源头）

    def tail(self, lines: int = 20) -> list[str]:
        try:
            text = self.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        return text.splitlines()[-lines:]


class _Child:
    """一个受管 child 的**可变**运行态（``ChildSpec`` 是冻结的声明）。"""

    def __init__(self, spec: ChildSpec, supervisor: "Supervisor") -> None:
        self.spec = spec
        self.sup = supervisor
        #: 本次 spawn 实际使用的 spec（``prepare`` 钩子可替换它，见 :meth:`spawn`）。
        self.current: ChildSpec = spec
        self.proc: subprocess.Popen[bytes] | None = None
        self.state: ChildState = "stopped"
        self.port: int | None = None
        self.ready_evidence: str = ""
        self.last_error: str = ""
        self.restarts: int = 0
        self.exit_code: int | None = None
        #: 就绪快照：spawn **之前**的端口文件 (port, mtime_ns) 或 None（当时不存在）。
        self._pre: tuple[int | None, int | None] | None = None
        self._log = _RollingLog(supervisor.log_dir / f"{spec.id}.log")
        self._pumps: list[threading.Thread] = []
        #: 用户主动停 ⇒ 监控线程不要再判"非正常退出"（否则会自己重启自己）。
        self._stopping = False
        #: Windows Job 句柄（关掉即杀光 job 内剩余进程）；POSIX 恒 None。
        self._job: int | None = None
        self._restart_times: list[float] = []
        self._lock = threading.Lock()

    # ---- 日志 ----
    @property
    def log_path(self) -> Path:
        return self._log.path

    def log_tail(self, lines: int = 20) -> list[str]:
        return self._log.tail(lines)

    # ---- 端口文件快照 ----
    def _port_file(self) -> Path | None:
        name = self.current.ready.port_file if self.current.ready else None
        if not name:
            return None
        return (self.current.cwd / name) if not Path(name).is_absolute() else Path(name)

    def _snapshot(self) -> tuple[int | None, int | None]:
        pf = self._port_file()
        if pf is None:
            return (None, None)
        try:
            st = pf.stat()
        except OSError:
            return (None, None)                       # 不存在 ⇒ 记 "没有"
        return (read_port(pf), st.st_mtime_ns)

    def _port_file_changed(self) -> bool:
        """相对 spawn 前快照，"文件消失过、或内容/mtime 变了"。

        ⚠ 这一条是治 EL 那个假绿的核心：**存在且非空 ≠ 就绪**——
        264.5 小时前的残留同样"存在且非空"。
        """
        want = self._pre
        if want is None:                              # 没声明端口文件 ⇒ 不由文件判
            return True
        now = self._snapshot()
        if now == (None, None):
            return False                              # 文件不在 ⇒ 一定没就绪
        if want == (None, None):
            return True                               # spawn 前没有、现在有了
        return now != want                            # 内容或 mtime 变了

    # ---- spawn / 就绪 ----
    def spawn(self) -> None:
        #: 本次 spawn 实际用的 spec：钩子可在 ``prepare`` 里**换掉它**（步骤 2 的接法：
        #: 把聚合端点的地址塞进 dsh 的 env/argv）。默认就是声明的那份。
        self.current: ChildSpec = self.spec
        self._fire_hooks("prepare")                   # ⭐ 步骤 2 在此拿到依赖的端口/URL
        spec = self.current
        self._pre = self._snapshot()                  # ⭐ 铁律 2 的前半：先记快照
        env = dict(os.environ)
        env.update({str(k): str(v) for k, v in spec.env.items()})
        # 关掉 Python 的 -P（安全路径）语义干扰：这里**不做**，只如实合并环境。
        env.setdefault("PYTHONUNBUFFERED", "1")       # 否则子进程输出会卡在缓冲里
        flags = _spawn_flags()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.proc = subprocess.Popen(           # noqa: S603 - argv 是声明给的，非 shell
                list(spec.argv), cwd=str(spec.cwd), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=flags,
                **({} if os.name == "nt" else {"start_new_session": True}),
            )
        except OSError as exc:
            self.proc = None
            self.state = "unhealthy"
            self.last_error = f"spawn 失败：{type(exc).__name__}: {exc}"
            raise MechaError(
                f"child {spec.id!r} 起不来：{exc}",
                kind="child_spawn_failed",
                hint="检查 argv/cwd 是否存在、以及 dsh 可执行是否在 PATH 上",
            ) from exc
        self.state = "starting"
        self.exit_code = None
        # ⭐ 放进 kill-on-close 的 job：**整棵树一起死**由内核保证
        #   （见 ``_make_kill_on_close_job`` 的注释：taskkill /T 在父退出后找不到树）
        self._job = _make_kill_on_close_job(self.proc.pid)
        self._pumps = self._start_pumps()
        threading.Thread(target=self._watch, name=f"watch-{spec.id}", daemon=True).start()

    def _start_pumps(self) -> list[threading.Thread]:
        proc = self.proc
        assert proc is not None and proc.stdout is not None
        out = proc.stdout

        def pump() -> None:
            # ⚠ **按行迭代**，不要用 ``out.read(4096)``：后者会**阻塞到缓冲区满**
            #   （或流关闭）才返回 ⇒ 子进程只写了一行时日志文件**根本不会出现**
            #   （本判据第一次跑就是这样红的）。按行读是"行一到就写"。
            try:
                for line in out:
                    self._log.write(line)
            except (OSError, ValueError):
                pass                                   # 进程没了/管道关了：正常收场
            finally:
                try:
                    out.close()
                except OSError:
                    pass

        t = threading.Thread(target=pump, name=f"log-{self.spec.id}", daemon=True)
        t.start()
        return [t]

    def _watch(self) -> None:
        """等真退出；按 restart 策略决定要不要重启。"""
        proc = self.proc
        if proc is None:
            return
        code = proc.wait()
        with self._lock:
            self.exit_code = code
            self.port = None
            if self._stopping:
                self.state = "exited"
                return
            if self.state != "unhealthy":
                self.state = "exited"
            self.last_error = self.last_error or f"进程退出（exit={code}）"
            if not self._should_restart(code):
                return
            if not self._within_budget():
                self.state = "unhealthy"
                self.last_error = (f"重启超限（{self.current.restart}，"
                                   f"{len(self._restart_times)} 次 / "
                                   f"{self.sup.max_restart_period_s:.0f}s）⇒ 停止重试")
                self.sup._write_state()                # 上报给状态页
                return
            self.restarts += 1
            self._restart_times.append(_now())
        # 锁外做重活（spawn + 等就绪），避免长时间持锁
        try:
            self.spawn()
        except MechaError as exc:
            self.state = "unhealthy"
            self.last_error = str(exc)
            self.sup._write_state()
            return
        self.wait_ready()
        self.sup._write_state()

    def _should_restart(self, code: int) -> bool:
        if self.current.restart == "temporary":
            return False
        if self.current.restart == "transient":
            return code != 0
        return True                                    # permanent

    def _within_budget(self) -> bool:
        cutoff = _now() - self.sup.max_restart_period_s
        self._restart_times = [t for t in self._restart_times if t >= cutoff]
        return len(self._restart_times) < self.sup.max_restarts

    def wait_ready(self) -> None:
        """轮询到就绪，或**响亮失败**（记 ``unhealthy`` + ``last_error``，不抛给调用方）。

        ## ``state`` 的两个"好"档必须自洽（提醒 1）
        - ``ready``：**声明了就绪判据且判据通过** ⇒ ``ready_evidence`` 里必然有
          "端口文件变了 + 探活通过"这类**可复核的证据**；
        - ``running``：**没声明 ready**（``ReadySpec`` 为 None）⇒ 起了就算在跑，
          ``ready_evidence`` 明写"未声明 ready"。
        ⇒ 断言"就绪"要看 ``state == "ready"`` **且** ``ready_evidence`` 里有证据，
        而不是只看一个字符串（判据里就是这么钉的）。
        """
        ready = self.current.ready
        if ready is None:
            self.state = "running"                     # 没声明就绪判据：起了就算在跑
            self.ready_evidence = "未声明 ready（起了即在跑）"
            self._fire_hooks("ready")
            return
        deadline = _now() + ready.timeout_s
        last_why = "尚无任何就绪证据"
        while _now() < deadline:
            proc = self.proc
            if proc is None or proc.poll() is not None:
                # 进程已死 ⇒ 立刻响亮失败，不等满超时（等满只会浪费 90s）
                self.state = "unhealthy"
                self.last_error = (f"就绪前进程已退出（exit={proc.poll() if proc else '?'}）；"
                                   f"最后一行日志：{self._tail_line()}")
                return
            if self._port_file_changed():
                port = self._read_port_now()
                ok, why = self._probe(port)
                if ok:
                    self.port = port
                    self.state = "ready"
                    self.ready_evidence = self._evidence(port, why)
                    self._fire_hooks("ready")
                    return
                last_why = why
            else:
                last_why = self._stale_reason()
            time.sleep(_POLL_S)
        self.state = "unhealthy"
        self.ready_evidence = f"超时（{ready.timeout_s:.0f}s）未就绪：{last_why}"
        self.last_error = self.ready_evidence

    def is_ready(self) -> bool:
        """**判"就绪"的唯一入口**（提醒 1）。

        要求 **两个条件同时成立**：``state`` 是判据通过后的那个档，**且** 证据非空。
        ⇒ 只把 ``state`` 设成 ``"ready"`` 而不留证据，**不算就绪**（判据会红）。
        """
        return self.state == "ready" and bool(self.ready_evidence.strip())

    def _fire_hooks(self, point: str) -> None:
        """就绪后/启动前的外部钩子（步骤 2 用它把端口接给聚合端点）。"""
        for fn in (self.sup._prepare_hooks if point == "prepare" else self.sup._ready_hooks):
            try:
                fn(self.spec, self)
            except Exception as exc:                   # noqa: BLE001 - 钩子不许拖垮进程管理
                self.last_error = f"{point} 钩子抛错：{type(exc).__name__}: {exc}"

    def _stale_reason(self) -> str:
        pf = self._port_file()
        if pf is None:
            return "未声明端口文件"
        if not pf.exists():
            return f"端口文件尚未出现（{pf.name}）"
        return (f"端口文件仍是 spawn 前的旧值（{pf.name}）"
                f"⇒ 存在且非空**不算就绪**（EL 实测的假绿就是这样来的）")

    def _read_port_now(self) -> int | None:
        pf = self._port_file()
        return read_port(pf) if pf is not None else None

    def _probe(self, port: int | None) -> tuple[bool, str]:
        ready = self.current.ready
        assert ready is not None
        if ready.probe is None and not ready.tcp:
            return True, "端口文件已变化"
        if port is None:
            return False, "端口文件里读不到端口"
        if ready.tcp:
            import socket
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                    pass
            except OSError as exc:
                return False, f"TCP {port} 连不上（{type(exc).__name__}）"
            return True, f"TCP {port} 可连"
        url = (ready.probe or "").replace("{port}", str(port))
        import urllib.error
        import urllib.request
        try:
            with urllib.request.urlopen(url, timeout=2.0) as resp:   # noqa: S310 - http(s) 由声明给
                code = int(getattr(resp, "status", 0))
        except urllib.error.HTTPError as exc:
            return False, f"{url} 返回 {exc.code}（非 2xx）"
        except OSError as exc:
            return False, f"{url} 连不上（{type(exc).__name__}）"
        if 200 <= code < 300:
            return True, f"{url} 返回 {code}"
        return False, f"{url} 返回 {code}（非 2xx）"

    def _evidence(self, port: int | None, why: str) -> str:
        pre = self._pre
        changed = ("端口文件此前不存在" if pre == (None, None)
                   else f"端口文件内容/mtime 变了（旧 {pre[0]!r} → 新 {port!r}）")
        return f"{changed} + {why}"

    def _tail_line(self) -> str:
        lines = self.log_tail(1)
        return lines[0] if lines else "(日志为空)"

    # ---- 停 ----
    def terminate(self) -> None:
        """先礼后兵（铁律 3）：terminate → 等**真退出** → **仍然**清整棵树。

        ⚠ 为什么"先礼成功"之后**还要**杀树：直系子进程礼貌退出**不代表它派生的
        孙进程也退**（实测：honor 退出后孙进程继续跑并占着端口）。
        ⇒ 礼是给"有机会自己收尾"的，**不是**给"整棵树的死亡许可"。
        """
        proc = self.proc
        self._stopping = True
        if proc is None:
            self.state = "stopped"
            return
        if proc.poll() is None:
            try:
                proc.terminate()                       # 先礼：给它机会自己收尾
            except OSError:
                pass
            if os.name == "nt":
                # ⚠ **必须趁父进程还在**：``taskkill /T`` 是**沿父 PID 走子树**的，
                # 父一退出，它就找不到那棵树了（实测：礼成之后孙进程活得好好的）。
                # ⇒ 礼后**立刻**清树，不等"礼"的结果。
                self.kill_tree()
                self._await_exit(proc, self.current.stop_timeout)
            else:
                if not self._await_exit(proc, self.current.stop_timeout):
                    self.kill_tree()
                else:
                    self.kill_tree()                   # 进程组还在 ⇒ 清掉残留的孙进程
        else:
            self.kill_tree()
        # ⭐ 最后关 job：把 job 里**剩下的**（孙）进程一起带走（内核保证）。
        _close_job(self._job)
        self._job = None
        self.state = "stopped"
        self.port = None
        self.proc = None

    def kill_tree(self) -> None:
        """杀**整棵树**：Windows ``taskkill /T /F``；POSIX ``killpg``。

        ⚠ **父进程已经退出时也要杀**（这是本条最容易做错的地方）：
        "先礼"成功、父进程退出之后，它派生的孙进程**会活下来**（ML 的注释记着实测：
        ``dsh.CMD→cmd.exe→node`` 留下持 3081 的孤儿 ⇒ 端口"莫名被占"）。
        所以这里**不以"父还活着"为前提**——按 **pid / 进程组**杀，父已死也照样执行。
        """
        proc = self.proc
        if proc is None:
            return
        if os.name == "nt":
            # 父已退出也能用：/T 沿**它名下的子树**清（同组的孙进程会被带走）
            subprocess.run(                                # noqa: S603,S607 - 固定可执行
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, check=False)
        else:
            # 父已退出时 os.getpgid(pid) 会抛 ⇒ 进程组 id == 组长 pid（start_new_session）
            try:
                pgid = os.getpgid(proc.pid)
            except (OSError, ProcessLookupError):
                pgid = proc.pid
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                try:
                    proc.kill()
                except OSError:
                    pass
        self._await_exit(proc, 3.0)

    @staticmethod
    def _await_exit(proc: subprocess.Popen[bytes], timeout: float) -> bool:
        """等**真退出**（``poll()`` 非 None）——不是"发了信号就算停"。

        先例：EL ``launcher_selfcheck.py:103-128`` 就是钉这段时序的。
        """
        deadline = _now() + timeout
        while _now() < deadline:
            if proc.poll() is not None:
                return True
            time.sleep(0.05)
        return proc.poll() is not None


class Supervisor:
    """一组 child 的管理者（本步不含 aggregator：编排归步骤 2）。

    ## 两个钩子：**为步骤 2 留的出口**（修正 1）

    启动顺序是拓扑序 ``svc* → aggregator → dsh``；而 dsh 需要在**聚合端点就绪后**才拿到
    它的 URL，聚合端点又需要在**各 svc 就绪后**拿到它们的端口。⇒ 光有"顺序"不够，
    还需要"**就绪之后做点什么**"，故留两个出口（现在**不焊死**、也**不实现**聚合）：

    - ``prepare_hooks``：spawn **之前**调 ``fn(spec, child)``；可**返回一份新的
      ``ChildSpec``** 替换本次实际使用的 spec（步骤 2 用它把端点地址塞进 dsh 的 env）；
    - ``ready_hooks``：就绪**之后**调 ``fn(spec, child)``（步骤 2 用它把 svc 端口交给聚合端点）。

    ⚠ **不要**在这里留"两个操作者同时活"的抽象：同一数据根只能一个写宿主
    （``write_lease`` 独占锁）⇒ 人机两条腿是**换腿**，不是同时跑（见 :meth:`switch_leg`）。
    """

    def __init__(self, *, log_dir: Path, state_path: Path | None = None,
                 max_restarts: int = 5, max_restart_period_s: float = 60.0,
                 prepare_hooks: Sequence = (), ready_hooks: Sequence = ()) -> None:
        self.log_dir = Path(log_dir)
        self.state_path = Path(state_path) if state_path is not None else None
        self.max_restarts = int(max_restarts)
        self.max_restart_period_s = float(max_restart_period_s)
        self._prepare_hooks = tuple(prepare_hooks)
        self._ready_hooks = tuple(ready_hooks)
        self._children: dict[str, _Child] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        #: 上一次 ``clean_stale`` 实际清掉了什么（**必须能被状态页看到**，修正 2）——
        #: "清掉了哪个孤儿"只写进日志就没了，等于没有可核对的账。
        self.cleaned: list[str] = []
        self.log_dir.mkdir(parents=True, exist_ok=True)

    # ---- 清理陈旧残留（修正 2）----
    def clean_stale(self, stale: Sequence[StaleItem]) -> list[str]:
        """把 ``plan_startup`` 探到的残留**执行**掉；返回人话清单（并记进 ``state()``）。

        ⚠ 为什么"执行"要归 Supervisor 而不是让调用方自己 kill：残留的判定与清理必须
        **同一处**——否则"谁判的、谁清的"分家，就会出现"判了没清/清了没记"。
        ⚠ 返回清单同时进 ``self.cleaned`` ⇒ 状态页看得到（修正 2 的硬要求）。
        """
        done: list[str] = []
        for item in stale:
            if item.kind == "orphan" and item.pid:
                if pid_alive(item.pid):
                    self._kill_pid_tree(item.pid)
                    still = pid_alive(item.pid)
                    done.append(
                        f"清掉孤儿进程 {item.target}（{item.why}）"
                        + ("" if not still else "⚠ 仍在（杀不掉，需人工）"))
                else:
                    done.append(f"孤儿进程 {item.target} 已自行消失（{item.why}）")
            elif item.kind == "port_file" and item.path is not None:
                try:
                    item.path.unlink()
                    done.append(f"清掉陈旧端口文件 {item.path}（{item.why}）")
                except OSError as exc:
                    done.append(f"陈旧端口文件 {item.path} 删不掉：{exc}")
        self.cleaned.extend(done)
        self._write_state()
        return done

    @staticmethod
    def _kill_pid_tree(pid: int) -> None:
        """按 pid 杀**整棵树**（我们没有它的 Popen 句柄时的路径）。"""
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],  # noqa: S603,S607
                           capture_output=True, check=False)
        else:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass

    def _lease_holders(self) -> dict[str, list[str]]:
        """``data_root -> [正在跑的 child id]``（写租约的互斥面）。"""
        out: dict[str, list[str]] = {}
        for cid in self._order:
            c = self._children[cid]
            root = c.current.data_root
            if root is None or c.proc is None or c.proc.poll() is not None:
                continue
            out.setdefault(str(root), []).append(cid)
        return out

    def assert_lease_exclusive(self) -> None:
        """**同一数据根同一时刻只许一个 child 在跑**（修正 4 的不变量）。

        为什么要在 Supervisor 这一层校验：``write_lease`` 只在**抢锁那一刻**报错，
        而"第二个 child 起晚了、恰好第一个还没释放"这种窗口期是**竞态**——
        靠运气发现的错误，要在状态上能**机械**判出来。
        """
        bad = {k: v for k, v in self._lease_holders().items() if len(v) > 1}
        if bad:
            raise MechaError(
                f"同一数据根有多个 child 在跑：{bad}",
                kind="lease_not_exclusive",
                hint="同一数据根只能一个写宿主（write_lease 的独占锁）"
                     "⇒ 换腿必须先停旧腿、等它真退出（switch_leg）",
            )

    def switch_leg(self, *, stop_id: str, start: ChildSpec,
                   timeout: float | None = None) -> dict:
        """**换腿**：停旧腿 ⇒ 等**真退出**（写租约随之释放）⇒ 起新腿 ⇒ 返回交接记录。

        ⚠ **顺序不能反**：先起新的会**抢不到租约**（``write_lease`` 独占，
        ``MechaError(kind="writer_lease_held")``，fail-closed）⇒ 必须在**旧腿真退出之后**
        才 spawn 新腿。

        ⚠ **这不是"两条腿同时活"**：框架的账是"**当前状态是它的折叠**"
        （``docs/v2/使用模式与核心原则.md``），所以换腿之后**账不间断**——
        GUI 起来读的就是刚才那份账，人看到的是"AI 干到哪儿了"。
        同一数据根**同一时刻只有一个写宿主**，这是硬约束，不是实现选择。

        @returns 交接记录（写进状态页/日志）：谁交给谁、花了多久、租约怎么让出来的。
        """
        if stop_id not in self._children:
            raise MechaError(
                f"switch_leg 的旧腿不存在：{stop_id!r}",
                kind="unknown_child",
                hint=f"已知：{sorted(self._children)}",
            )
        wait = float(timeout if timeout is not None else self._children[stop_id].current.stop_timeout)
        t0 = _now()
        self.stop(stop_id)
        c = self._children[stop_id]
        released = _now() - t0
        # 自证"真退出了"：写租约随进程退出释放，所以这里必须确认进程不在
        proc = c.proc
        gone = proc is None or proc.poll() is not None
        record = {
            "from": stop_id, "to": start.id,
            "stopped": True, "exited": gone, "release_s": round(released, 3),
            "waited_timeout": wait,
            "lease_root": str(start.data_root) if start.data_root else None,
        }
        if not gone:
            record["error"] = "旧腿未确认退出 ⇒ 不起新腿（先起会抢不到租约）"
            self.cleaned.append(f"换腿失败：{stop_id} 未确认退出")
            self._write_state()
            return record
        self.start(start)                                  # 旧腿已退出 ⇒ 现在起是安全的
        record["started"] = True
        record["port"] = self._children[start.id].port
        self.cleaned.append(
            f"换腿：{stop_id} → {start.id}（旧腿释放用了 {released:.2f}s）")
        self._write_state()
        return record

    # ---- 生命周期 ----
    def start(self, child: ChildSpec) -> None:
        with self._lock:
            if child.id in self._children:
                raise MechaError(
                    f"child id 重复：{child.id!r}",
                    kind="duplicate_child",
                    hint="child id 全局唯一（将来也是工具前缀）⇒ 先 stop 再 start，或改名",
                )
            # ⭐ 写租约互斥（修正 4）：**在 spawn 之前**判，别等子进程抢锁失败才发现。
            if child.data_root is not None:
                holders = self._lease_holders().get(str(child.data_root), [])
                if holders:
                    raise MechaError(
                        f"数据根 {child.data_root} 已被 {holders} 持有 ⇒ 不能再起 {child.id!r}",
                        kind="lease_not_exclusive",
                        hint="同一数据根同一时刻只能一个写宿主；换腿请用 switch_leg"
                             "（停旧腿 → 等真退出 → 再起新腿）",
                    )
            c = _Child(child, self)
            self._children[child.id] = c
            self._order.append(child.id)
        try:
            c.spawn()
        except MechaError as exc:
            c.state = "unhealthy"
            c.last_error = str(exc)
            self._write_state()
            raise
        c.wait_ready()
        self._write_state()

    def stop(self, child_id: str) -> None:
        """停进程、**保留 spec**（= OTP ``terminate_child``）。"""
        c = self._children.get(child_id)
        if c is None:
            raise MechaError(
                f"未知 child：{child_id!r}",
                kind="unknown_child",
                hint=f"已知：{sorted(self._children)}",
            )
        c.terminate()
        self._write_state()

    def shutdown(self) -> None:
        """**逆序**收尾全部（后起的先停：依赖方向上是"先撤上层"）。幂等。"""
        for cid in reversed([c for c in self._order if c in self._children]):
            c = self._children.get(cid)
            if c is None:
                continue
            try:
                c.terminate()
            except Exception:                              # noqa: BLE001 - 收尾不许被一个 child 拖死
                pass
        self._write_state()

    def set_active(self, child_ids: Sequence[str]) -> None:
        """声明"这一轮该活着的是哪些"（one_for_all / rest_for_one 的共同控制面）。"""
        want = [c for c in child_ids if c in self._children]
        for cid in self._order:
            if cid not in want and cid in self._children:
                self._children[cid].terminate()
        self._write_state()

    # ---- 观测 ----
    def state(self) -> dict:
        with self._lock:
            kids = []
            holders = self._lease_holders()
            for cid in self._order:
                c = self._children[cid]
                root = c.current.data_root
                running = c.proc is not None and c.proc.poll() is None
                kids.append({
                    "id": c.spec.id,
                    "pid": (c.proc.pid if c.proc is not None else None),
                    "state": c.state,
                    "port": c.port,                     # ⭐ 只可能是**回读**值
                    #: **就绪** = 判据通过 + 有证据（`is_ready()`，提醒 1）
                    "ready": c.is_ready(),
                    "ready_evidence": c.ready_evidence,
                    "last_error": c.last_error,
                    "restarts": c.restarts,
                    "exit_code": c.exit_code,
                    "significant": c.spec.significant,
                    #: ⭐ **写权归属**（修正 4）：换腿时人最需要看的一件事。
                    #: 声明了 data_root **且** 进程在跑 = 它此刻是那个数据根的写宿主。
                    "data_root": (str(root) if root is not None else None),
                    "holds_write_lease": bool(
                        root is not None and running and cid in holders.get(str(root), [])),
                    "log": str(c.log_path),
                })
            #: 上一次 clean_stale 清掉了什么（修正 2：清掉的必须看得见）
            return {"children": kids, "cleaned": list(self.cleaned)}

    def child(self, child_id: str) -> _Child:
        return self._children[child_id]

    def _write_state(self) -> None:
        if self.state_path is None:
            return
        import json
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps(self.state(), ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass


def freed_by_taskkill_note() -> str:
    """给报告/文档用的一句人话（避免把同一句解释抄两处）。"""
    return "Windows 上 taskkill /T /F 杀整棵树；POSIX 上 killpg"


def which_or_raise(command: Sequence[str]) -> tuple[str, ...]:
    """把 ``argv`` 的第一段按 PATH 解析；找不到 ⇒ **响亮失败**（不静默等到 spawn 才炸）。"""
    if not command:
        raise MechaError("空 argv", kind="empty_argv", hint="command 至少要有第一段")
    exe = shutil.which(command[0])
    if exe is None:
        raise MechaError(
            f"找不到可执行文件：{command[0]!r}",
            kind="executable_not_found",
            hint="确认它在 PATH 上，或在站点声明里给绝对路径",
        )
    return (exe, *command[1:])
