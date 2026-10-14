"""轻量操作范围：把命令、业务事件和长任务归到同一笔操作。

这是统一账的最小机制。它只携带关联元数据，不替项目定义事务、领域状态或
投影；项目若有数据库事务，继续在自己的 provider 中把该范围绑定到事务即可。
"""

from __future__ import annotations

import contextvars
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from collections.abc import Iterator


@dataclass(frozen=True)
class Operation:
    id: str
    name: str
    actor: str = ""
    reason: str = ""
    call_id: str = ""


_CURRENT: contextvars.ContextVar[Operation | None] = contextvars.ContextVar(
    "mecha_operation", default=None
)


def current_operation() -> Operation | None:
    return _CURRENT.get()


def current_operation_id() -> str:
    op = _CURRENT.get()
    return op.id if op is not None else ""


@contextmanager
def operation(name: str, *, actor: str = "", reason: str = "", call_id: str = "",
              operation_id: str = "") -> Iterator[Operation]:
    """建立一笔操作范围；嵌套调用复用外层操作。"""
    parent = _CURRENT.get()
    if parent is not None:
        yield parent
        return
    op = Operation(operation_id or uuid.uuid4().hex, name, actor, reason, call_id)
    token = _CURRENT.set(op)
    try:
        yield op
    finally:
        _CURRENT.reset(token)
