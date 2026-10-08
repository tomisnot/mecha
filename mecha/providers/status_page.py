"""选择页 / 状态页（**一个页面、两个阶段**）——``Monitor`` 那一层的只读传输。

## 为什么是"一个页面两个阶段"

阶段 A（选择页）：列出扫到的项目（一句话简介 + 上次状态），**默认全勾**，点「启动 ▸」。
阶段 B（状态页）：**同一个页面原地**变成运行状态（每 child 一行 + 日志链接 + 「收尾退出」）。

⇒ 不做两个路由/两次跳转：用户的心智是"选完就看它跑"，刷新/跳转只会丢上下文。
传输用 ``http.server.ThreadingHTTPServer``（框架已有先例：``cockpit.py``），
**只读 + 一个 POST**（``POST /start``）。

## ⚠ 一条刻意的文案纪律（实测要求）

**"启动失败"与"读不到状态"必须用不同措辞**，不许都写"未知"：
- 前者：有 ``last_error``（**具体原因**：哪个 child、什么错）⇒ 显示原因；
- 后者：还没起、没有状态 ⇒ 显示"还没启动（不是读不到）"。
把两者混成"未知"，人就没法判断"该去查日志"还是"该去点启动"。

## Known Limitations and Deferred Work

- **不做鉴权、不做 TLS**：只读 + 回环是全部安全模型（与 ``cockpit`` 同一立场）；
  要暴露到非回环得由宿主在前面加真正的网关。
- **不做访问日志与限流**：``log_message`` 静音；轮询频率归页面。
- 页面是无依赖的手写字符串（**不引前端框架**）：框架零第三方依赖是纪律；
  这里要的只是"一屏状态 + 一个按钮"。
"""

from __future__ import annotations

import html
import json
import threading
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urlsplit

__all__ = ["PageOptions", "StatusPage"]


class Controller(Protocol):
    """页面需要的最小控制面（``launch.LaunchController`` 结构上满足它）。"""

    def sites(self) -> object: ...
    def started(self) -> bool: ...
    def last_status(self) -> dict: ...
    def state(self) -> dict: ...
    def start(self, enabled: list[str]) -> dict: ...
    def shutdown(self) -> dict: ...


@dataclass(frozen=True)
class PageOptions:
    host: str = "127.0.0.1"
    port: int = 0                     # 0 = 让 OS 分配（库内零端口字面量）
    title: str = "mecha"
    open_browser: bool = True


_CSS = """
body{font:14px/1.6 system-ui,"Segoe UI",sans-serif;margin:0;background:#0f1115;color:#e6e6e6}
main{max-width:960px;margin:0 auto;padding:24px 20px 64px}
h1{font-size:20px;margin:0 0 4px}
.sub{color:#8b93a1;margin:0 0 20px}
.card{background:#171a21;border:1px solid #242a35;border-radius:10px;padding:14px 16px;margin:0 0 10px}
.row{display:flex;gap:12px;align-items:flex-start}
.grow{flex:1}
.id{font-weight:600}
.brief{color:#9aa3b2}
.badge{font-size:12px;padding:2px 8px;border-radius:999px;border:1px solid #2d3442;color:#9aa3b2}
.b-ready{color:#7ee787;border-color:#2ea043}
.b-unhealthy{color:#ff7b72;border-color:#8b2c26}
.b-starting,.b-running{color:#d29922;border-color:#8a6d1f}
.b-stopped,.b-exited{color:#9aa3b2}
.ev{color:#8b93a1;font-size:12px;word-break:break-all}
.err{color:#ff7b72;font-size:13px;margin-top:6px}
button{background:#2f81f7;color:#fff;border:0;border-radius:8px;padding:9px 16px;font:inherit;cursor:pointer}
button.ghost{background:transparent;border:1px solid #2d3442;color:#e6e6e6}
button:disabled{opacity:.5;cursor:default}
label{display:flex;gap:8px;align-items:center;cursor:pointer}
pre{background:#0b0e13;border:1px solid #242a35;border-radius:8px;padding:10px;overflow:auto;max-height:280px}
a{color:#79c0ff}
.note{color:#8b93a1;font-size:12px}
"""


def _badge(state: str) -> str:
    return f'<span class="badge b-{html.escape(state)}">{html.escape(state)}</span>'


