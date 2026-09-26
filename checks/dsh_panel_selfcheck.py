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
| **缺一份可用的 `tsc`** | 「环境缺硬依赖：**一份可用的 tsc**（不是框架 Python 故障）」 | 环境 |
| **类型检查不过** | 「**资产类型检查失败**（不是框架 Python 故障）」+ TS 错误行 | 资产（**只在类型位置**的错） |
| 本脚本自己抛异常 | 带 Python 栈 | **框架** |

## ⚠ 第二关：**类型检查**（为什么它是必需的，而不是锦上添花）

`node --test` **只剥类型、不做类型检查** ⇒ **只在类型位置的错它一个都看不见**。
真实缺口（2026-09-26，第一个跑 `tsc` 的消费者抓到的）：

```
src/panel/panel-data.ts(160,53): error TS2304: Cannot find name 'Ev'.
src/panel/panel-data.ts(167,53): error TS2304: Cannot find name 'ConfigWire'.
```
`Ev` / `ConfigWire` 定义在 `panel-view.ts`（是 export），而 `panel-data.ts` 拿它们当
返回类型时**漏了 import** ⇒ 运行时被剥掉、**自测全绿**，而**消费者一跑 `npm run typecheck` 就红**。
⇒ **资产发布了框架自己无法检查的代码，第一个消费者付账**——所以本判据现在**借消费者的
`tsc`**（`<项目>/dsh/node_modules/typescript`，与 `phase5_*` 硬编码 EL 仓根同族先例）
跑一遍 `--strict --noUncheckedIndexedAccess`（含 `.tsx`）。**缺 tsc 即红，不软跳过。**

⚠ **它第一次跑就抓到了本判据作者自己的 5 处类型错**（`panel-data.test.ts` 里
`extras.ok` 在 `noUncheckedIndexedAccess` 下是 `… | undefined`）——**这就是这条守卫的价值**。

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
- **第二条硬外部依赖**：类型检查**借**接入项目装的 `tsc`（硬编码 `TSC_ROOTS`，取第一个
  可用的；`MECHA_TSC_ROOT` 可覆盖）。**缺即红**。⇒ **本仓完整门禁需要 `node` 与一份
  可用的 `tsc`**（已写进根 `README.md` 与 `run_all.py` 的 Known Limitations）。
- 类型检查的口径是**本判据选定的那一套**（`--strict --noUncheckedIndexedAccess` +
  ESM/bundler 解析 + `jsx: react-jsx`）：消费者的口径若**更严**，它仍可能先红
  ⇒ **消费者跑自己的 `npm run typecheck` 仍是验收义务**（本判据是"底线"，不是替代）。
