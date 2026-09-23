"""ADR 格式门禁（总纲 §6.1 + ADR制度.md §5，五组 A-E）。

扫描 docs/v2/notes/{proposed,implemented,rejected}/ 下每篇 note：

  A  必填四节齐全；``Alternatives considered`` 非空且不是敷衍的"无"
  B  头部字段（状态/作者/日期）合法；状态字面量与所在目录一致；
     文件名日期与头部日期一致且为真实日期
  C  proposed/ 的 note 不许被 mecha/ 或 src/ 的代码引用（防把提案当定论）
  D  rejected/ 每条必须含"重新考虑条件"（懒否决拒收）
  E  不许误报：合成一篇完全合规的 note 必须 0 命中；
     另附违例语料，每种违例必须至少 1 命中（L7：守卫必须报告变化）

archived/ 是冻结区，本门禁跳过其内容（归档即冻结，制度 §3.4）。

对"不含无"的执行口径：ADR制度 §5 的原文意图是禁止把"无"当作 Alternatives
的答案（"不许写无"）。中文里"无法/无关/无论"是正常词，故判定口径为：
整节为空、或整节（或任一行）剥标点后恰为"无"，才算违例。

用法：python checks/adr_format_selfcheck.py
退出码：0 = 全绿；1 = 有违例或自测失败。

## Known Limitations and Deferred Work

- 组 C 只扫 ``mecha/`` 与 ``src/`` 下 ``.py``；非 .py 资产（HTML/JS 等）的引用
  不在本门禁范围（总纲 D-1 的"扫描面扩到非 .py"属 Phase 4 词汇守卫，不是这里）。
- 组 B 不校验"取代必须继承全部 rationale"（制度 §3.3）——那是跨 note 的
  语义判断，机械不可判，留给人审。
- "无"的判定口径是对 ADR制度 §5 字面（"不含无"）的收窄解释，理由见模块 docstring。
"""

from __future__ import annotations

import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

LIFECYCLES = ("proposed", "implemented", "rejected", "archived")
REQUIRED_SECTIONS = ("Problem", "Decision", "Consequences", "Alternatives considered")
STATUS_RE = re.compile(r"^(proposed|implemented|rejected|archived)(\s+—.*)?$")
FILENAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+)\.md$")
STATUS_LINE_RE = re.compile(r"^>\s*\*\*状态\*\*\s*[:：]\s*(.+?)\s*$")
AUTHOR_LINE_RE = re.compile(r"^>\s*\*\*作者\*\*\s*[:：]\s*\S")
DATE_LINE_RE = re.compile(r"^>\s*\*\*日期\*\*\s*[:：]\s*(\d{4}-\d{2}-\d{2})\s*$")
CODE_SCAN_DIRS = ("mecha", "src")
CODE_SCAN_SUFFIXES = (".py",)


def _sections(text: str) -> dict[str, str]:
    """按 '## 标题' 切分正文，返回 {标题: 节内容}。"""
    out: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            current = m.group(1)
            out.setdefault(current, [])
        elif current is not None:
            out[current].append(line)
    return {k: "\n".join(v) for k, v in out.items()}


def _is_lazily_none(body: str) -> bool:
    """整节剥空白后为空，或任一行剥标点后恰为'无'，视为敷衍答案。"""
    if not body.strip():
        return True
    for line in body.splitlines():
        if re.fullmatch(r"无\s*[。\.！!，,、]*", line.strip()):
            return True
    return False


