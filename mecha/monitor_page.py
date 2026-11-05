"""宿主无关的只读监控页。

数据直接来自各软件自己的 ``cockpit`` 端点（``/history``），不依赖 dsh 面板资产。
页面把“未声明监控面 / 读不到 / 已连上但没有事件 / 有事件”分成四种可读状态。
"""

from __future__ import annotations

import html
import json
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .portfile import read_port
from .site import Service

HISTORY_LIMIT = 100
TASK_LIMIT = 50


@dataclass(frozen=True)
class ServiceHistory:
    service_id: str
    declared: bool
    ok: bool
    events: tuple[dict, ...] = ()
    error: str = ""


def fetch_service_history(service: Service, *, timeout: float = 1.0) -> ServiceHistory:
    """读取一个软件的历史；读不到与确实为空分开表达。"""
    if service.monitor is None:
        return ServiceHistory(service.id, False, False,
                              error="这个软件没有声明监控面")
    port_file = service.monitor_port_file()
    port = read_port(port_file)
    if port is None:
        return ServiceHistory(
            service.id, True, False,
            error="读不到监控端口（不是“没有事件”：端口文件不存在或内容非法）")
    url = f"http://127.0.0.1:{int(port)}/history?since_seq=0&limit={HISTORY_LIMIT}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return ServiceHistory(service.id, True, False,
                              error=f"读不到监控端点（{type(exc).__name__}: {exc}）")
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        return ServiceHistory(service.id, True, False,
                              error="监控端点回执形状不对（缺 events 列表）")
    events = tuple(e for e in payload["events"] if isinstance(e, dict))
    return ServiceHistory(service.id, True, True, events=events)


def _fmt(value: object) -> str:
    if value is None:
        return "∅"
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= 180 else text[:177] + "…"


def _render_service(row: ServiceHistory) -> str:
    title = f'<h3>{html.escape(row.service_id)}</h3>'
    if not row.declared:
        return (f'<section class="svc"><div class="head">{title}'
                '<span class="badge muted">未声明监控面</span></div>'
                '<p class="muted">这个软件没有提供只读监控端点；不是“没有事件”。</p>'
                '</section>')
    if not row.ok:
        return (f'<section class="svc"><div class="head">{title}'
                '<span class="badge err">读不到</span></div>'
                f'<p class="err">{html.escape(row.error)}</p></section>')
    if not row.events:
        return (f'<section class="svc"><div class="head">{title}'
                '<span class="badge ok">已连接</span></div>'
                '<p class="muted">已连上，账里还没有事件。</p></section>')
    rows = []
    for event in reversed(row.events[-HISTORY_LIMIT:]):
        rows.append(
            '<tr>'
            f'<td>#{html.escape(str(event.get("seq", "")))}</td>'
            f'<td>{html.escape(str(event.get("actor", "")))}</td>'
            f'<td>{html.escape(str(event.get("target", "")))}</td>'
            f'<td>{html.escape(str(event.get("reason", "")))}</td>'
            f'<td class="val">{html.escape(_fmt(event.get("before")))}'
            f' → {html.escape(_fmt(event.get("after")))}</td>'
            '</tr>')
    return (f'<section class="svc"><div class="head">{title}'
            f'<span class="badge ok">最近 {len(row.events)} 条</span></div>'
            '<table><thead><tr><th>seq</th><th>actor</th><th>target</th>'
            '<th>reason</th><th>before → after</th></tr></thead><tbody>'
            + ''.join(rows) + '</tbody></table></section>')


def _render_tasks(task_ledger: Path | None) -> str:
    """Codex Hook 任务账的只读视图；与软件 History 分开标。"""
    if task_ledger is None:
        return ''
    path = Path(task_ledger)
    if not path.is_file():
        return (f'<section class="svc"><div class="head"><h3>AI 任务</h3>'
                '<span class="badge muted">尚未产生</span></div>'
                '<p class="muted">这本账在 Hook 首次收到任务回执时创建；'
                '文件不存在不等于任务没有运行。</p></section>')
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    if not rows:
        return (f'<section class="svc"><div class="head"><h3>AI 任务</h3>'
                '<span class="badge ok">已连接</span></div>'
                '<p class="muted">账已创建，但还没有 Hook 记录。</p></section>')
    body = ''.join(
        '<tr>'
        f'<td>{html.escape(str(r.get("ts", "")))}</td>'
        f'<td>{html.escape(str(r.get("kind", "")))}</td>'
        f'<td>{html.escape(str(r.get("tool", "")))}</td>'
        f'<td>{html.escape(str(r.get("task_id", "") or "—"))}</td>'
        '</tr>' for r in rows[-TASK_LIMIT:][::-1])
    return ('<section class="svc"><div class="head"><h3>AI 任务（Codex Hook 账）</h3>'
            f'<span class="badge ok">最近 {min(len(rows), TASK_LIMIT)} 条</span></div>'
            '<p class="muted">这是外部宿主的观察账；业务终态仍以各软件 task_wait / '
            'JobRegistry 为准。</p><table><thead><tr><th>时间</th><th>事件</th>'
            '<th>工具</th><th>任务 id</th></tr></thead><tbody>'
            + body + '</tbody></table></section>')


def render_monitor_page(services: Sequence[Service], *,
                        fetcher: Callable[[Service], ServiceHistory] = fetch_service_history,
                        task_ledger: Path | None = None) -> str:
    """生成整页 HTML；``fetcher`` 可注入，便于判据不依赖真实网络。"""
    rows = [fetcher(svc) for svc in services]
    body = ''.join(_render_service(row) for row in rows) or (
        '<p class="muted">当前没有声明任何软件。</p>')
    body = _render_tasks(task_ledger) + body
    return (
        '<!doctype html><html lang="zh"><head><meta charset="utf-8">'
        '<title>mecha · 监控</title><style>'
        'body{font:14px/1.6 system-ui,"Segoe UI",sans-serif;margin:0;'
        'background:#f6f8fa;color:#1f2328}'
        'header{padding:14px 22px;border-bottom:1px solid #d0d7de;'
        'background:#fff;display:flex;gap:16px;align-items:center}'
        'h1{font-size:18px;margin:0;flex:1}a{color:#0969da;text-decoration:none}'
        'main{padding:14px 22px;max-width:1280px;margin:auto}'
        '.svc{background:#fff;border:1px solid #d0d7de;border-radius:8px;'
        'padding:10px 12px;margin:0 0 12px}.head{display:flex;gap:10px;'
        'align-items:center}.head h3{font-size:15px;margin:0;flex:1}'
        '.badge{font-size:12px;border-radius:10px;padding:1px 8px;'
        'border:1px solid #d0d7de}.badge.ok{background:#dafbe1;color:#1a7f37}'
        '.badge.err{background:#ffebe9;color:#cf222e}'
        '.badge.muted{background:#f1efE8;color:#57606a}'
        '.muted{color:#57606a}.err{color:#cf222e}'
        'table{width:100%;border-collapse:collapse;margin-top:8px}'
        'th,td{text-align:left;border-bottom:1px solid #eaeef2;padding:3px 5px;'
        'vertical-align:top}th{color:#57606a;font-size:12px}'
        '.val{font-family:ui-monospace,Consolas,monospace;font-size:12px}'
        '</style></head><body><header><h1>mecha · 监控</h1>'
        '<a href="/">控制台</a></header><main>' + body + '</main></body></html>'
    )


__all__ = ["HISTORY_LIMIT", "TASK_LIMIT", "ServiceHistory",
           "fetch_service_history", "render_monitor_page"]
