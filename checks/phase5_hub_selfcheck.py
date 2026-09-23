"""Phase 5 第 1 步判据：v2 MCP Hub 的真协议往返（AI 操作者"像手脚"的实测）。

起真 hub 子进程（独立进程 ⇒ import mecha = v2，绕开 v1 同名冲突），用真
mcp 2.x streamable-http 客户端连上，验：

1. list_tools：7 个工具全在，且 set_config 的 inputSchema 带 key/value 属性
   （Phase 4 的 schema 声明没在 MCP 层丢——**kwargs 闭包必须被覆写）。
2. 写经门：set_config 成功回执 + Hub 侧 journal 落一条 actor=ai 事件（磁盘为证）。
3. 教学错误：未知键 → 回执文本含 hint（L4 过线不降级）。
4. 真物理：设骨架配置 → run_sim 真演化，回执含 ok+method。
5. 端口文件：.mcp-port-v2 格式兼容 dsh 插件 resolveHubUrl（裸端口）。

缺 EL / 缺 mcp 客户端依赖 = 红（不假绿）。预计 < 60 s（cache 建 + 小演化）。

用法：python checks/phase5_hub_selfcheck.py
退出码：0 全绿；1 有失败。

## Known Limitations and Deferred Work

- 不验 dsh 侧（re0-mecha 插件）的真连接——那是 TS 端 + 浏览器活体，判据只到
  "协议形状与端口发现兼容"这一层。
- 只测单客户端会话；并发会话（GUI 迁移后的人机同进程场景）归 Step 2 判据。
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EL_ROOT = Path(r"D:\学\BaiduSyncdisk\编程\code\python\Energy Level")
sys.path.insert(0, str(ROOT))

EXPECTED_TOOLS = {"set_config", "read_config", "query_params", "estimate_run",
                  "preview_run", "run_sim", "query_levels"}


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _fail(out: list[str], msg: str) -> None:
    print(f"  ✗ {msg}")
    out.append(msg)


async def drive(port: int, out: list[str]) -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    url = f"http://127.0.0.1:{port}/mcp"
    async with streamable_http_client(url) as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as sess:
            await sess.initialize()

            # 1. list_tools：形状不降级
            tools = (await sess.list_tools()).tools
            names = {t.name for t in tools}
            if names == EXPECTED_TOOLS:
                _ok(f"list_tools 7 个工具齐（{sorted(names)}）")
            else:
                _fail(out, f"工具集不符：{names}")
            sc = next((t for t in tools if t.name == "set_config"), None)
            schema_obj = getattr(sc, "input_schema", None) or getattr(sc, "inputSchema", None)
            props = (schema_obj or {}).get("properties", {}) if sc else {}
            if {"key", "value"} <= set(props):
                _ok("set_config 的 inputSchema 带 key/value（Phase 4 声明过线不丢）")
            else:
                _fail(out, f"inputSchema 被 **kwargs 掏空：{props}")

            async def call(name, **args):
                res = await asyncio.wait_for(
                    sess.call_tool(name, args or None), timeout=90)
                text = "".join(c.text for c in res.content
                               if getattr(c, "type", "") == "text")
                is_err = getattr(res, "is_error", None)
                if is_err is None:
                    is_err = getattr(res, "isError", False)
                return bool(is_err), text

            # 2. 写经门
            is_err, text = await call("set_config", key="field.B_gauss", value=1.0)
            if not is_err and '"applied": true' in text:
                _ok("set_config 经 v2 门写入成功（MCP 往返）")
            else:
                _fail(out, f"set_config 失败：{text}")

            # 3. 教学错误过线
            is_err, text = await call("set_config", key="dynamic.nope", value=1)
            if is_err and "提示" in text:
                _ok("未知键 → 回执含教学 hint（L4 过线不降级）")
            else:
                _fail(out, f"错误教学缺失：is_err={is_err} {text[:120]}")

            # 4. 真物理：骨架配置 → run_sim
            for k, v in (("groups.list", [{"n": 60, "l": 0, "j": 0.5},
                                          {"n": 60, "l": 1, "j": 1.5}]),
                         ("dynamic.mode", 0), ("dynamic.method", "mixed"),
                         ("dynamic.t_max", 5.0), ("dynamic.npts", 50),
                         ("dynamic.decay_index", 0)):
                is_err, text = await call("set_config", key=k, value=v)
                if is_err:
                    _fail(out, f"骨架键 {k} 被拒：{text[:120]}")
                    return
            is_err, text = await call("query_levels")
            try:
                lv = json.loads(text)
                key0 = lv["states"][0]["key"]
            except Exception as e:                       # noqa: BLE001
                _fail(out, f"query_levels 往返失败：{e} {text[:120]}")
                return
            await call("set_config", key="dynamic.psi_single", value=key0)
            t0 = time.time()
            is_err, text = await call("run_sim")
            if not is_err and '"ok": true' in text and '"method"' in text:
                _ok(f"run_sim 真演化过线完成（{time.time() - t0:.1f}s，回执含 ok+method）")
            else:
                _fail(out, f"run_sim 往返失败：{text[:200]}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out: list[str] = []
    print("Phase 5 第 1 步判据（真 MCP 协议往返）：")
    if not (EL_ROOT / "src" / "core" / "protocol.py").exists():
        print(f"  ✗ EL 根不存在：{EL_ROOT}（缺 EL 即红）")
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="mecha-hub-"))
    port_file = tmp / ".mcp-port-v2"
    data_dir = "hubdata"
    log_file = (tmp / "hub.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "examples" / "energy_level" / "hub.py"),
         "--root", str(EL_ROOT), "--data-root", str(tmp),
         "--data-dir", data_dir,
         "--port-file", str(port_file), "--open-gate"],
        cwd=str(ROOT), stdout=log_file, stderr=subprocess.STDOUT)
    try:
        # 等端口文件（cache 建要十几秒）
        port = None
        deadline = time.time() + 90
        while time.time() < deadline:
            if proc.poll() is not None:
                _fail(out, f"hub 进程提前退出 code={proc.returncode}")
                break
            if port_file.exists():
                raw = port_file.read_text(encoding="utf-8").strip()
                if raw.isdigit():
                    port = int(raw)
                    break
            time.sleep(0.5)
        if port is None and not out:
            _fail(out, "等 .mcp-port-v2 超时")
        if port is not None:
            _ok(f"hub 就绪，端口文件 = {port}（裸端口，兼容 dsh resolveHubUrl）")
            asyncio.run(drive(port, out))
            # 5. 磁盘为证：journal 落了 actor=ai 的事件
            journal = tmp / data_dir / "journal.jsonl"
            if journal.exists():
                recs = [json.loads(l) for l in
                        journal.read_text(encoding="utf-8").splitlines() if l.strip()]
                if recs and all(r.get("actor") == "ai" for r in recs) \
                        and any(r.get("key") == "field.B_gauss" for r in recs):
                    _ok(f"journal {len(recs)} 条全 actor=ai（写经 v2 门，磁盘为证）")
                else:
                    _fail(out, f"journal 归因异常：{recs[:3]}")
            else:
                _fail(out, "journal 未落盘（Hub 没经 History）")
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()

    if out:
        print(f"phase5_hub_selfcheck: FAIL（{len(out)} 条）")
        return 1
    print("phase5_hub_selfcheck: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
