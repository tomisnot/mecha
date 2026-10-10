"""端口：**公布—发现—使用**的单一实现（文件原语 + 端口生命周期 + 探活/挑端口）。

## 两条地基（先读这两段，再读代码）

1. **端口文件是发现辅助，不是真值**：读不到 = "未知"（不假装知道），所以
   :func:`read_port` 对"路径 ``None`` / 不存在 / 空 / 非数字"一律回 ``None``。
2. **"写进文件"和"端口真的在听"是两件事**：只写不问 ⇒ 消费方拿到**死地址**，
   现象是"连上了但空白"（假绿）。⇒ 公布必须**发生在确认在听之后**
   （:meth:`PortBinding.publish_when_listening`），消费方也必须**读 + 确认在听**
   （:meth:`PortBinding.wait_listening`），不能只读文件。

## 规则清单（每条都在下面或判据里有对应）

| # | 规则 | 谁保证 |
|---|---|---|
| R1 | 读不到/坏内容都是"未知"（回 ``None``） | :func:`read_port` |
| R2 | **启动前无条件清**（``terminate`` 不跑 ``finally``） | :func:`clear_port_file` / :meth:`PortBinding.clear_stale` |
| R3 | **收尾只删仍是自己写的那个**（交接窗口里不误删接管者） | :func:`clear_port_file` / :meth:`PortBinding.clear` |
| R4 | **只在真的在听之后才公布** | :meth:`PortBinding.publish_when_listening` |
| R5 | 消费方**读 + 确认在听**才算拿到 | :meth:`PortBinding.wait_listening` / :meth:`~PortBinding.is_listening` |
| R6 | ``0.0.0.0`` 等回环写法**按 ``127.0.0.1`` 探**（否则探活假失败） | :attr:`PortBinding.probe_host` |
| R7 | **端口=0 让 OS 挑、再回读**是首选；子进程/必须在 spawn 前给端口时才 :func:`pick_free_port`（⚠ 有 TOCTOU，见其 docstring） | :func:`pick_free_port` / :func:`ensure_free` |
| R8 | 超时/失败**如实回**（不抛、不静默假成功） | 各方法的 ``bool`` 回执 |

## 为什么归框架（而不是各家自己写）

同一套安全论证在框架里重复两次就是**两处可以各自漂移**（本模块原话）。实测：
框架自己那份三连（清陈旧 → 回读端口 → 写文件 → 收尾按内容清）在
``providers/mcp.py`` 与 ``cockpit.py`` 里各有一份；"探活"在 ``launch.py`` 与
``supervise.py`` 里各有一份；而在**项目侧**，"等就听 → 才公布 → 开浏览器"这一公里
两家各自写了一遍（一个固定端口的项目写了"预检被占则响亮失败"，一个用子进程的项目
写了"挑空闲端口"）。⇒ 库拥有规则，宿主只拥有"文件叫什么、页面长什么样"。

本模块**不持有任何文件名默认值**：端口文件叫什么、放在哪个根下，是宿主与它的
消费方（插件 / 启动编排 / 人）之间的发现约定，属宿主领域——库内零项目字面量是
既有纪律。调用方给路径，本模块只管规则。

## Known Limitations and Deferred Work

- 只有"一个端口一个文件"的形态。要一次暴露多个端口时由宿主各调一次（不引入
  描述符格式——"模式 / pid / 多个端口写进一个 JSON"是宿主自有的运行期描述符，
  属宿主，不进库）。
- 不抢锁、不探活 pid：``expected`` 语义只比较**文件内容**，不校验那个端口背后
  是否真有进程。真正的单写者不变量由 ``write_lease`` 的 OS 级独占锁保证，本模块
  只负责"别删错文件"这一半。
- 所有 ``OSError`` 静默：端口文件是**发现辅助**，不是真值。读不到返回 ``None``，
  调用方按"未知"处理（不假装知道）；写失败不抛——起不来服务的原因在 bind 那一步，
  不在这里。
- ⚠ :func:`pick_free_port` **有 TOCTOU 窗口**（bind→读→关→别人可能抢）：首选永远是
  "服务自己 ``port=0``、起来后回读 ``server_address``"。它只为"必须在 spawn 之前
  拿到端口"的形态存在（例如给一个子进程传 ``--port``），且**起不来要如实报**。
- ⚠ :meth:`PortBinding.open_when_ready` 只是"把人面带到眼前"的便利（best-effort、
  开不了不算失败）。**要不要开是宿主的选择**（``enabled=False`` 或宿主自己开）。
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

from .errors import MechaError

__all__ = [
    "PortBinding",
    "clear_port_file",
    "ensure_free",
    "pick_free_port",
    "read_port",
    "wait_listening",
    "write_port_file",
]

#: 探活的默认超时（秒）：一个已经 bind 的端口应当**立刻**可连，所以它很短；
#: ⚠ 不要拿它当"服务启动超时"——那是 `ReadySpec.timeout_s` 的事。
PROBE_TIMEOUT_S = 0.3
#: "等它就听"的默认上限（秒）：服务要 import/装配，属于"小启动"。
WAIT_READY_TIMEOUT_S = 25.0


def read_port(path: str | Path | None) -> int | None:
    """读裸端口；路径为 ``None``、文件不存在、空内容、非数字一律返回 ``None``。

    「非数字也是 ``None``」是刻意的：这里回答的是"端口是多少"，不是"文件合不
    合法"——损坏的发现文件应当被当成"不知道"，由调用方决定要不要清掉重写。
    """
    if path is None:
        return None
    try:
        raw = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(raw) if raw.isdigit() else None


def write_port_file(path: str | Path | None, port: int) -> None:
    """写裸端口（纯十进制文本，无换行以外的装饰）。``path`` 为 ``None`` 则什么都不做。"""
    if path is None:
        return
    try:
        Path(path).write_text(str(int(port)), encoding="utf-8")
    except OSError:
        pass


def clear_port_file(path: str | Path | None, expected: int | None = None) -> None:
    """删端口文件。

    ``expected=None`` → **无条件删**：调用时机是**启动前**清陈旧文件，安全性靠
    "已持写租约 ⇒ 同根无别的活宿主 ⇒ 文件必属死进程"（见模块 docstring）。

    ``expected=<端口>`` → **只在文件内容仍是该端口时才删**：调用时机是**收尾**，
    不可误删接管者刚写的新端口。
    """
    if path is None:
        return
    target = Path(path)
    try:
        if not target.exists():
            return
        if expected is not None:
            raw = target.read_text(encoding="utf-8").strip()
            if raw and raw != str(int(expected)):
                return
        target.unlink()
    except OSError:
        pass


# --------------------------------------------------------------------------- 探活 / 挑端口


def probe_host(host: str) -> str:
    """把"等回环"的写法归一成**可连的**回环地址（R6）。

    ⚠ 实测踩点：``0.0.0.0`` / ``""`` / ``"*"`` 是"监听所有网卡"的写法，
    **不是**可连地址 ⇒ 拿它去 ``create_connection`` 会假失败（服务明明起着）。
    """
    return "127.0.0.1" if host in ("0.0.0.0", "", "*", "::") else host


def is_listening(host: str, port: int, *, timeout: float = PROBE_TIMEOUT_S) -> bool:
    """``port`` 上**真的**有人在听吗（TCP 连得上）。**不抛。**"""
    try:
        with socket.create_connection((probe_host(host), int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def wait_listening(host: str, port: int, *, timeout: float = WAIT_READY_TIMEOUT_S,
                   interval: float = 0.2) -> bool:
    """等到``port``真的在听；超时回 ``False``（R8：**如实**，不假装）。

    ⚠ 等待期间**不阻塞调用方**以外的任何东西：它是普通轮询，调用方自己决定
    在哪个线程里等（框架的端点用后台线程，判据用主线程）。
    """
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        if is_listening(host, port):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def pick_free_port(host: str = "127.0.0.1") -> int:
    """挑一个**当前**空闲的端口（让 OS 分配）。

    ⚠ **首选不是它**：能让服务自己 ``port=0``、起来后回读 ``server_address`` 的形态，
    就用那条（无 TOCTOU）。本函数只为"必须在 spawn **之前**把端口交给别人"的形态存在
    （例如给子进程传 ``--port``）——**那个窗口里端口可能被抢**，所以消费方必须把
    "起不来/连不上"如实报出来，不许假设"挑到了就一定能用"。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((probe_host(host), 0))
        return int(s.getsockname()[1])


