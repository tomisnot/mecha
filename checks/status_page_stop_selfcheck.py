"""判据 ㉔：关控制台这一下必须**幂等、且两路同时来也只关一次**。

## 为什么会写它

真跑出来的：`python -m mecha --all --ai-host codex` 之后按 `--stop`，launcher
日志里每次都刷一条

```
AttributeError: 'NoneType' object has no attribute 'server_close'
  File ".../status_page.py", line 688, in stop
    self._httpd.server_close()
```

根因不是"忘了判空"（判空一直在），而是**判空与使用之间有窗口**：`/shutdown`
用 `threading.Timer(0.6, page.stop)` 延后停，主循环 `serve_forever()` 退出后的
`finally` 又停一次。两路都能通过判空，先到的一路把 `_httpd` 置空，后到的那路
拿着 `None` 去 `server_close()`。

⇒ 修法是**先原子摘走再关**（`_stop_lock`）：谁摘到谁关，另一路直接返回。

## 四条断言（⛔ 第一条是红证）

| # | 断言 | 怎么判 |
|---|---|---|
| ① | 两路同时 stop()，底层 `shutdown`/`server_close` **各只跑一次** | 给实例换上计数版并让 `shutdown` 慢 200ms，把窗口撑开；旧实现两路都会进去 ⇒ 计数 2 |
| ② | 两路都不抛 | 收集线程异常；旧实现后到那路必抛 AttributeError |
| ③ | 关完之后端口真的不听了 | 对回读端口 `connect_ex` 非 0 |
| ④ | 再 stop() 一次仍然安静，`port` 变 None | 幂等，且不拿已关对象再取端口 |

⚠ 判据起**真的** `StatusPage`（真 socket、真线程），不用替身：这个 bug 只在
"真两路 + 真 server"里出现。
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mecha.providers.status_page import PageOptions, StatusPage  # noqa: E402

FAILS: list[str] = []


def _check(name: str, cond: bool, extra: object = "") -> None:
    print(f"  {'✓' if cond else '✗'} {name}" + (f" —— {extra}" if not cond and extra else ""))
    if not cond:
        FAILS.append(name)


def check_stop_is_atomic() -> None:
    page = StatusPage(object(), PageOptions(port=0, open_browser=False))
    port = page.start()
    httpd = page._httpd
    assert httpd is not None

    calls = {"shutdown": 0, "close": 0}
    real_shutdown, real_close = httpd.shutdown, httpd.server_close

    def slow_shutdown() -> None:
        #: 把「判空 → 关」的窗口从微秒撑到 200ms，让旧实现稳定地翻车，
        #: 而不是靠调度运气偶尔撞上。
        calls["shutdown"] += 1
        time.sleep(0.2)
        real_shutdown()

    def counted_close() -> None:
        calls["close"] += 1
        real_close()

    httpd.shutdown = slow_shutdown            # type: ignore[method-assign]
    httpd.server_close = counted_close        # type: ignore[method-assign]

    errors: list[str] = []
    barrier = threading.Barrier(2, timeout=10)

    def stop_once() -> None:
        try:
            barrier.wait()
            page.stop()
        except Exception as exc:              # noqa: BLE001 - 就是要抓它抛不抛
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=stop_once, name=f"stop-{i}") for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    _check("两路同时 stop()，底层只关一次（旧实现会关两次）",
           calls == {"shutdown": 1, "close": 1}, f"calls={calls}")
    _check("两路都不抛（旧实现必抛 AttributeError）", not errors, "; ".join(errors))
    with socket.socket() as probe:
        probe.settimeout(1.0)
        _check("关完之后端口真的不听了", probe.connect_ex(("127.0.0.1", port)) != 0)
    try:
        page.stop()
        quiet = True
    except Exception as exc:                  # noqa: BLE001
        quiet = False
        errors.append(f"第二次 stop 抛了：{exc}")
    _check("再 stop() 一次仍然安静（幂等），且 port 变 None",
           quiet and page.port is None, f"port={page.port}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("控制台收尾判据：")
    check_stop_is_atomic()
    if FAILS:
        print(f"status_page_stop_selfcheck: FAIL（{len(FAILS)} 条）")
        return 1
    print("status_page_stop_selfcheck: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
