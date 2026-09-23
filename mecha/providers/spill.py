"""Spill Provider：大载荷落盘 + 不透明定位符（总纲 §5.2⑤ Spill 行）。

Definition：把超过阈值的大文本落到盘上，回一个**不透明定位符** + 取回指引。
消费者**不许假设本地路径**（消 D-10：v1 的 _MAX_JOURNAL_BYTES/截断头没人标血统）。
local 实现写文件；dsh 侧用 ``ctx.spillStore``（注入宿主句柄）。

## Known Limitations and Deferred Work

- 定位符是 ``$<id>`` 形式的不透明句柄；local 实现内部映射到文件名，但对外
  不暴露路径（消费者拿 $id 调 retrieve）。
- 体积阈值可注入（不写死 8MiB，消 D-10）；默认值在此声明为 DEFAULT_SPILL_BYTES，
  是 [EL 派生] 待第二项目确认的血统值。
- 不做自动 GC（归档/清理归 gc 策略，Phase 5 随 EL 消费配置定）。
"""

from __future__ import annotations

import itertools
from pathlib import Path

from ..data_layout import DataLayout
from ..errors import MechaError

#: [EL 派生] 体积阈值默认值，等第二个项目确认（消 D-10：从散落常量收成单点）。
DEFAULT_SPILL_BYTES = 8 * 1024 * 1024


class SpillStore:
    """Spill Definition（具体服务，不是接口）。"""

    def put(self, text: str) -> str:
        raise NotImplementedError

    def retrieve(self, locator: str) -> str:
        raise NotImplementedError

    def should_spill(self, text: str) -> bool:
        raise NotImplementedError


class LocalSpillStore(SpillStore):
    """local 出厂默认：落文件，回 $id 不透明句柄。"""

    def __init__(self, layout: DataLayout, *, threshold_bytes: int = DEFAULT_SPILL_BYTES) -> None:
        self._dir = layout.data_dir / "spill"
        self._threshold = threshold_bytes
        self._ids = itertools.count(1)
        self._map: dict[str, Path] = {}

    def should_spill(self, text: str) -> bool:
        return len(text.encode("utf-8")) > self._threshold

    def put(self, text: str) -> str:
        self._dir.mkdir(parents=True, exist_ok=True)
        n = next(self._ids)
        sid = f"${n}"
        path = self._dir / f"spill-{n}.txt"
        path.write_text(text, encoding="utf-8")
        self._map[sid] = path
        return sid

    def retrieve(self, locator: str) -> str:
        path = self._map.get(locator)
        if path is None or not path.exists():
            raise MechaError(
                f"定位符 {locator!r} 无对应载荷",
                kind="bad_locator",
                hint="定位符由 put 返回；不透明，消费者不得自行拼路径",
            )
        return path.read_text(encoding="utf-8")
