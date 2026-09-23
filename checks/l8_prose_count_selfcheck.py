"""L8 散文计数守卫（D-34：文档不写计数，只写名字或指向单一来源）。

扫 mecha/、examples/、checks/ 的 .py（docstring + 注释）与 README.md、
docs/v2/notes/**.md，抓**会增长的集合**的手写数量：`N 个 工具/provider/模块/
能力/seam/原语` 与 `N 条 判据/门禁`。这类数字随功能增删必漂（v1 付过两次账：
18→16、16→17，五处手抄全红；本轮又漂了一次 7→11）。

**不抓**框架固定常量：`4 个必实现` / `4 个方法`（= FRAMEWORK_CALLS，由
phase2_surface_selfcheck 用 `__abstractmethods__` 钉死，是"指向单一来源"的
合法例外）。

判据纪律（L7）：
  E+ 不误报：现仓合规文本必须 0 命中（否则守卫自己先红）。
  E- 能抓坏：合成含 `7 个工具` / `5 条判据` 的文件必须被抓。

用法：python checks/l8_prose_count_selfcheck.py
退出码：0 全绿；1 有违例或自测失败。

## Known Limitations and Deferred Work

- 只抓"会增长的集合"的手写数量（工具/provider/模块/能力/seam/原语/判据/门禁）。
  框架固定常量（`4 个必实现` = FRAMEWORK_CALLS，由 phase2 用 `__abstractmethods__`
  钉死）合法，不抓——它是"指向单一来源"的例外。
- 按文件名豁免 L8 规则的**定义性 ADR**（`文档手写计数.md` 必须引用历史计数
  "16 个工具"来解释本规则）。不做跨文件引号配对豁免——那会误吞真违例（假绿）。
- 正则粗匹配中文量词，可能漏抓异形写法（"共 7 项工具"）；命中即红是下限，
  不承诺穷尽。扩词表等真实漏抓发生再加（n=0 不预建）。
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 会增长的集合：手写数量必漂 ⇒ 禁。
GROWING = r"工具|providers?|模块|能力|seams?|原语"
GROWING_COUNT = re.compile(rf"(\d+)\s*个\s*({GROWING})")
# 判据/门禁数量同属会漂（每加一条判据就变）。
GROWING_TIAO = re.compile(r"(\d+)\s*条\s*(判据|门禁)")

SCAN_GLOBS = (
    "mecha/**/*.py", "examples/**/*.py", "checks/**/*.py",
    "README.md", "docs/v2/notes/**/*.md",
)

#: 本守卫自身含测试语料（`7 个工具` 等反例字符串），扫描时排除。
SELF = Path(__file__).name

#: L8 规则的**定义性 ADR** 必须引用历史计数（"16 个工具"等）来解释本规则，
#: 按文件名豁免（不做脆弱的跨文件引号配对——那会把真违例误吞成假绿）。
RULE_ADRS = frozenset({"2026-09-23-文档手写计数.md"})


def _scan_text(text: str) -> list[str]:
    hits: list[str] = []
    for m in GROWING_COUNT.finditer(text):
        hits.append(f"{m.group(0)!r}（{m.group(2)} 数量手写，改名字清单或指向来源）")
    for m in GROWING_TIAO.finditer(text):
        hits.append(f"{m.group(0)!r}（判据/门禁数量手写，改指向 run_all 或名字清单）")
    return hits


def check_tree(root: Path) -> list[str]:
    violations: list[str] = []
    for glob in SCAN_GLOBS:
        for path in sorted(root.glob(glob)):
            if not path.is_file() or path.name in (SELF, *RULE_ADRS):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for hit in _scan_text(text):
                violations.append(f"{path.relative_to(root).as_posix()}: {hit}")
    return violations


def run_selftest() -> list[str]:
    """L7 对偶：合规 0 命中；合成违例必被抓。"""
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "mecha").mkdir()
        # E+ 合规：名字清单 + 框架固定常量（4 个必实现不抓）
        (tmp / "mecha" / "clean.py").write_text(
            '"""工具：set_config / run_sim / submit_job（清单见 schemas()）。\n'
            '必实现只有 4 个（FRAMEWORK_CALLS 钉死）。"""\n', encoding="utf-8")
        clean_hits = check_tree(tmp)
        if clean_hits:
            failures.append(f"E+ 合规样例被误报：{clean_hits}")
        # E- 违例：手写工具数 + 判据数
        (tmp / "mecha" / "dirty.py").write_text(
            '"""暴露 7 个工具。"""\n# 共 5 条判据\n', encoding="utf-8")
        dirty_hits = check_tree(tmp)
        if not any("7 个工具" in h for h in dirty_hits):
            failures.append(f"E- 未抓到 '7 个工具'：{dirty_hits}")
        if not any("5 条判据" in h for h in dirty_hits):
            failures.append(f"E- 未抓到 '5 条判据'：{dirty_hits}")
    return failures


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    violations = check_tree(ROOT)
    selftest_failures = run_selftest()
    for line in violations:
        print(f"[违例] {line}")
    for line in selftest_failures:
        print(f"[自测失败] {line}")
    if violations:
        print(f"l8_prose_count_selfcheck: FAIL（{len(violations)} 条违例）")
    if selftest_failures:
        print(f"l8_prose_count_selfcheck: FAIL（{len(selftest_failures)} 条自测失败）")
    if not violations and not selftest_failures:
        print("l8_prose_count_selfcheck: PASS（散文无手写增长计数，守卫自测通过）")
    return 1 if (violations or selftest_failures) else 0


if __name__ == "__main__":
    sys.exit(main())
