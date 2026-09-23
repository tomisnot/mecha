"""格式版本化与迁移制度（总纲 §6.4；制度登记见 ADR 格式版本化与迁移制度）。

四件套的机制核心：

1. ``FORMAT_VERSION``：当前持久格式版本；只有结构性变化才 bump。
2. 相邻边迁移链：迁移只声明 ``vN -> vN+1``；读取时组合相邻链，旧版本
   不隐含回退、不隐含降级。
3. released-format 政策：已发布磁盘产物不许移动/覆盖/删除；迁移在旁边
   排他发布新版本命名的后继（由 history 的 provider 执行）。
4. corpus 拒识清单：按来源列出故意不支持的历史转换 + 确切拒识原因。

## Known Limitations and Deferred Work

- 迁移函数签名是 ``list[dict] -> list[dict]``（事件记录表）。若未来格式
  不再是逐行 JSON（如换二进制），迁移链要扩签名——届时写取代 ADR。
- 拒识清单按 (来源格式, 目标版本) 精确匹配，不做前缀/通配；来源少时刻意
  保持严格（宁缺勿滥猜）。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from .errors import MechaError

#: 当前 History/快照格式版本。只有结构性格式变化才允许修改此值。
FORMAT_VERSION: int = 1

#: journal 文件名（v1 起）。released-format 政策：已发布文件名永不改。
JOURNAL_FILENAME: str = "journal.jsonl"

_HEADER_RE = re.compile(r"^v(\d+)$")


def parse_format_header(header: str) -> int:
    """解析格式头（如 ``'v1'``）为版本号；坏格式明确报错。"""
    m = _HEADER_RE.match(header.strip())
    if not m:
        raise MechaError(
            f"无法识别的格式头 {header!r}",
            kind="bad_format_header",
            hint="格式头必须是 'vN'（如 'v1'）；见 format_version.FORMAT_VERSION",
        )
    return int(m.group(1))


MigrationStage = Callable[[list[dict]], list[dict]]
"""相邻边迁移：把 vN 的事件记录表改写为 vN+1 的事件记录表。"""


@dataclass
class RejectedConversion:
    """corpus 拒识清单的一行：故意不支持的转换 + 确切原因。"""

    source: str
    target: int
    reason: str


class MigrationChain:
    """相邻边 Stage 迁移链。只注册 vN→vN+1 单步；组合发生在读取时。"""

    def __init__(self) -> None:
        self._edges: dict[int, MigrationStage] = {}
        self._rejected: list[RejectedConversion] = []

    def add_stage(self, from_version: int, stage: MigrationStage) -> None:
        """注册一条相邻边；重复注册同一条边报错（防止静默覆盖已发布迁移）。"""
        if from_version in self._edges:
            raise MechaError(
                f"迁移边 v{from_version}->v{from_version + 1} 已注册",
                kind="duplicate_migration_edge",
                hint="一条边只能有一个实现；取代请写新 ADR 并换链实例",
            )
        self._edges[from_version] = stage

    def reject(self, source: str, target: int, reason: str) -> None:
        """登记一条故意不支持的转换（corpus 拒识清单）。"""
        self._rejected.append(RejectedConversion(source, target, reason))

    def rejection_corpus(self) -> list[RejectedConversion]:
        return list(self._rejected)

    def upgrade(self, header: str, events: list[dict]) -> list[dict]:
        """把 (格式头, 事件表) 升到当前版本。

        - 未来版本：拒绝（降级不支持）。
        - 缺相邻边：拒绝并给出确切原因（含拒识清单匹配）。
        """
        version = parse_format_header(header)
        if version > FORMAT_VERSION:
            raise MechaError(
                f"磁盘格式 v{version} 比本库支持的 v{FORMAT_VERSION} 更新",
                kind="future_format",
                hint="降级不支持；升级本库或换用产生该格式的库版本",
            )
        probe = RejectionProbe(self._rejected)
        hit = probe.match(header, version)
        if hit is not None:
            raise MechaError(
                f"该来源的历史被故意拒绝：{hit.reason}",
                kind="rejected_conversion",
                hint="见 MigrationChain.rejection_corpus() 的登记",
            )
        while version < FORMAT_VERSION:
            stage = self._edges.get(version)
            if stage is None:
                raise MechaError(
                    f"缺少迁移边 v{version}->v{version + 1}，无法从 v{version} 读取",
                    kind="missing_migration_edge",
                    hint="相邻边链必须完整；旧版本不隐含跳级回退",
                )
            events = stage(events)
            version += 1
        return events


@dataclass
class RejectionProbe:
    """按 (来源头, 目标版本) 精确匹配拒识清单。"""

    entries: list[RejectedConversion] = field(default_factory=list)

    def match(self, header: str, version: int) -> RejectedConversion | None:
        for e in self.entries:
            if e.source == header and e.target == version:
                return e
        return None
