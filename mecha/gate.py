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
- ``set_batch`` 是**原子**的（先全校验再全落地，任一键非法一个都不写）：
  真实调用方 = GUI 存档恢复（v1 的 20-存档教训）。无 set_many——批量只有
  原子一种形态（ADR set-many-裁决 + set-batch-原子批量）。
- 不内置 one-shot 审批：需要"每次写都问人"时，把 approval 包在 validate 里
  （validate 抛 GateDenied 即拒绝），而非在 Gate 里加审批分支（保持门单一职责）。
- 物理演化（run）不在本模块：那是 Surface 的另一条 executor（洞 2）。Gate 只管
  域状态写。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

from .authority import Authority
from .errors import GateDenied, MechaError
from .history import Event, History

Validator = Callable[[str, object], None]

#: 快照里"**这个键本来不存在**"的哨兵。⚠ 不能用 `None` 代替：`None` 是**合法值**
#: （EL 的"留空=回默认"就是写 `None`）⇒ 回滚时两者必须能区分。
_MISSING = object()


def accept_all(key: str, value: object) -> None:
    """显式"任何值都合法"的校验器哨兵（不传 validate 会报错，逼你表态）。"""
    return None


@dataclass(frozen=True)
class Channel:
    """一条操作者通道。**side**（human/ai）定写权，**actor**（标签）进史做归因。

    side 在构造时钉死，frozen ⇒ 请求内容改不了它（硬纪律 4）。actor 默认等于
    side；宿主可给更细的标签（'gui'/'mcp'/'codeact'）——消费方（如 GUI 面板）
    按标签跳过自刷新、做来源高亮。标签不扩写权面：ai 侧标签拿不到 human 的闸。
    """

    actor: str
    side: str = ""

    def __post_init__(self) -> None:
        side = self.side or self.actor
        if side not in ("human", "ai"):
            raise GateDenied(
                f"未知通道 side={side!r}（actor={self.actor!r}）",
                kind="bad_channel",
                hint="side 只能是 'human' 或 'ai'（宿主装配时钉死）；"
                     "细标签放 actor（如 Channel('gui','human')）",
            )
        if not self.actor or not self.actor.strip():
            raise GateDenied("通道标签不能为空", kind="bad_channel",
                             hint="Channel('gui','human')：标签非空")
            # frozen dataclass：用 object.__setattr__ 填默认 side
        object.__setattr__(self, "side", side)


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

    def check(self, channel: Channel) -> None:
        """**只检查**写权：此刻这个侧能不能写（`set` 的第 1 步，单独暴露）。

        ⚠ 为什么需要这个口子：**副作用不一定发生在 Gate 里**——域状态外置的项目
        （外置库/外部 API）的副作用在命令体里，而写权检查**必须发生在任何副作用之前**
        （否则"改动已落地、只有审计失败"= 静默越权）。所以调用方需要一个
        "只检查、不落地"的入口。

        `set` 自己也调它 ⇒ **这套判定的语义只有一份**（不许复制出第二份）。
        """
        self._authority.gate(channel.side)

    def record(self, key: str, value: object, actor: str, reason: str,
               call_id: str = "", *, undoable: bool | None = None) -> Event:
        """**只记史、不改快照**（`set` 的第 4 步，单独暴露）。

        ⚠ 为什么需要这个口子：**审计事件属于事件史，不属于域状态快照**
        （`command.<name>` 不是配置键）。它此前借 `gate.set` 落史，于是**混进了快照**
        ——在 `/config` 视图里变成"契约外的键（orphans）"，而快照的"后写覆盖"语义
        对审计**本来就没有意义**（同名命令只剩最后一次）。`set` 自己也调它 ⇒ 这一步
        **只有一份语义**。
        """
        return self._history.append(key, value, actor, reason, call_id,
                                    undoable=undoable)

    def validate_only(self, key: str, value: object) -> None:
        """**dry-run**：这个值会被写口接受吗（校验 + 预检）——**不落地、不记史**。

        用途：调用方想"先试再写"时**不必复刻宿主的校验规则**。
        ⚠ 它**不检查写权**（那是 :meth:`check` 的事）——写权与"值合不合法"是两件事。
        """
        self._validate(key, value)
        self._history.precheck(key, value)

    def set(self, channel: Channel, key: str, value: object, reason: str,
            call_id: str = "", *, undoable: bool | None = None) -> Event:
        """四步变更口（**全有或全无**）。返回落地的 History 事件。

        ``call_id`` 是当次操作的调用标识（AI 侧来自 dsh 的 tool callId，人类
        侧来自 GUI），写进 History 供与操作者行为史双向互引（总纲 §5.2②）。

        ⚠ **失败时序**（2026-09-26 起）：前三步（写权 / 校验 / 预检）都在**碰快照之前**
        ⇒ 它们的失败**一个字节都不动**；第 4 步（记史）万一失败 ⇒ **快照回滚**到本次
        之前再抛（否则快照会与史不一致——而"快照必须能从史折叠出来"是硬不变量）。
        """
        # 1) authority.gate —— 写权看 side（human/ai），不看请求内容；actor 标签不扩权
        self.check(channel)
        # 2) validate —— 在落地之前，失败则快照一字节不动
        self._validate(key, value)
        # 3) 预检（可预见的落地期失败，如"值不可序列化"）—— 同样在落地之前
        self._history.precheck(key, value)
        # 4) 落到执行器 —— 快照是 Gate 私有，只有这里能改
        prev = self._snapshot.get(key, _MISSING)
        self._snapshot[key] = value
        try:
            # 5) History.append —— 落地之后才记，带当次 call_id 供互引
            return self.record(key, value, channel.actor, reason, call_id,
                               undoable=undoable)
        except BaseException:
            # 写口"全有或全无"：记史没成 ⇒ 快照回到本次之前（含"本来没这个键"）
            if prev is _MISSING:
                self._snapshot.pop(key, None)
            else:
                self._snapshot[key] = prev
            raise

    def seed(self, key: str, value: object, actor: str, reason: str = "seed",
             call_id: str = "", *, undoable: bool | None = None) -> Event:
        """启动种子：落到快照 + 记史，但**不过写权门**（面板初值回填等装配期写）。

        与 set 的区别：**只有写权那一关不同** —— set 是操作者发起的变更（要过
        ``authority.gate``）；seed 是装配期把已知值灌进快照（如 GUI 首次把面板当前值
        种进史）。**校验（validate）照走**：初值也是值，"快照里只放校验过的值"这条
        不变量对 seed 同样成立。
        ⚠ 2026-09-26 之前 seed **连 validate 也跳过**，而 docstring 写的是"仍走
        validate **之外**的同一落地路径"——那句话很容易读反（字面"在 validate 之外"，
        读起来像"验证仍在"），现已改正。
        归因上 actor 通常是 'session'/'bootstrap'，区别于 human/ai。
        """
        self._validate(key, value)
        self._snapshot[key] = value
        return self._history.append(key, value, actor, reason, call_id,
                                    undoable=undoable)

    def set_batch(self, channel: Channel, items: dict[str, object], reason: str,
                  call_id: str = "") -> list[Event]:
        """批量写（v1 存档恢复教训，ADR set-batch-原子批量）。整批共用一个 call_id。

        ⚠ **"原子"覆盖到哪一层，如实说清**：
        * **校验 + 预检阶段是原子的**：任一键非法或预检不过 ⇒ **一个都不落地**
          （快照一个字节不动）；
        * **落地阶段**：`append`-only 日志**撤不回** ⇒ 若第 k 项记史失败，**前 k-1 项
          已经进了史**。此时**只回滚"史里还没有"的键**，让**快照 == fold(史)**；
          ⚠ 盲目"全回滚"反而会把不变量弄坏（快照会丢掉史里已经有的那几笔写）。
        * **不可预见**的进程级故障（磁盘满 / OOM）无从保证——进程状态本身已不可信；
          预检（`History.precheck`）的作用是把**可预见**的那类挡在落地之前。
        """
        self._authority.gate(channel.side)
        # 阶段一：全校验 + 全预检（都不碰快照）
        for key, value in items.items():
            self._validate(key, value)
        for key, value in items.items():
            self._history.precheck(key, value)
        # 阶段二：全落地（前面的闸都过了）
        prevs = {k: self._snapshot.get(k, _MISSING) for k in items}
        events: list[Event] = []
        done: set[str] = set()
        try:
            for key, value in items.items():
                self._snapshot[key] = value
                events.append(self._history.append(key, value, channel.actor, reason,
                                                   call_id))
                done.add(key)
        except BaseException:
            # 只回滚"**史里还没有**"的键（史里已有的必须留着，否则快照与 fold(史) 不一致）
            for key in items:
                if key in done:
                    continue
                prev = prevs[key]
                if prev is _MISSING:
                    self._snapshot.pop(key, None)
                else:
                    self._snapshot[key] = prev
            raise
        return events
        return events
