"""常驻控制台（**一页**，不是"选完就换状态页"的向导）。

## 形态（三层架构 §7：面板是**常驻控制台**）

```
┌────────────────────────────────────────────────────────────┐
│ mecha · 控制台                              [◀ 收起]        │
├──────────────┬─────────────────────────────────────────────┤
│ 侧栏（可折叠）│ 接入的项目（可多选）                          │
│  ML          │ ☑ │ Machine Learning │ [●──] │ ready │ 写权… │
│  EL          │ ☑ │ Energy Level     │ [●──] │ ready │ 写权… │
│ ─────────    │ AI 宿主（两行滑块互斥，默认 dsh）             │
│ AI · dsh     │   │ dsh   │ [●──] │ running │ 由启动器托管… │
│              │   │ codex │ [──○] │   —     │ 外部 MCP…     │
├──────────────┴─────────────────────────────────────────────┤
│ 运行输出（默认只留这一条标题栏，往上拖才展开；高度记在本机）    │
└────────────────────────────────────────────────────────────┘
```

⭐ **两类必须分开**（2026-11-05 用户裁决）：上面是**接入的项目**（多选，表达"打算开哪几个"），
下面是 **AI 宿主**（dsh / codex 两行滑块，**开一个自动关另一个**）。

⚠ 宿主行原来混在项目表里、且种类由 `--ai-host` 在命令行定死 ⇒ 启动阶段它是个摆设
（开关决定不了什么，启动后只用来重启）。现在**滑块就是唯一控件**（与项目行同形，
没有单选圆点），语义分两段：

| 时机 | 打开某一行 | 关掉 |
|---|---|---|
| 启动前 | 这一轮用它（只记选择，**不 spawn**） | 这一轮不用它 |
| 启动后 | 起它的进程（受管）/ 写它的配置（外部） | 停它的进程 |

打开**另一个**时若旧的还在跑：`set_host_kind` 会**先停旧的、再起新的**（真值只有一个
`host_kind` ⇒ 互斥是自动的）。两行都关 = 这一轮不启用宿主（等价 `--ai-host none`）。

⭐ **四个控件分工**（§7 的表 + 宿主滑块）：
- **存活滑块** ⇒ 动 ①（起/停软件）；
- **宿主滑块** ⇒ 换 AI 宿主（dsh / codex，互斥）——⚠ **不动软件、不动写权**；
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

## ⚠ 用户给的布局要求（**必须遵守**，别退回旧形态）

1. **左侧列表可折叠**，且折叠后**真的让出宽度**（不是"变窄一点"）——
   用 ``grid-template-columns`` 在 260px 与 0 之间切换，而不是缩小字体/内边距；
2. **不要固定高度**：列表在**自己的格子里滚动**（``overflow-y:auto`` + ``min-height:0``），
   **页面本身不被撑长**；
3. **不要卡片框**（没有 border+radius 的"卡片"容器）；
4. **不许出现"详情"这种占一整行的标题**——每行只放控件与状态；
5. ⭐ **两类分开**：接入的项目（多选）与 AI 宿主（单选）不混在一张表里；
6. ⭐ **调试终端默认收起**，只留一条标题栏（28px）；**标题栏就是把手**，往上拖才展开，
   高度记在本机（`localStorage`），双击折叠。

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
import socket
import threading
import webbrowser
from pathlib import Path
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Protocol
from urllib.parse import parse_qs, urlsplit

from ..site import DSH_CHILD_ID

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
    def dsh_spec(self) -> object: ...
    def services(self) -> object: ...
    def started(self) -> bool: ...
    def intent(self) -> dict: ...
    def last_status(self) -> dict: ...
    def state(self) -> dict: ...
    def start(self, enabled: list) -> dict: ...
    def set_service_enabled(self, svc_id: str, enabled: bool) -> dict: ...
    def set_intent(self, svc_id: str, enabled: bool) -> None: ...
    def host_info(self) -> dict: ...
    def host_choices(self) -> list: ...
    def set_host_kind(self, kind: str) -> dict: ...
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
body{font:14px/1.6 system-ui,"Segoe UI",sans-serif;margin:0;background:#0f1115;color:#e6e6e6;
     /* ⭐ 结构式满高：shell 吃剩余 ⇒ **页头/页脚改高度都不会再把底部挤出去** */
     display:flex;flex-direction:column;height:100vh;overflow:hidden}
header{display:flex;align-items:center;gap:12px;padding:13px 18px;border-bottom:1px solid #242a35}
h1{font-size:18px;margin:0;flex:1}
h3{font-size:13px;margin:8px 0 4px;color:#8b93a1;font-weight:600}
/* ⚠ 折叠：整列宽度在 260px 与 0 之间切换 ⇒ **真的让出宽度**（不是缩小内边距） */
#shell{display:grid;grid-template-columns:260px 1fr;flex:1;min-height:0}
header,footer{flex:0 0 auto}
#shell.collapsed{grid-template-columns:0 1fr}
#side{border-right:1px solid #242a35;overflow-y:auto;min-height:0}
#shell.collapsed #side{display:none}
#main{overflow-y:auto;min-height:0;padding:4px 18px 14px}
.terminal{flex:0 0 auto;height:28px;min-height:28px;max-height:70vh;border-top:1px solid #242a35;
          background:#0b0e13;display:flex;flex-direction:column;overflow:hidden}
/*: ⭐ 标题条就是**把手**：默认只有这一条（28px），往上拖才展开 —— 用户要的是
   "想看的时候拉出来，不想看的时候拉低"。⚠ 光标 `ns-resize` 是唯一提示，别再画边框。 */
.terminal-head{padding:6px 18px;color:#8b93a1;font-size:12px;border-bottom:1px solid #1b2029;
               cursor:ns-resize;user-select:none;display:flex;gap:10px;align-items:center}
.terminal-head .grip{margin-left:auto;color:#5f6b7a}
#console{margin:0;padding:8px 18px;overflow:auto;white-space:pre-wrap;word-break:break-word;
         color:#c9d1d9;font:12px/1.45 ui-monospace,SFMono-Regular,Consolas,monospace;flex:1;
         max-height:none;border:0;border-radius:0;background:#0b0e13;display:none}
#term.open #console{display:block}
/*: 两类之间分开：项目（可多选）与 AI 宿主（只能选一个） */
.group{display:flex;gap:10px;align-items:baseline;padding:14px 2px 6px;color:#8b93a1;
       font-size:12px;font-weight:600;border-bottom:1px solid #242a35}
.sidesep{border-top:1px solid #242a35;margin:6px 0}
.row{display:grid;grid-template-columns:24px 170px 92px 88px 1fr;gap:10px;align-items:center;
     padding:8px 2px;border-bottom:1px solid #1b2029}   /* ⚠ 只有分隔线，**不套卡片框** */
.brief{grid-column:2 / span 4;color:#8b93a1;font-size:12px;margin:-4px 0 2px}
.name{font-weight:600}
/* ⭐ 提示/错误是**整块文字**：⚠ 不许用 `.row`（那是 5 列网格 ⇒ 文字会被塞进 24px 的第 1 格，
   实测现象是**一字一行**竖排）。 */
.notice{padding:9px 2px;border-bottom:1px solid #1b2029}
/* ⭐ 写权那一格的**收容器**：里面可能有多个元素（真值 + 按钮），必须只占**一格**。 */
.mode{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
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
footer{display:flex;gap:10px;align-items:center;padding:11px 18px;border-top:1px solid #242a35;
       flex-wrap:wrap}                 /* ⚠ 窄窗口/文字多时**换行**，别把右边裁掉 */
/*: ⚠⚠ **`flex-basis:100%` 是必须的**（实测踩到的坑，用户截图抓到）：
   原来写 `flex:1;min-width:0`，而**中文的最小内容宽度就是 1 个字**（汉字之间处处可断）
   ⇒ 它**塌成一字一行**、在页面最右边竖着排。⇒ 让它**独占一行**（宽度 = 整行）就不可能竖排。 */
.foot-note{flex:1 1 100%}
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


def _host_notice(out: dict) -> str:
    """宿主动作的结果：**每一步单独报**（与写权那一套同形，不合并成"看起来成功了"）。

    ⚠ 拨宿主滑块会真的停/起一个进程，也会真的写一份配置 ⇒ 只回一句"已切换"是撒谎：
    人需要知道是哪一步没成（停旧的 / 起新的 / 写配置）。
    """
    steps = out.get("steps") or []
    act = str(out.get("action") or "switch")
    verb = {"open": "启用", "close": "关闭", "switch": "切到"}.get(act, "切换")
    kind = html.escape(str(out.get("kind") or out.get("id") or ""))
    detail = "；".join(
        f'{s.get("step")}：{"✅" if s.get("ok") else "❌ " + str(s.get("error", ""))}'
        for s in steps)
    if not steps:
        return (f'<div class="notice muted" data-live="1">当前就是 '
                f'<strong>{kind}</strong>（没动任何东西）。</div>')
    cls = "notice" if out.get("ok") else "notice err"
    title = f"已{verb} " if out.get("ok") else f"{verb}未完成："
    return (f'<div class="{cls}" data-live="1">{title}<strong>{kind}</strong>'
            f'<br><span class="muted">{html.escape(detail)}</span></div>')


def _mode_cell(svc: object, ctl_state: dict | None) -> str:
    """写权那栏（③）——⭐ 显示**从控制端点读回来的真值**，不是面板记的意图。

    ## ⚠⚠ 两件事**分开**（2026-10-09 审计偏差的修正，设计文档 §7.1.1 已补）

    | 问的问题 | 取决于 | 面板 |
    |---|---|---|
    | **能不能切写权**？ | ⭐ **控制端点通不通** | 能不能给按钮 |
    | **有没有"手动面"可开**？ | ⭐ **`has_gui`** | 按钮**叫什么、干什么** |

    ⚠ 原先 `has_gui=false` ⇒ **一个按钮都不给** ⇒ 那个项目**再也切不了写权**
    ⇒ **正是 §7.1.1 那条裁决要避免的盲区**（裁决写在端点那层，这层又长回来了）。
    ⇒ 现在：`has_gui=false` **也给按钮**，但它是**另一个动作**（干的事真的不同）：

    | `has_gui` | 归人的按钮 | 干什么 |
    |---|---|---|
    | `true` | 「**切到手动**」 | 切写权 **+ 开窗**（两步分开报告） |
    | `false` | 「**收回写权（冻结）**」 | ⭐ **只**切写权 ⇒ `locked`（⚠ 不调开窗 —— 它没有面，调了只会响亮失败） |
    | ⭐ **共用档**（`action="none"`） | **不画按钮** | ⭐ 本项目**没有写权切换需求**（见下） |

    ⚠ **为什么不给 `human`**：`HUMAN` 的语义是"人侧通道可写"，
    而**没有手动面的软件里人没有可写通道** ⇒ 那是**骗人的标签**；
    人的真实意图是"先把写权收回" ⇒ `locked`（谁都不能写）**才是老实的那个**。

    ⭐⭐ **共用档为什么连按钮都不画**（2026-10-10 用户裁决：
    **"如果有 AI 与人共用的模式，说明该项目没有写权切换的需求"**）：
    `Mode.OPEN` 下**两侧都能写** ⇒ 「切到手动 / 交还给 AI」这两个标签**描述不了任何真实差别**
    （按下去也不改变谁能写）⇒ 画出来就是**假按钮**。这类项目的入口在**它自己**
    （`how_to_open` 那句原话），不在这里。⚠ 也不许把共用报成"人工持有"：
    共用档 `human_holds` 与 `ai_can_write` **同时为真**，单看任何一个都会误报。

    形态（**不许出现别的**）：

    | 形态 | 何时 | 渲染 |
    |---|---|---|
    | 能点 | 控制端点可用 + 真值说**人工持有** | 「交还给 AI」 |
    | 能点 | 控制端点可用 + 真值说**AI 持有** | `has_gui` ⇒ 「切到手动」；否则 ⇒ 「收回写权（冻结）」 |
    | ⭐ **无按钮** | 声明为**共用档**（`action="none"`） | 真值徽标 + 一行"无切换需求"（**不画按钮**） |
    | 读不到 | 控制端点可用但**读操作失败**（如项目还没实现它） | 「读不到」+ 原因（**不显示旧值/不显示 0**） |
    | 不可用 | **软件没起来** | 灰按钮 + 为什么（不是点了才报错） |

    ⚠ 端口号**绝不出现**在这里（契约：不许把 ``:control-port`` 写进给 AI 的文本/输出）。
    """
    sid = html.escape(str(getattr(svc, "id", "")))
    st = ctl_state or {}
    read = st.get("read") or None
    has_gui = bool(getattr(svc, "has_gui", False))
    #: ⭐ 归人那个动作**由控制器给**（`human` / `freeze` / `none`）—— 面板**不自己判** `has_gui`
    #: （判两遍 = 两份真值；而"该给哪个动作"是控制器的事实）。
    #: ⚠ 控制器返回的**就是动作名**（能直接发给 `POST /mode`），面板只负责**贴标签**。
    #: ⭐ `"none"` = 共用档：**没有可切的档** ⇒ 不画按钮（2026-10-10 用户裁决）。
    act = str(st.get("action") or ("human" if has_gui else "freeze"))
    shared = bool((read or {}).get("shared"))
    label = {"human": "切到手动", "freeze": "收回写权（冻结）"}.get(act, act)
    # ⭐ 真值那一栏：**读得到就报真值；读不到就如实说读不到**（铁律 8）
    if read:
        mode = html.escape(str(read.get("mode", "?")))
        if shared:
            who = "AI 与人共用"                 # ⚠ 不许报成"人工持有"（两者同时为真）
        else:
            who = "人工持有" if read.get("human_holds") else ("AI 持有" if read.get("ai_can_write")
                                                            else "无人可写（locked）")
        truth = f'<span class="badge">写权 {mode} · {who}</span>'
    elif st.get("ok") or st.get("read_error"):
        truth = '<span class="pending">写权：读不到（不是"没有"）</span>'
    else:
        truth = '<span class="pending">写权：—</span>'
    #: ⭐ 共用档：按钮是**假按钮**（两侧都能写，按下去不改变任何事）⇒ 只留真值 + 一句说明。
    if shared or act == "none":
        return (truth + '<span class="muted"> 本项目声明为**共用档**：AI 与人共用写权，'
                        '没有切换需求（要动请走它自己的入口）</span>')

    if not st.get("ok"):
        why = html.escape(str(st.get("why") or "控制端点不可用"))
        return (truth + f' <button class="ghost" disabled title="{why}">{label}</button>'
                f'<span class="pending"> {why}</span>')
    human_holds = bool((read or {}).get("human_holds"))
    if not read:
        # 读不到 ⇒ **不给可点的按钮**（按下去也是猜），但如实说明
        return (truth + f'<button class="ghost" disabled title="写权读不到，先查明原因">'
                        f'{label}</button>')
    if human_holds:
        # ⭐ **待命语义**：写被拒、读照常，面板与 dsh **不关闭**（这是语义，不是状态）
        return (truth + ' <button class="ghost" '
                f"onclick=\"mode('{sid}','ai')\">交还给 AI</button>")
    return truth + f' <button onclick="mode(\'{sid}\',\'{act}\')">{label}</button>'


def _service_row(svc: object, row: dict | None, intent: bool, started: bool,
                 ctl_state: dict | None,
                site_name: str = "") -> str:
    sid = html.escape(str(getattr(svc, "id", "")))
    running = bool((row or {}).get("pid"))
    state = str((row or {}).get("state") or ("stopped" if started else "stopped"))
    checked = " checked" if (running or (not started and intent)) else ""
    # ⚠ tail 里**不报控制端口**（契约：不许把 `:control-port` 写进任何给 AI 的文本/输出）；
    #   这里索性连能力面端口也不报 —— 端口对人不是信息，对"泄漏"却是风险面。
    #: ⭐ 设计 §1.1 规则 6：面板要列【站点名 + 该站点的全部服务】。
    #: ⚠ 服务 id（ML）与站点名（ML Toolbox）**不是一回事** —— 只显示 id 会丢掉
    #: "这个服务属于哪个项目"。
    prefix = f"{site_name} · " if site_name else ""
    tail = f'pid {(row or {}).get("pid")}' if running else "未运行"
    return (
        f'<div class="row" data-row="{sid}">'
        # 左侧勾选**只表达"打算开"**（意图）⇒ 不可点：真正的控件是右边那个滑块
        f'<input type="checkbox"{checked} disabled title="打算开（启动前）">'
        f'<span class="name">{sid}</span>'
        f'<span class="sw"><input type="checkbox" id="sw-{sid}"{checked}'
        f' onchange="toggle(\'{sid}\', this.checked)"><label for="sw-{sid}"></label></span>'
        f'{_badge(state)}'
        #: ⚠⭐ **必须包一层**：`_mode_cell` 返回的是**多个并列元素**（真值 + 按钮）
        #: ⇒ 直接放进 5 列网格会**各占一格、撑出隐式列**（实测：按钮被挤成竖排、错位）。
        f'<span class="mode">{_mode_cell(svc, ctl_state)}</span>'
        f'<span class="brief">{html.escape(prefix)}{html.escape(str(getattr(svc, "brief", "")))}'
        f' · <span class="muted">{html.escape(tail)}</span></span>'
        f'</div>')


def _host_row(choice: dict, kind: str, row: dict | None,
              info: dict | None = None) -> str:
    """AI 宿主的一行：**与项目行同一个形状**（滑块 + 名字 + 状态 + 说明）。

    ⚠ 这里**没有圆点**：宿主两行的滑块**互斥**（开一个自动关另一个），
    圆点与滑块表达同一件事，画两遍只会让人以为要分两步点。
    用户原话："不需要那个选择圆点了，格式也与上面几个接入项目一致了"。

    | 情形 | 滑块 | 徽标 |
    |---|---|---|
    | 这一轮用它（`host_kind` 是它） | 开 | 进程状态 / 外部连接 |
    | 没用它 | 关 | `—` |
    """
    value = str(choice.get("kind") or "")
    label = str(choice.get("label") or value)
    managed = bool(choice.get("managed"))
    note = str(choice.get("note") or "")
    picked = value == kind
    running = bool((row or {}).get("pid")) if picked else False
    sid = html.escape(str((row or {}).get("id") or label))
    #: ⭐ 两行都有滑块（含外部宿主）：开它就是"这一轮用它"。
    switch = (f'<span class="sw"><input type="checkbox" id="sw-{sid}"'
              f'{" checked" if picked else ""}'
              f' onchange="toggle(\'{sid}\', this.checked)">'
              f'<label for="sw-{sid}"></label></span>')
    if not picked:
        badge = '<span class="muted">—</span>'
    elif managed:
        badge = _badge(str((row or {}).get("state") or "stopped"))
    else:
        badge = _badge("external")
    #: ⭐ 界面入口：只有"当前宿主 + 界面真在跑"才给链接（否则点过去是个死端口）。
    link = ""
    if picked and info:
        ui_url = str(info.get("ui_url") or "")
        if ui_url and info.get("ui_running"):
            link = (f' <a href="{html.escape(ui_url)}" target="_blank" '
                    f'rel="noreferrer">打开界面 →</a>')
        elif ui_url:
            link = '<span class="pending">（界面未起）</span>'
    return (
        f'<div class="row" data-row="{html.escape(value)}">'
        #: 与项目行完全同形：第一格是**不可点的指示框**（项目行那边是"打算开"，
        #: 这边是"这一轮用它"）；真正的控件是右边那个滑块。
        f'<input type="checkbox"{" checked" if picked else ""} disabled '
        f'title="这一轮用它（真正的控件是右边的滑块）">'
        f'<span class="name">{html.escape(label)}</span>'
        f'{switch}{badge}'
        f'<span class="brief muted">{html.escape(note)}{link}</span>'
        f'</div>')


def _host_block(choices: list[dict], kind: str, rows: dict,
                info: dict | None = None) -> list[str]:
    """AI 宿主那一组：标题 + 两行滑块（**互斥**，开一个自动关另一个）。"""
    head = ('<div class="group">AI 宿主'
            '<span class="pending">开一个会关掉另一个 · 默认 dsh</span></div>')
    items = [_host_row(c, kind, rows.get(str(c.get("kind") or "")),
                       info if str(c.get("kind") or "") == kind else None)
             for c in choices]
    return [head, *items]


def _side_item(dom_id: str, on: bool, label: str = "") -> str:
    """左栏一条；`data-side` 是局部刷新的定位键（宿主那条固定叫 host）。"""
    text = label or dom_id
    return (f'<div class="sideitem{" on" if on else ""}" '
            f'data-side="{html.escape(dom_id)}">{html.escape(text)}</div>')


def _mode_notice(out: dict) -> str:
    """写权那一步的失败回执（成功时页面不弹提示，真值自己会变）。"""
    detail = "；".join(
        f'{s["step"]}：{"✅" if s["ok"] else "❌ " + str(s.get("error", ""))}'
        for s in out.get("steps", [])) or str(out.get("error", ""))
    return (f'<div class="notice err" data-live="1">'
            f'{html.escape(str(out.get("error", "写权操作失败")))}<br>'
            f'<span class="muted">{html.escape(detail)}</span></div>')


def _row_html(controller: Controller, sid: str) -> tuple[str, str]:
    """重画**一行**（项目或宿主）+ 左栏对应那一条 —— 局部刷新用。

    ⚠ 为什么要有它：整页替换会把 `#main` / `#side` 两个滚动容器**重建** ⇒
    滚动位置归零（用户实测：滑到底下操作，一拨滑块就被弹回顶部、按钮都点错）。
    滑块是高频、单行的操作，不该重绘整页。
    """
    st = controller.state()
    rows = {str(r.get("id")): r for r in st.get("children", [])}
    started = controller.started()
    intent = controller.intent()

    #: 宿主那两行（dsh / codex）：左边一条是"AI · 当前宿主"
    if sid in ("dsh", "codex"):
        try:
            info = dict(controller.host_info())      # type: ignore[attr-defined]
        except Exception:                            # noqa: BLE001 - 老替身
            info = {"kind": "dsh", "label": "dsh", "managed": True, "child_id": "dsh"}
        kind = str(info.get("kind") or "")
        try:
            choices = [dict(c) for c in controller.host_choices()]  # type: ignore[attr-defined]
        except Exception:                            # noqa: BLE001
            choices = [{"kind": "dsh", "label": "dsh", "managed": True, "note": ""}]
        choice = next((c for c in choices if str(c.get("kind") or "") == sid), None)
        if choice is None:
            return "", ""
        row = _host_row(choice, kind, rows.get(sid))
        on = kind == sid
        return row, _side_item("host", on, label=f"AI · {info.get('label') or sid}")

    svc = next((s for s in controller.services()          # type: ignore[arg-type]
                if str(getattr(s, "id", "")) == sid), None)
    if svc is None:
        return "", ""
    try:
        ctl_state = controller.write_authority_state(sid)
    except Exception as exc:                             # noqa: BLE001 - 页面如实降级
        ctl_state = {"ok": False, "why": f"控制端点状态读不到：{exc}"}
    site_name = ""
    _sites = getattr(controller, "sites", ()) or ()
    if not callable(_sites):
        for _site in _sites:
            for _svc in (getattr(_site, "services", ()) or ()):
                if str(getattr(_svc, "id", "")) == sid:
                    site_name = str(getattr(_site, "name", ""))
    row = _service_row(svc, rows.get(sid), bool(intent.get(sid)), started, ctl_state,
                       site_name=site_name)
    on = bool((rows.get(sid) or {}).get("pid")) or (not started and bool(intent.get(sid)))
    return row, _side_item(sid, on)


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

    #: ⭐ 服务 -> 站点名（供行里冠上项目名）。
    #: ⚠ **防御式**：判据里的替身可能没有 `.services` ——
    #: 不能因为"显示一个项目名"把 30 项判据弄红（今天已经在替身上栽过一次）。
    site_of: dict[str, str] = {}
    _sites = getattr(controller, "sites", ()) or ()
    #: ⚠⚠ 判据里的**替身把 `sites` 写成了方法**（真实现是属性）——
    #: 这正是当初面板起不来的同一个坑。⇒ 可调用就当"没有"，
    #: 但**绝不允许因为"显示一个项目名"把整页弄崩**。
    if callable(_sites):
        _sites = ()
    for _site in _sites:
        for _svc in (getattr(_site, "services", ()) or ()):
            site_of[str(getattr(_svc, "id", ""))] = str(getattr(_site, "name", ""))
    items: list[str] = []
    side: list[str] = []
    #: ⭐ **两类分开的第一类**：接入的项目（可多选，勾"打算开哪几个"）。
    items.append('<div class="group">接入的项目'
                 '<span class="pending">可多选 · 存活滑块随时可动</span></div>')
    for svc in services:
        sid = str(getattr(svc, "id", ""))
        row = rows.get(sid)
        # ⚠ 写权栏的可用性由控制器如实给出（**软件没起来 ⇒ 灰按钮**，不是点了才报错）
        try:
            ctl_state = controller.write_authority_state(sid)
        except Exception as exc:                       # noqa: BLE001 - 页面如实降级
            ctl_state = {"ok": False, "why": f"控制端点状态读不到：{exc}"}
        items.append(_service_row(svc, row, bool(intent.get(sid)), started, ctl_state,
                                 site_name=site_of.get(sid, "")))
        on = bool((row or {}).get("pid")) or (not started and bool(intent.get(sid)))
        side.append(f'<div class="sideitem{" on" if on else ""}" '
                    f'data-side="{html.escape(sid)}">{html.escape(sid)}</div>')

    err = str(st.get("last_error") or "")
    if err:
        head = f'<div class="notice err">启动失败：{html.escape(err)}</div>'
    elif not started:
        # ⚠ 与"启动失败"**不同措辞**（"还没起"≠"读不到"）
        head = '<div class="notice muted">还没启动（不是读不到状态）：用滑块选好，再点「启动 ▸」。</div>'
    else:
        head = ""
    cleaned = st.get("cleaned") or []
    if cleaned:
        #: ⭐⭐ **机器能自己解决的事，就别摊给人看**（用户原话：「要是他都能预判出现什么问题了，
        #: 干脆替用户解决了不就得了」）。
        #: ⚠ 原来那几句明细是 `<h3>` + 列表**摊在页面正中** —— 而它**早就被处理掉了**，
        #: 人看了只会想「这关我什么事、我是不是该做点什么」。
        #: ⇒ ⭐ **账照旧留**（`state.json` 与日志里一条不少 —— 纪律：清掉了什么必须可查），
        #: 页面只给**一行**：已自动处理、无需操作。
        #: ⚠ 反过来：**需要人动手**的事（如杀不掉的孤儿进程）**不许走这条**，那种必须醒目。
        items.append(
            f'<div class="notice muted">已自动清掉 {len(cleaned)} 项陈旧残留'
            f'（<strong>无需你操作</strong>）'
            f'<span class="pending">　明细见 state.json 与 supervisor 日志</span></div>')
    #: ⭐ **两类分开的第二类**：AI 宿主。名称/托管状态/child id 由适配器给，
    #: 可选哪两个由控制器给（控制台只提供 dsh / codex）。
    try:
        host_info = dict(controller.host_info())  # type: ignore[attr-defined]
    except Exception:                                      # noqa: BLE001 - 旧替身按 dsh 渲染
        host_info = {"kind": "dsh", "label": "dsh", "managed": True,
                     "child_id": DSH_CHILD_ID, "external": False}
    kind = str(host_info.get("kind") or host_info.get("label") or "dsh")
    try:
        choices = [dict(c) for c in controller.host_choices()]  # type: ignore[attr-defined]
    except Exception:                                      # noqa: BLE001 - 旧替身只认 dsh
        choices = [{"kind": "dsh", "label": "dsh", "managed": True,
                    "note": "由启动器托管：关掉再打开 = 重启它"}]
    #: ⚠ 两个都关（`host_kind` = none）时**不要**补一行 none 出来：
    #: 宿主区固定两行，两行都关就是"这一轮不用宿主"。
    if kind != "none" and not any(str(c.get("kind") or "") == kind for c in choices):
        choices.insert(0, {"kind": kind, "label": str(host_info.get("label") or kind),
                           "managed": bool(host_info.get("managed")),
                           "note": str(host_info.get("note") or "")})
    host_child_id = str(host_info.get("child_id") or "")
    host_row = rows.get(host_child_id) if host_child_id else None
    items.extend(_host_block(choices, kind, rows, host_info))
    #: ⚠ 左栏也要有它 —— 否则两处清单不一致（右栏 4 行、左栏 3 行）。
    side.append('<div class="sidesep"></div>')
    side.append('<div class="sideitem"'
                + (' on' if (host_row or {}).get("pid") else '')
                + ' data-side="host">AI · '
                + html.escape(str(host_info.get("label", "dsh"))) + '</div>')

    #: ⭐ **AI 那条腿的地址**（T4）：聚合端点**每次启动都变**，所以页面必须**现取**
    #: （`state()["ai_face"]` 里是控制器按端口文件读出来的），不能写死。
    #: ⚠ 只给人看 —— 控制信息不上 AI 那条腿。
    _ai = st.get("ai_face") or {}
    ai_line = ""
    if _ai.get("aggregate_url") or _ai.get("dsh_url") or _ai.get("host_config"):
        host_label = html.escape(str(_ai.get("host_label") or _ai.get("host") or "dsh"))
        host_addr = (_ai.get("dsh_url") or _ai.get("host_config") or "（外部配置）")
        ai_line = ('<div class="pending" style="padding:2px 8px">'
                   f'AI 面：聚合端点 {html.escape(str(_ai.get("aggregate_url") or "（未知）"))}'
                   f'　·　{host_label} {html.escape(str(host_addr))}'
                   '　（⚠ 聚合地址每次启动会变；要固定用 `python -m mecha --aggregate-port <端口>`）'
                   '</div>')

    return (
        '<!doctype html><html lang="zh"><head><meta charset="utf-8">'
        f'<title>{html.escape(_TITLE)}</title><style>{_CSS}</style></head><body>'
        f'<header><h1>{html.escape(_TITLE)} · 控制台</h1>'
        f'<span class="muted">{html.escape(" · ".join(st.get("sites") or ["?"]))}</span>'
        '<button class="ghost" onclick="fold()">◀ 收起</button>'
        '<a href="/monitor" style="color:#79c0ff;padding:8px 4px">监控</a>'
        '<button class="ghost" onclick="location.reload()">刷新</button></header>'
        + ai_line
        + f'<div id="shell"><div id="side">{"".join(side)}</div>'
        f'<div id="main">{head}{"".join(items)}</div></div>'
        '<section class="terminal" id="term">'
        '<div class="terminal-head" id="term-head">运行输出 · 每秒刷新（进程状态、最近 stdout/stderr、就绪证据和聚合同步错误）'
        '<span class="grip">往上拖拉出 · 双击折叠</span></div>'
        '<pre id="console">正在读取启动器状态…</pre></section>'
        '<footer><button type="button" onclick="allOn(true)">全部启用</button>'
        '<button type="button" onclick="allOn(false)">全部停用</button>'
        '<button onclick="startNow(this)">启动 ▸</button>'
        '<button class="ghost" onclick="shutdownNow()">收尾退出</button>'
        '<span class="muted">存活滑块随时可动</span>'
        #: ⭐ 必须明说：用户实测问过关掉 web 窗口会不会关掉它 ——
        #: ⚠ 答案是不会（浏览器只是客户端）⇒ 不说清，人就只能去任务管理器强杀（跳过收尾）。
        '<span class="pending">⚠ 关掉浏览器<strong>不会</strong>停服务；要停请点「收尾退出」'
        '（或双击 <code>stop_mecha.pyw</code> / <code>python -m mecha --stop</code>）</span>'
        '<span class="pending foot-note">切写权<strong>不动进程、不动连接</strong>：'
        '面板与 dsh 待命 —— 写被拒、读照常</span>'
        '</footer>'
        '<script>'
        'function fold(){document.getElementById("shell").classList.toggle("collapsed")}'
        #: ⭐⭐ **整页替换也要保住滚动位置**：`#main` / `#side` 是内部滚动容器，
        #: 换 DOM 后它们是新元素 ⇒ scrollTop 归零（用户实测：滑到底下操作，一刷新
        #: 被弹回顶部、按钮都点错）。⚠ 先取旧元素的值，换完再赋给新元素。
        'function scrollNow(){const m=document.getElementById("main"),s=document.getElementById("side");'
        'return [m?m.scrollTop:0,s?s.scrollTop:0]}'
        'function scrollTo2(v){const m=document.getElementById("main"),s=document.getElementById("side");'
        'if(m)m.scrollTop=v[0];if(s)s.scrollTop=v[1]}'
        'async function post(p,b){const keep=scrollNow();'
        'const r=await fetch(p,{method:"POST",body:b||""});'
        'document.documentElement.innerHTML=await r.text();scrollTo2(keep);termApply()}'
        #: ⭐ **局部刷新**：拨滑块/切写权只换那**一行**（带 `X-MeCha-Partial`），
        #: 不重建整页 ⇒ 滚动、动画、焦点都还在。服务端给的是一行 HTML + 左栏一条。
        'async function partial(p,b){const r=await fetch(p,{method:"POST",'
        'headers:{"X-MeCha-Partial":"1"},body:b});'
        'if(!r.ok)return null;try{return await r.json()}catch(e){return null}}'
        'function applyPartial(d,id){if(!d)return false;'
        'if(d.reload){location.reload();return true}'
        'const keep=scrollNow();let hit=false;'
        'const rows=d.rows||{};for(const k in rows){'
        'const el=document.querySelector(\'[data-row="\'+k+\'"]\');'
        'if(el){el.outerHTML=rows[k];hit=true}}'
        'if(!hit)return false;'
        'const side=document.querySelector(\'[data-side="\'+id+\'"]\');'
        'if(d.side&&side)side.outerHTML=d.side;'
        'if(id==="dsh"||id==="codex"){const hs=document.querySelector(\'[data-side="host"]\');'
        'if(d.side&&hs)hs.outerHTML=d.side}'
        'scrollTo2(keep);'
        'if(d.notice){document.querySelectorAll("[data-live]").forEach(e=>e.remove());'
        'const m=document.getElementById("main");'
        'if(m)m.insertAdjacentHTML("afterbegin",d.notice)}'
        'return true}'
        'async function toggle(id,on){const body=new URLSearchParams({svc:id,on:on?"1":"0"});'
        'const d=await partial("/service",body);'
        'if(!applyPartial(d,id))await post("/service",body)}'
        'function startNow(b){b.disabled=true;b.textContent="启动中…（拉起软件要几秒）";'
        'post("/start")}'
        'async function mode(id,act){const body=new URLSearchParams({svc:id,action:act});'
        'const d=await partial("/mode",body);'
        'if(!applyPartial(d,id))await post("/mode",body)}'
        'function allOn(v){post("/all", new URLSearchParams({on:v?"1":"0"}))}'
        'function shutdownNow(){if(confirm("收尾退出：逆序停掉全部 child？"))post("/shutdown")}'
        'async function pollConsole(){try{const r=await fetch("/console.json",{cache:"no-store"});'
        'const d=await r.json();const lines=[];'
        'if(d.last_error)lines.push("[launcher] ERROR "+d.last_error);'
        'if(d.aggregate_sync_error)lines.push("[aggregate] ERROR "+d.aggregate_sync_error);'
        'if(d.aggregate_publish_error)lines.push("[aggregate] ERROR "+d.aggregate_publish_error);'
        '(d.launcher_logs||[]).forEach(x=>lines.push("[launcher] "+x));'
        '(d.children||[]).forEach(c=>{const pid=c.pid?" pid="+c.pid:"";'
        'const port=c.port?" port="+c.port+"/"+(c.port_live?"live":"dead"):"";'
        'lines.push("["+c.id+"] "+(c.state||"?")+pid+port);'
        'if(c.last_error)lines.push("  ! "+c.last_error);'
        'if(c.port_error)lines.push("  port: "+c.port_error);'
        'if(c.ready_evidence)lines.push("  ready: "+c.ready_evidence);'
        '(c.logs||[]).forEach(x=>lines.push("["+c.id+"] "+x));});'
        'const box=document.getElementById("console");if(!box)return;'
        'const follow=box.scrollHeight-box.scrollTop-box.clientHeight<40;box.textContent=lines.join("\\n")||"（暂无子进程输出）";'
        'if(follow)box.scrollTop=box.scrollHeight;}catch(e){const box=document.getElementById("console");if(box)box.textContent="[console] "+e;}}'
        'pollConsole();setInterval(pollConsole,1000);'
        #: ⭐ 终端高度：**默认只留标题条**（28px），拖出来才展开；高度记在本机。
        #: ⚠ 事件必须挂在 `document` 上：`post()` 会整页换 DOM（`innerHTML`），
        #: 直接挂在元素上的监听会跟着旧节点一起消失（而脚本不会重跑）。
        'function termSet(h){const t=document.getElementById("term");if(!t)return;'
        'h=Math.max(28,Math.min(Math.round(window.innerHeight*0.7),Math.round(h)));'
        't.style.height=h+"px";t.classList.toggle("open",h>40);'
        'try{localStorage.setItem("mecha.termH",String(h))}catch(e){}}'
        'function termApply(){let h=28;try{h=parseInt(localStorage.getItem("mecha.termH")||"28",10)}catch(e){}'
        'termSet(isNaN(h)?28:h)}'
        'termApply();'
        '(function(){let drag=false,y0=0,h0=28;'
        'document.addEventListener("pointerdown",function(e){'
        'const hd=e.target.closest&&e.target.closest("#term-head");if(!hd)return;'
        'drag=true;y0=e.clientY;const t=document.getElementById("term");'
        'h0=t?t.getBoundingClientRect().height:28;e.preventDefault();});'
        'document.addEventListener("pointermove",function(e){if(!drag)return;termSet(h0-(e.clientY-y0));});'
        'document.addEventListener("pointerup",function(){drag=false});'
        'document.addEventListener("dblclick",function(e){'
        'const hd=e.target.closest&&e.target.closest("#term-head");if(!hd)return;'
        'const t=document.getElementById("term");termSet(t&&t.classList.contains("open")?28:220);});'
        '})();'
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

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self) -> None:                              # noqa: N802
        path = urlsplit(self.path).path
        if path == "/":
            return self._html(200, render_page(self.controller))
        if path == "/state.json":
            return self._send(200, json.dumps(self.controller.state(), ensure_ascii=False)
                              .encode("utf-8"), "application/json; charset=utf-8")
        if path == "/console.json":
            return self._send(200, json.dumps(self.page.console_snapshot(), ensure_ascii=False)
                              .encode("utf-8"), "application/json; charset=utf-8")
        if path == "/monitor":
            from ..monitor_page import render_monitor_page
            try:
                info = self.controller.host_info()  # type: ignore[attr-defined]
            except Exception:                       # noqa: BLE001 - 旧替身无宿主信息
                info = {}
            ledger = info.get("task_ledger") or None
            return self._html(200, render_monitor_page(
                self.controller.services(),
                task_ledger=Path(ledger) if ledger else None))
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
        raw_body = self._body()
        #: ⭐ **局部刷新**：前端带这个头就只要"变了的那一行"，不换整页
        #: （整页换 DOM ⇒ `#main`/`#side` 的滚动位置归零，滑到底下操作会被弹回顶部）。
        partial = self.headers.get("X-MeCha-Partial") == "1"
        if path in ("/mecha/task/wait", "/mecha/task/cancel"):
            try:
                payload = json.loads(raw_body or "{}")
                result = (self.controller.task_wait(payload) if path.endswith("/wait")
                          else self.controller.task_cancel(payload))
                return self._send(200, json.dumps(result, ensure_ascii=False).encode("utf-8"),
                                  "application/json; charset=utf-8")
            except Exception as exc:                       # noqa: BLE001
                return self._send(500, json.dumps({"is_error": True, "error": str(exc)},
                                                  ensure_ascii=False).encode("utf-8"),
                                  "application/json; charset=utf-8")
        form = parse_qs(raw_body)
        try:
            if path == "/start":
                enabled = [s for s, v in self.controller.intent().items() if v]
                self.controller.start(enabled)
            elif path == "/service":                       # ⭐ 存活滑块（单个软件 / 宿主行）
                svc = (form.get("svc") or [""])[0]
                on = (form.get("on") or ["0"])[0] == "1"
                out = self.controller.set_service_enabled(svc, on)
                #: ⭐ 宿主滑块的动作要**逐步回执**（它可能真停了/起了进程、写了配置）。
                steps_html = (_host_notice(out)
                              if isinstance(out, dict) and out.get("steps") else "")
                if partial:
                    #: ⚠ 宿主两行是**互斥**的：拨 codex 会把 dsh 也关掉 ⇒ 只回一行
                    #: 会让另一行留在"开"的样子（这正是用户担心的"只有一个开"被打破）。
                    ids = ["dsh", "codex"] if svc in ("dsh", "codex") else [svc]
                    rows_out: dict[str, str] = {}
                    side_out = ""
                    for one in ids:
                        one_row, one_side = _row_html(self.controller, one)
                        if one_row:
                            rows_out[one] = one_row
                        side_out = side_out or one_side
                    if not rows_out:                  # 行没了（如软件被删）⇒ 让前端整页重载
                        return self._json(200, {"reload": True})
                    return self._json(200, {
                        "ok": bool((out or {}).get("ok", True)),
                        "rows": rows_out, "side": side_out, "notice": steps_html})
                if steps_html:
                    page = render_page(self.controller)
                    return self._html(200, page.replace(
                        "<div id=\"main\">", f'<div id="main">{steps_html}'))
            elif path == "/mode":                          # ⭐ 写权按钮（切到手动 / 交还给 AI）
                svc = (form.get("svc") or [""])[0]
                action = (form.get("action") or [""])[0]
                out = self.controller.control_write_authority(svc, action)
                if partial:
                    row, side = _row_html(self.controller, svc)
                    return self._json(200, {
                        "ok": bool(out.get("ok")),
                        "rows": {svc: row} if row else {},
                        "side": side,
                        "notice": "" if out.get("ok") else _mode_notice(out)})
                if not out.get("ok"):
                    # ⚠ **如实报告哪一步成/哪一步败**（不合并成一次"看起来成功了"）
                    page = render_page(self.controller)
                    return self._html(200, page.replace(
                        "<div id=\"main\">", f'<div id="main">{_mode_notice(out)}'))
            elif path == "/host":                          # ⭐ AI 宿主：二选一（dsh / codex）
                kind = (form.get("host") or [""])[0]
                out = self.controller.set_host_kind(kind)   # type: ignore[attr-defined]
                page = render_page(self.controller)
                return self._html(200, page.replace(
                    "<div id=\"main\">", f'<div id="main">{_host_notice(out)}'))
            elif path == "/all":                           # 批量：只改**意图**（不 spawn）
                on = (form.get("on") or ["1"])[0] == "1"
                for svc in self.controller.services():     # type: ignore[union-attr]
                    self.controller.set_intent(str(getattr(svc, "id", "")), on)
            elif path == "/shutdown":
                self.controller.shutdown()
                #: ⭐⭐ **收尾必须把 launcher 自己也停掉**（实测抓到的真 bug）：
                #: 原来只调 `controller.shutdown()`（停子进程）⇒ 而 `serve_forever()`
                #: **还在阻塞** ⇒ ⚠ **launcher 永远不退出** ⇒ 用户点了「收尾退出」
                #: （或者 `--stop`）却看见它**还在跑**，只能去任务管理器强杀（**跳过收尾**）。
                #: ⚠ 用**定时器延后**停：先把这次响应发出去，否则浏览器只看得到连接被断。
                threading.Timer(0.6, self.page.stop).start()
            else:
                return self._html(404, "<h1>404</h1><p>没有这个页面。</p>")
        except Exception as exc:                           # noqa: BLE001 - 页面如实报故障
            return self._html(200, f'<div class="notice err">{html.escape(str(exc))}</div>')
        return self._html(200, render_page(self.controller))


class StatusPage:
    """常驻控制台的传输层（起/停/URL；渲染见 :func:`render_page`）。"""

    def __init__(self, controller: Controller, opts: PageOptions = PageOptions()) -> None:
        self.controller = controller
        self.opts = opts
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        #: ⭐ **停旗**：`serve_forever()` 靠它退出。
        #: ⚠ 原来那个循环**只认 Ctrl+C** ⇒ 无控制台入口下**永远退不出**（实测）。
        self._stopped = threading.Event()
        #: ⭐ stop() 会被「/shutdown 的定时器」与「主循环 finally」两路同时调，
        #: 摘 `_httpd` 必须是原子的，否则两路都以为自己在关同一个 server。
        self._stop_lock = threading.Lock()

    def start(self) -> int:
        """起服务，返回**实际绑定端口**（回读，不用传入的偏好值）。"""
        #: ⚠ **这里是属性不是方法**（`LaunchController.sites` 是一个 tuple 属性）。
        #: 原先写成 `self.controller.sites()` ⇒ `TypeError: 'tuple' object is not callable`
        #: ⇒ ⭐ **页面根本起不来**，而且**没有任何判据抓到**：所有判据只调 `render_page(stub)`，
        #: 而 `render_page` 读的是 `state()["sites"]`、**不碰 `sites`** ⇒ 这一行从未被执行过。
        #: 判据的替身还把 `sites` 写成了方法（与页面同错、与真实现不同）⇒ 全绿。
        #: ⇒ 现在有一条**真起 `StatusPage`** 的判据钉住（见 `checks/launch_selfcheck.py`）。
        #: ⭐ **标题是框架那一层的**：这张控制台管的是**启动器**（它下面才是各家软件）。
        #: ⚠ 旧实现取 `self.controller.sites[0].name` ⇒ 三个站点一起起时标题写成
        #: **"mecha · ML Toolbox · 控制台"**（第一个站点的名字，纯属加载顺序的偶然）——
        #: 用户实测抓到："ML Toolbox 不该在这个层次"。
        #: ⇒ 标题只写 `mecha`（模块常量），**站点名由表头那串** `state()["sites"]` **承担**
        #: （那里本来就列全了，两处并列反而让人以为是"这个站点专属的控制台"）。
        #: ⚠ 也**不再写模块全局**：那是"两个实例互相改标题"的写法（本该是实例的事）。
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
        self._stopped.clear()
        try:
            #: ⚠⭐ 原实现是 `while True: wait(0.5)` —— **只有 Ctrl+C 能出来**，
            #: 而 `page.stop()` 停的是**另一个线程里的 HTTP 服务**、打不断这个循环。
            #: ⇒ 实测：点了「收尾退出」/ `--stop` 之后 **launcher 还在跑**，
            #: 用户只能去任务管理器**强杀**（跳过收尾）。⇒ 现在认**停旗**。
            while not self._stopped.is_set():
                self._stopped.wait(0.5)
        except KeyboardInterrupt:
            raise

    @property
    def port(self) -> int | None:
        #: 同 stop() 的窗口问题：先取本地引用，别在判空和取端口之间被别人置空。
        httpd = self._httpd
        return int(httpd.server_address[1]) if httpd else None

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

    def console_snapshot(self) -> dict:
        """给启动页终端区的有界快照。

        进程状态仍来自 Supervisor；这里只把每个 child 的最后一小段日志
        复制到页面传输，避免浏览器为了看实时输出反复拉完整日志文件。
        """
        state = self.controller.state()
        out = {
            "last_error": state.get("last_error", ""),
            "aggregate_sync_error": state.get("aggregate_sync_error", ""),
            "aggregate_publish_error": state.get("aggregate_publish_error", ""),
            "children": [],
        }
        from ..runtime_console import console
        out["launcher_logs"] = console().recent(400)
        sup = getattr(self.controller, "super", None)
        for row in state.get("children", []) or []:
            item = dict(row)
            try:
                child = sup.child(str(row.get("id")))
                item["logs"] = child.log_tail(80)
            except Exception as exc:                       # noqa: BLE001
                item["logs"] = [f"日志读取失败：{type(exc).__name__}: {exc}"]
            port = item.get("port")
            if isinstance(port, int) and port > 0 and item.get("state") in ("ready", "running"):
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                        item["port_live"] = True
                except OSError as exc:
                    item["port_live"] = False
                    item["port_error"] = f"{type(exc).__name__}: {exc}"
            out["children"].append(item)
        return out

    def stop(self) -> None:
        #: ⭐ 先立旗：让 `serve_forever()` 那个循环退出（否则 launcher 不退出）。
        self._stopped.set()
        #: ⚠ **stop() 会被两路同时调**：`/shutdown` 用 `threading.Timer` 延后停，
        #: 主循环退出后的 `finally` 又停一次。原来「判空 → 用属性」中间有窗口，
        #: 另一路把 `_httpd` 置空 ⇒ `AttributeError: 'NoneType' object has no
        #: attribute 'server_close'`（实测：每次 `--stop` 都刷一条）。⇒ 先**原子
        #: 摘走**再关，谁摘到谁关，另一路直接返回。
        with self._stop_lock:
            httpd, self._httpd = self._httpd, None
        if httpd is not None:
            try:
                httpd.shutdown()
                httpd.server_close()
            except OSError:
                pass
