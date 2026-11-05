"""Codex Hooks 的 wire 形状。

字段来自 Codex ``codex-rs/hooks/src/schema.rs``。当前首版只提供可测试的纯转换，
宿主适配器的长任务桥接线在后续阶段启用。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..errors import MechaError

POST_TOOL_USE = "PostToolUse"
PERMISSION_REQUEST = "PermissionRequest"


@dataclass(frozen=True)
class ToolUseEvent:
    session_id: str
    turn_id: str
    tool_name: str
    tool_use_id: str
    tool_input: Any
    tool_response: Any


def parse_post_tool_use(payload: Mapping[str, object]) -> ToolUseEvent:
    if payload.get("hook_event_name") != POST_TOOL_USE:
        raise MechaError("不是 PostToolUse 事件", kind="bad_codex_hook_event",
                         hint="hook_event_name 必须逐字等于 PostToolUse")
    required = ("session_id", "turn_id", "tool_name", "tool_use_id",
                "tool_input", "tool_response")
    missing = [k for k in required if k not in payload]
    if missing:
        raise MechaError(f"PostToolUse 缺字段：{missing}", kind="bad_codex_hook_payload")
    return ToolUseEvent(
        session_id=str(payload["session_id"]),
        turn_id=str(payload["turn_id"]),
        tool_name=str(payload["tool_name"]),
        tool_use_id=str(payload["tool_use_id"]),
        tool_input=payload["tool_input"],
        tool_response=payload["tool_response"],
    )


def permission_response(*, allow: bool, message: str = "") -> dict:
    """生成 PermissionRequest hook 的唯一合法输出形状。"""
    behavior = "allow" if allow else "deny"
    decision: dict[str, object] = {"behavior": behavior}
    if not allow and message:
        decision["message"] = message
    return {
        "hookSpecificOutput": {
            "hookEventName": PERMISSION_REQUEST,
            "decision": decision,
        }
    }


def permission_response_json(*, allow: bool, message: str = "") -> str:
    return json.dumps(permission_response(allow=allow, message=message),
                      ensure_ascii=False)


def bare_tool_name(name: str) -> str:
    """去掉宿主可能附加的 ``mcp__<server>__`` 前缀，只用于匹配项目工具名。"""
    if not name.startswith("mcp__"):
        return name
    parts = name.split("__")
    return parts[-1] if len(parts) >= 3 else name


__all__ = [
    "PERMISSION_REQUEST", "POST_TOOL_USE", "ToolUseEvent", "bare_tool_name",
    "parse_post_tool_use", "permission_response", "permission_response_json",
]