def _render_select(rows: list[dict], last: dict, last_error: str) -> str:
    prev = {str(c.get("id")): c for c in last.get("children", [])}
    items = []
    for row in rows:
        sid, brief = row["id"], row["brief"]
        old = prev.get(sid)
        old_txt = "上次：" + (str(old.get("state")) if old else "无记录")
        items.append(
            f'<div class="card"><div class="row">'
            f'<label><input type="checkbox" name="svc" value="{html.escape(sid)}" checked></label>'
            f'<div class="grow"><div class="id">{html.escape(sid)} '
            f'<span class="badge">{html.escape(old_txt)}</span></div>'
            f'<div class="brief">{html.escape(brief)}</div></div></div></div>')
    # ⚠ 文案纪律：这里只说"还没启动"；**失败**另有 _render_error 的那一路
    err = (f'<div class="err">上次启动失败：{html.escape(last_error)}</div>'
           if last_error else '')
    return (f'<h1>{html.escape(_TITLE)} · 选择要启用的项目</h1>'
            f'<p class="sub">默认全勾。<b>这不会启动 GUI</b>——纯 GUI 用户不走 mecha。</p>'
            f'{err}'
            f'<form method="post" action="/start">{items}'
            f'<div class="row" style="margin-top:14px">'
            f'<button type="submit">启动 ▸</button>'
            f'<button type="button" class="ghost" onclick="allOn(true)">全部启用</button>'
            f'<button type="button" class="ghost" onclick="allOn(false)">全部取消</button>'
            f'</div></form>'
            f'<script>function allOn(v){{document.querySelectorAll(\'input[name=svc]\')'
            f'.forEach(function(c){{c.checked=v}})}}</script>')


def _render_status(st: dict) -> str:
    kids = st.get("children", [])
    last_error = str(st.get("last_error") or "")
    cleaned = st.get("cleaned") or []
    if last_error:
        head = f'<div class="err">启动失败：{html.escape(last_error)}</div>'
    elif not kids:
        # ⚠ 与"启动失败"**不同措辞**（这一路是"还没起"，不是"读不到"）
        head = '<p class="sub">还没启动任何 child（不是读不到状态）。</p>'
    else:
        head = ''
    rows = []
    for c in kids:
        cid = html.escape(str(c.get("id")))
        lease = c.get("holds_write_lease")
        lease_txt = ('<span class="badge b-ready">写权</span>' if lease
                     else '<span class="badge">非写宿主</span>')
        ev = html.escape(str(c.get("ready_evidence") or ""))
        err = (f'<div class="err">{html.escape(str(c.get("last_error")))}</div>'
               if c.get("last_error") else '')
        port = c.get("port")
        port_txt = f'端口 <b>{port}</b>' if port else '端口 —'
        rows.append(
            f'<div class="card"><div class="row"><div class="grow">'
            f'<div class="id">{cid} {_badge(str(c.get("state")))} {lease_txt}</div>'
            f'<div class="ev">pid {c.get("pid")} · {port_txt} · 重启 {c.get("restarts")}'
            f' · exit {c.get("exit_code")}</div>'
            f'<div class="ev">{ev}</div>{err}'
            f'<div class="note"><a href="/logs/{cid}">看日志</a></div>'
            f'</div></div></div>')
    cleaned_txt = ''
    if cleaned:
        items = ''.join(f'<li>{html.escape(str(x))}</li>' for x in cleaned)
        cleaned_txt = (f'<div class="card"><div class="id">启动前清掉的陈旧残留</div>'
                       f'<ul class="ev">{items}</ul></div>')
    return (f'<h1>{html.escape(_TITLE)} · 运行状态</h1>'
            f'<p class="sub">启用：{html.escape(", ".join(st.get("enabled") or []) or "—")}'
            f'（每 2s 自动刷新）</p>{head}{cleaned_txt}{"".join(rows)}'
            f'<form method="post" action="/shutdown" '
            f'onsubmit="return confirm(\'收尾退出：逆序停掉全部 child？\')">'
            f'<button class="ghost" type="submit">收尾退出</button></form>'
            f'<script>setTimeout(function(){{location.reload()}},2000)</script>')


_TITLE = "mecha"


def render_page(controller: Controller) -> str:
    """整页 HTML（**阶段 A / B 同页**：由 ``started()`` 决定渲染哪一版）。"""
    if not controller.started():
        rows = []
        for site in controller.sites():                    # type: ignore[union-attr]
            for svc in site.services:
                rows.append({"id": svc.id, "brief": svc.brief})
        body = _render_select(rows, controller.last_status(),
                              str(controller.last_status().get("last_error") or ""))
    else:
        body = _render_status(controller.state())
    return (f'<!doctype html><html lang="zh"><head><meta charset="utf-8">'
            f'<title>{html.escape(_TITLE)}</title><style>{_CSS}</style></head>'
            f'<body><main>{body}</main></body></html>')


