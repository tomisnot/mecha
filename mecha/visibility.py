"""能力可见性：ToolRestriction 取代 op 名表（总纲 §7 Phase 3；消 D-4/D-5）。

v1 的 ``autonomy.classify()`` 靠三张硬编码 op 名表判级，实测把新项目自己发明的
只读动词判成"不可逆/安全攸关"⇒ 接进去就废（D-4 最高危）。

v2 抄 dsh 的定案件形状（``core/tools`` 的 ``{allow, deny}`` + ``restrict():
() => void``），语义是**可见性过滤**，不是危险分级：

- 未声明的动词**默认可见**（可读）。deny 只能缩减，不能扩充。
- 多个 restriction 取**交集**；后注册者不能撤销先注册者的拒绝（单调）。
- 返回 disposer（可逆副作用）。
- **不绑 permission-presets**（太新，3 周自我推翻过；ADR 绑定-permission-presets）。

## Known Limitations and Deferred Work

- 本模块只管"可见/不可见"，不管"要不要审批"——后者是 Approval Provider 的
  one-shot 决策（两个正交机制，不混）。
- 不实现 scope 分层（dsh 有 agent scope 交集语义）；当前 n=1 只有一个操作者
  作用域，分层等第二个消费方（推迟）。
- 无"危险动词"概念残留：不可逆性由项目的 validate/Approval 表达，不在库里分级
  （消 D-5：op 名表整体移除）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

Disposer = Callable[[], None]


class ToolRestriction:
    """单个作用域对其继承内容的实时过滤器（dsh ToolRestriction 同形）。

    ``allow`` 是"只保留这些"；``deny`` 是"移除这些"。两者都不在名单里的
    已注册能力默认可见。
    """

    def __init__(self, *, allow: Iterable[str] | None = None,
                 deny: Iterable[str] | None = None) -> None:
        if allow is not None:
            allow = tuple(allow)
            if not allow:
                raise ValueError("ToolRestriction.allow 不能是空集合（空=全删，请改用 deny）")
            self.allow: frozenset[str] | None = frozenset(allow)
        else:
            self.allow = None
        self.deny: frozenset[str] = frozenset(deny or ())

    def visible(self, name: str) -> bool:
        if self.allow is not None and name not in self.allow:
            return False
        return name not in self.deny


class VisibilityPolicy:
    """注册表 + 多层 restriction 的交集求解。"""

    def __init__(self) -> None:
        self._names: set[str] = set()
        self._restrictions: dict[int, ToolRestriction] = {}
        self._token = 0

    def declare(self, name: str) -> None:
        """声明一个能力名（由 Surface 的注册项自动调用）。"""
        self._names.add(name)

    def restrict(self, restriction: ToolRestriction) -> Disposer:
        """加一层过滤；返回撤销该层的 disposer。

        未知名字直接报错（fail loud at load，dsh AGENTS 约定）——拼错动词名
        静默失效是最坏的结果。
        """
        unknown = set()
        if restriction.allow is not None:
            unknown |= set(restriction.allow) - self._names
        unknown |= set(restriction.deny) - self._names
        if unknown:
            raise ValueError(
                f"restriction 引用了未声明的能力名 {sorted(unknown)}",
            )
        self._token += 1
        token = self._token
        self._restrictions[token] = restriction

        def dispose() -> None:
            self._restrictions.pop(token, None)

        return dispose

    def visible(self) -> list[str]:
        """当前可见的能力名（排序）。未声明 restriction 时全部可见。"""
        return sorted(n for n in self._names
                      if all(r.visible(n) for r in self._restrictions.values()))

    def is_visible(self, name: str) -> bool:
        return name in self.visible()
