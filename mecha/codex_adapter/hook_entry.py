"""Codex Hook 命令入口。

只做两件不改变 Codex 语义的事：
- ``post-tool-use``：把任务提交回执写入 ``tasks.jsonl``；
- ``permission-request``：记录一次待审批审计，然后**不输出 decision**，
  让 Codex 继续走它原有的审批流程。

本模块不直接改项目状态，也不替代 mecha 的 Gate/Authority。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .hooks import bare_tool_name, parse_post_tool_use
from .tasks import append_record, extract_task_id


def handle_post_tool_use(payload: dict, ledger: Path, *,
                         task_id_field: str = "job_id") -> dict:
    event = parse_post_tool_use(payload)
    task_id = extract_task_id(event.tool_response, task_id_field)
    append_record(ledger, {
        "kind": "task.submitted" if task_id else "task.tool",
        "tool": bare_tool_name(event.tool_name),
        "raw_tool": event.tool_name,
        "task_id": task_id or "",
        "session_id": event.session_id,
        "turn_id": event.turn_id,
        "tool_use_id": event.tool_use_id,
    })
    return {}


def handle_permission_request(payload: dict, ledger: Path) -> dict:
    append_record(ledger, {
        "kind": "permission.requested",
        "tool": bare_tool_name(str(payload.get("tool_name", ""))),
        "raw_tool": str(payload.get("tool_name", "")),
        "session_id": str(payload.get("session_id", "")),
        "turn_id": str(payload.get("turn_id", "")),
    })
    # 不返回 decision：Codex 将继续其正常 approval flow。
    return {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mecha.codex_adapter.hook_entry")
    parser.add_argument("mode", choices=("post-tool-use", "permission-request"))
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--task-id-field", default="job_id")
    args = parser.parse_args(argv)
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw or "{}")
    except ValueError as exc:
        print(f"mecha codex hook: invalid JSON: {exc}", file=sys.stderr)
        return 0
    if not isinstance(payload, dict):
        print("mecha codex hook: payload is not an object", file=sys.stderr)
        return 0
    ledger = Path(args.ledger)
    if args.mode == "post-tool-use":
        output = handle_post_tool_use(payload, ledger,
                                      task_id_field=args.task_id_field)
    else:
        output = handle_permission_request(payload, ledger)
    if output:
        sys.stdout.write(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
