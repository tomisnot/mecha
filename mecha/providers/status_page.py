"""常驻控制台（**一页**，不是"选完就换状态页"的向导）。

## 形态（三层架构 §7：面板是**常驻控制台**）

```
┌────────────────────────────────────────────────────────────┐
│ mecha · 控制台                              [◀ 收起]        │
├──────────────┬─────────────────────────────────────────────┤
│ 侧栏（可折叠）│ ☑ │ Machine Learning │ [●──] │ ready │ 写权… │
│  ML          │   │ 传统机器学习 / 神经网络 / 自动调参          │
│  EL          │ ☑ │ Energy Level     │ [●──] │ ready │ 写权… │
│  GP          │ ☐ │ Get Paper        │ [──○] │ stopped│  —   │
├──────────────┴─────────────────────────────────────────────┤
│ [全部启用] [全部停用] [启动 ▸] [收尾退出]                     │
└────────────────────────────────────────────────────────────┘
```

⭐ **三个控件分工**（§7 的表）：
- **存活滑块** ⇒ 动 ①（起/停软件）；
- **写权按钮** ⇒ 动 ③（切 AI/手动）——⭐ **走控制端点那条腿**（§7.1.1），
  **切到手动 = 两次调用、按顺序、分开报告**（`set_write_authority("human")` →
  `open_manual_surface()`）：⚠ 合并成一次会让"写权切了但窗口没开"看起来像成功；
- `has_gui=False` 的项目 ⇒ 写权那栏显示 **"—"**（它只有 AI 一条腿，没有"手动"这一档）。

## ⚠ 「待命」是**语义**，不是状态（§1）

切完写权之后 **面板与 dsh 都不关闭**：它们的**写**被闸门拒、**读**照常 ⇒
它们**本来就是"待命"这个语义**。⇒ 界面显示"已切人工 · 待命"，而不是"已断开/已关闭"。

## ⚠ 端口号不许进页面（契约 §7.1.1）

`.control-port` **只给框架**；⚠ **不许把 `:control-port` 写进任何给 AI 的文本/工具输出**。
⇒ 本页面**不渲染任何端口号**（连能力面端口也不渲染：端口对人不是信息，对"泄漏"却是风险面）。

## ⚠ 用户给的四条布局要求（**必须遵守**，别退回旧形态）

1. **左侧列表可折叠**，且折叠后**真的让出宽度**（不是"变窄一点"）——
   用 ``grid-template-columns`` 在 260px 与 0 之间切换，而不是缩小字体/内边距；
2. **不要固定高度**：列表在**自己的格子里滚动**（``overflow-y:auto`` + ``min-height:0``），
   **页面本身不被撑长**；
3. **不要卡片框**（没有 border+radius 的"卡片"容器）；
4. **不许出现"详情"这种占一整行的标题**——每行只放控件与状态。

## Known Limitations and Deferred Work

- **写权控件不做**（本轮）：它要控制通道（§7.1.1 两条端点）。页面显示"待接"并**注明依赖**，
  **不假装能点**（半成品通道比没有更坏）。
- **不做鉴权 / TLS**：只读为主 + 几个 POST，绑回环（与 ``cockpit`` 同一立场）。
- **不做访问日志与限流**：``log_message`` 静音；轮询/刷新归页面。
- **不引前端框架**：零依赖是纪律；这里要的是"一屏控件 + 几个 POST"。
- **勾选框（左侧那列）只表达"打算开"**（意图），**不可点**：真正的控件是存活滑块。
  启动前的意图存在 ``LaunchController.intent()`` 里；启动后"存活"一律读
  ``supervisor.state()``（铁律 7：进程事实只有一个来源）。
"""

from __future__ import annotations

import html
import json
import threading
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Protocol
from urllib.parse import parse_qs, urlsplit

__all__ = ["PageOptions", "StatusPage", "render_page", "MODE_PENDING_TEXT"]

#: 写权那栏在本轮的文案（⚠ 刻意叫"待接"：它现在**不能点**，依赖控制通道）
MODE_PENDING_TEXT = "待接（需控制通道 §7.1.1）"
_TITLE = "mecha"


