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
                cockpit（Monitor 的只读传输）/ portfile（端口发现规则）/
                tools（工具形态层）/ toolhost（工具定义→宿主传输）/
                roles / format_version / data_layout / errors /
                artifacts（宿主产物托管）+ assembly（具名装配点）
mecha/providers/  Provider 层（dsh 的 churn 关在这里）：log_local / jobs /
                approval / skills / codeact —— local 是出厂默认
checks/         判据（见下）+ 门禁；python checks/run_all.py 一条跑全
docs/v2/        架构总纲（规范源：为什么这么设计）+ 架构详解（建成态导览：代码
                现在长什么样）+ 框架准入线（改动纪律 R 系列）+ 操作陷阱（工具与
                测量层面的坑）+ 遗留登记 + ADR 制度 + notes/（ADR 四态树）
docs/capabilities.md  生成的 capability map（禁止手改，改了判据红）
scripts/        gen_capability_map.py（map 生成器）
```

## 怎么起（30 秒）

mecha 是**领域无关框架**——接入 = 实现 `Engine` 的四个必方法（schema/run/
summary/estimate），经具名装配点 `assemble` 组装。下面是一个自包含的最小例子
（不依赖任何外部项目，可直接跑）：

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
（ADR `领域参考实现搬回所属项目`）。EL 起软件走 `python launcher.py`（两模式：
GUI 纯本地 / AI 经 dsh），详见 `docs/v2/notes/implemented/` 下 `两模式启动` 与
`phase5-el接线` 两篇 ADR。本仓判据的 phase5_* 直接 import EL 仓的 `mecha_v2`
（缺 EL 即红，不假绿）。

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
