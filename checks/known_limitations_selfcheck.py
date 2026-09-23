"""Known Limitations 门禁（总纲 §6.2）。

每个模块（``mecha/`` 与 ``checks/`` 下的 ``.py`` 文件）的模块 docstring 必须含
``## Known Limitations and Deferred Work`` 段，且段内有非空内容——强制每个模块
自述它不擅长什么（治"血统标签自己也会漂"）。

用法：python checks/known_limitations_selfcheck.py
退出码：0 = 全绿；1 = 有违例或自测失败。

## Known Limitations and Deferred Work

- 只认模块级 docstring；把 Limitations 写在 README 或注释里不算数（刻意如此：
  自述必须离代码最近）。
- 空段（只有标题没有内容）算违例；"无"作为段内容同样算违例——没有局限的模块
  不存在，写"无"即敷衍。
- 本门禁不校验段内容是否属实（机械不可判，人审）。
"""

from __future__ import annotations

import ast
import re
import sys
import tempfile
from pathlib import Path

SECTION = "## Known Limitations and Deferred Work"
SCAN_DIRS = ("mecha", "checks")


def _is_lazily_none(body: str) -> bool:
    if not body.strip():
        return True
    for line in body.splitlines():
        if re.fullmatch(r"无\s*[。\.！!，,、]*", line.strip()):
            return True
    return False


def check_module(path: Path, repo_root: Path) -> list[str]:
    """单文件校验：模块 docstring 含 Limitations 段且非空。"""
    rel = path.relative_to(repo_root)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as e:
        return [f"X: 无法解析（SyntaxError: {e}）：{rel}"]
    doc = ast.get_docstring(tree)
    if doc is None:
        return [f"缺模块 docstring：{rel}"]
    if SECTION not in doc:
        return [f"缺 '{SECTION}' 段：{rel}"]
    after = doc.split(SECTION, 1)[1]
    # 段内容 = 到下一个 '## ' 标题（或文末）
    body = re.split(r"^##\s", after, maxsplit=1, flags=re.MULTILINE)[0]
    if _is_lazily_none(body):
        return [f"'{SECTION}' 段为空或敷衍的'无'：{rel}"]
    return []


def validate_tree(repo_root: Path) -> list[str]:
    v: list[str] = []
    for d in SCAN_DIRS:
        base = repo_root / d
        if base.is_dir():
            for path in sorted(base.rglob("*.py")):
                v.extend(check_module(path, repo_root))
    return v


def run_selftest() -> list[str]:
    """组 E 同款：合规样例 0 命中，违例语料必须被抓。"""
    failures: list[str] = []
    cases = {
        "good.py": '"""模块。\n\n## Known Limitations and Deferred Work\n\n- 只测装配不测单元。\n"""\n',
        "nodoc.py": "x = 1\n",
        "nosec.py": '"""模块。\n\n## 别的段\n有内容。\n"""\n',
        "empty.py": '"""模块。\n\n## Known Limitations and Deferred Work\n"""\n',
        "wu.py": '"""模块。\n\n## Known Limitations and Deferred Work\n无。\n"""\n',
    }
    expected = {
        "good.py": 0, "nodoc.py": 1, "nosec.py": 1, "empty.py": 1, "wu.py": 1,
    }
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "mecha").mkdir()
        for name, content in cases.items():
            (root / "mecha" / name).write_text(content, encoding="utf-8")
        hits = validate_tree(root)
        for name, want in expected.items():
            got = len([h for h in hits if h.endswith(f"：mecha\\{name}") or h.endswith(f"：mecha/{name}")])
            if got != want:
                failures.append(f"E: {name} 期望 {want} 命中，实际 {got}；全部命中：{hits}")
    return failures


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    repo_root = Path(__file__).resolve().parent.parent
    violations = validate_tree(repo_root)
    selftest_failures = run_selftest()

    for line in violations:
        print(f"[违例] {line}")
    for line in selftest_failures:
        print(f"[自测失败] {line}")
    if violations:
        print(f"known_limitations_selfcheck: FAIL（{len(violations)} 条违例）")
    if selftest_failures:
        print(f"known_limitations_selfcheck: FAIL（{len(selftest_failures)} 条自测失败）")
    if not violations and not selftest_failures:
        print("known_limitations_selfcheck: PASS")
    return 1 if (violations or selftest_failures) else 0


if __name__ == "__main__":
    sys.exit(main())