class Controller(Protocol):
    """页面需要的最小控制面（``launch.LaunchController`` 结构上满足它）。

    ⚠ **`sites` 是属性，不是方法** —— 原先这里写成 ``def sites(self)``，
    与页面里的 `self.controller.sites()` 一起构成一个**从未被执行过**的错误契约
    （真实现是 `self.sites = sites`）⇒ 页面起不来、判据却全绿。
    ⇒ 契约必须**照真实现**写，否则它会替页面撒谎。
    """

    #: 站点元组（`launch.LaunchController.sites` 是属性）。
    sites: object
    def services(self) -> object: ...
    def started(self) -> bool: ...
    def intent(self) -> dict: ...
    def last_status(self) -> dict: ...
    def state(self) -> dict: ...
    def start(self, enabled: list) -> dict: ...
    def set_service_enabled(self, svc_id: str, enabled: bool) -> dict: ...
    def set_intent(self, svc_id: str, enabled: bool) -> None: ...
    def write_authority_state(self, svc_id: str) -> dict: ...
    def control_write_authority(self, svc_id: str, action: str) -> dict: ...
    def shutdown(self) -> dict: ...


@dataclass(frozen=True)
class PageOptions:
    host: str = "127.0.0.1"
    port: int = 0                     # 0 = 让 OS 分配（库内零端口字面量）
    title: str = "mecha"
    open_browser: bool = True


_CSS = """
*{box-sizing:border-box}
body{font:14px/1.6 system-ui,"Segoe UI",sans-serif;margin:0;background:#0f1115;color:#e6e6e6}
header{display:flex;align-items:center;gap:12px;padding:13px 18px;border-bottom:1px solid #242a35}
h1{font-size:18px;margin:0;flex:1}
h3{font-size:13px;margin:8px 0 4px;color:#8b93a1;font-weight:600}
/* ⚠ 折叠：整列宽度在 260px 与 0 之间切换 ⇒ **真的让出宽度**（不是缩小内边距） */
#shell{display:grid;grid-template-columns:260px 1fr;height:calc(100vh - 104px)}
#shell.collapsed{grid-template-columns:0 1fr}
#side{border-right:1px solid #242a35;overflow-y:auto;min-height:0}
#shell.collapsed #side{display:none}
#main{overflow-y:auto;min-height:0;padding:4px 18px 14px}
.row{display:grid;grid-template-columns:24px 170px 92px 88px 1fr;gap:10px;align-items:center;
     padding:8px 2px;border-bottom:1px solid #1b2029}   /* ⚠ 只有分隔线，**不套卡片框** */
.brief{grid-column:2 / span 4;color:#8b93a1;font-size:12px;margin:-4px 0 2px}
.name{font-weight:600}
.badge{font-size:12px;padding:1px 8px;border-radius:999px;border:1px solid #2d3442;color:#9aa3b2;
       justify-self:start}
.b-ready{color:#7ee787;border-color:#2ea043}
.b-unhealthy{color:#ff7b72;border-color:#8b2c26}
.b-starting,.b-running{color:#d29922;border-color:#8a6d1f}
.pending{color:#8b93a1;font-size:12px}
.muted{color:#8b93a1}
.err{color:#ff7b72}
button{background:#2f81f7;color:#fff;border:0;border-radius:8px;padding:8px 14px;font:inherit;cursor:pointer}
button.ghost{background:transparent;border:1px solid #2d3442;color:#e6e6e6}
footer{display:flex;gap:10px;align-items:center;padding:11px 18px;border-top:1px solid #242a35}
/* 存活滑块：checkbox + 纯 CSS 开关（零依赖） */
.sw{position:relative;display:inline-block;width:50px;height:23px}
.sw input{display:none}
.sw label{position:absolute;inset:0;background:#2d3442;border-radius:999px;cursor:pointer;
          transition:background .15s}
.sw label:after{content:"";position:absolute;top:3px;left:3px;width:17px;height:17px;border-radius:50%;
                background:#e6e6e6;transition:left .15s}
.sw input:checked + label{background:#2ea043}
.sw input:checked + label:after{left:30px}
.sideitem{padding:7px 12px;border-bottom:1px solid #1b2029;font-size:13px}
.sideitem.on{color:#7ee787}
pre{background:#0b0e13;border:1px solid #242a35;border-radius:8px;padding:10px;overflow:auto;max-height:280px}
a{color:#79c0ff}
"""


def _badge(state: str) -> str:
    return f'<span class="badge b-{html.escape(state)}">{html.escape(state)}</span>'


