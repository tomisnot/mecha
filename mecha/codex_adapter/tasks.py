"""Codex Hook 产生的最小任务账。

它不是 mecha 的领域 History，也不是第二个 JobRegistry：只是外部宿主 Hook 的
审计/观察事实，供 ``/monitor`` 展示。真正的任务状态仍以项目自己的
``task_wait`` / ``task_cancel`` 或 ``JobRegistry`` 为准。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TASK_LEDGER_FILENAME = "tasks.jsonl"


def ledger_path(runtime_root: Path) -> Path:
    return Path(runtime_root) / "codex-home" / TASK_LEDGER_FILENAME


def append_record(path: Path, record: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": datetime.now(timezone.utc).isoformat(), **record}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return path


def read_records(path: Path | None, *, limit: int = 50) -> list[dict]:
    if path is None or not Path(path).is_file():
        return []
    rows: list[dict] = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows[-limit:]


def _from_value(value: object, field: str, depth: int = 0) -> object | None:
    if depth > 6:
        return None
    if isinstance(value, dict):
        if field in value:
            return value[field]
        for key in ("structuredContent", "structured", "value", "text",
                    "content", "result"):
            if key in value:
                found = _from_value(value[key], field, depth + 1)
                if found is not None:
                    return found
        return None
    if isinstance(value, (list, tuple)):
        joined = "".join(str(x) for x in value if isinstance(x, str))
        return _from_value(joined, field, depth + 1) if joined else None
    if isinstance(value, str) and value.strip().startswith(("{", "[")):
        try:
            return _from_value(json.loads(value), field, depth + 1)
        except ValueError:
            return None
    return None


def extract_task_id(tool_response: Any, field: str = "job_id") -> str | None:
    """从各层嵌套的 MCP 回执中提取宿主任务 id；不存在就返回 None。"""
    found = _from_value(tool_response, field)
    if found is None:
        return None
    return str(found)


__all__ = ["TASK_LEDGER_FILENAME", "append_record", "extract_task_id",
           "ledger_path", "read_records"]
