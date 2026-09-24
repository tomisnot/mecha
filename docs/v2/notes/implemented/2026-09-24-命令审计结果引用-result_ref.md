# 命令审计结果引用：result_ref 是不透明槽

> **状态**：implemented
> **作者**：ML×mecha 衔接（Lead）
> **日期**：2026-09-24

## Problem

通用命令面（ADR `通用命令面-命令是注册项`）只把"操作语义"写进审计事件：
命令名、ok、scope、回执键、错误 kind。这对"命令有没有发生"够用，但对
"这次命令产出的宿主结果在哪里"不够用：

- 宿主结果不能整体进 History（大对象、artifacts、estimator 都不是事件载荷）；
- 但审计若完全不带引用，History 就无法把一次命令调用与宿主的 `run_id` /
  artifact locator 关联起来，AI 操作史与领域结果史只能靠外部约定对齐；
- 第二个宿主（ML）的真实调用面要求这条互引关系是机制，而不是宿主自己再写
  一条 `ml.command.*` 事件——那会与核心命令审计形成双份事件、两套口径。

## Decision

给 `CommandResult` 增加一个**不透明**的 `result_ref` 槽：

1. `CommandResult(ok, values, error=None, result_ref=None)`；
   `result_ref` 必须是映射（或 `None`），否则构造即抛
   `bad_command_result_ref`。
2. `CommandRegistry.invoke` 在写 `command.<name>` 审计事件时，若命令体给了
   `result_ref`，就把它原样写进审计记录的 `result_ref` 字段；没有给则
   **不增加该字段**（保持既有审计形状向后兼容）。
3. 核心只检查"是映射"，**不解释**其中的 `run_id` / `kind` / locator 等键；
   领域语义仍归宿主。结果体依然由宿主保存，History 只拿引用。
4. 该槽不参与 `output_schema.required` 校验：它不是回执键，是审计引用。

## Consequences

**换来**：命令调用与宿主结果有机制级互引；宿主不必再自建第二套命令事件；
History 保持轻量；下一个消费面（Monitor、断线找回、Artifact Contract）可以从
同一份审计记录取引用，而不是解析各宿主的私有事件。

**付出**：审计记录多一个可选字段；宿主适配层需要把领域结果 ID 组装成映射；
若宿主把大对象塞进 `result_ref`，那是宿主自己的纪律问题（核心只保证形状，
不保证内容大小）——这一点在宿主判据里用"事件体 < N 字符"反向钉。

**放弃了什么**：放弃"审计只允许固定七字段"的封闭形状；放弃让核心理解
`run_id` / `dataset_id` 等任何领域引用键。

## Alternatives considered

**否决：让宿主继续自写 `ml.command.*` 事件。** 两份命令事件会造成双份口径，
Monitor 摘要与历史回放要在两套来源里选，违反单一来源纪律。

**否决：把结果体直接放进审计值。** 大对象进 History 是既有 D 级问题的复发，
且会把 append-only 日志变成事实上的结果存储。

**否决：核心定义 `result_ref` 的具体字段。** 那等于把某个宿主的领域词汇写进
核心；违反“分类留宿主”。

**否决：用 `payload` 里的某个约定键（如 `__result_ref__`）承载。** 约定键
会污染宿主回执命名空间，且无法在构造期校验形状。
