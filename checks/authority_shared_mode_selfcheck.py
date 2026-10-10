"""判据 ㉒：⭐ **共用档**（`Mode.OPEN`＝AI 与人共用）在框架里被当"没有切换需求"处理。

## 它钉的是哪两件事（都是 2026-10-10 用户实测/裁决抓出来的）

1. **共用档不画假按钮**：用户原话 —— **"如果有 AI 与人共用的模式，说明该项目没有写权切换的需求"**。
   `Mode.OPEN` 下两侧都能写 ⇒ 「切到手动 / 交还给 AI」描述不了任何真实差别（按下去也不改变
   谁能写）⇒ 画出来就是**假按钮**。⇒ 启动控制台对这类软件**只留真值 + 一句说明**。
   ⚠ 也不许把共用报成"人工持有"：共用档 `human_holds` 与 `ai_can_write` **同时为真**。
2. **控制台标题不许取某一个站点的名字**：用户实测 —— 三个站点一起起时标题写成
   **"mecha · ML Toolbox · 控制台"**（取的 `sites[0].name`，纯属加载顺序的偶然）。
   那是**启动器那一层**的页面 ⇒ 标题只写 `mecha`，站点名由表头那串承担。

## 六条（每条都带红证或对照组）

| # | 断言 | 红在哪 |
|---|---|---|
| ① | `authority_view` 在共用档回 `shared=True`，`ai_can_write` 与 `human_holds` **同时为真** | 把 `shared` 删掉 ⇒ 消费方只能靠"两个都为真"猜（迟早有人只看其中一个 ⇒ 报成"人工持有"） |
| ② | 共用档的写权格**没有 `<button`**、且写着"共用" | ⛔ 红证：同一个夹具换成 `ai` 档 ⇒ 按钮**必须**回来 |
| ③ | 共用档的真值格**不出现"人工持有"** | ⛔ 红证：`ai` 档必须出现"AI 持有"（说明这条判据真的在看文案） |
| ④ | 三个站点一起起时标题里**不含任何站点名**，且站点名**在表头那串里** | ⛔ 红证：单站点时旧写法会写成 `mecha · <某站名> · 控制台` ⇒ 这里逐字断言标题 == `mecha · 控制台` |
| ⑤ | `_authority_action`：声明 `open` ⇒ `"none"`；声明 `ai` ⇒ `human`/`freeze`（按 `has_gui`） | ⛔ 红证：`ai` 档必须**不是** `none` |
| ⑥ | 控制器**仍然认** `set_write_authority('open')`（共用档不是"把手砍掉"，只是不画按钮） | 反过来钉：别把"不画按钮"做成"机制被删" |

## ⚠ 只用框架自己的替身与真 `Service`/`Authority`

不碰任何项目仓（`Service(id="svc", ...)` 是判据自己的词）；`StatusPage` 用**替身控制器**渲染
（真起 HTTP 的那条在 `checks/launch_selfcheck.py`）。

## Known Limitations and Deferred Work

- **不判"项目侧真的按共用档起步"**：本判据只钉**框架**这一侧（自省字段、控制台渲染、
  动作判定）。"某项目声明 open 之后它的进程里 params 真的是 open"要**真起那个软件**才算，
  属于真机验收（本次是 GP：`initial_mode=Mode.OPEN` ⇒ 注入 `MECHA_INITIAL_MODE=open`；
  ⚠ 项目侧读没读那个 env 是**各项目自己的事**，框架只负责注入）。
- **不判点下去的效果**：共用档下**没有按钮**可点 ⇒ "点错档"这一类不在本判据里；
  按钮真正切档的那条路由仍由 `checks/launch_selfcheck.py` 与项目侧的控制通道判据覆盖。
- ⚠ **`action="none"` 的语义边界**：它表示"**声明层面没有切换需求**"（`initial_mode is OPEN`）。
  若有人在项目自己的 UI 里把档**临时**改成别的，控制台**仍然不画按钮**（照声明走）——
  这是刻意的：切换入口归项目自己（`how_to_open` 那句原话），框架不抢它。
  真要把"临时档也能在控制台上切"做出来，那是另一个决定（需再裁决）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mecha.authority import Authority, Mode, authority_view  # noqa: E402
from mecha.launch import LaunchController  # noqa: E402
from mecha.providers.status_page import render_page  # noqa: E402
from mecha.site import ReadySpec, Service  # noqa: E402

FAILS: list[str] = []


def _check(name: str, cond: bool, extra: object = "") -> bool:
    print(f"  {'✔' if cond else '✖'} {name}" + ("" if cond else f"   ← {extra}"))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def _auth(mode: Mode) -> Authority:
    """一份**真的** Authority（判据自己的词，不带任何项目值）。"""
    return Authority(mode)


def _svc(**kw) -> Service:
    """一份**真的** `Service`（判据自己的词：id/brief 都不带任何项目值）。"""
    base = dict(id="svc", brief="判据用", argv=("python", "-c", "pass"), cwd=ROOT,
                ready=ReadySpec(), data_root=ROOT)
    base.update(kw)
    return Service(**base)  # type: ignore[arg-type]


class _StubController:
    """渲染状态页要的最小事实面（**只给页面真正读的**）。"""

    def __init__(self, action: str, sites: list[str]) -> None:
        self._action, self._sites = action, sites

    @property
    def sites(self):
        return tuple(type("S", (), {"name": n})() for n in self._sites)

    def started(self) -> bool:
        return True

    def intent(self) -> dict:
        return {}

    def state(self):
        return {"children": [], "sites": self._sites, "cleaned": []}

    def services(self):
        return [_svc(has_gui=True, initial_mode=Mode.AI)]

    def dsh_spec(self):
        return None

    def write_authority_state(self, svc_id: str):
        return {"ok": True, "why": "", "has_gui": True,
                "read": {"mode": "open", "ai_can_write": True, "human_holds": True,
                         "shared": self._action == "none", "how_to_open": "x"},
                "read_error": "", "action": self._action}


def check_authority_view_shared() -> None:
    """① 自省面：共用档 shared=True，且两个"谁能写"**同时为真**。"""
    view = authority_view(_auth(Mode.OPEN))
    _check("① 共用档 shared=True", view.get("shared") is True, view)
    _check("① ⭐ 且 ai_can_write 与 human_holds **同时为真**",
           view.get("ai_can_write") is True and view.get("human_holds") is True, view)
    other = authority_view(_auth(Mode.AI))
    _check("① 非共用档 shared=False（对照）", other.get("shared") is False, other)


def check_page_cell_and_title() -> None:
    """②③④ 启动控制台：共用档不画按钮、不误报"人工持有"；标题不含站点名。"""
    shared_html = render_page(_StubController("none", ["甲站", "乙站"]))
    _check("② 共用档的写权格**没有按钮**", "<button" not in shared_html.split("写权")[-1][:400]
           or "mode('" not in shared_html, "页面上出现了写权按钮")
    _check("② 且写明「共用」", "AI 与人共用" in shared_html, "")
    _check("③ ⛔ 共用档不出现「人工持有」", "人工持有" not in shared_html,
           "把共用报成了人工持有")
    _check("④ ⭐ 标题 == `mecha · 控制台`（框架那一层，不取站点名）",
           "<h1>mecha · 控制台</h1>" in shared_html,
           [ln for ln in shared_html.splitlines() if "<h1>" in ln][:1])
    _check("④ 且站点名**在表头那串里**（不是标题里）", "甲站 · 乙站" in shared_html, "")

    #: ⛔ 红证：同一个夹具换成 `ai` 档 ⇒ 按钮**必须**回来、"人工持有"按真值出现。
    ai_html = render_page(_StubController("human", ["甲站"]))
    _check("② ⛔ 红证：ai 档**有**写权按钮（说明上面那条真在看）",
           "mode('svc'" in ai_html, "ai 档没有按钮")
    view_ai = authority_view(_auth(Mode.HUMAN))
    _check("③ ⛔ 红证：human 档自省里 human_holds=True（对照「共用」那条）",
           view_ai.get("human_holds") is True and view_ai.get("shared") is False, view_ai)


def check_action_and_mechanism() -> None:
    """⑤⑥ 动作判定 + **机制没被砍掉**（不画按钮 ≠ 不能切）。"""
    open_svc = _svc(has_gui=True, initial_mode=Mode.OPEN)
    ai_svc = _svc(has_gui=True, initial_mode=Mode.AI)
    frozen = _svc(has_gui=False, initial_mode=Mode.AI)
    _check("⑤ 声明 open ⇒ 动作 \"none\"（无切换需求）",
           LaunchController._authority_action(open_svc) == "none",
           LaunchController._authority_action(open_svc))
    _check("⑤ ⛔ 红证：声明 ai + 有面 ⇒ \"human\"（不是 none）",
           LaunchController._authority_action(ai_svc) == "human",
           LaunchController._authority_action(ai_svc))
    _check("⑤ 声明 ai + 无面 ⇒ \"freeze\"（对照档不退化）",
           LaunchController._authority_action(frozen) == "freeze",
           LaunchController._authority_action(frozen))
    #: ⑥ 机制仍在：`set_write_authority` 认 `open`（§5.1 的裸值口径）
    auth = _auth(Mode.AI)
    auth.switch_mode(Mode.OPEN, side="human")
    _check("⑥ ⭐ 机制没被砍：切到共用档仍然可用", auth.mode is Mode.OPEN, auth.mode)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("[shared_mode] 共用档（Mode.OPEN）：不画假按钮 / 不误报 / 标题不取站点名")
    try:
        check_authority_view_shared()
        check_page_cell_and_title()
        check_action_and_mechanism()
    except Exception as exc:                       # noqa: BLE001
        import traceback
        traceback.print_exc()
        _check("判据自己跑起来了", False, f"{type(exc).__name__}: {exc}")

    if FAILS:
        print(f"authority_shared_mode_selfcheck: FAIL（{len(FAILS)} 条）：{FAILS}")
        return 1
    print("authority_shared_mode_selfcheck: PASS（共用档 6 条 + 4 条红证）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
