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
- 不做"键名等价推断"（b_field ≡ field.B_gauss 靠猜是假绿的温床）；要人话
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


class Monitor:
    """读面：只读，无写口。构造时只拿得到 History 事件的只读访问。"""

    def __init__(self, history_events: Callable[[], list[Event]],
                 summarizer: Summarizer) -> None:
        self._events = history_events
        self._summarize = summarizer

    def read(self) -> MonitorView:
        events = self._events()
        raw = fold(events)
        summary = dict(self._summarize(events))
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
