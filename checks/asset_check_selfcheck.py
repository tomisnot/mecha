"""`mecha/asset_check.py` 的自检 —— 共享资产判据件本身也要有守卫。

它守的承诺：**"逐字副本 + 指纹表"这条资产约定，其机械部分是框架拥有的规则**，
项目不该各写一遍（实测两个项目各写了 218 行同功能判据，形态还不同）。

本判据验四件事，每件都**两个方向**（R7/R11：观测量必须能变）：

A. `text_sha256` —— **行尾必须归一**（`\\r\\n` 与 `\\n` 同指纹）；**内容变了必须变**。
B. `parse_node_counts` —— 正常汇总行解析得出四计数；**垃圾输入返回空表**（不许瞎猜）。
C. `audit_carriers` —— 全符返回空；**漂移被抓**；**缺文件必须抛**（不许静默跳过、R15）。
D. `run_node_suite` —— ⭐ 四条判定各有反面：
   `fail>0` ⇒ 红；**`skipped>0` ⇒ 红（假绿防线）**；`pass<min_pass` ⇒ 红；**缺 node ⇒ 红（不软跳过）**；
   且正常套件 ⇒ 绿（否则"永远红"的守卫没有信息量）。

⚠ 红证方式：本判据**自己构造输入**（临时目录里现写 TS 套件），不靠改仓内文件（R16：扰动自证落上）。

## Known Limitations and Deferred Work

- **不验"项目该钉哪些载体"**：那是对应项目自己的判断（给少了就是漏钉）。
  本判据只验"`audit_carriers` 与 `run_node_suite` 这两个**规则**在两个方向上都会变"。
- **不验真实资产的指纹**：资产副本是否与参考实现一致，由各项目自己的指纹判据守
  （框架侧只跑参考实现自身的自测，见 `dsh_panel_selfcheck.py`）。
- **`run_node_suite` 的判定依赖 `node --test` 的 TAP 汇总行格式**：格式变了 ⇒ 本判据里那三条会红
  （不是静默放行）。⚠ 但"格式变了"与"套件真坏了"在消息上**未必一眼可分** —— 未做区分，如实记着。
- **D 组会真起 `node` 子进程**（在临时目录里，秒级）：**本判据硬依赖 `node`**。
  机器上没有 node 时，D 组除"缺 node ⇒ 红"那条（它用的是假 node 名）之外都会红 ⇒
  与 `dsh_panel_selfcheck` 同款"硬依赖 node，不软跳过"口径。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mecha.asset_check import (  # noqa: E402
    audit_carriers,
    parse_node_counts,
    run_node_suite,
    text_sha256,
)

FAILS: list[str] = []


def _check(name: str, cond: bool, extra: object = "") -> None:
    if cond:
        print(f"  ✔ {name}")
    else:
        print(f"  ✖ {name}" + (f"  <- {extra}" if extra != "" else ""))
        FAILS.append(name)


def main() -> int:
    # ⚠ 与同目录其它判据同款：中文 Windows 的默认控制台编码是 GBK，而本判据打印 `✔`/`✖`
    #   ⇒ **不加这一行会以 UnicodeEncodeError 崩**（不是判据红，是它自己跑不完）。
    #   2026-10-01 实测踩到：这条判据此前被 run_all 的旧清单遮蔽、从未真跑过。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("[asset_check_selfcheck] 共享资产判据件自检")
    print("  — A. text_sha256：行尾归一 + 内容敏感 —")
    _check("CRLF 与 LF 同指纹（行尾配置不许打败比对）",
           text_sha256(b"a\r\nb") == text_sha256(b"a\nb"))
    _check("内容变了指纹必须变（否则观测量是恒定的，R11）",
           text_sha256(b"a\nb") != text_sha256(b"a\nc"))

    print("  — B. parse_node_counts —")
    got = parse_node_counts("\u2139 tests 42\n\u2139 pass 42\n\u2139 fail 0\n\u2139 skipped 0\n")
    _check("正常汇总行解析出四计数",
           got == {"tests": 42, "pass": 42, "fail": 0, "skipped": 0}, got)
    _check("垃圾输入 ⇒ 空表（不瞎猜）", parse_node_counts("not a tap summary") == {})

    print("  — C. audit_carriers —")
    good = {"a.ts": text_sha256(b"hello\n")}
    _check("全符 ⇒ 空清单", audit_carriers(good, lambda _n: b"hello\n") == [])
    drifted = audit_carriers(good, lambda _n: b"hellO\n")
    _check("漂移 ⇒ 被抓（1 条）", len(drifted) == 1, drifted)

    def _missing(name: str) -> bytes:
        raise FileNotFoundError(name)

    try:
        audit_carriers({"gone.ts": "0" * 64}, _missing)
        _check("缺文件 ⇒ 必须抛（不许静默跳过，R15）", False, "没有抛")
    except FileNotFoundError:
        _check("缺文件 ⇒ 必须抛（不许静默跳过，R15）", True)

    print("  — D. run_node_suite：四条判定 + 正常路径 —")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "ok.test.mjs").write_text(
            "import {test} from 'node:test'\n"
            "import assert from 'node:assert/strict'\n"
            "test('a',()=>assert.equal(1,1))\n"
            "test('b',()=>assert.equal(2,2))\n",
            encoding="utf-8",
        )
        r_ok = run_node_suite(d, ["ok.test.mjs"], min_pass=2)
        _check("正常套件 ⇒ 绿（否则本守卫恒红、没有信息量）", r_ok.ok, r_ok.detail)
        _check("计数如实（pass=2）", r_ok.counts.get("pass") == 2, r_ok.counts)

        r_floor = run_node_suite(d, ["ok.test.mjs"], min_pass=99)
        _check("pass < 下限 ⇒ 红（防判据被掏空）", not r_floor.ok, r_floor.detail)

        (d / "bad.test.mjs").write_text(
            "import {test} from 'node:test'\n"
            "import assert from 'node:assert/strict'\n"
            "test('x',()=>assert.equal(1,2))\n",
            encoding="utf-8",
        )
        r_fail = run_node_suite(d, ["bad.test.mjs"], min_pass=0)
        _check("fail>0 ⇒ 红", not r_fail.ok, r_fail.detail)

        (d / "skip.test.mjs").write_text(
            "import {test} from 'node:test'\ntest('s',{skip:true},()=>{})\n",
            encoding="utf-8",
        )
        r_skip = run_node_suite(d, ["skip.test.mjs"], min_pass=0)
        _check("⭐ 全被跳过 ⇒ 红（假绿防线：跑起来了 ≠ 用例真的跑了）", not r_skip.ok, r_skip.detail)

    r_nonode = run_node_suite(Path.cwd(), ["x.test.mjs"], min_pass=0, node="definitely-not-node-xyz")
    _check("缺 node ⇒ 红（不软跳过）", not r_nonode.ok, r_nonode.detail)

    print()
    if FAILS:
        print(f"asset_check_selfcheck: FAIL（{len(FAILS)} 条）")
        for f in FAILS:
            print(f"  - {f}")
        return 1
    print("asset_check_selfcheck: PASS（共享资产判据件四类行为、两方向皆变）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
