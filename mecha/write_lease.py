"""跨进程写租约：同一数据目录同时只允许一个写宿主（审查 2.2）。

History 的核心不变量是"append-only + seq 单调递增"。seq 由宿主进程内存里的
`_seq` 分配后落盘——**两个宿主（如 GUI 进程 + Hub 进程）同时指向同一数据目录**
时，各自从盘上续到同一 `last_seq`、各自从 `_seq=0` 起写 ⇒ journal 出现**重复
seq**，破坏按 seq 寻址与 call_id 互引（总纲 §5.2②）。

本模块用一把 **OS 级独占文件锁**（Windows `msvcrt.locking` / POSIX `fcntl.flock`）
钉住数据目录：第一个 `JsonlLogSink` 拿到锁、把 PID 写进锁文件；第二个宿主构造
时拿不到锁 ⇒ 抛 `MechaError(kind="writer_lease_held")`（fail-closed，与 Approval
"fail closed" 同纪律）。锁随文件描述符关闭自动释放（进程崩溃即解锁，无残留）。

## Known Limitations and Deferred Work

- 只"拒绝第二个宿主"，未实现"降级为只读客户"（审查提的另一选项）：只读客户要
  处理"读时对方正在追加"的可见性，且当前无此需求（并存部署各用各目录即可）——
  需要时写 ADR 再加（n=1 不建）。
- 锁文件 `.writer.lock` 是运行时产物，非 journal 资产（不受 released-format
  政策管辖）。
- 租约是**进程内 + 跨进程**都生效（同一进程开两个 sink 指向同目录也拒），
  因为锁按文件描述符计。
"""

from __future__ import annotations

import os
from pathlib import Path

from .errors import MechaError

#: 租约文件名（数据目录内）。运行时锁，非持久格式资产。
LEASE_FILENAME = ".writer.lock"


class WriteLease:
    """持有数据目录的独占写锁；构造即抢锁，抢不到抛 MechaError。"""

    def __init__(self, data_dir: Path | str) -> None:
        self._dir = Path(data_dir)
        self._fd: int | None = None
        self._path = self._dir / LEASE_FILENAME
        self.acquire()

    def acquire(self) -> None:
        if self._fd is not None:          # 幂等：已持有不重复抢
            return
        self._dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self._path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            self._try_lock(fd)
        except OSError:
            os.close(fd)
            raise MechaError(
                f"数据目录已被另一个写宿主占用：{self._dir}",
                kind="writer_lease_held",
                hint="同一数据目录同时只允许一个 Gate 宿主（History 的 seq 单调"
                     "不变量要求单写者）；两个宿主各写各的会产出重复 seq。",
                suggest="给并存部署各用不同 data_dir_name（如 GUI 用 '.mecha-v2'、"
                        "Hub 用 '.mecha-v2-hub'），或先停掉另一个宿主。",
            )
        # 抢到锁：写 PID 便于人排查是谁占着（锁覆盖字节 0，写自己持有的文件安全）。
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, str(os.getpid()).encode("ascii"))
        except OSError:
            pass
        self._fd = fd

    @staticmethod
    def _try_lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)   # 非阻塞独占锁 1 字节
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            os.close(fd)

    def __enter__(self) -> "WriteLease":
        return self

    def __exit__(self, *exc) -> bool:
        self.release()
        return False

    def __del__(self) -> None:
        self.release()
