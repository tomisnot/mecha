# mecha v2

**这台软件的操作者无关层**：写入门（Gate）、域状态史（History）、能力面
（Surface）、人类监控面（Monitor）。AI 适配器只是它的一个操作者适配器，和
GUI 平级——不是主子关系。软件不依附 dsh：dsh 消失时，数据、域状态史、监控
面全部照常，只少一个操作者。

## 它是什么 / 不是什么

| 是 | 不是 |
|---|---|
| 成熟软件（如仿真程序）接入 AI 操作者的**门与史** | 一个自给自足的 AI 框架（v1 的旧定位） |
| dsh 等宿主里的一个 Provider + 操作者适配器 | dsh 的能力族/子组件（已被否决，见 notes/rejected） |
| 人机同路的**一道**写入门 | 第二套写权门、第二份真值 |

## 仓库地图

```
mecha/          稳定脊椎：gate / history / surface / monitor / authority /
                tools（工具形态层）/ visibility / roles / format_version /
                data_layout / errors + assembly（具名装配点）
mecha/providers/  Provider 层（dsh 的 churn 关在这里）：log_local / jobs /
                spill / approval / skills / codeact —— local 是出厂默认
examples/energy_level/  EL（Rb-87 里德堡仿真）作为第一个 Provider 的参考实现
checks/         判据（见下）+ 门禁；python checks/run_all.py 一条跑全
docs/v2/        架构总纲 + 遗留登记 + ADR 制度 + notes/（ADR 四态树）
docs/capabilities.md  生成的 capability map（禁止手改，改了判据红）
scripts/        gen_capability_map.py（map 生成器）
```

## 怎么起（30 秒）

```python
from pathlib import Path
from mecha.assembly import assemble
from mecha.authority import Mode
from examples.energy_level.engine import ElEngine, make_validator

EL = Path(r"D:\学\BaiduSyncdisk\编程\code\python\Energy Level")
engine = ElEngine(EL)                      # 首次建 ARC cache ~3-13s
sw = assemble(root=Path("./my-software"), data_dir_name=".mydata",
              engine=engine, summarizer=lambda evs: {},
              validate=make_validator(engine))
sw.authority.switch_mode(Mode.AI, actor="human")   # 出厂 LOCKED，人类开闸

from examples.energy_level.tools import build_tool_registry
tools = build_tool_registry(engine, sw.gate, sw.surface,
                            sw.authority, sw.channels["ai"])
print([t["name"] for t in tools.schemas()])   # 7 个工具，喂给宿主 ctx.tools
```

写权出厂 LOCKED（nothing ships enabled 用在写权上）；`validate` 必填，不校验
请显式传 `accept_all`。

真项目接线（EL 消费配置）：在 EL 仓设 `RE0_V2=1` 再起 GUI——GUI 与 AI（MCP）
过**同一道 v2 门**（一个 Gate、一份 History），人工夺权走 `:8767/switch_mode`
（token 在 `.human-token`）。详见 `docs/v2/notes/implemented/2026-09-23-phase5-el接线.md`。

## 验证

```
python checks/run_all.py     # 全部门禁（含真引擎端到端，需 EL 仓在位）
```

判据纪律：测装配不测单元（L6）；每条"能抓坏"配"不许误报"对偶（L7）；
缺真依赖即红不假绿。

## 与 v1 的关系

从零重写。v1 的原创在纪律不在代码（L1-L9，见总纲 §3），全部保留；机制层
84% 被 dsh 成熟件或本仓更瘦的实现取代。禁读 v1 旧代码（防锚定）。

## 决策怎么查

一切非平凡决策在 `docs/v2/notes/{implemented,rejected}/`，格式有门禁。
被否决的路为什么被否决，看 `rejected/`（12 条，全带重新考虑条件）。
