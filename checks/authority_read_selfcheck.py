"""写权自省（`read_write_authority`，上升进框架）的判据。

## ⚠ 造法纪律（老规矩：先问"怎么故意退化还能让它绿"）

- **判据 1 的陷阱**：只断言"返回了 dict"⇒ 把 `ai_can_write` **恒写 True** 也能绿。
  ⇒ **必须逐档断言**（locked / human / ai / open 四种 mode 各一个期望值）。
- **判据 2 的陷阱**：只断言"返回值里有 mode"⇒ read **顺手落一笔账**照样绿
  ⇒ 必须断言"账**条数不变**、mode 不变"（前后对比）。
- **判据 4 的陷阱**：只断言"AI 面上有 read"⇒ **把 set 也注册进去**照样绿
  ⇒ 必须**同时**断言"AI 面上**没有** set/open"。
- **判据 5 的陷阱**：只断言"hint 里有那个词"⇒ **叠两遍**也照样绿 ⇒ 必须断言**只出现一次**。

## Known Limitations and Deferred Work

- **不测真软件进程内的 Authority**：本档测的是**框架**这份唯一实现
  （软件的 Authority 由各项目持有）。⇒ 用框架自己的 ``Authority`` 即可，
  这正是"上升进框架"的意思：**同一份实现**两处注册。
- **不测"聚合端点自动把 read 挂给 AI"**：那要一条"从软件进程读当前写权"的
  运行期通路（每个后端一次控制调用 + 缓存失效策略）。⚠ 本轮**不做半成品**：
  `register_authority_read` 已就位，**接线**留给下一轮（见报告"未决"）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mecha.authority as A  # noqa: E402
from mecha.authority import (READ_AUTHORITY_TOOL, Authority, Mode,  # noqa: E402
                             authority_view, register_authority_read)
from mecha.errors import GateDenied  # noqa: E402
from mecha.tools import ToolRegistry  # noqa: E402

FAILS: list[str] = []


def _check(name: str, cond: bool, extra: object = "") -> None:
    if cond:
        print(f"  ✔ {name}")
    else:
        print(f"  ✖ {name}" + (f"  <- {extra}" if extra != "" else ""))
        FAILS.append(name)


def check_modes_are_all_correct() -> None:
    """⭐ ① 四种 mode 下两个布尔**都对**（逐档断言，不是"返回了 dict"）。"""
    expect = {
        Mode.LOCKED: (False, False),   # 谁都不可写
        Mode.HUMAN: (False, True),     # 只有人工
        Mode.AI: (True, False),        # 只有 AI
        Mode.OPEN: (True, True),       # 两侧都行
    }
    for m, (ai_ok, human_ok) in expect.items():
        v = authority_view(Authority(m))
        _check(f"① mode={m.value!r} ⇒ ai_can_write={ai_ok} / human_holds={human_ok}",
               v["ai_can_write"] is ai_ok and v["human_holds"] is human_ok, f"v={v}")
    # ⚠ 反向对偶：四种 mode 至少产生**两种不同的组合**（防"恒 True / 恒 False"蒙对一档）
    combos = {(authority_view(Authority(m))["ai_can_write"],
               authority_view(Authority(m))["human_holds"]) for m in Mode}
    _check("① 四种 mode 产生**≥3 种不同组合**（恒真/恒假蒙不过去）", len(combos) >= 3,
           f"combos={combos}")


def check_pure_query_no_side_effects() -> None:
    """⭐ ② 纯查询：调前调后 **mode 不变、账不增长**。"""
    a = Authority(Mode.AI)
    audit: list[str] = []
    a.watch(lambda m: audit.append(m.value))       # 任何 mode 变更都会被记下
    before_mode = a.mode
    n_before = len(audit)
    v1 = authority_view(a)
    v2 = authority_view(a)
    _check("② 调用之后 **mode 不变**", a.mode is before_mode, f"mode={a.mode}")
    _check("② 且**没有任何 mode 变更事件**（watch 没被触发）",
           len(audit) == n_before, f"audit={audit}")
    _check("② 调两次结果**一致**（纯函数性）", v1 == v2, f"v1={v1} v2={v2}")
    # ⚠ 它还**不该**动 Gate：被拒的探侧不改变"下次能不能写"
    a2 = Authority(Mode.AI)
    try:
        a2.gate("human")
    except GateDenied:
        pass
    _check("② 探侧失败**不改变**闸门判定（再探 ai 仍然放行）",
           authority_view(a2)["ai_can_write"] is True)


def check_mode_is_bare_value() -> None:
    """⭐ ③ `mode` 是**裸值**（`'ai'` 不是 `'Mode.AI'`）。"""
    for m in Mode:
        v = authority_view(Authority(m))
        _check(f"③ mode={m.value!r} 是裸值（不是 {str(m)!r}）",
               v["mode"] == m.value and v["mode"] != str(m), f"got={v['mode']!r}")


def check_ai_surface_has_read_but_not_write() -> None:
    """⭐ ④ 注册进一面之后：**有 read、没有 set/open**（§7.1.1：不许 AI 给自己解锁）。"""
    reg = ToolRegistry()
    register_authority_read(reg, Authority(Mode.AI))
    names = [s["name"] for s in reg.schemas()]
    _check("④ 面上有 `read_write_authority`", READ_AUTHORITY_TOOL in names, f"names={names}")
    _check("④ ⭐ 面上**没有** `set_write_authority`（不许 AI 切写权）",
           "set_write_authority" not in names, f"names={names}")
    _check("④ ⭐ 面上**没有** `open_manual_surface`（不许 AI 开人手面）",
           "open_manual_surface" not in names, f"names={names}")
    _check("④ ⭐ 这一面**只有这一个**工具（本函数只注册只读自省）",
           names == [READ_AUTHORITY_TOOL], f"names={names}")
    out = reg.execute(READ_AUTHORITY_TOOL, {})
    _check("④ 它能被调用且回真值", (out.get("value") or {}).get("mode") == "ai",
           f"out={out}")


def check_denial_hint_points_to_read() -> None:
    """⭐ ⑤ 写被拒的 hint **含** `read_write_authority`，且**不重复叠**。"""
    for m, side in ((Mode.LOCKED, "ai"), (Mode.AI, "human"), (Mode.LOCKED, "human")):
        a = Authority(m)
        try:
            a.gate(side)
            _check(f"⑤ mode={m.value} side={side} 应当被拒", False, "居然放行了")
            continue
        except GateDenied as exc:
            hint = exc.hint or ""
            _check(f"⑤ mode={m.value}/side={side} 的 hint 指向 `read_write_authority`",
                   READ_AUTHORITY_TOOL in hint, f"hint={hint!r}")
            _check(f"⑤ 且**只出现一次**（不重复叠）", hint.count(READ_AUTHORITY_TOOL) == 1,
                   f"hint={hint!r}")
            _check(f"⑤ 幂等：再叠一次不变化",
                   A._with_read_hint(hint) == hint, f"again={A._with_read_hint(hint)!r}")


def check_how_to_open_is_registration_side() -> None:
    """⑥ `how_to_open` 由**注册方**给；框架缺省值**中性**（不含项目名/领域词）。"""
    d = authority_view(Authority(Mode.HUMAN))["how_to_open"]
    _check("⑥ 缺省 `how_to_open` 是**中性**文案（提到的只是「启动面板」）",
           "面板" in d, f"d={d!r}")
    for word in ("GP", "EL", "ML", "paperpilot", "energy", "getpaper", "/settings"):
        _check(f"⑥ 框架缺省文案里**没有** {word!r}", word.lower() not in d.lower(), f"d={d!r}")
    reg = ToolRegistry()
    register_authority_read(reg, Authority(Mode.HUMAN),
                            how_to_open="本项目：点右上角那个开关")
    got = (reg.execute(READ_AUTHORITY_TOOL, {}).get("value") or {}).get("how_to_open")
    _check("⑥ 注册方给的值**原样透出**（框架不写死自己的）", got == "本项目：点右上角那个开关",
           f"got={got!r}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("[authority_read_selfcheck] 写权自省（上升进框架）")
    check_modes_are_all_correct()
    check_pure_query_no_side_effects()
    check_mode_is_bare_value()
    check_ai_surface_has_read_but_not_write()
    check_denial_hint_points_to_read()
    check_how_to_open_is_registration_side()
    if FAILS:
        print(f"authority_read_selfcheck: FAIL（{len(FAILS)} 条）：{FAILS}")
        return 1
    print("authority_read_selfcheck: PASS（①—⑥ 都钉住）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
