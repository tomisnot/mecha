"""ScopePolicy：通道 side 对命令 scope 的写权过滤（第二个宿主逼出的通用机制）。

Authority 只回答"此刻哪一侧能写"（HUMAN/AI/LOCKED 全局互斥）。第二个真实宿主
（ML Toolbox）需要更细的一层：同一个 mode 下，AI 可以跑训练/对比，但不应该
顺手导出数据或删除运行——这类"能写，但有作用域边界"的需求不是某个项目的
特例，而是多宿主共有的权限维度。

本模块只写**机制**：

- scope 是不透明字符串（如命令名或领域动作名），核心不解释其含义；
- 授予按 ``(side, scope)`` 记；默认**拒绝**（fail closed），显式 ``default_allow=True``
  才放开未列出的 scope；
- ``check`` 失败抛 ``GateDenied(kind='scope_denied')``，调用方（CommandRegistry）
  据此在**任何副作用之前**拒绝；
- 未注入 ScopePolicy 时框架不做 scope 过滤（向后兼容，EL 等单宿主不受影响）。

## Known Limitations and Deferred Work

- 不支持角色/用户级主体，只认 mecha 既有的 side（'human'/'ai'）——主体分层等
  第三个宿主出现再抽象。
- 不支持通配符/层级 scope（如 ``ml.*``）：当前只有命令名粒度的真实压力，
  提前做层级会是 n=1 的过度设计。
- 与 Approval 正交：scope 决定"能不能做"，Approval 决定"这一次要不要人点头"，
  两者都不替对方做决定。
"""

from __future__ import annotations

from collections.abc import Iterable

from .errors import GateDenied

__all__ = ["ScopePolicy"]


class ScopePolicy:
    """``(side, scope)`` 允许表；默认拒绝。"""

    def __init__(self, *, default_allow: bool = False) -> None:
        self._default_allow = bool(default_allow)
        self._grants: dict[str, set[str]] = {}

    def grant(self, side: str, *scopes: str) -> None:
        """给某个 side 授予若干 scope（幂等）。"""
        if not isinstance(side, str) or not side.strip():
            raise ValueError("ScopePolicy.grant 需要非空 side")
        bucket = self._grants.setdefault(side, set())
        for scope in scopes:
            if not isinstance(scope, str) or not scope.strip():
                raise ValueError("ScopePolicy.grant 需要非空 scope")
            bucket.add(scope)

    def revoke(self, side: str, *scopes: str) -> None:
        """收回某个 side 的若干 scope；不存在时静默（幂等撤销）。"""
        bucket = self._grants.get(side)
        if not bucket:
            return
        for scope in scopes:
            bucket.discard(scope)
        if not bucket:
            self._grants.pop(side, None)

    def allows(self, side: str, scope: str) -> bool:
        if scope in self._grants.get(side, set()):
            return True
        return self._default_allow

    def check(self, side: str, scopes: Iterable[str]) -> None:
        """命令 scope 全授予才放行；否则在副作用之前 fail closed。"""
        required = [str(s) for s in scopes]
        denied = [s for s in required if not self.allows(side, s)]
        if denied:
            raise GateDenied(
                f"side={side!r} 未被授予 scope {denied}",
                kind="scope_denied",
                hint="scope 由宿主装配时显式授予；默认拒绝未列出的 scope",
                suggest=f"grant({side!r}, {', '.join(repr(s) for s in denied)})",
            )

    def scopes_for(self, side: str) -> list[str]:
        return sorted(self._grants.get(side, set()))
