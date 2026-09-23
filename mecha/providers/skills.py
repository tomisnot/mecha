"""Skill 目录：单一来源 + 四象限调用策略（总纲 §7 Phase 3；消 16→18 漂移）。

v1 的 SKILL.md 三份手维护，动词表在文档里漂移。v2 的纪律（L8）：**目录从
注册项生成**，不手抄。每个 skill 携带 ``modelInvocable`` / ``userInvocable``
两个正向布尔（dsh skills 的四象限），四象限 = 两种组合的笛卡尔积：

- (True, True)  模型目录与人类命令都可见
- (True, False) 仅模型
- (False, True) 仅人类
- (False, False) 只经受信 get()

## Known Limitations and Deferred Work

- 本模块是"目录数据结构 + 生成"，不是 dsh skill provider 的完整 seam
  （rank 分层、watcher、失效事件）——那是宿主 dsh 的事；我们只保证**我们的
  动词清单只有一个来源**（Surface 的注册项）。
- 不缓存：目录每次从注册项现算（量小，缓存是过早优化）。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ..surface import EntryKind, Surface


@dataclass(frozen=True)
class SkillEntry:
    name: str
    description: str
    model_invocable: bool = True
    user_invocable: bool = True


class SkillCatalog:
    """从 Surface 注册项派生的目录（单一来源；手写清单即漂移根源）。"""

    def __init__(self, surface: Surface) -> None:
        self._surface = surface
        self._meta: dict[str, SkillEntry] = {}

    def declare(self, name: str, description: str, *,
                model_invocable: bool = True,
                user_invocable: bool = True) -> None:
        """为**已注册**的动词声明目录元数据；未注册的名字直接报错（fail loud）。"""
        registered = {e.name for e in self._surface.entries(EntryKind.QUERY)}
        if name not in registered:
            raise ValueError(
                f"skill {name!r} 未注册于 Surface；先 register 再 declare"
                "（目录只有一个来源：注册项）",
            )
        self._meta[name] = SkillEntry(name, description, model_invocable, user_invocable)

    def catalog(self, *, model_facing: bool | None = None) -> list[SkillEntry]:
        """生成目录。model_facing=True 只给模型可见项（投影，不另存一份）。"""
        out: list[SkillEntry] = []
        for e in self._surface.entries(EntryKind.QUERY):
            meta = self._meta.get(e.name)
            if meta is None:
                # 注册了但没 declare：默认可见 + 空描述（不静默丢项，L3 反面：
                # 丢项会让目录"看起来全绿"其实缺）
                meta = SkillEntry(e.name, "")
            if model_facing is None:
                out.append(meta)
            elif model_facing and meta.model_invocable:
                out.append(meta)
            elif not model_facing and meta.user_invocable:
                out.append(meta)
        return sorted(out, key=lambda s: s.name)

    def names(self) -> Iterable[str]:
        return [s.name for s in self.catalog()]
