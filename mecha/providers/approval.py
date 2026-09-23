"""Approval Provider：one-shot 授权委托（总纲 §5.2⑤ 硬约束）。

dsh 的 approval **刻意窄**：one-shot only，无 allow-always / 无规则记忆 /
无撤销 / 无 grant store，**fail closed**。因此：

- "human 模式持续一段时间的写权互斥"由 Authority 的 mode 状态机自管（见
  authority.py），**不在**本模块。
- 本模块只回答"此刻这个 actor 能做这个操作吗"的**单次**决策。
- 无回答方（无审批通道）时以 ``unavailable`` **拒绝**（fail closed），绝不放行。

local 实现：默认拒绝（无人值守时不放行任何需审批的操作），可注入一个决策函数
模拟人类点击。dsh 侧 Provider 委托 ``ctx.approval``（注入宿主句柄）。

## Known Limitations and Deferred Work

- 不实现 grant store / allow-always——那是 dsh 明确不提供、且 Authority 已否决
  的形态（ADR 删光-Authority-模式机）。需要"记住这次同意"归 mode 状态机，不归这里。
- 决策函数是同步的；异步审批（等人点按钮）等真实 UI 接入再做，当前 n=0。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..errors import GateDenied


@dataclass(frozen=True)
class ApprovalDecision:
    allowed: bool
    reason: str = ""


class ApprovalProvider:
    """Approval Definition：单次授权。"""

    def request(self, actor: str, op: str, detail: str = "") -> ApprovalDecision:
        raise NotImplementedError


#: 决策函数：给定 (actor, op, detail) 返回是否放行。
Decider = Callable[[str, str, str], bool]


class LocalApproval(ApprovalProvider):
    """local 出厂默认：fail closed。不给决策函数就一律拒绝。"""

    def __init__(self, decider: Decider | None = None) -> None:
        self._decider = decider

    def request(self, actor: str, op: str, detail: str = "") -> ApprovalDecision:
        if self._decider is None:
            # fail closed：无审批通道 = 拒绝，不是放行
            return ApprovalDecision(False, "无审批通道，默认拒绝（fail closed）")
        try:
            ok = bool(self._decider(actor, op, detail))
        except Exception as e:  # noqa: BLE001 - 决策器抛错按拒绝处理
            return ApprovalDecision(False, f"审批决策器异常，按拒绝处理：{e}")
        return ApprovalDecision(ok, "" if ok else "审批未通过")

    def require(self, actor: str, op: str, detail: str = "") -> None:
        """便捷：拒绝时抛 GateDenied（供塞进 Gate 的 validate）。"""
        d = self.request(actor, op, detail)
        if not d.allowed:
            raise GateDenied(
                f"操作 {op!r} 未获授权：{d.reason}",
                kind="approval_denied",
                hint="审批是 one-shot；需要持续放行请由人类通道切 mode",
                suggest="Authority.switch_mode 或提供 ApprovalProvider 决策器",
            )
