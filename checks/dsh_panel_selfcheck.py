"""dsh 面板参考实现（``mecha/dsh-panel/``）的守卫：跑它的**共享自测** + 自证非退化。

## 为什么挂进全门禁（R10）

这份资产是**三个项目要复制**的东西。框架**自己不跑它的自测** ⇒ 参考实现写错了，
**三份副本一起错**，而"除参数块外逐字一致"那条判据还会兴高采烈地**强制**那个错
——**比不做还坏**。所以它必须进 `run_all`。

## ⚠ 硬依赖 `node`（**缺即红，不软跳过**）

"有 node 就跑、没有就跳过"正是 D 系列/R10 明令禁止的"**存在但从不执行**"的假绿。
本仓**早有同族硬依赖**：`phase5_*` 直接 import EL 仓的 `mecha_v2`（**缺 EL 即红**）。
⇒ mecha 的**完整门禁需要 `node`**（这是本条的代价，已如实写进根 `README.md` 与本目录
`README.md`）。没有 node 时**不会**假装通过。

## 错误信息必须能分辨（别让人误判成"框架坏了"）

| 情况 | 末行怎么读 | 是谁的问题 |
|---|---|---|
| 缺 `node` | 「**环境缺硬依赖 node**（**不是框架 Python 故障**）」 | 环境 |
| 自测红 | 「**资产自测失败**（不是框架 Python 故障）」+ 红掉的用例名 | 资产（参考实现或它的自测） |
| 一个用例都没跑 | 「资产自测**一个用例都没跑**（守卫是空的）」 | 资产（自测被删） |
| **跑起来了但没有用例真的跑** | 「**跑起来了但没有用例真的跑**（skipped=N）」 | 资产（自测被 **skip** 掉） |
| 本脚本自己抛异常 | 带 Python 栈 | **框架** |

## ⚠ R16 的一个新实例（本判据自己踩的，留注释防复发）

**我一度确信"守卫的空洞已被堵住"，其实只堵了一半。**
第一版只解析 `tests/pass/fail`，通过条件是 `tests > 0 且 fail == 0`，并自认为
"`tests == 0` 视为红 ⇒ 空守卫挡住了"。**实测**：把副本里的用例**全改成 `test.skip`**
⇒ node 报 `tests 21 / pass 0 / fail 0 / skipped 21 / exit 0` ⇒ **本判据判 PASS，
而且它自己那行就打印着 `0/21`**（自己的输出在喊，判据没读）。

⇒ R16 在本判据上的形态**不是**"扰动没落上"，而是：**我确信我守住了某个东西，
而它旁边有一个我没看的口子**。现在解析 **四个计数**并要求
**`skipped == 0 且 pass > 0`**（规格取自 Get Paper 那份更严的判据：它显式断言
`pass` / `fail 0` / `skipped 0`）。

## Known Limitations and Deferred Work

- 只跑 **Node 原生剥类型**（Node ≥ 22.6）下的自测；**不验** `tsdown` 构建、**不验**
  真实 dsh 实例里跑起来（本仓 README 的"未验证"节与资产 README 都写了这两条）。
- `check(panel_dir=…)` 的 `panel_dir` **可注入**：判据与红证可以喂一个**合成面板**
  （比如故意让某个用例红的副本、或**全部 skip 的副本**）来验各支出口说话是否清楚。
  生产路径用默认值。
- 只按 `*.test.ts` 收集用例：**新增测试文件会被自动跑到**（不需要改本脚本，避免"手抄
  清单"那类漂移，D4）。反过来，**删掉全部测试文件**会被"一个用例都没跑"这一支抓住，
  **把用例全 skip** 会被"跑起来了但没有用例真的跑"这一支抓住。
- 计数靠解析 `node --test` 的**报告文本**（`ℹ tests/pass/fail/skipped`）：报告格式若变，
  解析会退化成"读不到计数"⇒ 那一支是**红**（不是静默放行）。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PANEL_DIR = ROOT / "mecha" / "dsh-panel"
TIMEOUT_SECONDS = 300

_COUNT_RE = re.compile(r"^\u2139\s+(tests|pass|fail|skipped)\s+(\d+)\s*$", re.M)
_FAIL_RE = re.compile(r"^\u2716\s+(.+?)\s*\(\d", re.M)


def _node() -> str | None:
    return shutil.which("node")


def _counts(output: str) -> dict[str, int]:
    """解析 `node --test` 的自证计数（`ℹ tests/pass/fail/skipped N`）。

    ⚠ 四个都要：**只读 `tests/pass/fail` 会漏掉"全被 skip"**（那时 `tests>0`、
    `fail=0`、`exit=0`，看着全绿而其实一个用例都没跑）——本判据自己踩过，见模块 docstring。
    """
    return {name: int(n) for name, n in _COUNT_RE.findall(output)}


def check(panel_dir: Path = PANEL_DIR) -> tuple[int, list[str]]:
    """跑面板资产的自测。返回 (退出码, 要打印的行；**最后一行是状态摘要**)。"""
    lines: list[str] = []

    node = _node()
    if node is None:
        lines.append(f"[dsh_panel] 面板资产：{panel_dir}")
        lines.append("[dsh_panel] 找不到 node：完整门禁需要它跑这份资产的自测"
                     "（本仓另有同族硬依赖：phase5_* 需要 EL 仓）")
        lines.append("dsh_panel_selfcheck: FAIL —— 环境缺硬依赖 node"
                     "（**不是框架 Python 故障**）：装上 node 后重跑；"
                     "若确实要移除本条目，那是**放弃**这份资产的自测覆盖（R10）")
        return 1, lines

    tests = sorted(panel_dir.glob("*.test.ts"))
    lines.append(f"[dsh_panel] 面板资产：{panel_dir}")
    lines.append(f"[dsh_panel] node：{node}")
    if not tests:
        lines.append(f"[dsh_panel] 资产里没有 *.test.ts：{panel_dir}")
        lines.append("dsh_panel_selfcheck: FAIL —— 资产自测**一个用例都没跑**"
                     "（守卫是空的）：测试文件不在了")
        return 1, lines

    cmd = [node, "--test", *(str(t) for t in tests)]
    lines.append("[dsh_panel] 跑：" + " ".join(cmd))
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        lines.append(f"dsh_panel_selfcheck: FAIL —— 资产自测超时（>{TIMEOUT_SECONDS}s）")
        return 1, lines

    out = (r.stdout or "") + (r.stderr or "")
    counts = _counts(out)
    ran, passed = counts.get("tests", 0), counts.get("pass", 0)
    failed_n, skipped_n = counts.get("fail", 0), counts.get("skipped", 0)
    # R8/R15 自证：**跑起来了**不等于**用例真的跑了** ⇒ 四个计数**都打出来**
    lines.append(f"[dsh_panel] 自证：测试文件 {len(tests)} 个；tests={ran} pass={passed} "
                 f"fail={failed_n} skipped={skipped_n}"
                 f"（要求 skipped=0 且 pass>0，否则视为红：跑起来了≠用例真的跑了）")

    if ran == 0:
        lines.append("dsh_panel_selfcheck: FAIL —— 资产自测**一个用例都没跑**"
                     "（守卫是空的）：node 退出码 "
                     f"{r.returncode}，没有解析到任何用例计数")
        return 1, lines

    if r.returncode != 0 or failed_n:
        names = list(dict.fromkeys(_FAIL_RE.findall(out)))
        lines.append("[dsh_panel] ✖ 红掉的用例：")
        for n in names:
            lines.append(f"    - {n}")
        lines.append(f"dsh_panel_selfcheck: FAIL —— **资产自测失败**"
                     f"（不是框架 Python 故障）：{failed_n or '有'} 用例红，见上方清单")
        return 1, lines

    if skipped_n > 0 or passed == 0:
        # ⚠ 这一支是本判据的**假绿修复**（R16 实例，见模块 docstring）：全被 skip 时
        # `tests>0 / fail=0 / exit=0`，光看那三个计数会判 PASS，而**一个用例都没跑**。
        lines.append("dsh_panel_selfcheck: FAIL —— 资产自测**跑起来了但没有用例真的跑**"
                     f"（skipped={skipped_n} pass={passed}）"
                     "：**不是**资产自测失败，是**它们被跳过了** ⇒ 守卫没有覆盖"
                     "（软跳过就是「存在但从不执行」的假绿）")
        return 1, lines

    lines.append(f"dsh_panel_selfcheck: PASS（资产自测 {passed}/{ran}，skipped=0；"
                 "node 硬依赖已满足）")
    return 0, lines


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    code, lines = check()
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
