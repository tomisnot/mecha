"""宿主无关监控页判据：四态可读、事件可见、读不到不等于空。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mecha.monitor_page import (ServiceHistory, fetch_service_history,  # noqa: E402
                                render_monitor_page)
from mecha.site import MonitorSpec, ReadySpec, Service  # noqa: E402

FAILS: list[str] = []


def _check(name: str, cond: bool, extra: object = "") -> None:
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"  ← {extra}"))
    if not cond:
        FAILS.append(name)


class _Svc:
    def __init__(self, sid: str, *, declared: bool = True,
                 port_file: Path | None = None) -> None:
        self.id = sid
        self.monitor = object() if declared else None
        self._port_file = port_file

    def monitor_port_file(self) -> Path | None:
        return self._port_file


def check_states() -> None:
    no_monitor = render_monitor_page(
        [_Svc("A", declared=False)],
        fetcher=lambda s: ServiceHistory(s.id, False, False,
                                         error="这个软件没有声明监控面"))
    _check("未声明监控面不是空白", "未声明监控面" in no_monitor
           and "不是“没有事件”" in no_monitor)

    unreadable = render_monitor_page(
        [_Svc("B")],
        fetcher=lambda s: ServiceHistory(s.id, True, False,
                                         error="读不到监控端口（不是“没有事件”）"))
    _check("读不到显式显示，不渲染成空表",
           "读不到" in unreadable and "不是“没有事件”" in unreadable)

    empty = render_monitor_page(
        [_Svc("C")], fetcher=lambda s: ServiceHistory(s.id, True, True, events=()))
    _check("已连接但零事件使用独立文案", "已连上，账里还没有事件" in empty)

    with_events = render_monitor_page(
        [_Svc("D")],
        fetcher=lambda s: ServiceHistory(s.id, True, True, events=(
            {"seq": 3, "actor": "ai", "target": "power", "reason": "set",
             "before": 1, "after": 2},)))
    _check("事件行包含 actor/target/before→after",
           "#3" in with_events and "power" in with_events
           and "1" in with_events and "2" in with_events
           and "ai" in with_events)


def check_task_ledger() -> None:
    with tempfile_dir() as td:
        ledger = td / "tasks.jsonl"
        ledger.write_text(
            json.dumps({"ts": "2026-01-01T00:00:00+00:00", "kind": "task.submitted",
                        "tool": "demo_submit", "task_id": "job-7"}) + "\n" +
            json.dumps({"ts": "2026-01-01T00:00:01+00:00",
                        "kind": "permission.requested", "tool": "demo_write"}) + "\n",
            encoding="utf-8")
        page = render_monitor_page([], task_ledger=ledger)
        _check("监控页显示 Hook 任务账", "AI 任务（Codex Hook 账）" in page
               and "job-7" in page and "demo_submit" in page)
        missing = render_monitor_page([], task_ledger=td / "none.jsonl")
        _check("任务账尚未创建时明说不是没有运行",
               "尚未产生" in missing and "不等于任务没有运行" in missing)


def check_missing_port_is_not_empty() -> None:
    with tempfile_dir() as td:
        svc = Service(
            id="X", brief="x", argv=(sys.executable, "-c", "pass"), cwd=td,
            ready=ReadySpec(port_file=".p"), data_root=td,
            monitor=MonitorSpec(route_path="/x", port_file=".missing"),
        )
        row = fetch_service_history(svc)
        _check("真实读取缺端口文件 ⇒ ok=False 且明确说读不到",
               row.declared and not row.ok and "读不到监控端口" in row.error)
        page = render_monitor_page([svc])
        _check("真实缺端口也走可读错误分支", "读不到" in page)


class tempfile_dir:
    def __enter__(self):
        import tempfile
        self._ctx = tempfile.TemporaryDirectory(prefix="monitor-page-")
        return Path(self._ctx.name)

    def __exit__(self, *exc):
        self._ctx.cleanup()
        return False


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("监控页判据：")
    check_states()
    check_task_ledger()
    check_missing_port_is_not_empty()
    if FAILS:
        print(f"monitor_page_selfcheck: FAIL（{len(FAILS)} 条）")
        return 1
    print("monitor_page_selfcheck: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
