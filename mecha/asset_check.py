"""共享资产的**判据件**：给"逐字副本 + 指纹表"这类资产用的通用守卫。

## 为什么它在框架里

框架随包发一份**共享资产**（`mecha/dsh-panel/`，宿主侧 TS 源码），项目按"**除参数块外逐字复制**"
的约定把它抄进自己的仓。这条约定有两条**与项目无关**的机械要求：

1. **副本没被就地改** ⇒ 逐件比对正文指纹（`\\r\\n` 归一，否则行尾配置会打败逐字节比对）；
2. **副本的共享单测真的跑了** ⇒ 跑 `node --test` 并断言 `fail == 0` **且 `skipped == 0` 且 `pass >= 下限`**。

⚠ 第 2 条里"`skipped == 0`"是**假绿防线**：跑起来了 ≠ 用例真的跑了。
（真缺陷史：某项目的资产判据第一版漏了 `skipped`，把"全被跳过"判成了 PASS。）

**两件都是"资产约定"的性质，不是项目的性质** ⇒ 每个接入项目各写一遍
＝ **N 份会各自漂移的守卫**（实测：两个项目各写了 218 行同功能的判据，
形态还不同——一个用 `Checker`+`CARRIERS` 表，一个用 pytest 三函数）。
按"库拥有规则，宿主拥有名字"（同 `portfile.py`）：**本模块拥有规则，项目只给表与路径。**

## 本模块**不含**任何项目值

不持文件名、不持路径、不持指纹、不持用例下限 —— 全部由调用方传入。
项目侧只留：**资产目录** 、**指纹表**（`{相对路径: sha256}`）、**要跑的用例文件**、**下限**。

## 用法（项目侧只剩一张表）

```python
from mecha.asset_check import audit_carriers, run_node_suite

def test_copies_match():
    bad = audit_carriers(CARRIERS, lambda n: (ASSET_DIR / n).read_bytes())
    assert not bad, bad

def test_shared_suite_really_ran():
    r = run_node_suite(ASSET_DIR, SHARED_TESTS, min_pass=10)
    assert r.ok, r.detail
```

## Known Limitations and Deferred Work

- **只支持"正文哈希"一种比对**：不做 AST / 语义比对，也不做"允许的差异"白名单
  （参数块这类例外由调用方**不放进表**来表达——那是既有约定，不给它加机制）。
- **`run_node_suite` 只认 `node --test` 的 TAP 汇总行**（`ℹ tests/pass/fail/skipped`）。
  ⚠ 这是**口径依赖**：Node 改了汇总行格式，本模块要跟着改（兜底是"解析不到即红"，
  不会静默放行——见 `ok` 的判定）。
- **不决定"该钉哪些文件"**：给少了就是漏钉，这是调用方的判断，本模块不替它猜。
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "text_sha256",
    "audit_carriers",
    "NodeSuiteResult",
    "run_node_suite",
    "parse_node_counts",
]


def text_sha256(data: bytes) -> str:
    """**正文**指纹：按 UTF-8 解码、``\\r\\n`` 归一成 ``\\n`` 后取 sha256。

    ⚠ 为什么必须归一化行尾：`git` 的 `core.autocrlf` 会让"同一内容"在不同检出状态下
    字节不同（有的仓提交时 LF→CRLF）⇒ **逐字节比对会被行尾配置打败**。
    比正文才是"内容一致"的正确口径。

    ⚠ 解码失败（非法 UTF-8）⇒ 按 `errors="replace"` 处理，**不抛**：
    那条路径的结果必然与期望指纹不等 ⇒ 判据照样红，且给出的是"某文件不符"这种可读结论。
    """
    text = data.decode("utf-8", errors="replace").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def audit_carriers(
    carriers: Mapping[str, str],
    read: Callable[[str], bytes],
) -> list[str]:
    """逐件比对指纹表，返回**不符清单**（空 = 全符）。

    :param carriers: ``{相对路径: 期望的正文 sha256}``（**项目给的表**）
    :param read: ``相对路径 -> bytes``；读不到时**抛错**（缺文件必须响亮，不许当成"符"）

    ⚠ 缺文件时**由 `read` 抛**、本函数不吞 —— "副本缺失"与"副本被改"都要红，
    而"读不到就跳过"正是 `except: continue` 那类静默丢成员（R15）。
    """
    drifted: list[str] = []
    for name, expected in carriers.items():
        actual = text_sha256(read(name))
        if actual != expected:
            drifted.append(f"{name}: 期望 {expected[:12]}… 实得 {actual[:12]}…")
    return drifted


#: `node --test` 的 TAP 汇总行：``ℹ tests 42`` / ``ℹ pass 42`` / ``ℹ fail 0`` / ``ℹ skipped 0``。
_COUNT_RE = re.compile(r"^\u2139\s+(tests|pass|fail|skipped)\s+(\d+)\s*$", re.M)


def parse_node_counts(output: str) -> dict[str, int]:
    """从 `node --test` 输出里解析四个计数。**解析不到就是空表**（由调用方判红）。"""
    return {m.group(1): int(m.group(2)) for m in _COUNT_RE.finditer(output)}


@dataclass(frozen=True)
class NodeSuiteResult:
    """一次 `node --test` 的结果（**含自证所需的全部计数**）。"""

    ok: bool
    detail: str
    counts: dict[str, int]
    output: str

    @property
    def summary(self) -> str:
        c = self.counts
        return (f"tests={c.get('tests', 0)} pass={c.get('pass', 0)} "
                f"fail={c.get('fail', 0)} skipped={c.get('skipped', 0)}")


def run_node_suite(
    workdir: str | Path,
    files: Iterable[str],
    *,
    min_pass: int,
    node: str = "node",
    timeout: float = 300.0,
    env: Mapping[str, str] | None = None,
) -> NodeSuiteResult:
    """跑共享单测并**自证"用例真的跑了"**。

    判定（四条全过才 `ok`）：

    1. 进程退出码为 0；
    2. **解析到了**汇总行（解析不到 ⇒ 红：格式变了也不许静默放行）；
    3. ``fail == 0``；
    4. ⭐ ``skipped == 0`` **且** ``pass >= min_pass`` —— 这一条防的是**假绿**：
       "跑起来了但没有用例真的跑"是最坏的一种绿（全被跳过时 `fail` 也是 0）。

    :param min_pass: **项目给的下限**（用例数的下界）。⚠ 它**不许大于**当前真实用例数，
        否则资产一加用例、判据反而红；它是"防塌缩"的下限，不是精确计数。
    """
    file_list = [str(f) for f in files]
    cmd = [node, "--test", *file_list]
    try:
        proc = subprocess.run(
            cmd, cwd=str(workdir), capture_output=True,
            # ⚠ **必须显式 utf-8 + errors="replace"**（2026-10-01 实测缺陷）：
            # `text=True` 会用**系统区域编码**解码（Windows 上是 GBK），而 `node --test`
            # 输出 UTF-8 ⇒ 读取线程直接 UnicodeDecodeError、stdout 变空 ⇒
            # 本函数会把"跑得很好"误判成"解析不到汇总行"。
            # `errors="replace"` 是第二层保险：**坏字节不该让守卫崩**，只该让它如实判红。
            encoding="utf-8", errors="replace",
            timeout=timeout, env=dict(env) if env is not None else None,
        )
    except FileNotFoundError:
        return NodeSuiteResult(False, f"缺 {node} ⇒ 红（不软跳过：软跳过就是'存在但从不执行'）", {}, "")
    except subprocess.TimeoutExpired:
        return NodeSuiteResult(False, f"`{node} --test` 超时（{timeout}s）", {}, "")

    output = (proc.stdout or "") + (proc.stderr or "")
    counts = parse_node_counts(output)
    if not counts:
        return NodeSuiteResult(False, f"解析不到汇总行（格式变了？退出码 {proc.returncode}）", counts, output)

    npass = counts.get("pass", 0)
    nfail = counts.get("fail", 0)
    nskip = counts.get("skipped", 0)
    if proc.returncode != 0 or nfail:
        return NodeSuiteResult(False, f"用例失败：{nfail} 条（退出码 {proc.returncode}）", counts, output)
    if nskip:
        return NodeSuiteResult(False, f"有 {nskip} 条被跳过 ⇒ 视为红（'跑起来了' ≠ '用例真的跑了'）", counts, output)
    if npass < min_pass:
        return NodeSuiteResult(
            False, f"实跑 {npass} 条 < 下限 {min_pass} ⇒ 判据疑似被掏空", counts, output)
    return NodeSuiteResult(True, f"自测 {npass} 条全过，skipped=0（下限 {min_pass}）", counts, output)