def _mode_cell(svc: object, ctl_state: dict | None) -> str:
    """写权那栏（③）——⭐ 显示**从控制端点读回来的真值**，不是面板记的意图。

    形态（**不许出现第五种**）：
    | 形态 | 何时 | 渲染 |
    |---|---|---|
    | 能点 | ``has_gui=true``、控制端点可用、真值说**人工持有** | 「交还给 AI」 |
    | 能点 | 同上，但真值说**AI 持有** | 「切到手动」 |
    | 读不到 | 控制端点可用但**读操作失败**（如项目还没实现它） | 「读不到」+ 原因（**不显示旧值/不显示 0**） |
    | 不可用 | ``has_gui=true`` 但**软件没起来** | 灰按钮 + 为什么（不是点了才报错） |
    | — | ``has_gui=false`` | 破折号（没有手动面）**但仍显示真值** |

    ⚠ 端口号**绝不出现**在这里（契约：不许把 ``:control-port`` 写进给 AI 的文本/输出）。
    """
    sid = html.escape(str(getattr(svc, "id", "")))
    st = ctl_state or {}
    read = st.get("read") or None
    # ⭐ 真值那一栏：**读得到就报真值；读不到就如实说读不到**（铁律 8）
    if read:
        mode = html.escape(str(read.get("mode", "?")))
        who = "人工持有" if read.get("human_holds") else ("AI 持有" if read.get("ai_can_write")
                                                        else "无人可写（locked）")
        truth = f'<span class="badge">写权 {mode} · {who}</span>'
    elif st.get("ok") or st.get("read_error"):
        truth = '<span class="pending">写权：读不到（不是"没有"）</span>'
    else:
        truth = '<span class="pending">写权：—</span>'

    if not getattr(svc, "has_gui", False):
        return truth        # 没有手动面 ⇒ 只报真值，**不给按钮**
    if not st.get("ok"):
        why = html.escape(str(st.get("why") or "控制端点不可用"))
        return (truth + f' <button class="ghost" disabled title="{why}">切到手动</button>'
                f'<span class="pending"> {why}</span>')
    human_holds = bool((read or {}).get("human_holds"))
    if not read:
        # 读不到 ⇒ **不给可点的按钮**（按下去也是猜），但如实说明
        return (truth + '<button class="ghost" disabled title="写权读不到，先查明原因">'
                        '切到手动</button>')
    if human_holds:
        # ⭐ **待命语义**：写被拒、读照常，面板与 dsh **不关闭**（这是语义，不是状态）
        return (truth + ' <button class="ghost" '
                f"onclick=\"mode('{sid}','ai')\">交还给 AI</button>")
    return truth + f' <button onclick="mode(\'{sid}\',\'human\')">切到手动</button>'


def _service_row(svc: object, row: dict | None, intent: bool, started: bool,
                 ctl_state: dict | None) -> str:
    sid = html.escape(str(getattr(svc, "id", "")))
    running = bool((row or {}).get("pid"))
    state = str((row or {}).get("state") or ("stopped" if started else "stopped"))
    checked = " checked" if (running or (not started and intent)) else ""
    # ⚠ tail 里**不报控制端口**（契约：不许把 `:control-port` 写进任何给 AI 的文本/输出）；
    #   这里索性连能力面端口也不报 —— 端口对人不是信息，对"泄漏"却是风险面。
    tail = f'pid {(row or {}).get("pid")}' if running else "未运行"
    return (
        f'<div class="row">'
        # 左侧勾选**只表达"打算开"**（意图）⇒ 不可点：真正的控件是右边那个滑块
        f'<input type="checkbox"{checked} disabled title="打算开（启动前）">'
        f'<span class="name">{sid}</span>'
        f'<span class="sw"><input type="checkbox" id="sw-{sid}"{checked}'
        f' onchange="toggle(\'{sid}\', this.checked)"><label for="sw-{sid}"></label></span>'
        f'{_badge(state)}'
        f'{_mode_cell(svc, ctl_state)}'
        f'<span class="brief">{html.escape(str(getattr(svc, "brief", "")))}'
        f' · <span class="muted">{html.escape(tail)}</span></span>'
        f'</div>')


