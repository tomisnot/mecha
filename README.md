# mecha

让有长期状态的软件同时服务于人和 AI：共用写入治理、操作记录、能力接口和监控。

mecha 是软件框架。软件拥有自己的状态、数据和生命周期；AI、GUI、CLI 等是它的操作者。AI 宿主断开后，软件、记录和人类操作界面仍可独立工作。任务判断由操作者完成，领域模型与业务规则由项目提供。

## 设计理念

- **状态属于软件**：数据和操作记录比一次会话、一个操作者活得久。
- **人机同门**：不同交互形式使用同一套写权规则和记录，不靠调用者自觉维护治理。
- **机制与内容分开**：框架提供接线与治理机制，项目决定参数、工具语义、策略和界面。
- **调用形式自由**：查询、后台任务、批处理、GUI 动作和组合调用可以各有适合的形状。
- **架构先消除错误空间**：验证重要关系和可观察结果，按改动范围运行，不以文档格式或重复检查代替设计。

理念与边界见 [使用模式与核心原则](docs/v2/使用模式与核心原则.md)；接入新项目从 [接入指南](docs/接入指南.md) 开始。

## 提供的机制

| 机制 | 用途 |
|---|---|
| Authority / Gate | 写权模式、受控写入、参数校验与归因 |
| History | 只追加的事件记录；从记录重建状态，支持项目注入折叠语义 |
| Surface / Commands / Tools | 引擎能力、受控命令和工具声明；供不同操作者适配 |
| Monitor / Cockpit | 只读状态与操作记录，保留原始依据 |
| Providers | 本地日志、后台任务、审批、技能、结构化动作序列和可选 MCP 传输 |
| Artifacts / Assembly | 产物托管和软件装配 |
| Site / Launcher / Aggregator | 多项目启动、进程管理、工具聚合、重连与动态工具更新 |

当前使用 mecha 的项目各自独立成仓，领域实现保留在项目中：

| 项目 | 定位 |
|---|---|
| [Energy Level](https://github.com/tomisnot/energy-level) | 里德堡原子与多体物理仿真 |
| [ML Toolbox](https://github.com/tomisnot/ml-toolbox) | 机器学习方法、训练观测与优化试验台 |
| [PaperPilot](https://github.com/tomisnot/get-paper) | 论文库、文献评审与研究工作台 |
| Tutor | 长期学习档案、讲解材料与反馈记录 |

## 安装

Python 3.10+。核心使用标准库；MCP 传输需要可选依赖。

```bash
git clone https://github.com/tomisnot/mecha.git
cd mecha
python -m pip install -e .
```

需要 MCP 或整站 AI 接入时：

```bash
python -m pip install -e ".[mcp]"
```

开发多个项目时，建议将它们安装到各自所用的 Python 环境，并使用同一版本的 mecha。MCP 传输之外的核心用法不要求安装 AI 宿主。

## 最小装配示例

下面只展示状态写入和留痕。真实项目的引擎、参数校验和业务能力由项目实现。

```python
from pathlib import Path

from mecha.assembly import assemble
from mecha.authority import Mode
from mecha.gate import accept_all
from mecha.surface import Engine


class MyEngine(Engine):
    def schema(self):
        return {"run_output_required": ["ok"]}

    def run(self, spec, *, context, **opts):
        return {"ok": True}

    def summary(self):
        return {"ok": True}

    def estimate(self, spec):
        return {"est_sec": 1.0}


sw = assemble(
    root=Path("./my-software"),
    data_dir_name=".mydata",
    engine=MyEngine(),
    summarizer=lambda events: {},
    validate=accept_all,
)
try:
    sw.authority.switch_mode(Mode.AI, side="human")
    sw.gate.set(sw.channels["ai"], "power", 42, reason="demo")
    print(sw.gate.snapshot)  # {"power": 42}
finally:
    sw.close()
```

工程中用自己的校验函数替换 `accept_all`，并将实际写入接到受控入口。

## 启动接入的项目

项目通过站点声明描述自己的软件入口、数据根、就绪条件和交互面。mecha 负责统一编排：

```bash
python -m mecha --root ../energy-level
python -m mecha --all --root ../energy-level --root ../ml-toolbox
python -m mecha --dry-run --all --root ../energy-level
```

`--root` 可重复使用；`--all` 跳过项目选择页，`--dry-run` 只查看启动计划。路径请替换为实际检出的仓库目录。

整站 AI 界面使用 dsh，需要其可执行命令在 PATH 中，并准备所选项目声明的插件资产。软件存活、客户端连接和写权是三个独立维度；切换写权不需要重启软件。见 [三层架构](docs/v2/软件-交互-写权-三层架构.md)。

## 开发与验证

```bash
python checks/run_all.py
python checks/run_all.py --only authority
python checks/run_all.py --suite integration
python checks/run_all.py --all
```

默认运行核心契约；集成、面板资产、真实项目和发布扫描分开选择。外部项目缺失会明确显示 SKIP，运行范围与依赖见 [验证说明](checks/README.md)。

测试按实际风险选择。文档记录设计理由、用法和真实限制，不要求每次修改新增登记表、固定章节或额外检查。

## 仓库导航

- [接入指南](docs/接入指南.md)：项目如何接入、哪些内容由谁提供。
- [架构总纲](docs/v2/架构总纲.md)：分层与设计依据。
- [框架准入线](docs/v2/框架准入线.md)：当前工程取舍与适量验证。
- [能力快照](docs/capabilities.md)：可按需生成的代码导览。
- [决策记录](docs/v2/notes)：已实现、否决及历史方案。
- `mecha/`：框架核心、Provider、启动与适配机制。
- `mecha/dsh-panel/`：供项目使用的共享面板资产。

能力快照可用 `python scripts/gen_capability_map.py` 更新；注册表与实际接口是能力来源。

## 当前边界

本地 Jobs 的任务表保存在进程内，进程重启后的任务追踪需要项目提供持久信息。默认产物定位符也仅在当前存储实例内有效。后台任务的并发策略、业务结果持久化和恢复方式由项目安排。各模块说明记录更具体的限制。

## 许可

[MIT](LICENSE)。
