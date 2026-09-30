"""Log Provider（local 出厂默认）：JSONL 落盘，实现 history.LogSink。

- 文件路径来自注入的 DataLayout（D-6：库不猜目录名）。
- released-format 政策：已提交文件**永不移动/覆盖/删除**；格式头带版本
  （format_version），读取走相邻边迁移链。
- 每行一个 JSON 事件；append 用追加打开 + flush（崩溃尾部由消费方修复，
  与 dsh JSONL provider 同思路）。
- 归档位置只有一个常量（消 D-9）：ARCHIVE_DIR_NAME，且保留策略可注入。

## Known Limitations and Deferred Work

- 不做压缩（zstd 之类等真实体量证据；dsh 的 .jsonl[.zstd] 是它的形状，
  不是我们的契约）。
- subscribe（心跳推送）未实现：v1 的心跳/轮询双值漂移（D-11）教训是"同一
  概念单一来源"——当前 Monitor 按需读，无订阅者，不预建（n=0 不建机制）。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from ..data_layout import DataLayout
from ..errors import MechaError
from ..history import Event
from ..format_version import FORMAT_VERSION, JOURNAL_FILENAME, MigrationChain
from ..write_lease import WriteLease

#: 归档目录名：全库唯一定义点（消 D-9 的"三处分散"）。
ARCHIVE_DIR_NAME = "archive"


class JsonlLogSink:
    """history.LogSink 的 local 实现。

    构造即抢**跨进程写租约**（审查 2.2）：同一数据目录同时只允许一个写宿主，
    第二个抛 `MechaError(kind="writer_lease_held")`。这是 History"seq 单调"
    不变量的地基——两个宿主各写各的会产出重复 seq。`exclusive=False` 留给
    纯读投影（如只读监控进程）绕过租约。
    """

    def __init__(self, layout: DataLayout, chain: MigrationChain | None = None,
                 *, exclusive: bool = True) -> None:
        self._dir = layout.data_dir
        self._path = self._dir / JOURNAL_FILENAME
        self._chain = chain or MigrationChain()
        self._records: list[dict] | None = None  # 惰性读缓存
        self._lease = WriteLease(self._dir) if exclusive else None

    # ---- LogSink 四原语（总纲 §5.2⑤ Log 行）----

    def _encode(self, record: dict) -> str:
        """把一条记录编成落盘行（**格式知识只有这一处**：append 与 precheck 共用）。"""
        return json.dumps({"format": f"v{FORMAT_VERSION}", **record},
                          ensure_ascii=False, separators=(",", ":"))

    def append(self, record: dict) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        line = self._encode(record)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
        if self._records is not None:
            self._records.append(record)

    def precheck(self, record: dict) -> None:
        """**落地之前**的预检：值与 ``append`` 走**同一条编码路径**（格式知识只有一份）。

        ⚠ 它只回答"**这个值能不能编码**"（JSON 可序列化）——磁盘满/权限这类要等真写
        才知道，那些由 Gate 的**回滚**兜（写口"全有或全无"是这两层合起来的效果）。
        ⚠ 它**不建目录、不写文件**（dry-run 不留痕）。
        """
        self._encode(record)

    def since(self, seq: int, *, actor: str | None = None, op: str | None = None,
              limit: int | None = None) -> Iterable[dict]:
        """按序号取（可加 actor / op 筛选与条数上限）。

        ⚠ 字段推理**只用 `Event.from_record`**（老记录也一致）——不许在这里复制第二份。
        """
        out: list[dict] = []
        for rec in self._load():
            if rec["seq"] <= seq:
                continue
            ev = Event.from_record(rec)
            if actor is not None and ev.actor != actor:
                continue
            if op is not None and ev.op != op:
                continue
            out.append(rec)
            if limit is not None and len(out) >= limit:
                break
        return out

    def last_seq(self) -> int:
        recs = self._load()
        return recs[-1]["seq"] if recs else 0

    def subscribe(self, callback) -> "object":
        raise MechaError(
            "local Log Provider 不提供 subscribe",
            kind="unsupported_primitive",
            hint="当前无订阅消费者（D-11 教训：不预建机制）；需要时写 ADR 再接线",
        )

    def close(self) -> None:
        """释放写租约（宿主关停/租约交接用）。幂等。"""
        if self._lease is not None:
            self._lease.release()

    # ---- 读取：格式头 + 相邻边迁移 + 崩溃尾容错 ----

    def _load(self) -> list[dict]:
        if self._records is not None:
            return self._records
        self._records = []
        if not self._path.exists():
            return self._records
        raw: list[dict] = []
        header: str | None = None
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # 崩溃尾部的半行：跳过（修复归消费方）
            if header is None:
                header = str(rec.get("format", "v1"))
            raw.append({k: v for k, v in rec.items() if k != "format"})
        if header is not None:
            raw = self._chain.upgrade(header, raw)
        self._records = raw
        return self._records
