"""领域词守卫（总纲 §9 反模式"在库里写死某个项目的目录名/端口/键名/示例值"；D-1/D-3/D-5）。

扫描面按**资产类型**划分，扩到非 .py（消 D-1 根因"三层扫描面都只扫 .py"）：
mecha/ 与 checks/ 下的 .py 源码 + 非 .py 资产（HTML/JS/CSS/JSON/YAML/TOML）。
docs/ 整体排除（ADR note 与审计文档正当引用被否决方案与 v1 值）。

词表在 checks/vocabulary.txt。命中 = 红。

自测（L7：证明它不是哑守卫，也不是永远返红的疯守卫）：
  E+ 合规样例必须 0 命中；E- 含领域词的合成文件必须被抓。

用法：python checks/vocabulary_selfcheck.py
退出码：0 全绿；1 有命中或自测失败。

## Known Limitations and Deferred Work

- 词表是人维护的黑名单：新领域值进库前若没人加词，守卫抓不到（黑名单固有
  局限）。治本是白名单（库内只许中性词），但词法白名单误报率过高，暂不采用。
- 只扫 mecha/ 与 checks/；EL 的领域实现（EL 仓 `mecha_v2` 包，原 examples/
  energy_level，见 ADR 领域参考实现搬回所属项目）**故意不扫**——那里出现项目
  领域值是合法的（§9 反模式禁的是**库里**写死项目值）。
- `checks/phase5_*.py` 显式豁免（EL 消费配置判据，职责就是引用 EL 键）；
  豁免名单是常量，新文件默认仍被扫。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORDLIST = ROOT / "checks" / "vocabulary.txt"
SCAN_DIRS = ("mecha", "checks")
ASSET_SUFFIXES = (".py", ".html", ".js", ".css", ".json", ".yaml", ".yml", ".toml")
#: EL 消费配置判据：它们的**职责**就是拿 EL 领域键跑端到端（ADR 领域参考实现
#: 搬回所属项目）。与 docs/ 排除同理——禁的是"库/通用判据写死项目值"，不是
#: "项目自己的接线判据引用项目值"。豁免是显式白名单，新增文件不会静默获免。
EXEMPT_FILES = frozenset({
    "phase5_el_selfcheck.py",
    "phase5_hub_selfcheck.py",
    "phase5_bridge_selfcheck.py",
})


def load_banned() -> list[str]:
    words: list[str] = []
    for line in WORDLIST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            words.append(line)
    return words


def scan_tree(root: Path, banned: list[str]) -> list[str]:
    hits: list[str] = []
    for d in SCAN_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if path == WORDLIST:            # 词表自身永不扫（含全部禁用词）
                continue
            if path.name in EXEMPT_FILES:   # EL 消费配置判据：领域值合法
                continue
            if path.suffix not in ASSET_SUFFIXES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for w in banned:
                if w in text:
                    rel = path.relative_to(root)
                    hits.append(f"{rel}: 含领域值 {w!r}")
    return hits


def run_selftest() -> list[str]:
    """E+ 不误报 / E- 能抓坏。"""
    import tempfile

    failures: list[str] = []
    banned = load_banned()
    if not banned:
        return ["词表为空——守卫是哑的（L3）"]
    sample_word = banned[0]
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "mecha").mkdir()
        # 合规：中性词
        (tmp / "mecha" / "clean.py").write_text(
            '"""中性 docstring。"""\nVALUE = 42\n', encoding="utf-8")
        # 违例：.py 含领域词
        (tmp / "mecha" / "dirty.py").write_text(
            f'"""用了 {sample_word} 当示例。"""\n', encoding="utf-8")
        # 违例：非 .py 资产含领域词（D-1 的扫描面扩展点）
        (tmp / "mecha" / "index.html").write_text(
            f"<title>{sample_word}</title>\n", encoding="utf-8")
        hits = scan_tree(tmp, banned)
        clean_hit = [h for h in hits if "clean.py" in h]
        dirty_py = [h for h in hits if "dirty.py" in h]
        dirty_html = [h for h in hits if "index.html" in h]
        if clean_hit:
            failures.append(f"E+: 合规文件被误报：{clean_hit}")
        if not dirty_py:
            failures.append("E-: .py 里的领域词没被抓到（哑守卫）")
        if not dirty_html:
            failures.append("E-: 非 .py 资产里的领域词没被抓到（扫描面没扩，D-1 复发）")
    return failures


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    banned = load_banned()
    hits = scan_tree(ROOT, banned)
    failures = run_selftest()
    for h in hits:
        print(f"[命中] {h}")
    for f in failures:
        print(f"[自测失败] {f}")
    if hits:
        print(f"vocabulary_selfcheck: FAIL（{len(hits)} 处领域值泄漏）")
    if failures:
        print(f"vocabulary_selfcheck: FAIL（{len(failures)} 条自测失败）")
    if not hits and not failures:
        print("vocabulary_selfcheck: PASS")
    return 1 if (hits or failures) else 0


if __name__ == "__main__":
    sys.exit(main())
