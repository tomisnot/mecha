"""个人路径守卫：被 git 跟踪的文件里出现"个人路径特征" ⇒ 红（公开仓不再泄漏本机路径）。

## ⚠ 为什么**必须用 Python**，**不能用 PowerShell 判**（这条比守卫本身更重要）

第一版探针我是用 PowerShell 写的：`git ls-files` 出来的**非 ASCII 路径被加了转义**
（`"docs\\v2\\…"`），`Select-String` 因此**打不开那些文件** ⇒ 扫描结果 **`TOTAL=0`**，
而那是一个**假绿**（"没命中"与"没读到"长得一模一样）。
**⇒ 是它自己报的 0 让我起疑 —— 否则我会拿一个假 0 去设计免除清单，把真泄漏全部"免除"掉。**

⇒ 结论：**判定必须在 Python 里做**（`subprocess` + `read_text`，路径不经 shell 引号），
**别把它"优化"回 PowerShell**。

## 特征怎么放（防"守卫扫到自己"）

特征**拆开拼**（`"D:" + chr(92)` 之类）⇒ 本文件里**不出现**任何一个完整特征字面量；
**另外**再把本文件与免除表**显式排除**（双保险）。二者缺一不可：
* 只靠"拆开拼"⇒ 后人一眼看不明白为什么要这么写；
* 只靠"自排除"⇒ 任何人**读这个文件**都会看到一串本机路径示例（那本身就是泄漏）。

## 免除表（**缩小的债**，不是永久豁免）

`checks/personal_paths_exempt.txt`：`<仓库内路径> <允许处数>`（**只写仓库内路径，不写个人路径**）。
命中数**超过**允许 ⇒ 红；**未登记**的文件有命中 ⇒ 红。
⚠ 本判据**打印**免除表的现状与"可以收紧"的条目，但**不因"该收紧"而红**
（否则本批 ①/② 会被同一条判据锁住）——**"债可见"与"债必还"是两件事，后者靠人推进**。

## Known Limitations and Deferred Work

- 只扫**被 git 跟踪**的文本文件（图片/轮子等二进制跳过）。**未跟踪的文件不扫** ——
  那是对的（不入库就不会泄漏），但后果要说清：**一个刚写、还没 `git add`** 的文件里
  有本机路径时，本判据**看不见**（等它进了库、下次跑才看见）。
- "个人路径"只有**有限的特征集**（盘符 / 用户目录 / 同步盘两种写法 / 仓库根名 / 邮箱 /
  本机用户名）：**别的主机专属信息**（内网域名、别的机器用户名…）**抓不到**。
- 免除表是**手抄位**（`<仓库内路径> <处数>`），它是"缩小的债"、**不会自己变小**；
  判据只打印"可收紧"，**不红**（理由见上）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXEMPT = ROOT / "checks" / "personal_paths_exempt.txt"
SELF = "checks/personal_paths_selfcheck.py"
SKIP_EXT = (".png", ".jpg", ".jpeg", ".gif", ".whl", ".pdf", ".ico", ".lock",
            ".pyc", ".pyo", ".so", ".dll", ".exe")
#: 路径里含这些目录名就跳过：**字节码与构建产物**。⚠ 不是洁癖 —— 红证时踩到过：
#: 临时副本里 `git add -A` 把 `checks/__pycache__/*.pyc` 也加了进去，而**本模块的字节码里
#: 有**本机用户名那类字面量** ⇒ 副本的"干净基线"当场变红（**假红**）。真仓里 `.pyc` 不入库、
#: 所以真仓是绿的 —— 但**守卫不该依赖"没人提交字节码"这个假设**。
SKIP_DIRS = ("__pycache__", ".pytest_cache", "node_modules")

#: 特征（**拆开拼**，理由见模块 docstring）。
PATTERNS = (
    "D:" + chr(92),                    # Windows 盘符路径
    "C:" + chr(92) + "Users",          # 用户目录
    "百度" + "同步盘",                  # 同步盘（中文名）
    "Baidu" + "Syncdisk",              # 同步盘（ASCII 名）
    "code" + "-nosync",                # 本机仓库根目录名
    "1037769406" + "@qq.com",          # 作者邮箱
    "LEN" + "OVO",                     # 本机用户名（拆开拼：守卫自身不许成为泄漏点）
)


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _fail(out: list[str], msg: str) -> None:
    print(f"  ✗ {msg}")
    out.append(msg)


def _tracked_files() -> list[str] | None:
    """被跟踪文件清单。⚠ **必须 `-z` + `core.quotePath=false`**：
    `git ls-files` 默认会给**非 ASCII 路径加引号并转义**（`"docs\\AI\\uXXXX.md"`）⇒
    路径拼出来打不开 ⇒ 那些文件**会被静默跳过**（本判据第一版正是这样：非 ASCII 路径
    **从没被扫过**，而结果上看起来"扫了 151 个文件"）。这与模块 docstring 里那条
    PowerShell 教训是**同一类病**：**读不到的，与没命中的，长得一模一样**。
    """
    try:
        r = subprocess.run(["git", "-c", "core.quotePath=false", "ls-files", "-z"],
                           cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"  ✗ git ls-files 跑不起来：{type(exc).__name__}: {exc}")
        return None
    if r.returncode != 0:
        print(f"  ✗ git ls-files 非零退出：{r.stderr.strip()[:120]}")
        return None
    return [ln for ln in r.stdout.split("\0") if ln.strip()]


def _load_exempt() -> dict[str, int] | None:
    if not EXEMPT.is_file():
        return {}
    table: dict[str, int] = {}
    for ln in EXEMPT.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        if len(parts) != 2 or not parts[1].isdigit():
            print(f"  ✗ 免除表格式错（要 '<路径> <处数>'）：{s!r}")
            return None
        table[parts[0]] = int(parts[1])
    return table


def _hits(text: str) -> int:
    return sum(text.count(p) for p in PATTERNS)


def scan() -> tuple[list[tuple[str, int]], int, dict[str, int], list[str], int] | None:
    """返回 (命中表, 列出文件数, 免除表, **读不到的**, **实际读到数**)。

    ⚠ 后两项是**这次事故的直接产物**：
    * **读不到**必须响亮（**不许静默 continue**）——"读不到的"与"没命中的"长得一模一样；
    * **实际读到几个**必须打印 —— "列了 151 个"不等于"读了 151 个"（本判据第一版就是这么瞎的：
      非 ASCII 路径**一个都没读到**，却打印"扫描 151 个文件"）。
    """
    files = _tracked_files()
    exempt = _load_exempt()
    if files is None or exempt is None:
        return None
    rows: list[tuple[str, int]] = []
    unreadable: list[str] = []
    read = 0
    for rel in files:
        if rel == SELF or rel == "checks/personal_paths_exempt.txt":
            continue
        if rel.endswith(SKIP_EXT):
            continue
        if any(part in SKIP_DIRS for part in rel.split("/")):
            continue
        p = ROOT / rel
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            # ⚠ **响亮**：读不到就不能对它下任何结论（本次病根正是这一行曾经是静默 continue）
            print(f"  ✗ 读不到被跟踪文件，**不能**对它下结论：{rel}（{type(exc).__name__}）")
            unreadable.append(rel)
            continue
        read += 1
        n = _hits(text)
        if n:
            rows.append((rel, n))
    return rows, len(files), exempt, unreadable, read


def check_no_personal_paths(out: list[str]) -> None:
    res = scan()
    if res is None:
        _fail(out, "前置失败：拿不到被跟踪文件或免除表（**不静默放行**）")
        return
    rows, listed, exempt, unreadable, read = res
    if unreadable:
        _fail(out, f"有被跟踪文件**读不到**（读不到 ⇒ 不下任何结论，响亮失败）："
                   f"{unreadable[:5]}（共 {len(unreadable)} 个）")
    if read == 0:
        _fail(out, "**一个文件都没读到** —— 这不是「没有泄漏」，这是判据瞎了")
    else:
        _ok(f"自证读到了东西：**实际读到 {read} 个**文件（列出 {listed} 个）")
    offenders: list[str] = []
    for rel, n in rows:
        allowed = exempt.get(rel)
        if allowed is None:
            offenders.append(f"{rel}（{n} 处，未登记免除）")
        elif n > allowed:
            offenders.append(f"{rel}（{n} 处 > 允许 {allowed}）")
    if offenders:
        _fail(out, f"被跟踪文件里出现个人路径特征：{offenders}")
    else:
        _ok(f"被跟踪文件无**新增**个人路径特征（列出 {listed} 个、读到 {read} 个）")

    debt = sum(n for rel, n in rows if rel in exempt)
    shrinkable = [rel for rel, n in rows if exempt.get(rel, 0) > n]
    if rows:
        print(f"    · 已登记的历史债：{len(rows)} 个文件 / {debt} 处"
              f"（免除表：checks/personal_paths_exempt.txt）")
    for rel in shrinkable:
        print(f"    · 可收紧（命中数已低于登记）：{rel}")

    # L7 对偶：判定不是哑的 —— 真特征必须命中、假特征必须 0 命中。
    if _hits("x " + PATTERNS[0] + "tmp") > 0 and _hits("NoSuchFeature_zzz") == 0:
        _ok("对偶：真特征命中、假特征 0 命中（非哑判据）")
    else:
        _fail(out, "对偶失败：特征匹配器不工作（假绿风险）")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out: list[str] = []
    print("个人路径守卫（公开仓不泄漏本机路径）：")
    check_no_personal_paths(out)
    if out:
        print(f"personal_paths_selfcheck: FAIL（{len(out)} 条）")
        return 1
    print("personal_paths_selfcheck: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
