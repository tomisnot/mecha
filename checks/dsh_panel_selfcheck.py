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
| 一个用例都没跑 | 「资产自测**一个用例都没跑**（守卫是空的）」 | 资产（自测被删/被跳过） |
| 本脚本自己抛异常 | 带 Python 栈 | **框架** |

## Known Limitations and Deferred Work

- 只跑 **Node 原生剥类型**（Node ≥ 22.6）下的自测；**不验** `tsdown` 构建、**不验**
  真实 dsh 实例里跑起来（本仓 README 的"未验证"节与资产 README 都写了这两条）。
- `check(panel_dir=…)` 的 `panel_dir` **可注入**：判据与红证可以喂一个**合成面板**
  （比如故意让某个用例红的副本）来验"自测失败"这一支说话是否清楚。生产路径用默认值。
- 只按 `*.test.ts` 收集用例：**新增测试文件会被自动跑到**（不需要改本脚本，避免"手抄
  清单"那类漂移，D4）。反过来，**删掉全部测试文件**会被"一个用例都没跑"这一支抓住。
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

_COUNT_RE = re.compile(r"^\u2139\s+(tests|pass|fail)\s+(\d+)\s*$", re.M)
_FAIL_RE = re.compile(r"^\u2716\s+(.+?)\s*\(\d", re.M)


def _node() -> str | None:
    return shutil.which("node")


def _counts(output: str) -> dict[str, int]:
    """解析 `node --test` 的自证计数（`ℹ tests N` / `ℹ pass N` / `ℹ fail N`）。"""
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
    ran, passed, failed_n = counts.get("tests", 0), counts.get("pass", 0), counts.get("fail", 0)
    # R8/R15 自证：**跑起来了**不等于**用例真的跑了**
    lines.append(f"[dsh_panel] 自证：测试文件 {len(tests)} 个；tests={ran} pass={passed} "
                 f"fail={failed_n}（0 用例 ⇒ 视为红：守卫是空的）")

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

    lines.append(f"dsh_panel_selfcheck: PASS（资产自测 {passed}/{ran}；node 硬依赖已满足）")
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