def _dsh_row(row: dict | None) -> str:
    sid = "dsh"
    running = bool((row or {}).get("pid"))
    return (
        f'<div class="row"><span></span><span class="name">dsh</span>'
        f'<span class="sw"><input type="checkbox" id="sw-{sid}"{" checked" if running else ""}'
        f' onchange="toggle(\'{sid}\', this.checked)"><label for="sw-{sid}"></label></span>'
        f'{_badge(str((row or {}).get("state") or "stopped"))}'
        f'<span class="muted">—</span>'
        f'<span class="brief muted">它只是 AI 那条腿的载体</span></div>')


def render_page(controller: Controller) -> str:
    """常驻控制台整页。

    ⭐ **始终列出全部已声明项目**（启动前、启动后、未启用、``has_gui=False`` 都在）
    —— 这正是"常驻控制台"与"选完就换页的向导"的分界。
    """
    started = controller.started()
    st = controller.state()
    rows = {str(r.get("id")): r for r in st.get("children", [])}
    intent = controller.intent()
    services = list(controller.services())          # type: ignore[arg-type]

    items: list[str] = []
    side: list[str] = []
    for svc in services:
        sid = str(getattr(svc, "id", ""))
        row = rows.get(sid)
        # ⚠ 写权栏的可用性由控制器如实给出（**软件没起来 ⇒ 灰按钮**，不是点了才报错）
        try:
            ctl_state = controller.write_authority_state(sid)
        except Exception as exc:                       # noqa: BLE001 - 页面如实降级
            ctl_state = {"ok": False, "why": f"控制端点状态读不到：{exc}"}
        items.append(_service_row(svc, row, bool(intent.get(sid)), started, ctl_state))
        on = bool((row or {}).get("pid")) or (not started and bool(intent.get(sid)))
        side.append(f'<div class="sideitem{" on" if on else ""}">{html.escape(sid)}</div>')

    err = str(st.get("last_error") or "")
    if err:
        head = f'<div class="row err">启动失败：{html.escape(err)}</div>'
    elif not started:
        # ⚠ 与"启动失败"**不同措辞**（"还没起"≠"读不到"）
        head = '<div class="row muted">还没启动（不是读不到状态）：用滑块选好，再点「启动 ▸」。</div>'
    else:
        head = ""
    cleaned = st.get("cleaned") or []
    if cleaned:
        lis = "".join(f"<li>{html.escape(str(x))}</li>" for x in cleaned)
        items.append(f'<h3>启动前清掉的陈旧残留</h3><ul class="muted">{lis}</ul>')
    if rows.get("dsh") is not None:
        items.append(_dsh_row(rows.get("dsh")))

    return (
        '<!doctype html><html lang="zh"><head><meta charset="utf-8">'
        f'<title>{html.escape(_TITLE)}</title><style>{_CSS}</style></head><body>'
        f'<header><h1>{html.escape(_TITLE)} · 控制台</h1>'
        f'<span class="muted">{html.escape(str((st.get("sites") or ["?"])[0]))}</span>'
        '<button class="ghost" onclick="fold()">◀ 收起</button>'
        '<button class="ghost" onclick="location.reload()">刷新</button></header>'
        f'<div id="shell"><div id="side">{"".join(side)}</div>'
        f'<div id="main">{head}{"".join(items)}</div></div>'
        '<footer><button type="button" onclick="allOn(true)">全部启用</button>'
        '<button type="button" onclick="allOn(false)">全部停用</button>'
        '<button onclick="startNow()">启动 ▸</button>'
        '<button class="ghost" onclick="shutdownNow()">收尾退出</button>'
        '<span class="muted">存活滑块随时可动</span>'
        '<span class="pending">切写权**不动进程、不动连接**：面板与 dsh 待命，写被拒、读照常</span>'
        '</footer>'
        '<script>'
        'function fold(){document.getElementById("shell").classList.toggle("collapsed")}'
        'async function post(p,b){const r=await fetch(p,{method:"POST",body:b||""});'
        'document.documentElement.innerHTML=await r.text()}'
        'function toggle(id,on){post("/service", new URLSearchParams({svc:id,on:on?"1":"0"}))}'
        'function mode(id,act){post("/mode", new URLSearchParams({svc:id,action:act}))}'
        'function allOn(v){post("/all", new URLSearchParams({on:v?"1":"0"}))}'
        'function startNow(){post("/start")}'
        'function shutdownNow(){if(confirm("收尾退出：逆序停掉全部 child？"))post("/shutdown")}'
        '</script></body></html>')


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
            return self._send(200, b'{"ok":true}', "application/json; charset=utf-8")
        if path.startswith("/logs/"):
            return self._html(200, self.page.render_log(path[len("/logs/"):]))
        return self._html(404, "<h1>404</h1><p>没有这个页面。</p>")

    def _body(self) -> str:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length).decode("utf-8") if length else ""

    def do_POST(self) -> None:                             # noqa: N802
        path = urlsplit(self.path).path
        form = parse_qs(self._body())
        try:
            if path == "/start":
                enabled = [s for s, v in self.controller.intent().items() if v]
                self.controller.start(enabled)
            elif path == "/service":                       # ⭐ 存活滑块（单个软件）
                svc = (form.get("svc") or [""])[0]
                on = (form.get("on") or ["0"])[0] == "1"
                self.controller.set_service_enabled(svc, on)
            elif path == "/mode":                          # ⭐ 写权按钮（切到手动 / 交还给 AI）
                svc = (form.get("svc") or [""])[0]
                action = (form.get("action") or [""])[0]
                out = self.controller.control_write_authority(svc, action)
                if not out.get("ok"):
                    # ⚠ **如实报告哪一步成/哪一步败**（不合并成一次"看起来成功了"）
                    detail = "；".join(
                        f'{s["step"]}：{"✅" if s["ok"] else "❌ " + str(s.get("error", ""))}'
                        for s in out.get("steps", [])) or str(out.get("error", ""))
                    page = render_page(self.controller)
                    return self._html(200, page.replace(
                        "<div id=\"main\">",
                        f'<div id="main"><div class="row err">'
                        f'{html.escape(str(out.get("error", "写权操作失败")))}<br>'
                        f'<span class="muted">{html.escape(detail)}</span></div>'))
            elif path == "/all":                           # 批量：只改**意图**（不 spawn）
                on = (form.get("on") or ["1"])[0] == "1"
                for svc in self.controller.services():     # type: ignore[union-attr]
                    self.controller.set_intent(str(getattr(svc, "id", "")), on)
            elif path == "/shutdown":
                self.controller.shutdown()
            else:
                return self._html(404, "<h1>404</h1><p>只有 /start 与 /service。</p>")
        except Exception as exc:                           # noqa: BLE001 - 页面如实报故障
            return self._html(200, f'<div class="row err">{html.escape(str(exc))}</div>')
        return self._html(200, render_page(self.controller))


