"""Launcher 运行日志汇聚：工作台读取这里，而不是依赖人工挑选 print。"""

from __future__ import annotations

import sys
import threading
import time
from collections import deque
from typing import TextIO


class _Tee:
    def __init__(self, original: TextIO, hub: "RuntimeConsole", stream: str) -> None:
        self.original = original
        self.hub = hub
        self.stream = stream
        self.pending = ""

    def write(self, text: str) -> int:
        self.original.write(text)
        self.original.flush()
        self.pending += str(text)
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            self.hub.append(line, self.stream)
        return len(str(text))

    def flush(self) -> None:
        self.original.flush()
        if self.pending:
            self.hub.append(self.pending, self.stream)
            self.pending = ""

    def isatty(self) -> bool:
        return self.original.isatty()

    def __getattr__(self, name: str):
        return getattr(self.original, name)


class RuntimeConsole:
    def __init__(self, limit: int = 500) -> None:
        self._lines: deque[str] = deque(maxlen=limit)
        self._lock = threading.Lock()

    def append(self, line: str, stream: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        prefix = "stderr" if stream == "stderr" else "stdout"
        with self._lock:
            self._lines.append(f"{stamp} [{prefix}] {line}")

    def recent(self, limit: int = 400) -> list[str]:
        with self._lock:
            return list(self._lines)[-limit:]


_console = RuntimeConsole()
_installed = False


def install() -> RuntimeConsole:
    """安装一次 stdout/stderr tee；原终端仍保留，工作台获得同一份完整流。"""
    global _installed
    if not _installed:
        sys.stdout = _Tee(sys.stdout, _console, "stdout")  # type: ignore[assignment]
        sys.stderr = _Tee(sys.stderr, _console, "stderr")  # type: ignore[assignment]
        _installed = True
    return _console


def console() -> RuntimeConsole:
    return _console
