# mecha v2

**人机同门**：写入门（Gate）、账（History）、能力面
（Surface）、人类监控面（Monitor）。AI 适配器只是它的一个操作者适配器，和
GUI 平级——不是主子关系。软件不依附 dsh：dsh 消失时，数据、账、监控
面全部照常，只少一个操作者。

## 框架与使用它的项目

| 仓 | 是什么 | 它用了 mecha 的哪一面 |
|---|---|---|
| **`mecha`**（本仓） | **框架**：人机同门 —— Gate 写入门 / History 账 / Surface 能力面 / Monitor 人类监控面 + Provider 层（log_local / jobs / approval / skills / codeact / mcp） | — |
| `energy-level` | 物理仿真 | **CodeAct 通道**跑 AI 演化 |
| `get-paper` | 论文管理与推荐 | **命令面 + 工具面（MCP）** |
| `ml-toolbox` | 机器学习方法试验台 | **宿主适配 + 面板** |
| `tutor` | 长期学习档案 | **领域事件账 + 命令面 + 人面** |

⚠ 项目**不是框架的一部分**：各自独立成仓、各自装自己的领域内容；**mecha 里没有任何领域实现**。

⭐ **第一次来该从哪读起**：**先读 [`docs/接入指南.md`](docs/接入指南.md)**
（六步顺序 + 每步验收 + 「该由谁提供」+ n=3 要回答的问题）。
项目仓是**用法样例**，不是入门读物 —— 从它们乱点最容易误以为「框架里有这些东西」。

## 安装（陌生人照这个做）

**前置**：Python ≥ 3.10。⭐ **mecha 不依赖任何第三方**（`dependencies = []`）——
核心只用标准库；只有 MCP 投影那条路要可选依赖（`mecha[mcp]`）。

```bash
git clone https://github.com/tomisnot/mecha && cd mecha
python checks/run_all.py        # 默认核心契约
```

日常开发先跑受影响的验证；集成、面板资产和真实项目显式选择，见 [验证说明](checks/README.md)。

**作为依赖被别家引用**（三个项目就是这么装的）：

```
mecha @ git+https://github.com/tomisnot/mecha@v0.2.0
```

⚠ **如实说明**：**仓库还没建 ⇒ 这个 URL 目前会 404**；等仓建好、`v0.2.0` 推上去后即可解析。

## 它是什么 / 不是什么

| 是 | 不是 |
|---|---|
| 成熟软件（如仿真程序）接入 AI 操作者的**门与史** | 一个自给自足的 AI 框架（v1 的旧定位） |
| dsh 等宿主里的一个 Provider + 操作者适配器 | dsh 的能力族/子组件（已被否决，见 notes/rejected） |
| 人机同路的**一道**写入门 | 第二套写权门、第二份真值 |

## 仓库地图

```
mecha/          软件自己的四件：gate / history / surface / monitor / authority /
                cockpit（Monitor 的只读传输）/ portfile（端口发现规则）/
                tools（形状）/ toolhost（工具定义→宿主传输）/
                roles / format_version / data_layout / errors /
                artifacts（宿主产物托管）+ assembly（具名装配点）
mecha/providers/  Provider 层（dsh 的 churn 关在这里）：log_local / jobs /
                approval / skills / codeact —— local 是出厂默认
checks/         结构与结果验证；run_all 默认核心，按套件或名称选范围
                （运行方式见 checks/README.md）
docs/v2/交接-给下一个模型.md  ⭐ **接手先读这个**（入口：读什么文档 / 用户要什么 /
                现在到哪了 / 别踩哪些坑）
docs/v2/        架构总纲（为什么这么设计）+ 架构详解（建成态导览：代码现在长什么样、
                三层与两条腿、启动功能）+ 框架准入线（改动纪律 R 系列）+ 操作陷阱（工具与
                测量层面的坑）+ 软件-交互-写权-三层架构（三层的第一版正文明细）+ 遗留登记 +
                ADR 制度 + notes/（ADR 四态树 + archived/ 过期文档）
docs/capabilities.md  生成的 capability map（禁止手改，改了判据红）
docs/接入指南.md       新项目接入的**顺序表 + 每步验收 + 边界**（第一次接入先读它；
                      也是 n=3 那个"陌生项目"的持久入口）
scripts/        gen_capability_map.py（map 生成器）
```

## 怎么起（30 秒）

mecha 是**领域无关框架**——接入 = 实现 `Engine` 的四个必方法（schema/run/
summary/estimate），经具名装配点 `assemble` 组装。下面是一个自包含的最小例子
（不依赖任何外部项目，可直接跑）：

> **要真接一个项目**（不只是跑这个最小例子）→ 走 [`docs/接入指南.md`](docs/接入指南.md)：
> 六步顺序 + 每步验收 + 「该由谁提供」（**别把领域塞进框架**）+ n=3 要回答的四个问题。

```python
from pathlib import Path
from mecha.assembly import assemble
from mecha.authority import Mode
from mecha.gate import accept_all
from mecha.surface import Engine

class MyEngine(Engine):
    def schema(self):  return {"run_output_required": ["ok"]}
    def run(self, spec, *, context, **opts): return {"ok": True}
    def summary(self): return {"ok": True}
    def estimate(self, spec): return {"est_sec": 1.0}

sw = assemble(root=Path("./my-software"), data_dir_name=".mydata",
              engine=MyEngine(), summarizer=lambda evs: {},
              validate=accept_all)          # 不校验请显式传 accept_all
sw.authority.switch_mode(Mode.AI, side="human")   # 出厂 LOCKED，人类侧开闸
sw.gate.set(sw.channels["ai"], "power", 42, reason="demo")
print(sw.gate.snapshot)                     # {'power': 42}——写经门、记进史
```

真项目接线（EL 消费配置）：领域实现住 **EL 项目仓**（`mecha_v2` 包：ElEngine +
build_tool_registry + V2Bridge + hub），不在本框架仓——"个性知识归所属项目"
（ADR `领域参考实现搬回所属项目`）。⭐ **起软件走 `python -m mecha --all`**（统一启动器；
⚠ 是 `-m mecha`，**不是** `-m mecha.launch` —— 后者是模块不是包，会**静默什么都不做**）。
⚠ 各家**不再自己编排进程**：EL 原来的看门人 `launcher.py` 已按三层架构删除
（它干的事——起进程、等就绪、编排 dsh——整块归框架）。
⭐ 架构权威见 [`docs/v2/软件-交互-写权-三层架构.md`](docs/v2/软件-交互-写权-三层架构.md)。
本仓判据的 phase5_* 直接 import EL 仓的 `mecha_v2`
（缺 EL 即红，不假绿）。

## 验证

```powershell
python checks/run_all.py                  # 默认核心
python checks/run_all.py --only authority  # 当前改动涉及的项
python checks/run_all.py --all             # 核心、集成、资产、真实项目
```

运行范围、外部依赖、SKIP 和发布扫描见 [checks/README.md](checks/README.md)。
文档不强制固定章节、计数禁令或验证章节；测试按实际风险选择，不要求每条配红证。
工程判断见 [框架准入线](docs/v2/框架准入线.md)。

## 与 v1 的关系

从零重写。v1 的原创在纪律不在代码（L1-L9，见总纲 §3），设计不变量保留，工程规则按当前准入线使用；机制层
84% 被 dsh 成熟件或本仓更瘦的实现取代。禁读 v1 旧代码（防锚定）。

## 决策怎么查

一切非平凡决策在 `docs/v2/notes/{implemented,rejected}/`，格式有门禁。
被否决的路为什么被否决，看 `rejected/`（12 条，全带重新考虑条件）。
