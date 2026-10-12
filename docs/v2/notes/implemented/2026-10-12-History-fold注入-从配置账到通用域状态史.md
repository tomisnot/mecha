# History fold 注入：从配置账到通用域状态史

> **状态**：implemented
> **作者**：主代理（n=2 触发：tutor + PaperPilot 两个"非 fold"消费方）
> **日期**：2026-10-12

## Problem

mecha 的 `History` 把"事件"设计成"**配置变更的痕迹**"，而不是"**域里发生过的事**"：

- `Event.kind ∈ {state, history}`；`fold` 只折 `state`（"后写覆盖先写"），`history` 事件"永不进快照"。
- 这在"配置-运行"软件（EL）里是完美的：快照 = 配置 fold，history 事件只是审计附件。

但总纲 L2 的原话是"**域状态史只能追加、快照可从它折叠出来**"——它**从来没说 fold 必须是
"后写覆盖"**，是当初的实现（提炼自一个"配置-运行"项目）把 fold 钉死了。

**n=2 证据（两个消费方都被迫绕道）**：

1. **tutor**（`D:\code-nosync\tutor`）：真状态是 `KnowledgeMap`（节点+掌握度），由**蒸馏**产生
   （LLM 判断 + FSRS 时间衰减 + 归并），不是 fold 出来的。它的"讲解/反馈"事件是**一等公民**、
   是账的主体，不是"审计附件"。塞 `kind="history"` 能用，但语义上是**降级**——主账被放进附件格。
2. **PaperPilot**（`Get Paper`）：真状态是 SQLite 论文库，事件是"抓取/评审/笔记/收藏"等**域数据
   变更**。它现在的变通是**双账**——自建 append-only 事件总线（DB 触发器钉死只增不改）+
   mecha 框架账（`command.*`），再靠 `/activity` 页把两本账并排互引。双账 = 两条可能分叉的流，
   互引是在**防**分叉，而不是**结构上不可能**分叉。

⇒ 框架的"state 中心主义"已经在**逼消费方绕道**；两家的 fold 函数语义不同（蒸馏 vs 物化），
但**形状一样**：`fold(events) → 项目自己的当前状态`。这正是抽象对了的证据。

## Decision

给 `History` 加**可选的 fold 注入**（构造参数），**默认行为一字不变**：

```python
History(sink, fold=fold)   # fold 缺省 = 现有"后写覆盖"（本模块原函数）
```

- **框架只保证三条机制**：append-only、seq 单调、**快照可由注入的 fold 从事件史算出**。
- **fold 的语义是项目的领域知识**（与 `validate` 同款"必填注入/显式表态"纪律的延伸）：
  EL/ML 不传 ⇒ 现状照旧；tutor 传"触发蒸馏"；PaperPilot 传"物化进 SQLite"。
- `snapshot()` 改调注入的 fold；`append_state`/`append_history`/`subscribe` 等**全不动**。
- 附带收益：PaperPilot 的双账**可收成一本**（域事件直接进 mecha History，fold 物化进 SQLite，
  `/activity` 不必再做两区互引——账本来就是一条流）。

⚠ **边界**：fold 注入只解决"快照怎么从账算出"，**不改变**"写必须过 Gate"（硬纪律 1）。
tutor 这类"事件即主体"的项目，写仍走 Command 面（`invoke(side_effect=True)`：写权门 + 审计），
事件进 History，fold 负责"从事件史算出当前状态"。Gate 的 `set`/`snapshot`（state 事件那套）
对它几乎用不到——那不是绕过门，是**这台软件的域状态本就不是"配置键值"**。

## Alternatives considered

- **双账**（PaperPilot 现状）：否决——两条流可能分叉，互引只是防、不是结构上不可能；
  且监控面分裂（`/activity` 要两区并排）。
- **塞进 `kind="history"`**（tutor 原拟）：否决——"只记史、永不进快照"是为一等公民事件
  准备的附件格；把主账放进去是语义降级，且"快照能从账折叠出来"这条硬不变量对它**不再成立**
  （history 事件根本不进 fold）。
- **为 tutor/PaperPilot 各加一个具名机制**（"蒸馏账"/"物化账"）：否决——具名 ABC 方法膨胀
  的同族（rejected/2026-09-23）；折叠语义千差万别，机制只能给"可注入"这一个口。

## Consequences

- **换来**：History 从"配置账"升格为**通用域状态史**（对齐 L2 原义）；两个"非 fold"项目
  不再绕道；PaperPilot 双账可收编、分叉在结构上不可能。
- **付出**：无——默认 fold 不变，现有消费方（EL/ML）零影响；新增只是一个构造参数 + 一次调用点替换。
- **风险与对策**：注入的 fold 是项目代码，框架不对其正确性负责（与 validate 同纪律）；
  判据在**项目侧**（"用注入 fold 重建的状态 == 直接维护的状态"）。

## Required verification

- **默认行为零变化**：`python checks/run_all.py` 全绿（28 PASS / 0 FAIL / 7 SKIP 缺外部依赖，
  与本改动无关）——证明现有消费方（不传 fold）路径一字未动。
- **注入生效（结构）**：`History()` 不传 fold ⇒ `snapshot()` 走模块默认（后写覆盖）；
  传入 fold ⇒ `snapshot()` 走注入函数。判据在项目侧（tutor 用注入 fold 重建 KnowledgeMap
  并断言与直接维护一致）。
