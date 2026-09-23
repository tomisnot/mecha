"""v2 MCP Hub：AI 操作者经这道门驱动 EL（Phase 5 第 1 步，独立进程）。

进程边界是关键：v1 的 mecha-sdk 与 v2 同名 `mecha`（pip editable 装着 v1），
同进程会撞名。Hub 作为**独立进程**跑，启动即把 v2 仓根插到 sys.path 最前 ⇒
本进程 `import mecha` = v2。

**两种部署形态，别混**（审查 2.3 纠偏）：
- **并存部署（本文件）**：GUI 跑 v1、AI 连这个 Hub（v2）。两进程**各写各的
  journal**（默认目录不同：`.mecha-v2-hub` vs v1 的 `.mecha`），**不汇流**——
  谁也不看谁的写。写租约（审查 2.2）保证每个目录各自单写者。
- **同一道门（bridge.V2Bridge.serve_mcp，实际部署路径）**：GUI 迁 v2
  （RE0_V2=1）后，MCP 服务器与 GUI **同进程共一 Gate**，一份真值一份史。
  本文件的 `to_mcp`（工具→MCPServer 转换器）被 serve_mcp 复用；独立进程
  模式只服务并存部署。

- 工具面（tools.build_tool_registry 注册的全部工具，清单见其 schemas()）注册进
  mcp 2.x MCPServer，写经 v2 Gate。
- streamable-http 传输：dsh 插件 re0-mecha 读 .mcp-port → 连 http://host:port/mcp。
- 工具入参 schema 直接投影 v2 声明（parameters 字段覆写——闭包 **kwargs 推不出
  参数，不覆写就等于把 Phase 4 的 schema 成果在 MCP 层丢掉）。

用法：python examples/energy_level/hub.py --root <EL根> [--port 0]
      [--data-dir .mecha-v2-hub] [--open-gate] [--port-file 路径]

## Known Limitations and Deferred Work

- 跨进程不共享内存快照：并存部署期 GUI(v1)+Hub(v2) 各管各的写、各写各的
  journal——**这是并存部署的语义，不是缺陷**；真正"人机同一 Gate"走
  serve_mcp（同进程）那一步（RE0_V2=1 已落地）。
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

from mecha.authority import Mode  # noqa: E402
from mecha.data_layout import resolve_data_dir  # noqa: E402
from mecha.tools import CURRENT_CALL_ID  # noqa: E402

# 模块级导入：`from __future__ import annotations` 下注解是字符串，mcp 的
# get_type_hints 要能从 _fn.__globals__（= 本模块）解析出 Context 才注入得成。
from mcp.server.mcpserver import Context  # noqa: E402

from examples.energy_level.engine import ElEngine, make_validator  # noqa: E402
from examples.energy_level.tools import build_tool_registry  # noqa: E402


def build_stack(el_root: str | Path, data_root: str | Path,
                data_dir_name: str = ".mecha-v2") -> dict:
    """装配 Hub 侧的 v2 栈——**经 mecha.assembly.assemble() 具名装配点**
    （D-33：真实路径与判据同走 assemble，装配漂移在真引擎上即红）。"""
    from mecha.assembly import assemble
    from examples.energy_level.bridge import el_summarizer
    engine = ElEngine(el_root)
    sw = assemble(root=Path(data_root), data_dir_name=data_dir_name,
                  engine=engine, summarizer=el_summarizer,
                  validate=make_validator(engine), mode=Mode.LOCKED,
                  project="energy-level")
    ai = sw.channels["ai"]
    tools = build_tool_registry(engine, sw.gate, sw.surface, sw.authority, ai,
                                sw.jobs)
    return {"engine": engine, "history": sw.history, "authority": sw.authority,
            "gate": sw.gate, "surface": sw.surface, "ai": ai, "jobs": sw.jobs,
            "tools": tools}


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

    def _fn(ctx: Context = None, **kwargs):
        # 请求作用域 call_id（审查 2.7）：MCP wire request id 作为本次工具调用的
        # 关联标识，写进 History 事件（gate.set 经 CURRENT_CALL_ID 读）。dsh 侧
        # 行为史按同一 id 记录 ⇒ "AI 做了什么"↔"域变成了什么"双向可走查（§5.2②）。
        # dsh 的 toolu_ 级 callId 若经 _meta 传，归 dsh 侧 Provider 接线（推迟项）。
        token = CURRENT_CALL_ID.set(ctx.request_id if ctx is not None else "")
        try:
            res = tools.execute(name, kwargs)
        finally:
            CURRENT_CALL_ID.reset(token)
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
    # ctx 走 __annotations__ 被 find_context_parameter 认出并注入（不进模型）。
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
                    help="数据目录名（注入；与 GUI 的 .mecha-v2 分开——并存部署"
                         "各写各的 journal，不汇流；写租约各保各目录单写者）")
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
        stack["authority"].switch_mode(Mode.AI, side="human")
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