class StatusPage:
    """常驻控制台的传输层（起/停/URL；渲染见 :func:`render_page`）。"""

    def __init__(self, controller: Controller, opts: PageOptions = PageOptions()) -> None:
        self.controller = controller
        self.opts = opts
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> int:
        """起服务，返回**实际绑定端口**（回读，不用传入的偏好值）。"""
        #: ⚠ **这里是属性不是方法**（`LaunchController.sites` 是一个 tuple 属性）。
        #: 原先写成 `self.controller.sites()` ⇒ `TypeError: 'tuple' object is not callable`
        #: ⇒ ⭐ **页面根本起不来**，而且**没有任何判据抓到**：所有判据只调 `render_page(stub)`，
        #: 而 `render_page` 读的是 `state()["sites"]`、**不碰 `sites`** ⇒ 这一行从未被执行过。
        #: 判据的替身还把 `sites` 写成了方法（与页面同错、与真实现不同）⇒ 全绿。
        #: ⇒ 现在有一条**真起 `StatusPage`** 的判据钉住（见 `checks/launch_selfcheck.py`）。
        site = self.controller.sites[0]
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
        """阻塞到 Ctrl-C（``--all`` 之后仍要让控制台可看）。"""
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
        """日志末尾若干行（面板上的"看日志"）。"""
        try:
            c = self.controller.super.child(child_id)      # type: ignore[attr-defined]
            lines = c.log_tail(200)
            body = html.escape("\n".join(lines) or "(日志为空)")
            path = html.escape(str(c.log_path))
        except Exception:                                  # noqa: BLE001 - 未知 child：如实说
            body = "（没有这个 child 的日志）"
            path = ""
        return (f'<h1>日志 · {html.escape(child_id)}</h1>'
                f'<p class="muted">{path}</p><pre>{body}</pre>'
                f'<p><a href="/">← 回控制台</a></p>')

    def stop(self) -> None:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except OSError:
                pass
            self._httpd = None