def validate_note(path: Path, lifecycle: str) -> list[str]:
    """校验单篇 note，返回违例列表（'组别: 说明'）。"""
    v: list[str] = []
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # --- 组 B：文件名与头部 ---
    fm = FILENAME_RE.match(path.name)
    if not fm:
        v.append(f"B: 文件名不符 YYYY-MM-DD-短名.md：{path.name}")
    fn_date = fm.group(1) if fm else None
    if fn_date:
        try:
            datetime.strptime(fn_date, "%Y-%m-%d")
        except ValueError:
            v.append(f"B: 文件名日期不是真实日期：{path.name}")

    if not lines or not lines[0].startswith("# "):
        v.append(f"B: 首行必须是 '# <标题>'：{path.name}")

    status = author = header_date = None
    for line in lines[:12]:
        if m := STATUS_LINE_RE.match(line):
            status = m.group(1)
        elif m := AUTHOR_LINE_RE.match(line):
            author = line
        elif m := DATE_LINE_RE.match(line):
            header_date = m.group(1)
    if status is None:
        v.append(f"B: 缺 '> **状态**：' 行：{path.name}")
    elif not STATUS_RE.match(status):
        v.append(f"B: 状态字面量非法：{status!r}（{path.name}）")
    elif status.split(" — ")[0] != lifecycle:
        v.append(f"B: 状态 {status.split(' — ')[0]!r} 与所在目录 {lifecycle!r} 不一致：{path.name}")
    if author is None:
        v.append(f"B: 缺 '> **作者**：' 行：{path.name}")
    if header_date is None:
        v.append(f"B: 缺 '> **日期**：YYYY-MM-DD' 行：{path.name}")
    else:
        try:
            datetime.strptime(header_date, "%Y-%m-%d")
        except ValueError:
            v.append(f"B: 头部日期不是真实日期：{path.name}")
        if fn_date and fn_date != header_date:
            v.append(f"B: 文件名日期 {fn_date} ≠ 头部日期 {header_date}：{path.name}")

    # --- 组 A：必填四节 ---
    secs = _sections(text)
    for name in REQUIRED_SECTIONS:
        if name not in secs:
            v.append(f"A: 缺必填节 '## {name}'：{path.name}")
    if "Alternatives considered" in secs and _is_lazily_none(secs["Alternatives considered"]):
        v.append(f"A: 'Alternatives considered' 为空或敷衍的'无'：{path.name}")

    # --- 组 D：rejected 必须有重新考虑条件 ---
    if lifecycle == "rejected" and "重新考虑条件" not in text:
        v.append(f"D: rejected note 缺'重新考虑条件'：{path.name}")

    return v


def check_proposed_not_referenced_by_code(repo_root: Path) -> list[str]:
    """组 C：proposed/ 的 note 文件名不许出现在库代码里。"""
    v: list[str] = []
    proposed_dir = repo_root / "docs" / "v2" / "notes" / "proposed"
    stems = [
        p.stem for p in sorted(proposed_dir.glob("*.md")) if p.name != ".gitkeep"
    ] if proposed_dir.is_dir() else []
    if not stems:
        return v
    code_files: list[Path] = []
    for d in CODE_SCAN_DIRS:
        base = repo_root / d
        if base.is_dir():
            code_files.extend(p for p in base.rglob("*") if p.suffix in CODE_SCAN_SUFFIXES)
    for path in code_files:
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for stem in stems:
            if stem in content:
                v.append(f"C: 代码引用了 proposed note {stem!r}（把提案当定论）：{path}")
    return v


def validate_tree(repo_root: Path) -> list[str]:
    """校验一棵完整的 note 树，返回全部违例。"""
    v: list[str] = []
    notes_root = repo_root / "docs" / "v2" / "notes"
    if not notes_root.is_dir():
        return [f"A: note 树不存在：{notes_root}"]
    for lifecycle in LIFECYCLES:
        d = notes_root / lifecycle
        if not d.is_dir():
            v.append(f"B: 缺生命周期目录 {lifecycle}/")
            continue
        if lifecycle == "archived":
            continue  # 归档即冻结，跳过格式校验
        for path in sorted(d.glob("*.md")):
            v.extend(validate_note(path, lifecycle))
    v.extend(check_proposed_not_referenced_by_code(repo_root))
    return v


# ---------------------------------------------------------------------------
# 组 E：自测语料（合成树必须被正确判定，防哑守卫 + 防误报）
# ---------------------------------------------------------------------------

def _synth_note(title: str, status: str, date: str, alternatives: str,
                extra_reject: bool = False) -> str:
    body = f"""# {title}

> **状态**：{status}
> **作者**：自测
> **日期**：{date}

## Problem
合成问题。

## Decision
合成决定。

## Consequences
合成后果。

## Alternatives considered
{alternatives}
"""
    if extra_reject:
        body += "\n**重新考虑条件**：合成条件。\n"
    return body