class _Handler(BaseHTTPRequestHandler):
    controller: Controller
    page: "StatusPage"

    def log_message(self, *args) -> None:                  # noqa: N802 - 基类约定
        pass                                               # 静音（见 Known Limitations）

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, code: int, text: str) -> None:
        self._send(code, text.encode("utf-8"), "text/html; charset=utf-8")

    def do_GET(self) -> None:                              # noqa: N802
        path = urlsplit(self.path).path
        if path == "/":
            return self._html(200, render_page(self.controller))
        if path == "/state.json":
            return self._send(200, json.dumps(self.controller.state(), ensure_ascii=False)
                              .encode("utf-8"), "application/json; charset=utf-8")
        if path == "/health":
            # 给"就绪探活"用的：状态页自己也是一个可探活的服务
            return self._send(200, b'{"ok":true}', "application/json; charset=utf-8")
        if path.startswith("/logs/"):
            cid = path[len("/logs/"):]
            return self._html(200, self.page.render_log(cid))
        return self._html(404, "<h1>404</h1><p>没有这个页面。</p>")

    def do_POST(self) -> None:                             # noqa: N802
        path = urlsplit(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        if path == "/start":
            enabled = parse_qs(raw).get("svc", [])
            self.controller.start(list(enabled))
            return self._html(200, render_page(self.controller))
        if path == "/shutdown":
            self.controller.shutdown()
            return self._html(200, render_page(self.controller))
        return self._html(404, "<h1>404</h1><p>只读 + 一个 /start。</p>")


class StatusPage:
    """两阶段页面的传输层（起/停/URL；实际渲染见 :func:`render_page`）。"""

    def __init__(self, controller: Controller, opts: PageOptions = PageOptions()) -> None:
        self.controller = controller
        self.opts = opts
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> int:
        """起服务，返回**实际绑定端口**（回读，不用传入的偏好值）。"""
        site = self.controller.sites()[0]                  # type: ignore[index]
        global _TITLE
        _TITLE = f"mecha · {site.name}"
        handler = type("_Bound", (_Handler,), {"controller": self.controller, "page": self})
        self._httpd = ThreadingHTTPServer((self.opts.host, self.opts.port), handler)
        port = int(self._httpd.server_address[1])          # ⭐ 回读实际端口
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        name="status-page", daemon=True)
        self._thread.start()
        if self.opts.open_browser:
            try:
                webbrowser.open(f"http://{self.opts.host}:{port}/")
            except Exception:                              # noqa: BLE001 - 打不开浏览器不算失败
                pass
        return port

    def serve_forever(self) -> None:
        """阻塞到 Ctrl-C（``--all`` 之后仍要让状态页可看）。"""
        if self._httpd is None:
            raise RuntimeError("先 start() 再 serve_forever()")
        try:
            while True:
                threading.Event().wait(0.5)
        except KeyboardInterrupt:
            raise

    @property
    def port(self) -> int | None:
        return int(self._httpd.server_address[1]) if self._httpd else None

    @property
    def url(self) -> str:
        return f"http://{self.opts.host}:{self.port}/"

    def render_log(self, child_id: str) -> str:
        """日志末尾若干行（页面上的"看日志"）。"""
        try:
            c = self.controller.super.child(child_id)      # type: ignore[attr-defined]
            lines = c.log_tail(200)
            body = html.escape("\n".join(lines) or "(日志为空)")
            path = html.escape(str(c.log_path))
        except Exception:                                  # noqa: BLE001 - 未知 child：如实说
            body = "（没有这个 child 的日志）"
            path = ""
        return (f'<h1>日志 · {html.escape(child_id)}</h1>'
                f'<p class="sub">{path}</p><pre>{body}</pre>'
                f'<p><a href="/">← 回状态页</a></p>')

    def stop(self) -> None:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except OSError:
                pass
            self._httpd = None


def render_log_path_only(controller: Controller, child_id: str) -> Path | None:
    """给判据/脚本用：拿日志路径而不渲染 HTML。"""
    try:
        return controller.super.child(child_id).log_path      # type: ignore[attr-defined]
    except Exception:                                        # noqa: BLE001
        return None