def ensure_free(host: str, port: int, *, label: str = "") -> None:
    """预检"这个端口现在能不能 bind"；不能就**响亮失败**（R7 的另一半）。

    ⚠ 为什么必须有（实测形态）：服务在**后台线程/子进程**里 bind 失败时，主流程
    会继续把别的面拉起来 ⇒ 留下"一部分起了、一部分死了"的**半残态**，而症状离病因很远
    （一个 Errno 10048 藏在某个线程的 stderr 里）。⇒ 起之前先探一下，占了就**当场说清**。
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((probe_host(host), int(port)))
    except OSError as exc:
        where = f"{label} " if label else ""
        raise MechaError(
            f"{where}端口 {probe_host(host)}:{int(port)} 现在不可用：{exc}",
            kind="port_in_use",
            hint="多半是上一次没退干净，或别的服务在用它；"
                 "查占用：netstat -ano | findstr :<port>（末列是 PID），"
                 "释放：taskkill /F /PID <pid>；或换一个端口再起",
        ) from exc
    finally:
        probe.close()


# --------------------------------------------------------------------------- 端口生命周期


@dataclass
class PortBinding:
    """一个端口的**全生命周期**：清陈旧 → （服务 bind）→ 等就听 → 公布 → 收尾清。

    它**不接管服务器**：服务怎么起（进程内线程 / uvicorn / 子进程）是调用方的事；
    这里只把"端口这件事"的规则收在一处 —— 清陈旧与收尾清怎么才对（R2/R3）、
    什么时候才能公布（R4）、消费方怎么才算拿到（R5/R6）、页面怎么带给人（R6）。

    典型用法（**进程内**服务，最省事）：

        b = PortBinding(port_file=root / ".mcp-port")
        b.clear_stale()                     # 起之前清陈旧
        server = ...bind(("127.0.0.1", 0)...)
        b.adopt(server.server_address[1])   # 回读真端口 + 公布
        try:
            ...
        finally:
            b.clear()                       # 只删仍是我写的那个

    典型用法（**子进程**，端口得先给）：

        b = PortBinding(port_file=root / ".tb-port")
        b.clear_stale()
        port = pick_free_port()
        proc = spawn(..., "--port", str(port))
        if not b.publish_when_listening(port):   # 等它就听，通了才公布
            return {"ready": False, "reason": "等了 N 秒没起来"}
    """

    #: 端口文件（``None`` ⇒ 不公布，只做探活/清陈旧这类事）。
    port_file: Path | None = None
    host: str = "127.0.0.1"
    #: 当前端口（``adopt`` / ``publish_when_listening`` 填）。
    port: int | None = field(default=None)

    # ---- R2 / R3：文件的两端 ----

    def clear_stale(self) -> None:
        """**启动前**无条件清陈旧（安全性见 :func:`clear_port_file` 的论证）。"""
        clear_port_file(self._path)

    def clear(self) -> None:
        """**收尾**按内容清（只删仍是自己写的那个）。重复调不炸。

        ⚠ **从没公布过 ⇒ 什么都不做**：``clear_port_file`` 的 ``expected=None`` 是
        "启动前**无条件**清"的语义，拿它当收尾用会**误删接管者刚写的新端口**
        （实测形态：一个从没 start 过的端点被 stop ⇒ 把别人的发现文件抹掉）。
        """
        port, self.port = self.port, None
        if port is None:
            return
        clear_port_file(self._path, port)

    # ---- R4：就绪之后才公布 ----

    def adopt(self, port: int) -> int:
        """接住**从服务器回读到的**真端口（``port=0`` 那条路的正确收尾）并公布。"""
        self.port = int(port)
        write_port_file(self._path, self.port)
        return self.port

    def publish_when_listening(self, port: int, *,
                               timeout: float = WAIT_READY_TIMEOUT_S) -> bool:
        """等 ``port`` 真的在听，**通了才公布**；超时**不公布**并回 ``False``（R4/R8）。

        ⚠ 为什么不是"先写文件再看"：写了但还没 listen ⇒ 消费方读到**死地址**，
        面板上显示成"连上了但空白"——与"服务没起"长得不一样，是最坏的那类假绿。
        """
        if not wait_listening(self.host, port, timeout=timeout):
            return False
        return self.adopt(port) == int(port)

    # ---- R5 / R6：消费方怎么才算"拿到" ----

    @property
    def probe_host(self) -> str:
        """可连的回环地址（``0.0.0.0`` 那类写法归一，R6）。"""
        return probe_host(self.host)

    def is_listening(self, port: int | None = None) -> bool:
        """当前（或给定）端口上有人在听吗。"""
        target = self.port if port is None else port
        return target is not None and is_listening(self.host, int(target))

    def wait_listening(self, port: int | None = None, *,
                       timeout: float = WAIT_READY_TIMEOUT_S) -> bool:
        """等到"有人在听"（给定端口，或已 adopt 的那个）。"""
        target = self.port if port is None else port
        return target is not None and wait_listening(self.host, int(target), timeout=timeout)

    def url(self, path: str = "") -> str:
        """给人/面板用的地址（``path`` 可带子路径）。⚠ 端口未知 ⇒ 抛（不编一个）。"""
        if self.port is None:
            raise MechaError("端口还不知道（没 adopt 过）", kind="port_unknown",
                             hint="先 bind 并 adopt 真端口，或先 publish_when_listening")
        suffix = path.lstrip("/")
        return f"http://{self.probe_host}:{int(self.port)}/" + suffix

    # ---- R6：把人面带到眼前（best-effort，宿主可选） ----

    def open_when_ready(self, path: str = "", *,
                        timeout: float = WAIT_READY_TIMEOUT_S,
                        enabled: bool = True, log=None) -> bool:
        """后台等就绪后打开浏览器；**开不了不算失败**（回 ``False`` 只是如实）。

        ⚠ 这是"便利"，不是机制：**要不要开浏览器是宿主的选择**
        （``enabled=False``，或宿主自己开）。它绝不抛、绝不阻塞调用方。
        """
        if not enabled:
            return False
        target = self.port
        if target is None:
            return False

        def _run() -> None:
            if not wait_listening(self.host, int(target), timeout=timeout):
                if log is not None:
                    with contextlib.suppress(Exception):
                        log(f"[port] 等了 {timeout:.0f}s 还没在听，不打开浏览器：{target}")
                return
            with contextlib.suppress(Exception):
                webbrowser.open(self.url(path))
        threading.Thread(target=_run, name="port-open-when-ready", daemon=True).start()
        return True

    # ---- 内部 ----

    @property
    def _path(self) -> Path | None:
        return Path(self.port_file) if self.port_file is not None else None
