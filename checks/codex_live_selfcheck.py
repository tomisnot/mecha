"""真实 Codex 链路：App Server 读取隔离配置、发现并调用 mecha 工具。

不调用模型，因此不需要 OpenAI 网络；它验证的是 Codex 自己的 MCP 发现与
tool catalog，而不是 mecha 内部替身。没有 Codex 可执行文件时输出 SKIP。

默认自建一个只挂 ``demo_echo`` 的聚合端点，与真实部署无关。要对着一份已经在
跑的 mecha 运行时复核（例如 ``python -m mecha --ai-host codex`` 之后），用
``--aggregate-url`` 指过去，并用 ``--tool`` 挑一个真实工具：

    python checks/codex_live_selfcheck.py \
        --aggregate-url http://127.0.0.1:3089/mcp --tool el_read_config
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _codex() -> str | None:
    return shutil.which("codex.cmd") or shutil.which("codex") or shutil.which("codex.exe")


def _send(proc: subprocess.Popen, message: dict) -> None:
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
    proc.stdin.flush()


def _await_id(lines: queue.Queue, wanted: int, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            line = lines.get(timeout=0.5)
        except queue.Empty:
            continue
        if line is None:
            raise RuntimeError("codex app-server closed stdout")
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if message.get("id") == wanted:
            return message
    raise TimeoutError(f"codex app-server did not answer id={wanted}")


def _open_thread(proc: subprocess.Popen, lines: queue.Queue, cwd: Path) -> str:
    """开一个临时会话。工具调用按协议要求挂在 thread 上，不跑模型推理。"""
    _send(proc, {"id": 2, "method": "thread/start", "params": {
        "ephemeral": True, "cwd": str(cwd)}})
    response = _await_id(lines, 2, timeout=120.0)
    if response.get("error"):
        raise RuntimeError(f"thread/start 失败：{response['error']}")
    result = response.get("result") or {}
    thread_id = (result.get("thread") or {}).get("id") or result.get("threadId")
    assert thread_id, f"thread/start 没有返回会话号：{result}"
    return str(thread_id)


def _call_tool(proc: subprocess.Popen, lines: queue.Queue,
               thread_id: str, tool: str, arguments: dict) -> str:
    """让 Codex 自己经 rmcp 客户端调用 mecha 端点，把回执文本拼回来。"""
    _send(proc, {"id": 3, "method": "mcpServer/tool/call", "params": {
        "threadId": thread_id, "server": "mecha", "tool": tool,
        "arguments": arguments}})
    response = _await_id(lines, 3, timeout=120.0)
    if response.get("error"):
        raise RuntimeError(f"mcpServer/tool/call 失败：{response['error']}")
    result = response.get("result") or {}
    assert not result.get("isError"), f"工具被标记为出错：{result}"
    blocks = [str(block.get("text", "")) for block in (result.get("content") or [])
              if isinstance(block, dict)]
    structured = result.get("structuredContent")
    if structured:
        blocks.append(json.dumps(structured, ensure_ascii=False))
    receipt = "\n".join(part for part in blocks if part)
    assert receipt, f"工具回执没有内容：{result}"
    return receipt


def _doctor_accepts(codex: str, env: dict, cwd: Path) -> None:
    """让 Codex 自己的解析器裁决这份 config.toml。

    我们只能在字符串层面断言写了 ``[[hooks.PostToolUse]]``；是否被接受由
    ``codex doctor`` 的 config 解析结果说话。旧版本没有该 JSON 字段时跳过，
    不让判据变成版本钉子。
    """
    try:
        done = subprocess.run([codex, "doctor", "--json"], env=env, cwd=str(cwd),
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=240)
        report = json.loads(done.stdout)
    except (subprocess.TimeoutExpired, ValueError) as exc:
        print(f"（跳过 Codex 配置解析复核：{type(exc).__name__}）")
        return
    checks = report.get("checks") if isinstance(report, dict) else None
    if not isinstance(checks, dict):
        print("（跳过 Codex 配置解析复核：doctor 报告结构不同）")
        return
    load = checks.get("config.load") or {}
    mcp = checks.get("mcp.config") or {}
    parse = str((load.get("details") or {}).get("config.toml parse", "")).lower()
    if parse:
        assert parse == "ok", f"Codex 拒绝解析这份配置：config.toml parse={parse}"
    if mcp:
        assert mcp.get("status") == "ok", (
            f"Codex 认为 MCP 配置不一致：{mcp.get('summary')}")
        details = mcp.get("details") or {}
        print("Codex 自检接受配置："
              f"mcp servers={details.get('configured servers')}，"
              f"streamable_http={details.get('streamable_http servers')}")


def check_live(aggregate_url: str = "", tool_name: str = "",
               arguments: dict | None = None) -> int:
    codex = _codex()
    if not codex:
        print("SKIP: 缺 Codex CLI（本项是真实宿主验收，不是框架失败）")
        return 0
    try:
        from mecha.codex_adapter.config import write_preparation
        from mecha.providers.mcp import McpEndpoint
        from mecha.tools import ToolRegistry, define_tool
    except ImportError as exc:
        print(f"SKIP: 缺 MCP extra（{exc}）")
        return 0

    endpoint = None
    if aggregate_url:
        url = aggregate_url
        wanted = tool_name or "demo_echo"
        print(f"对着已在跑的聚合端点复核：{url}（工具 {wanted}）")
    else:
        registry = ToolRegistry()
        registry.register(define_tool(
            name="demo_echo", description="Echo a short text.",
            parameters={"text": {"type": "string"}},
            output_schema={"type": "object", "required": ["ok", "echo"]},
            execute=lambda **kw: {"ok": True, "echo": str(kw.get("text", ""))},
        ))
        endpoint = McpEndpoint(registry, server_name="mecha", instructions="live check")
        endpoint.start()
        url = endpoint.url
        wanted = tool_name or "demo_echo"
    proc: subprocess.Popen | None = None
    #: ⚠ 不用 TemporaryDirectory 自动递归清理：Codex 会在 CODEX_HOME 里留下
    #: sqlite 文件，Windows 上递归删除既可能遇锁，也可能因非常规文件递归失控。
    #: 判据只负责真实发现，临时目录交给系统清理。
    td = Path(tempfile.mkdtemp(prefix="mecha-codex-live-"))
    try:
        paths, _command = write_preparation(td, aggregate_url=url)
        text = paths.config.read_text(encoding="utf-8")
        assert "[mcp_servers.mecha]" in text
        assert "[[hooks.PostToolUse]]" in text
        env = dict(os.environ, CODEX_HOME=str(paths.home))
        proc = subprocess.Popen(
            [codex, "app-server", "--stdio"], env=env, cwd=td,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        assert proc.stdin is not None and proc.stdout is not None
        lines: queue.Queue[str | None] = queue.Queue()

        def reader() -> None:
            assert proc and proc.stdout
            for line in proc.stdout:
                lines.put(line)
            lines.put(None)

        threading.Thread(target=reader, daemon=True).start()
        _send(proc, {"id": 0, "method": "initialize", "params": {
            "clientInfo": {"name": "mecha-live-check", "title": "mecha live check",
                           "version": "0.1.0"}}})
        init = _await_id(lines, 0)
        assert init.get("result"), f"initialize failed: {init}"
        _send(proc, {"id": 1, "method": "mcpServerStatus/list", "params": {}})
        response = _await_id(lines, 1)
        rows = (response.get("result") or {}).get("data") or []
        mecha = next((row for row in rows if row.get("name") == "mecha"), None)
        assert mecha is not None, f"mecha server not discovered: {rows}"
        tools = mecha.get("tools") or {}
        assert wanted in tools, f"{wanted} not discovered: {sorted(tools)}"
        print(f"真实 Codex 已发现 mecha MCP：{len(tools)} 个工具，"
              f"含 {wanted}")

        thread_id = _open_thread(proc, lines, td)
        print(f"真实 Codex 已开出会话：thread={thread_id[:12]}…")
        marker = f"mecha-live-{int(time.time())}"
        #: 只有「自建替身」这一路才塞进随机标记并回读它：那证明请求真的穿过了
        #: Codex 的 rmcp 客户端到达 mecha，而不是读到了缓存回执。对着真实工具
        #: 复核时不改它的入参，只要求回执非空且未被标记为出错。
        echo_probe = arguments is None and wanted == "demo_echo"
        payload = {"text": marker} if echo_probe else dict(arguments or {})
        receipt = _call_tool(proc, lines, thread_id, wanted, payload)
        if echo_probe:
            assert marker in receipt, (
                f"工具回执里没有标记 {marker!r}：{receipt[:300]}")
        print(f"真实 Codex 已调用 mecha 工具：{wanted} -> {receipt[:200]}")
        _doctor_accepts(codex, env, td)
        print("codex_live_selfcheck: PASS")
        return 0
    finally:
        #: 先关 stdin 让 Codex 自己收摊：它断开时会关闭到 mecha 的流式 HTTP
        #: 会话；直接 terminate 会让 uvicorn 侧抛 ConnectionResetError 噪音。
        if proc is not None and proc.poll() is None:
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        if endpoint is not None:
            endpoint.stop()


def _cli() -> tuple[str, str, dict | None]:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--aggregate-url", default="",
                    help="对着一份已在跑的 mecha 聚合端点复核，默认自建替身")
    ap.add_argument("--tool", default="", help="要调用的工具名，默认 demo_echo")
    ap.add_argument("--arguments", default="",
                    help="工具入参 JSON，默认 {'text': 本次随机标记}")
    args = ap.parse_args()
    if args.arguments:
        try:
            payload = json.loads(args.arguments)
        except ValueError as exc:
            raise SystemExit(f"--arguments 不是合法 JSON：{exc}")
        if not isinstance(payload, dict):
            raise SystemExit("--arguments 必须是 JSON 对象")
    else:
        payload = None
    return args.aggregate_url, args.tool, payload


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    #: 真宿主退场时会强行断开到聚合端点的流式 HTTP 会话。Windows 的 Proactor
    #: 事件循环在连接被对端重置时把 ConnectionResetError 交给 loop 异常处理器，
    #: asyncio 的默认处理器再打一整屏回溯——那是拆机噪音，不是判据。判据全部走
    #: 客户端断言，所以这里把 HTTP 栈与 asyncio 的日志压到 CRITICAL。
    for name in ("mcp", "httpx", "uvicorn", "uvicorn.error", "uvicorn.access",
                 "asyncio"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    try:
        return check_live(*_cli())
    except Exception as exc:  # noqa: BLE001 - 真实验收必须给出具体失败
        import traceback
        traceback.print_exc()
        print(f"codex_live_selfcheck: FAIL（{type(exc).__name__}: {exc}）")
        return 1


if __name__ == "__main__":
    sys.exit(main())