- **不验** `tsdown` 打包、**不验**真实 dsh 实例里跑起来。
"""

from __future__ import annotations

import os
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
_TS_ERROR_RE = re.compile(r"error TS\d+", re.M)

#: **借消费者的 `tsc`** 做类型检查（本仓自己没有 node_modules）。
#:
#: ⚠ 与 `phase5_*` 同族先例：那些判据**硬编码 EL 仓根**并"缺 EL 即红"——本判据同样
#: 硬编码几个**接入项目的根**，取第一个装着 `typescript` + `@types` 的。**缺即红**。
#: 可用环境变量 `MECHA_TSC_ROOT` 显式指定一个（指向含 `node_modules` 的那一层，通常是
#: 某项目的 `dsh/`）。
TSC_ROOTS = (
    Path(r"D:\学\BaiduSyncdisk\编程\code\python\Energy Level\dsh"),
    Path(r"D:\学\BaiduSyncdisk\编程\code\python\Get Paper\dsh"),
)


def _node() -> str | None:
    return shutil.which("node")


def _counts(output: str) -> dict[str, int]:
    """解析 `node --test` 的自证计数（`ℹ tests/pass/fail/skipped N`）。

    ⚠ 四个都要：**只读 `tests/pass/fail` 会漏掉"全被 skip"**（那时 `tests>0`、
    `fail=0`、`exit=0`，看着全绿而其实一个用例都没跑）——本判据自己踩过，见模块 docstring。
    """
    return {name: int(n) for name, n in _COUNT_RE.findall(output)}


def _tsc() -> tuple[Path, Path] | None:
    """找一个可用的 `tsc` + `@types` 根。返回 (tsc, typeRoots) 或 None。

    为什么**必须借**：资产的 `.tsx` 与 `.ts` 里都可能有**只在类型位置**的错误
    （漏 import 的类型名是最常见的一种）。`node --test` 只剥类型、**不做类型检查**
    ⇒ 框架自己看不见，而**第一个消费者一跑 `npm run typecheck` 就红**。
    真实实例：`panel-data.ts` 漏 `import type { ConfigWire, Ev }` ⇒
    消费者报四条 `TS2304: Cannot find name`，而**本判据当时是全绿的**。
    """
    cands: list[Path] = []
    env = os.environ.get("MECHA_TSC_ROOT")
    if env:
        cands.append(Path(env))
    cands.extend(TSC_ROOTS)
    for dsh in cands:
        tsc = dsh / "node_modules" / "typescript" / "bin" / "tsc"
        types = dsh / "node_modules" / "@types"
        if tsc.is_file() and (types / "node").is_dir():
            return tsc, types
    return None


def _typecheck(panel_dir: Path, tsc: Path, types: Path) -> tuple[int, list[str], int]:
    """对资产做**严格类型检查**（`--noEmit`）。返回 (退出码, 错误行, 检查的文件数)。"""
    files = sorted(str(p) for p in list(panel_dir.glob("*.ts")) + list(panel_dir.glob("*.tsx")))
    cmd = ["node", str(tsc), "--noEmit", "--strict",
           "--noUncheckedIndexedAccess", "--allowImportingTsExtensions",
           "--target", "es2022", "--module", "esnext", "--moduleResolution", "bundler",
           "--jsx", "react-jsx", "--skipLibCheck",
           "--typeRoots", str(types), "--types", "node,react", *files]
    try:
        r = subprocess.run(cmd, cwd=str(panel_dir), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired) as exc:  # noqa: BLE001
        return 1, [f"tsc 跑不起来：{type(exc).__name__}: {exc}"], len(files)
    out = (r.stdout or "") + (r.stderr or "")
    errs = [l.strip() for l in out.splitlines() if _TS_ERROR_RE.search(l)]
    if r.returncode != 0 and not errs:
        errs = [l.strip() for l in out.splitlines() if l.strip()][:5]
    return r.returncode, errs, len(files)


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

    # ---- 第二关：**类型检查**（node --test 只看运行时，看不见"只在类型位置"的错）----
    found = _tsc()
    if found is None:
        lines.append("[dsh_panel] 找不到可用的 tsc：本判据要**借一个接入项目装的那份**"
                     "（`<项目>/dsh/node_modules/typescript`），或用 `MECHA_TSC_ROOT` 指定")
        for d in TSC_ROOTS:
            lines.append(f"    - 找过：{d}")
        lines.append("dsh_panel_selfcheck: FAIL —— 环境缺硬依赖：**一份可用的 tsc**"
                     "（**不是框架 Python 故障**）：类型检查是资产的第一道消费者防线"
                     "（漏 import 的类型名只有它看得见）")
        return 1, lines
    tsc, types = found
    tc_code, tc_errs, n_files = _typecheck(panel_dir, tsc, types)
    lines.append(f"[dsh_panel] 类型检查：{tsc}（借来的）；检查 {n_files} 个文件"
                 f"（.ts + .tsx）；--strict --noUncheckedIndexedAccess")
    if tc_code != 0:
        lines.append("[dsh_panel] ✖ 类型错误：")
        for e in tc_errs[:20]:
            lines.append(f"    - {e}")
        lines.append("dsh_panel_selfcheck: FAIL —— **资产类型检查失败**"
                     "（不是框架 Python 故障）：见上方 TS 错误"
                     "（`node --test` 剥类型、看不见这些）")
        return 1, lines

    lines.append(f"dsh_panel_selfcheck: PASS（资产自测 {passed}/{ran}，skipped=0；"
                 f"类型检查 {n_files} 文件 0 错；node + 借来的 tsc 硬依赖已满足）")
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
