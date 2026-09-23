"""Gate：操作者无关的唯一写入门（总纲 §5.2①，L1 + 硬纪律 1/4）。

唯一变更口是 ``set``，四步顺序不可换：

    authority.gate(actor) → validate(key, value) → 落到执行器 → History.append(...)

- ⭐ 门在**执行器**上，不在包装层：快照只有 ``Gate`` 持有，外部拿不到可写引用，
  任何绕过 ``set`` 的直写都没有入口（判据经执行器测拒绝）。
- ``actor`` 由**通道**决定：``Channel`` 在宿主装配时钉死 actor，请求内容改不了它
  （硬纪律 4）⇒ AI 无法传 ``actor='human'`` 伪造人类归因或借此拿写权。
- 报错时快照一个字节都没动（validate 在落地之前）。

## Known Limitations and Deferred Work

- ``validate`` 是**必填**注入（``Callable[[key, value], None]``，项目声明键的
  合法值）。真要"任何值都合法"必须显式传 ``accept_all``——库不静默放行
  （审查 P5-2：与"库不猜"同纪律；装配点漏传校验器是静默坏事的温床）。
- 无 ``set_many``：v2 实测零调用方，按"死契约删除"裁决（ADR
  2026-09-23-set-many-裁决）。存档恢复等批量场景接线时，带真实调用方重写
  原子性 ADR，并继承 v1 教训（20 份旧存档靠"先全校验再全落地"救过）。
- 不内置 one-shot 审批：需要"每次写都问人"时，把 approval 包在 validate 里
  （validate 抛 GateDenied 即拒绝），而非在 Gate 里加审批分支（保持门单一职责）。
- 物理演化（run）不在本模块：那是 Surface 的另一条 executor（洞 2）。Gate 只管
  域状态写。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .authority import Authority
from .errors import GateDenied, MechaError
from .history import Event, History

Validator = Callable[[str, object], None]


def accept_all(key: str, value: object) -> None:
    """显式"任何值都合法"的校验器哨兵（不传 validate 会报错，逼你表态）。"""
    return None


@dataclass(frozen=True)
class Channel:
    """一条操作者通道。actor 在构造时钉死，frozen ⇒ 请求内容改不了它。

    只接受已知操作者类别；宿主装配时创建 Channel('human') / Channel('ai')。
    """

    actor: str

    def __post_init__(self) -> None:
        if self.actor not in ("human", "ai"):
            raise GateDenied(
                f"未知通道 actor={self.actor!r}",
                kind="bad_channel",
                hint="actor 由通道类型决定，只能是 'human' 或 'ai'（宿主装配时钉死）",
            )


class Gate:
    """写入门。持有快照 + 域状态史，唯一写口是 set。"""

    def __init__(
        self,
        authority: Authority,
        history: History,
        *,
        validate: Validator,
    ) -> None:
        if validate is None:
            raise MechaError(
                "Gate 必须注入 validate；不校验请显式传 accept_all",
                kind="missing_validator",
                hint="隐式放行一切值与『库不猜』同纪律相反（审查 P5-2）",
                suggest="Gate(authority, history, validate=accept_all)",
            )
        self._authority = authority
        self._history = history
        self._validate = validate
        self._snapshot: dict[str, object] = history.snapshot()

    @property
    def snapshot(self) -> dict[str, object]:
        """只读投影（返回副本；外部改它不回写门）。"""
        return dict(self._snapshot)

    def set(self, channel: Channel, key: str, value: object, reason: str,
            call_id: str = "") -> Event:
        """四步变更口。返回落地的 History 事件。

        ``call_id`` 是当次操作的调用标识（AI 侧来自 dsh 的 tool callId，人类
        侧来自 GUI），写进 History 供与操作者行为史双向互引（总纲 §5.2②）。
        """
        # 1) authority.gate —— actor 由通道决定，不看请求内容
        self._authority.gate(channel.actor)
        # 2) validate —— 在落地之前，失败则快照一字节不动
        self._validate(key, value)
        # 3) 落到执行器 —— 快照是 Gate 私有，只有这里能改
        self._snapshot[key] = value
        # 4) History.append —— 落地之后才记，带当次 call_id 供互引
        return self._history.append(key, value, channel.actor, reason, call_id)
