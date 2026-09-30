"""一次性演示（**不是判据**）：命令声明 → AI 工具面。

## Known Limitations and Deferred Work

- 一次性演示，**不是判据**：跑完即弃；输出用来人工核对投影的几件事。
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mecha.commands import define_command                      # noqa: E402
from mecha.errors import MechaError                            # noqa: E402
from mecha.projection import ProjectionOverrides, project       # noqa: E402
from mecha.scopes import ScopePolicy                            # noqa: E402

SPEC = define_command(
    name="get_paper", description="取一篇论文", side_effect=False, scope=("read",),
    parameters={"type": "object",
                "properties": {"paper_id": {"type": "string"}, "actor": {"type": "string"}},
                "required": ["paper_id", "actor"]},
    output_schema={"type": "object", "required": ["ok"]},
)
print("== 输入 CommandSpec:", SPEC.name, "| scope", SPEC.scope,
      "| required", SPEC.parameters["required"])
pol = ScopePolicy()
pol.grant("ai", "read", "analysis")
tool = project(SPEC, policy=pol)
print("== A policy 允许:", tool.name, "|", tool.description,
      "| required", tool.parameters["required"], "| kind", tool.kind,
      "| scope", list(tool.scope), "| reversible", tool.reversible)
ov = ProjectionOverrides(rename={"get_paper": "fetch_paper"}, description="覆盖文案",
                         omit_params=("paper_id",), kind="read_telemetry",
                         scope=("read", "analysis"))
t2 = project(SPEC, overrides=ov, policy=pol)
print("== A2 overrides:", t2.name, "|", t2.description,
      "| required", t2.parameters["required"], "| kind", t2.kind, "| scope", list(t2.scope))
print("== A3 policy 不开放:", project(SPEC, policy=ScopePolicy()),
      "（None = 不投影：看不见，省 AI 去试）")
try:
    project(SPEC, required_source={"get_paper": ["paper_id", "text"]})
    print("== B 漂移: ✗ 竟然没抛")
except MechaError as e:
    print("== B 漂移硬失败: ✓", e.kind, "|", e.message)
