"""Monitor：人类监控面（总纲 §5.2④，第一交付物；硬纪律 5）。

与 Gate/History/Surface 并列的第一层。**只读，无任何写回路径**（写回 ADR 已
否决 Monitor 写回）。两层信任：

- **概括层**（derived）：我们写的、给人看的、可能出错 ⇒ 必须标明派生。
- **原始层**（authoritative）：History 本身，append-only，不随 dsh 生死。

唯一硬纪律：概括必须能从原始推出；两者不一致时**原始赢**，且概括必须显示为
存疑（``disputed``）。

## Known Limitations and Deferred Work

- 概括函数是注入的纯函数（``Callable[[list[Event]], Mapping[str, object]]``）；
  本模块不内置任何领域概括（那是项目 Provider 的事）。
- 不提供 UI：Monitor 是后端读面（v1 cockpit/backend.py 方向），GUI 归项目。
- 对账口径：概括里凡是 ``Claim``（对某键值的复述），其值与原始折叠比对；
  ``Claim(value, target=原始键)`` 可显式指向原始键（人话概括也可对账，审查
  P3-b），省略 target 则用概括键名本身。target 不存在或值不等 ⇒ 原始赢、
  整份读数存疑。自由描述文本不比对（机械不可判其属实性，人审）。
- 不做"键名等价推断"（把两个不同写法的键名靠猜当同一个是假绿的温床）；要人话
  键名可对账，必须显式写 Claim(target=...)。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .history import Event, fold

Summarizer = Callable[[list[Event]], Mapping[str, object]]


@dataclass(frozen=True)
class Claim:
    """概括层对某个键值的复述——可被原始层证伪的最小单位。

    ``target`` 指定它复述的**原始键名**（人话概括用，审查 P3-b）；省略则用
    概括字典里的键名本身。
    """

    value: object
    target: str = ""


@dataclass(frozen=True)
class MonitorView:
    """一次监控读数：两层并列 + 存疑标记。"""

    summary: Mapping[str, object]
    raw_snapshot: Mapping[str, object]
    #: 概括层与原始层不可调和时为 True（原始赢，概括显示存疑）
    disputed: bool
    dispute_reason: str = ""
    #: **概括层故障**的人可读说明（正常为空）。它让"**故障**"与"**撒谎**"在 wire 上
    #: 可区分 —— 两者都是 `disputed=True`，但原因不同（2026-09-26 加）。
    summary_error: str = ""


class Monitor:
    """读面：只读，无写口。构造时只拿得到 History 事件的只读访问。"""

    def __init__(self, history_events: Callable[[], list[Event]],
                 summarizer: Summarizer) -> None:
        self._events = history_events
        self._summarize = summarizer

    def read(self) -> MonitorView:
        """读一次：**概括层故障 ⇒ 降级读数**（不整体抛），原始层照常给出。

        ⚠ 为什么不再抛（2026-09-26 改）：硬纪律是"概括与原始不可调和 ⇒ **原始赢**、
        概括标存疑"，而"**概括根本没出来**"是它的**极端情形** ⇒ 原始层照赢、概括照标存疑。
        此前 summarizer 抛错会让**整个读面**一起不可读（旧 Known Limitations 登记为
        "未支持"，现已支持）。⇒ 补偿：`summary_error` 明文写清故障，**不让它变成静默降级**。
        """
        events = self._events()
        raw = fold(events)
        try:
            summary = dict(self._summarize(events))
        except BaseException as e:                      # noqa: BLE001 - 概括层故障 ⇒ 降级
            why = f"概括层故障：{type(e).__name__}: {e}"
            return MonitorView({}, raw, True, why, why)
        for k, v in summary.items():
            if isinstance(v, Claim):
                target = v.target or k
                if target not in raw:
                    return MonitorView(summary, raw, True,
                                       f"概括声称键 {target!r}，原始史里没有")
                if v.value != raw[target]:
                    return MonitorView(summary, raw, True,
                                       f"键 {target!r} 概括={v.value!r} 与原始={raw[target]!r} 不一致")
        return MonitorView(summary, raw, False)