def run_selftest() -> list[str]:
    """返回自测失败列表（空 = 守卫行为正确）。"""
    failures: list[str] = []
    good = _synth_note("合规样例", "implemented", "2026-09-23", "**否决：样例方案。** 理由：不成立。")

    def build(tmp: Path, lifecycle: str, filename: str, content: str,
              code_ref: str | None = None) -> Path:
        d = tmp / "docs" / "v2" / "notes" / lifecycle
        d.mkdir(parents=True, exist_ok=True)
        (d / filename).write_text(content, encoding="utf-8")
        if code_ref:
            c = tmp / "mecha"
            c.mkdir(parents=True, exist_ok=True)
            (c / "mod.py").write_text(f"# 引用 {code_ref}\n", encoding="utf-8")
        return tmp

    # E+：完全合规必须 0 命中（四种生命周期各测一遍）
    for lifecycle, status, alt, reject in (
        ("implemented", "implemented", "**否决：X。** 理由：Y。", False),
        ("proposed", "proposed", "**否决：X。** 理由：Y。", False),
        ("rejected", "rejected — 一句话理由", "**否决：X。** 理由：Y。", True),
    ):
        with tempfile.TemporaryDirectory() as td:
            root = build(Path(td), lifecycle, "2026-09-23-合规.md",
                         _synth_note("合规", status, "2026-09-23", alt, reject))
            hits = [h for h in validate_tree(root) if "合规.md" in h]
            if hits:
                failures.append(f"E+: {lifecycle}/ 合规样例被误报：{hits}")

    # E-：每种违例必须至少命中一条（违例语料 → 期望组别前缀）
    cases: list[tuple[str, str, str, str, list[str]]] = [
        # (lifecycle, status, filename, content, 期望违例前缀)
        ("implemented", "implemented", "2026-09-23-缺节.md",
         "# 缺节\n\n> **状态**：implemented\n> **作者**：自测\n> **日期**：2026-09-23\n\n## Problem\n有\n\n## Decision\n有\n\n## Alternatives considered\n**否决：X。** 理由：Y。\n",
         ["A: 缺必填节 '## Consequences'"]),
        ("implemented", "implemented", "2026-09-23-空替代.md",
         _synth_note("空替代", "implemented", "2026-09-23", "无。"),
         ["A: 'Alternatives considered'"]),
        ("implemented", "accepted", "2026-09-23-坏状态.md",
         _synth_note("坏状态", "accepted", "2026-09-23", "**否决：X。** 理由：Y。"),
         ["B: 状态字面量非法"]),
        ("implemented", "implemented", "2026-01-02-日期不符.md",
         _synth_note("日期不符", "implemented", "2026-09-23", "**否决：X。** 理由：Y。"),
         ["B: 文件名日期"]),
        ("proposed", "implemented", "2026-09-23-错位.md",
         _synth_note("错位", "implemented", "2026-09-23", "**否决：X。** 理由：Y。"),
         ["B: 状态"]),
        ("rejected", "rejected — 理由", "2026-09-23-懒否决.md",
         _synth_note("懒否决", "rejected — 理由", "2026-09-23", "**否决：X。** 理由：Y。", False),
         ["D: rejected note"]),
    ]
    for lifecycle, status, filename, content, expected in cases:
        with tempfile.TemporaryDirectory() as td:
            root = build(Path(td), lifecycle, filename, content)
            hits = validate_tree(root)
            for prefix in expected:
                if not any(h.startswith(prefix) or prefix in h for h in hits):
                    failures.append(f"E-: 违例 {filename!r} 未被抓到（期望含 {prefix!r}），实际：{hits}")

    # 组 C 违例：proposed note 被代码引用必须红
    with tempfile.TemporaryDirectory() as td:
        root = build(Path(td), "proposed", "2026-09-23-提案.md",
                     _synth_note("提案", "proposed", "2026-09-23", "**否决：X。** 理由：Y。"),
                     code_ref="2026-09-23-提案")
        hits = validate_tree(root)
        if not any(h.startswith("C:") for h in hits):
            failures.append(f"E-: proposed note 被代码引用未被抓到，实际：{hits}")

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
        print(f"adr_format_selfcheck: FAIL（{len(violations)} 条违例）")
    if selftest_failures:
        print(f"adr_format_selfcheck: FAIL（{len(selftest_failures)} 条自测失败）")
    if not violations and not selftest_failures:
        print("adr_format_selfcheck: PASS（note 树合规，守卫自测通过）")
    return 1 if (violations or selftest_failures) else 0


if __name__ == "__main__":
    sys.exit(main())
