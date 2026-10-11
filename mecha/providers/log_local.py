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

#: ⭐ **磁盘上保留多少条账**（2026-10-10 用户裁决："磁盘只存储最近一千条，旧的全删"）。
#: ⚠ 这是**保留策略**，不是"只留能显示的"：面板只显示最近 100 条（见 `cockpit.HISTORY_LIMIT`），
#: 盘上留 1000 条是为了"最近发生的事还能查"。
JOURNAL_KEEP = 1000
#: 何时真裁剪：超过 `KEEP * SLACK` 才一次裁回 `KEEP`（⚠ 逐条裁剪 = 每次 append 重写整个文件）。
JOURNAL_TRIM_SLACK = 1.2


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
        self._trim_if_needed()

    def _trim_if_needed(self) -> None:
        """⭐ **保留策略：盘上只留最近 `JOURNAL_KEEP` 条**（超出就裁，旧的真删）。

        ## 为什么这么做（2026-10-10 用户裁决 + 代价如实记）

        用户要求："磁盘只存储最近一千条，旧的全删"。
        ⚠ **代价**：`history.py` 的地基是"append-only：只追加、**永不改写**"，而裁剪必然改写
        ⇒ 这里是**明写的例外**，只做一件事：**只删最旧的那些行、保留其余逐字不变**
        （重写用**临时文件 + 原子替换**，崩溃点看见的是"旧的完整"或"新的完整"，不会是半截）。
        ⚠ 它**不动 seq**：`seq` 仍单调递增（裁掉旧行不重新编号，`last_seq()` 从盘上残余推）。
        ⚠ **不静默**：真裁了就记一行（谁都能在日志里看到"账被裁过"）。

        批量触发（超 `KEEP*SLACK` 才裁回 `KEEP`）⇒ 摊销成本可接受；
        ⚠ 逐条裁剪会在**每次 append** 重写整个文件，那是不能接受的。
        """
        keep = int(JOURNAL_KEEP)
        if keep <= 0:
            return
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        if len(lines) <= int(keep * JOURNAL_TRIM_SLACK):
            return
        tail = lines[-keep:]
        tmp = self._path.with_suffix(self._path.suffix + ".trim")
        try:
            tmp.write_text("\n".join(tail) + "\n", encoding="utf-8")
            tmp.replace(self._path)                      # 原子替换（Windows 上 replace 可覆盖）
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass
            return
        self._records = None                             # 缓存作废（下次 _load 重读）
        print(f"[log] 账已裁剪：只留最近 {keep} 条（删掉 {len(lines) - keep} 条旧的）",
              flush=True)

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
