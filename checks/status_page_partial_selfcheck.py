"""判据 ㉖：拨滑块**不重绘整页**（局部刷新 + 滚动位置不丢）。

## 为什么要有它

用户实测抱怨："启动之前每关闭/打开一个滑块都要刷新一遍，一次性关三个滑块就给我
刷新了三遍；而且每次刷新会更新滑动条位置，本来在下面操作的，一刷新上上面去了，
按钮都按错了。"

根因在旧实现：`post()` 收到整页 HTML 就 `document.documentElement.innerHTML = ...`
⇒ `#main` / `#side` 这两个内部滚动容器被**重建**，`scrollTop` 归零。

## 五条断言

| # | 断言 | 为什么 |
|---|---|---|
| ① | 带 `X-MeCha-Partial: 1` 拨项目滑块 ⇒ 只回**那一行** + 左栏一条，是 JSON | 不重绘整页 |
| ② | 拨宿主滑块 ⇒ **两行都回**（dsh / codex） | 互斥：只回一行会让另一行留在"开"的样子 |
| ③ | 两个滑块的开合在**同一个响应**里已经互斥 | 前端不用自己推 |
| ④ | 不带这个头 ⇒ 仍返回整页 HTML | 老客户端/脚本没被破坏 |
| ⑤ | 页面脚本：有 `data-row` 定位、`scrollNow/scrollTo2` 兜底、整页替换后恢复滚动 | 兜底路径也要不跳 |

⚠ 判据起**真的** `StatusPage`（真 socket、真 HTTP），只把控制器换成替身。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mecha.providers.status_page import PageOptions, StatusPage, render_page  # noqa: E402
from mecha.site import DSHSpec, ReadySpec, Service, Site  # noqa: E402

FAILS: list[str] = []


def _check(name: str, cond: bool, extra: object = "") -> None:
    print(f"  {'✓' if cond else '✗'} {name}" + (f" —— {extra}" if not cond and extra else ""))
    if not cond:
        FAILS.append(name)


class StubController:
    """只够页面渲染：一行软件 + 两行宿主。"""

    def __init__(self, tmp: Path) -> None:
        self.sites = (Site(
            name="S",
            services=(Service(id="AAA", brief="判据用", argv=(sys.executable, "-c", "pass"),
                              cwd=tmp, ready=ReadySpec(port_file=".mcp-port"),
                              data_root=tmp / "a", has_gui=True),),
            dsh=DSHSpec(command=(sys.executable, "-c", "pass"), cwd=tmp)),)
        self._intent = True
        self.host = "dsh"

    # ---- 页面要的读 ----
    def dsh_spec(self): return self.sites[0].dsh
    def services(self): return self.sites[0].services
    def started(self): return False
    def intent(self): return {"AAA": self._intent}
    def last_status(self): return {}
    def state(self):
        return {"children": [], "sites": ["S"], "last_error": "", "ai_face": {}}
    def write_authority_state(self, sid): return {"ok": False, "why": "还没启动"}
    def host_info(self):
        return {"kind": self.host, "label": self.host,
                "managed": self.host == "dsh",
                "child_id": "dsh" if self.host == "dsh" else None,
                "external": self.host == "codex"}
    def host_choices(self):
        return [{"kind": "dsh", "label": "dsh", "managed": True, "note": "由启动器托管"},
                {"kind": "codex", "label": "codex", "managed": False, "note": "外部 MCP"}]

    # ---- 页面要的写 ----
    def set_service_enabled(self, sid, on):
        if sid == "AAA":
            self._intent = on
            return {"ok": True, "id": sid, "enabled": on, "applied": "intent"}
        self.host = sid if on else "none"
        return {"ok": True, "id": sid, "kind": self.host,
                "action": "open" if on else "close",
                "steps": [{"step": f"这一轮用 {sid}" if on else "这一轮不启用 AI 宿主",
                           "ok": True}]}

    def control_write_authority(self, sid, action):
        return {"ok": False, "error": "控制端点不可用", "steps": []}


def _post(port: int, path: str, body: str, *, partial: bool):
    headers = {"X-MeCha-Partial": "1"} if partial else {}
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body.encode(),
                                 method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.headers.get("Content-Type", ""), resp.read()


def check_partial(tmp: Path) -> None:
    ctl = StubController(tmp)
    page = StatusPage(ctl, PageOptions(port=0, open_browser=False))
    port = page.start()
    try:
        ctype, body = _post(port, "/service", "svc=AAA&on=0", partial=True)
        data = json.loads(body.decode("utf-8"))
        row = data.get("rows", {}).get("AAA", "")
        _check("① 拨项目滑块只回那一行（JSON）",
               "application/json" in ctype and row and 'data-row="AAA"' in row,
               f"ctype={ctype} keys={sorted(data)}")
        _check("① 同一响应里带左栏那一条（供局部替换）",
               'data-side="AAA"' in data.get("side", ""), data.get("side", "")[:80])

        ctype, body = _post(port, "/service", "svc=codex&on=1", partial=True)
        data = json.loads(body.decode("utf-8"))
        rows = data.get("rows", {})
        _check("② 拨宿主滑块 ⇒ **两行都回**（互斥要一起画）",
               set(rows) == {"dsh", "codex"}, sorted(rows))
        _check("③ 同一个响应里 dsh 已关、codex 已开",
               'id="sw-dsh" checked' not in rows.get("dsh", "")
               and 'id="sw-codex" checked' in rows.get("codex", ""),
               "两行的开合不对")

        ctype, body = _post(port, "/service", "svc=AAA&on=1", partial=False)
        _check("④ 不带局部头 ⇒ 仍是整页 HTML（不破坏老客户端）",
               "text/html" in ctype and b"<html" in body.lower(), ctype)
    finally:
        page.stop()


def check_script_marks(tmp: Path) -> None:
    ctl = StubController(tmp)
    html = render_page(ctl)
    _check("⑤ 每行带 data-row 定位键", html.count('data-row="') >= 3,
           html.count('data-row="'))
    _check("⑤ 整页替换的兜底路径会恢复滚动",
           "scrollNow" in html and "scrollTo2" in html
           and "scrollTo2(keep)" in html)
    #: 语法检查：脚本一大段拼起来的字符串，错一个引号整页就死（而页面判据看不出来）。
    script = re.search(r"<script>(.*)</script>", html, re.S)
    if script is None:
        _check("⑤ 页面里有脚本块", False)
        return
    node = subprocess.run(["node", "--check", "-"], input=script.group(1),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    if node.returncode != 0 and "not recognized" in (node.stderr or ""):
        print("  （跳过 node 语法检查：本机没有 node）")
        return
    _check("⑤ 页面脚本语法通过（node --check）", node.returncode == 0,
           (node.stderr or "")[:200])


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("局部刷新判据：")
    with tempfile.TemporaryDirectory(prefix="mecha-partial-") as td:
        check_partial(Path(td))
        check_script_marks(Path(td))
    if FAILS:
        print(f"status_page_partial_selfcheck: FAIL（{len(FAILS)} 条）")
        return 1
    print("status_page_partial_selfcheck: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
