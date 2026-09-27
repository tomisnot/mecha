"""Authority：写权模式机（总纲 §5.2⑤ 硬约束的库内半边）。

dsh 的 approval **刻意窄**——one-shot only，无 allow-always / 无规则记忆 /
无撤销 / 无 grant store。因此"human 模式持续一段时间的写权互斥"必须自管：
本模块只持有 **mode 状态机**；单次授权决策委托给 Approval Provider（Phase 3）。

``actor`` 由**通道**决定（硬纪律 4）：通道在构造时绑定，请求内容永远改不了它。
⇒ AI 适配器拿到的通道写死 actor='ai'，无法伪造 'human'。

## 四种模式（**默认仍是 LOCKED**，出厂不放开）

| mode | 谁能写 |
|---|---|
| ``HUMAN`` | 只有 human 侧 |
| ``AI`` | 只有 ai 侧 |
| ``OPEN`` | **两侧都能写**（2026-09-26 加：给"人机低冲突"的项目去掉那道多余的闸） |
| ``LOCKED`` | **都不能写**（维护/危险态；**出厂默认**） |

⚠ **``OPEN`` 的代价（用它就要认）**：两侧并发写时是"**后写覆盖**"，**没有**先到先得、
也没有 CAS/期望旧值 ⇒ 真冲突时**后写的赢、先写的悄无声息地没了**（域状态史里仍能查到
两笔，所以"丢了"是可查的，不是不可知）。⇒ 只在**确实低冲突**的项目上用。
⚠ ``OPEN`` **不新增身份**：``side`` 仍是 ``human``/``ai`` 两个（``Channel`` 构造时钉死），
细标签照旧只进 ``actor`` 做归因、**不扩写权**。切模式**仍只由 human 侧**发起。

## Known Limitations and Deferred Work

- mode 是进程内状态，不落 History（它是操作者协调状态，不是域状态）。崩溃后
  回到出厂 mode；后果（审查 P5-3）：AI 写了一半崩溃，重启后人类看到的是
  LOCKED 而不是"刚才 AI 在写"——丢失的只是协调态，域状态史本身完整可查。
  是否需要持久化等第二个项目表态（n=1 推迟）。
- `watch` 的回调在 `switch_mode` 的**调用线程**上同步执行（与 History.subscribe
  同纪律）：GUI 侧订阅者自己负责编组回主线程（Qt 信号），本模块不 import UI 库。
  通知**不补发**——订阅者起步时应先读一次 `mode` 再挂 watch（先挂后读的竞态窗口
  以"读"为准即可，协调态本就允许瞬时滞后）。
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
    OPEN = "open"     # **两侧都能写**（2026-09-26 加；代价是"后写覆盖"，见模块 docstring）
    LOCKED = "locked" # 谁都不可写（维护/危险态）


#: 出厂默认。dsh 消失/独立部署都从这里起（nothing ships enabled 的反面：
#: 写权默认收紧，不默认放开）。
DEFAULT_MODE = Mode.LOCKED


class Authority:
    """mode 状态机 + 通道归因。one-shot 授权经 approval 回调委托出去。"""

    def __init__(self, mode: Mode = DEFAULT_MODE) -> None:
        self._mode = Mode(mode)
        self._watchers: list = []

    @property
    def mode(self) -> Mode:
        return self._mode

    def watch(self, callback) -> "Callable[[], None]":
        """订阅模式变更：每次**真变化**（新 mode ≠ 旧 mode）在 switch_mode 的
        调用线程上同步回调 `callback(new_mode)`。返回 disposer（退订）。

        给谁用：GUI 按钮/状态栏、人工控制网页——mode 是协调态**不落 History**
        （见模块 docstring），史订阅收不到它，UI 要实时反映"此刻谁能写"就订这里。
        坏订阅者不拖垮模式机（回调抛错被吞，与 History.subscribe 同纪律）。
        """
        self._watchers.append(callback)

        def dispose() -> None:
            try:
                self._watchers.remove(callback)
            except ValueError:
                pass

        return dispose

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
        new = Mode(new_mode)
        if new is self._mode:
            return                      # 幂等：真变化才通知，UI 不吃重复刷新
        self._mode = new
        for cb in list(self._watchers):  # 快照迭代：回调里可退订
            try:
                cb(new)
            except Exception:            # noqa: BLE001 - 坏订阅者不拖垮模式机
                pass

    def gate(self, side: str) -> None:
        """四步 set 的第一步：此刻这个 **侧**在当前 mode 下能写吗。

        不做 one-shot 授权（那是 Approval Provider 的事）；这里只判 mode 与
        side 通道的匹配（写权看 side，不看 actor 标签）。
        ⚠ ``OPEN`` 是唯一"两侧都放行"的模式（**默认不是它**，出厂仍是 LOCKED）；
        它**不校验 side 是不是 human/ai**——那个白名单在 ``Channel`` 构造期钉死
        （见 ``gate.Channel.__post_init__``），本方法只做 mode 与 side 的匹配。
        """
        if self._mode is Mode.LOCKED:
            raise GateDenied(
                "写权处于 LOCKED，任何通道都不能改域状态",
                kind="authority_locked",
                hint="先由人类侧切到目标模式",
                suggest="authority.switch_mode(Mode.AI, 'human')",
            )
        if self._mode is Mode.OPEN:
            # 都允许：合法 side（human/ai）都能写。⚠ 代价是"后写覆盖"（模块 docstring）
            return
        if side != self._mode.value:
            raise GateDenied(
                f"当前模式 {self._mode.value!r} 不接受 side={side!r}",
                kind="authority_mode_mismatch",
                hint=f"只有 {self._mode.value!r} 侧此刻可写",
                suggest="切换模式或改用正确侧的通道",
            )
