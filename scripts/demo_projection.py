"""一次性演示（**不是判据**）：命令声明 → AI 工具面。

## Known Limitations and Deferred Work

- 一次性演示，**不是判据**：跑完即弃；输出用来人工核对投影的几件事。
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mecha.commands import define_command                      # noqa: E402
from mecha.errors import MechaError                            # noqa: E402
from mecha.projection import (ProjectionOverrides, project,     # noqa: E402
                              projected_required)
from mecha.scopes import ScopePolicy                            # noqa: E402
from mecha.tools import ToolRegistry                            # noqa: E402

SPEC = define_command(
    name="get_paper", description="取一篇论文", side_effect=False, scope=("read",),
    parameters={"type": "object",
                "properties": {"paper_id": {"type": "string"}, "actor": {"type": "string"}},
                "required": ["paper_id", "actor"]},
    output_schema={"type": "object", "required": ["ok"]},
)
print("== 输入 CommandSpec:", SPEC.name, "| scope", SPEC.scope,
      "| parameters 形状", sorted(SPEC.parameters),
      "| required", SPEC.parameters["required"])
pol = ScopePolicy()
pol.grant("ai", "read", "analysis")


def runner(**kwargs):
    return {"ok": True, "ran": kwargs}


tool = project(SPEC, execute=runner, policy=pol)
print("== A policy 允许:", tool.name, "|", tool.description,
      "| parameters 形状", sorted(tool.parameters), "| kind", tool.kind,
      "| scope", list(tool.scope), "| reversible", tool.reversible)
print("== A 必填（投影后）:", projected_required(SPEC), "（actor 已剔除）")
print("== A 真跑到执行面:", tool.execute(paper_id="p1"))
reg = ToolRegistry()
reg.register(tool)                       # 产物【直接注册】：宿主不再写降形层
print("== D 直接注册后调用:", reg.execute("read_paper", {"paper_id": "p2"}))
ov = ProjectionOverrides(rename={"get_paper": "fetch_paper"}, description="覆盖文案",
                         omit_params=("paper_id",), kind="read_telemetry",
                         scope=("read", "analysis"))
t2 = project(SPEC, execute=runner, overrides=ov, policy=pol)
print("== A2 overrides:", t2.name, "|", t2.description,
      "| parameters", sorted(t2.parameters), "| kind", t2.kind, "| scope", list(t2.scope))
print("== A3 policy 不开放:", project(SPEC, execute=runner, policy=ScopePolicy()),
      "（None = 不投影：看不见，省 AI 去试）")
try:
    project(SPEC, required_source={"get_paper": ["paper_id", "text"]}, execute=runner)
    print("== B 漂移: x 竟然没抛")
except MechaError as e:
    print("== B 漂移硬失败: OK", e.kind, "|", e.message)
try:
    project(SPEC, policy=pol)
    print("== C 不给 execute: x 竟然没抛")
except TypeError as e:
    print("== C 不给 execute ⇒ 构造期抛:", type(e).__name__, "|", e)
