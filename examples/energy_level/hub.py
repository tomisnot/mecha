"""v2 MCP Hub：AI 操作者经这道门驱动 EL（Phase 5 第 1 步，独立进程）。

进程边界是关键：v1 的 mecha-sdk 与 v2 同名 `mecha`（pip editable 装着 v1），
同进程会撞名。Hub 作为**独立进程**跑，启动即把 v2 仓根插到 sys.path 最前 ⇒
本进程 `import mecha` = v2。两进程经**同一份磁盘 journal** 汇流（History
append-only，可从盘续）。

- 7 个工具（tools.build_tool_registry）注册进 mcp 2.x MCPServer，写经 v2 Gate。
- streamable-http 传输：dsh 插件 re0-mecha 读 .mcp-port → 连 http://host:port/mcp。
- 工具入参 schema 直接投影 v2 声明（parameters 字段覆写——闭包 **kwargs 推不出
  参数，不覆写就等于把 Phase 4 的 schema 成果在 MCP 层丢掉）。

用法：python examples/energy_level/hub.py --root <EL根> [--port 0]
      [--data-dir .mecha-v2] [--open-gate] [--port-file 路径]

## Known Limitations and Deferred Work

- 跨进程不共享内存快照：GUI(v1)+Hub(v2) 并存期各管各的写，只在磁盘 journal
  追加汇流；真正"人机同一 Gate"要等 GUI 迁 v2（同进程）那一步。
- 未接 dsh ctx.approval：AI 写权由 Authority 模式机决定（出厂 LOCKED，
  --open-gate 或人工端点开闸）；细粒度每次写问人待 approval Provider 接线。
- run_sim 只回摘要（pops/t 不过线）；完整数组走 Spill Provider（待接线）。
- parameters 覆写是 mcp 2.2 的适配垫片（私有字段）；mcp 升级破坏时改这一处，
  不外泄进库。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# —— 进程内 v2 优先：把本仓根插到最前，确保 import mecha = v2 ——
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from mecha.authority import Authority, Mode  # noqa: E402
from mecha.data_layout import resolve_data_dir  # noqa: E402
from mecha.gate import Channel, Gate  # noqa: E402
from mecha.history import History  # noqa: E402
from mecha.surface import Surface  # noqa: E402

from examples.energy_level.engine import ElEngine, make_validator  # noqa: E402
from examples.energy_level.tools import build_tool_registry  # noqa: E402
from examples.energy_level.bridge import make_jsonl_history  # noqa: E402


def build_stack(el_root: str | Path, data_root: str | Path,
                data_dir_name: str = ".mecha-v2") -> dict:
    """装配 Hub 侧的 v2 栈（engine + gate + surface + tools）。"""
    engine = ElEngine(el_root)
    layout = resolve_data_dir(Path(data_root), data_dir_name)
    history, _sink = make_jsonl_history(layout)
    authority = Authority(Mode.LOCKED)     # 出厂收紧；人类开闸
    gate = Gate(authority, history, validate=make_validator(engine))
    surface = Surface(engine)
    ai = Channel("ai")
    tools = build_tool_registry(engine, gate, surface, authority, ai)
    return {"engine": engine, "history": history, "authority": authority,
            "gate": gate, "surface": surface, "ai": ai, "tools": tools}


def to_mcp(stack: dict):
    """把 v2 ToolRegistry 投影成 mcp 2.x MCPServer（工具形状不降级）。"""
    from mcp.server.mcpserver import MCPServer

    tools = stack["tools"]
    server = MCPServer(name="mecha-el",
                       instructions="Rydberg 仿真软件的操作工具集。"
                                    "先 query_params 看可写键，set_config 写，"
                                    "estimate_run 估时，run_sim 跑。")
    for schema in tools.schemas():
        _register(server, tools, schema)
    return server


def _register(server, tools, schema: dict) -> None:
    name = schema["name"]
    parameters = schema["parameters"]

    def _fn(**kwargs):
        res = tools.execute(name, kwargs)
        if res["is_error"]:
            err = res["error"]
            info = err.get("info", {})
            text = err["message"]
            if info.get("hint"):
                text += f"\n提示：{info['hint']}"
            if info.get("suggest"):
                text += f"\n替代：{info['suggest']}"
            # 抛框架指定的 ToolError ⇒ mcp 把结果标 is_error=True 且走"预期
            # 失败"路径（不刷 exception traceback）。教学文本原样给模型。
            from mcp.server.mcpserver.exceptions import ToolError
            raise ToolError(text)
        return json.dumps(res["value"], ensure_ascii=False, default=str)

    # MCP 从函数签名建 pydantic 入参模型；**kwargs 会得到一个名为 kwargs 的必填
    # 字段（错）。先给闭包一个"真实命名参数"的合成签名，让模型字段=参数名。
    import inspect
    sig_params = [inspect.Parameter(k, inspect.Parameter.KEYWORD_ONLY, default=None)
                  for k in parameters]
    _fn.__signature__ = inspect.Signature(sig_params, return_annotation=str)

    server.add_tool(_fn, name=name, description=schema["description"])
    # 再把对外声明覆写成 v2 的形状（带类型/描述；Phase 4 成果过线不丢）
    props = {}
    required = []
    for pk, pv in parameters.items():
        node = dict(pv) if isinstance(pv, dict) else {"type": "string"}
        if node.pop("required", False):
            required.append(pk)
        props[pk] = node
    input_schema = {"type": "object", "properties": props}
    if required:
        input_schema["required"] = required
    tool = server._tool_manager._tools[name]     # mcp 2.2 适配垫片（见 Limitations）
    tool.parameters = input_schema


def _free_port(host: str) -> int:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((host, 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="mecha v2 MCP Hub（EL）")
    ap.add_argument("--root", default=os.environ.get("MECHA_EL_ROOT", ""),
                    help="EL 项目根（含 src/core/protocol.py）")
    ap.add_argument("--data-root", default="", help="数据目录父（默认=EL 根）")
    ap.add_argument("--data-dir", default=".mecha-v2-hub",
                    help="数据目录名（注入；与 GUI 的 .mecha-v2 分开，"
                         "并存期两进程各有权威 journal，经 follow_journal 单向同步）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0, help="0=自动选空闲端口")
    ap.add_argument("--port-file", default="",
                    help="端口文件（默认 <root>/.mcp-port，与 dsh 插件 re0-mecha "
                         "的发现约定一致；v1 serve 运行时勿同时起 hub）")
    ap.add_argument("--open-gate", action="store_true",
                    help="启动即开 ai 写权（演示/测试；默认 LOCKED）")
    args = ap.parse_args(argv)

    if not args.root:
        print("必须给 --root 或环境变量 MECHA_EL_ROOT（EL 项目根）")
        return 2
    data_root = args.data_root or args.root
    stack = build_stack(args.root, data_root, args.data_dir)
    if args.open_gate:
        stack["authority"].switch_mode(Mode.AI, actor="human")
        print("[hub] 写权已开到 ai 模式（--open-gate）")

    server = to_mcp(stack)
    port = args.port or _free_port(args.host)
    port_file = args.port_file or str(Path(args.root) / ".mcp-port")
    Path(port_file).write_text(str(port), encoding="utf-8")
    print(f"[hub] {port_file} ← {port}；"
          f"工具 {len(stack['tools'].schemas())} 个；"
          f"journal={resolve_data_dir(Path(data_root), args.data_dir).data_dir}")
    try:
        server.run(transport="streamable-http", host=args.host, port=port)
    finally:
        try:
            if Path(port_file).exists() \
                    and Path(port_file).read_text(encoding="utf-8").strip() == str(port):
                Path(port_file).unlink()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
