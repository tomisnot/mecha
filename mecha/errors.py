"""错误词汇（L4：错误消息是 LLM 的 UI，必须可教学）。

形状对齐 dsh 的 ``ToolFailure {message, info}``（总纲 §7 Phase 4）：
``kind`` 是机读分类，``hint`` 是给人/模型看的下一步指引，``suggest`` 是
可选的替代操作名。构造即校验——坏错误比没有错误更坏（L3）。

## Known Limitations and Deferred Work

- ``kind`` 是开放字符串（项目可扩），不维护封闭枚举表（L8：文档不写计数）。
- 不做异常链序列化：跨进程回传由 Provider 层负责，本模块只定义形状。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MechaError(Exception):
    """可教学错误：message 说清事实，hint 说清下一步，suggest 给出替代操作。"""

    message: str
    kind: str = "error"
    hint: str = ""
    suggest: str = ""

    def __post_init__(self) -> None:
        if not self.message.strip():
            raise ValueError("MechaError.message 不能为空")
        if not self.kind.strip():
            raise ValueError("MechaError.kind 不能为空")
        super().__init__(self.message)

    def to_failure(self) -> dict[str, object]:
        """对齐 ToolFailure {message, info} 的投影。"""
        return {
            "message": self.message,
            "info": {"kind": self.kind, "hint": self.hint, "suggest": self.suggest},
        }

    def teaching_text(self) -> str:
        """模型可见文本：事实 + 指引 + 替代（L4：一条消息 ≈ 零试错成本）。"""
        parts = [self.message]
        if self.hint:
            parts.append(f"提示：{self.hint}")
        if self.suggest:
            parts.append(f"替代：{self.suggest}")
        return "；".join(parts)


class GateDenied(MechaError):
    """写入门拒绝（authority 不过 / validate 不过 / 绕过执行器的直写）。"""


class UnknownKey(MechaError):
    """set 的键不在声明面内。

    ``typo()`` 工厂自动生 did_you_mean（L4：v1 的头号教学特征——打错键名
    当场猜你想写哪个）。库不持有领域键表：候选集由调用方传入（审查 2.5）。
    """

    @classmethod
    def typo(cls, key: object, candidates, *, message: str = "",
             hint: str = "", kind: str = "unknown_key") -> "UnknownKey":
        import difflib
        close = difflib.get_close_matches(
            str(key), [str(c) for c in candidates], n=3, cutoff=0.4)
        return cls(
            message or f"键 {key!r} 不在可写清单",
            kind=kind, hint=hint,
            suggest=("你是不是想写：" + "、".join(close)) if close else "",
        )


class UnknownOp(MechaError):
    """调用了能力面里不存在的操作。"""


class InvalidSpec(MechaError):
    """run 的 spec 不合法。"""


@dataclass(frozen=True)
class ToolFailure:
    """对齐 dsh ``ToolFailure {message, info}`` 的失败形状（Phase 4）。

    MechaError 是异常；ToolFailure 是它的**跨边界投影**（回执里的 dict）。
    """

    message: str
    kind: str = "error"
    hint: str = ""
    suggest: str = ""

    def to_failure(self) -> dict[str, object]:
        return {
            "message": self.message,
            "info": {"kind": self.kind, "hint": self.hint, "suggest": self.suggest},
        }
