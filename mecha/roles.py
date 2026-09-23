"""角色声明机制（总纲 §5.4 交付 1：每个 seam 在自己身上标角色）。

``role ∈ {definition, provider, consumer}``；Definition 允许是具体服务而不是
接口（glossary：seam 是三件套整体）。注册即校验，capability map 由
``scripts/gen_capability_map.py`` 从这里生成——分类是生成的，不是手写的。

## Known Limitations and Deferred Work

- 角色表是进程内的：多进程部署时每个进程各自注册，map 生成只覆盖单进程
  视角（当前无此需求，n=1）。
- 不校验"definition 必须先于 provider 注册"——顺序由装配测试保证（L6）。
- 同名能力的 provider/consumer 允许任意多个，不检查"有 provider 无
  definition"这类拓扑缺口；map 生成后由人审（机械判据等第二个项目）。
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from .errors import MechaError

Disposer = Callable[[], None]


class Role(str, Enum):
    DEFINITION = "definition"
    PROVIDER = "provider"
    CONSUMER = "consumer"


@dataclass(frozen=True)
class CapabilityEntry:
    """一个能力在 map 里的一行：名字 / 角色 / provider / 消费方。"""

    name: str
    role: Role
    providers: tuple[str, ...]
    consumers: tuple[str, ...]


class RoleRegistry:
    """能力角色登记表（具体服务，不是接口）。register 返回 disposer。

    每次登记是一个独立贡献；disposer 只撤销自己那一次，不牵连同名的其他贡献。
    """

    def __init__(self) -> None:
        self._contribs: dict[tuple[str, Role], dict[int, tuple[str, str]]] = {}
        self._ids = itertools.count(1)

    def register(
        self,
        name: str,
        role: Role,
        *,
        provider: str = "",
        consumer: str = "",
    ) -> Disposer:
        """登记一个能力的角色；返回撤销本次登记的 disposer。"""
        if not name.strip():
            raise MechaError(
                "能力名不能为空",
                kind="bad_registration",
                hint="每个扩展点必须有一个非空能力名",
            )
        if role is Role.PROVIDER and not provider:
            raise MechaError(
                f"能力 {name!r} 登记为 provider 却没给 provider 名",
                kind="bad_registration",
                hint="provider 行必须写明是哪个 provider",
            )
        if role is Role.CONSUMER and not consumer:
            raise MechaError(
                f"能力 {name!r} 登记为 consumer 却没给 consumer 名",
                kind="bad_registration",
                hint="consumer 行必须写明是谁在消费",
            )
        key = (name, Role(role))
        token = next(self._ids)
        self._contribs.setdefault(key, {})[token] = (provider, consumer)

        def dispose() -> None:
            row = self._contribs.get(key)
            if row is None:
                return
            row.pop(token, None)
            if not row:
                del self._contribs[key]

        return dispose

    def entries(self) -> list[CapabilityEntry]:
        """按 (name, role) 排序的聚合行，供 map 生成。"""
        out: list[CapabilityEntry] = []
        for (name, role), row in sorted(self._contribs.items()):
            providers = tuple(sorted({p for p, _ in row.values() if p}))
            consumers = tuple(sorted({c for _, c in row.values() if c}))
            out.append(CapabilityEntry(name, role, providers, consumers))
        return out

    def names(self) -> list[str]:
        """有声明的能力名（去重、排序）。"""
        return sorted({name for name, _ in self._contribs})
