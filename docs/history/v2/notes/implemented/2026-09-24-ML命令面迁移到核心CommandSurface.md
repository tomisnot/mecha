# ML 命令面迁移到核心 Command Surface

> **状态**：implemented
> **作者**：ML×mecha 衔接（Lead）
> **日期**：2026-09-24
> **落地**：核心加 `CommandResult.result_ref` 与审计写入；ML adapter 经
> `assemble_ml_mecha` 注册 4 条命令并把 `MLEngine.run` 路由到核心
> `CommandRegistry.invoke`；只读查询走 `Surface.query`；命令审计键
> `command.<name>` 在 ML validator 中显式放行。

## Problem

mecha 已有两个真实操作面来源，但第二宿主没有真正消费新核心机制：

1. **核心已有 additive 的通用命令面**（`mecha/commands.py`，ADR
   `通用命令面-命令是注册项`），它提供逐命令的参数/回执/副作用/审批声明，
   并由 `CommandRegistry.invoke` 做契约校验与副作用审计。
2. **ML adapter 仍然走 `Engine.run(spec)` + 自有 `ml_mecha/commands.py`
   声明**：`MLEngine.run` 按 `spec["command"]` 分派，命令声明只用于校验与
   文档投影，实际执行没有经过核心命令面。
3. **两边的事件模型有重叠风险**：核心命令面对有副作用命令写
   `command.<name>` 审计事件；ML adapter 另写 `ml.command.<name>` 事件。
   若直接迁移而不做选择，History 会出现两份意图相近的命令事件。

不处理的后果：核心 Command Surface 没有被第二个宿主真实消费，无法证明它
“通用”；ML adapter 继续自建一条分派路径，薄适配目标只完成了一半；两套命令
审计并存会让 Monitor 与历史回放出现双份溯源。

## Decision

分两步迁移，先补机制再换路径：

1. **扩展核心命令审计，允许携带结果引用（result_ref）**：
   `CommandSpec` / `CommandRegistry.invoke` 的回执与审计事件允许一个
   **不透明**的 `result_ref` 映射（例如 `{"kind": "ml_run", "run_id": "..."}`）。
   核心只检查它是不是映射，不解释内容；大对象仍由宿主保存。这样命令审计
   可以在不复制领域事件的前提下，把“命令调用”和“宿主结果引用”钉在一起。
2. **ML 侧迁移有副作用命令到核心命令面**：`prepare_dataset` /
   `run_method` / `run_method_batch` / `compare_methods` 在装配点注册进
   `Software.commands`，工具层改为经 `software.commands.invoke(...)`
   调用，不再直接调 `MLEngine.run`。
3. **只读查询不迁移**：`method_catalog` / `method_detail` /
   `dataset_detail` / `run_detail` 继续走 `Surface.query`（只读不经 Gate，
   与 D-4 口径一致）。
4. **事件模型收敛**：核心 `command.<name>` 审计事件成为**唯一命令审计**；
   ML 的 `ml.command.*` 事件在迁移完成后删除。ML run store 仍是结果真值，
   审计只保留操作语义 + `result_ref`。
5. **迁移前置条件**：ML validator 显式放行 `command.*` 前缀的审计键
   （当前它按域状态键白名单拒收该前缀）。

## Consequences

**换来**：第二宿主真实消费核心 Command Surface；命令声明、参数契约、回执
契约、副作用审计只有一套；History 里命令审计与结果引用可互引；ToolHost
后续可以从 `CommandRegistry.specs()` 自动投影工具，而不必继续维护手写工具面。

**付出**：核心 `commands.py` 增加一个不透明 `result_ref` 槽与相应契约校验；
ML adapter 的装配点、工具层、测试同步改造；迁移期间需要同时维护旧
`MLEngine.run` 路径与新的 core command 路径，直到两个宿主的命令面测试都绿。

**放弃了什么**：放弃“命令面先只做声明、不接分派”的短期简单状态；放弃继续
让 ML 拥有第二套命令审计；放弃把 ToolHost 自动投影提前到迁移之前（它依赖
`result_ref` 与核心命令面被真实消费）。

## Alternatives considered

**否决：保持现状，ML 继续用 `Engine.run(spec)` 分派。** 短期零风险，但核心
Command Surface 会永远停在“只有判据、没有真实宿主”的状态；第二宿主的压力
无法证明机制通用，ToolHost 自动投影也没有可投影的单一来源。

**否决：直接把 `command.<name>` 与 `ml.command.<name>` 并列保留。** 两份
命令事件会让 Monitor 摘要、历史回放与审计对账各选一份口径，违反“单一
来源”的既有纪律（M3/D-9 同类病）。

**否决：让核心解释 `run_id` / `dataset_id` / `result_ref` 的领域语义。**
这会把 ML 词汇写进核心，违反衔接基调 R2 与“分类留宿主”。`result_ref`
只能是不透明映射。

**否决：先做 ToolHost 自动投影，再迁移命令。** 自动投影需要命令面是唯一
来源；当前两个来源并存，先投影只会把“手写工具面”换成“两份声明互相漂移”。

**否决：ML 在 adapter 内自建一个核心命令面等价物。** 那正是“第二套机制”，
不是薄适配；核心已有机制却不消费，等于用代码证明框架没有通用性。
