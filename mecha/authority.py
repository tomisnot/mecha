"""Authority：写权模式机（总纲 §5.2⑤ 硬约束的库内半边）。

dsh 的 approval **刻意窄**——one-shot only，无 allow-always / 无规则记忆 /
无撤销 / 无 grant store。因此"human 模式持续一段时间的写权互斥"必须自管：
本模块只持有 **mode 状态机**；单次授权决策委托给 Approval Provider（Phase 3）。

``actor`` 由**通道**决定（硬纪律 4）：通道在构造时绑定，请求内容永远改不了它。
⇒ AI 适配器拿到的通道写死 actor='ai'，无法伪造 'human'。

## Known Limitations and Deferred Work

- mode 是进程内状态，不落 History（它是操作者协调状态，不是域状态）。崩溃后
  回到出厂 mode；后果（审查 P5-3）：AI 写了一半崩溃，重启后人类看到的是
  LOCKED 而不是"刚才 AI 在写"——丢失的只是协调态，域状态史本身完整可查。
  是否需要持久化等第二个项目表态（n=1 推迟）。
- 不实现"超时自动降级"：需要时经 Approval Provider 的 one-shot 重取，而非
  本模块内置计时器。
"""

from __future__ import annotations

from enum import Enum

from .errors import GateDenied


class Mode(str, Enum):
    """写权模式：谁能经 Gate 落地。"""

    HUMAN = "human"   # 只有 human 通道可写
    AI = "ai"         # 只有 ai 通道可写
    LOCKED = "locked" # 谁都不可写（维护/危险态）


#: 出厂默认。dsh 消失/独立部署都从这里起（nothing ships enabled 的反面：
#: 写权默认收紧，不默认放开）。
DEFAULT_MODE = Mode.LOCKED


class Authority:
    """mode 状态机 + 通道归因。one-shot 授权经 approval 回调委托出去。"""

    def __init__(self, mode: Mode = DEFAULT_MODE) -> None:
        self._mode = Mode(mode)

    @property
    def mode(self) -> Mode:
        return self._mode

    def switch_mode(self, new_mode: Mode, side: str) -> None:
        """切模式。只有 human **侧**能切——AI 不能把自己解锁。

        参数是 `side`（'human'/'ai'）不是 actor 标签（硬纪律 4：side 定写权，
        'gui'/'mcp' 这些细标签归因用，不扩权面）。GUI 在 human 侧 ⇒ 传
        `Channel('gui','human').side`（='human'）可切；传 `.actor`（='gui'）
        会被拒——审查 2.4 的"同名不同义"陷阱由此从命名上根除。
        """
        if side != "human":
            raise GateDenied(
                f"side={side!r} 不能切换写权模式",
                kind="authority_switch_denied",
                hint="只有人类侧（side='human'）可切模式；模式机是写权的最后闸门",
                suggest="传 Channel(...).side 而非 .actor；细标签先经适配器映射到 side",
            )
        self._mode = Mode(new_mode)

    def gate(self, side: str) -> None:
        """四步 set 的第一步：此刻这个 **侧**在当前 mode 下能写吗。

        不做 one-shot 授权（那是 Approval Provider 的事）；这里只判 mode 与
        side 通道的匹配（写权看 side，不看 actor 标签）。
        """
        if self._mode is Mode.LOCKED:
            raise GateDenied(
                "写权处于 LOCKED，任何通道都不能改域状态",
                kind="authority_locked",
                hint="先由人类侧切到目标模式",
                suggest="authority.switch_mode(Mode.AI, 'human')",
            )
        if side != self._mode.value:
            raise GateDenied(
                f"当前模式 {self._mode.value!r} 不接受 side={side!r}",
                kind="authority_mode_mismatch",
                hint=f"只有 {self._mode.value!r} 侧此刻可写",
                suggest="切换模式或改用正确侧的通道",
            )
