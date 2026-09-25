# Phase 3 Provider 替换：吸收 dsh 定案件，churn 关在 Provider 层

> **状态**：implemented
> **作者**：mecha v2
> **日期**：2026-09-23

## Problem

v1 自带 jobs/spill/autonomy/codeact 一族（审计 708 行重复造轮子）。dsh 有对应
成熟件，但成熟度分层（总纲 §4.4）决定哪些能当基、哪些只能观察。

## Decision

四个 seam 落地（`mecha/providers/`），**local 是出厂默认，dsh 侧是显式 opt-in**：

- **Jobs**：单一 `JobRegistry`，job 所有权在实例内（D-21 双 runner 销号）。
  dsh 侧注册 `ctx.jobs` 的接线归 Phase 5。
- **Spill**：`LocalSpillStore` 回**不透明定位符** `$id`，消费者不得拼路径
  （D-10 销号：体积阈值 `DEFAULT_SPILL_BYTES` 标 [EL 派生] 可注入，不再是散落常量）。
- **Approval**：`LocalApproval` **fail closed**（无决策器=拒绝）；one-shot 委托，
  持续写权互斥留在 Authority mode 机（ADR 删光-Authority-模式机）。
- **Log**：`JsonlLogSink`（见 Phase 1）。
- **autonomy op 名表 → `ToolRestriction`**（`visibility.py`，定案）：未声明动词
  **默认可读**（D-4/D-5 销号）；deny 单调、多层取交集、disposer 可撤销；引用
  未知动词当场报错。**preset 只参考不绑定**（ADR 绑定-permission-presets）。
- **codeact 保留自研**（ADR 追-dsh-PTC-code-runtime），补逐子调用记 History。
- **SKILL.md 手维护 → `SkillCatalog`**：目录从 Surface 注册项生成（16→18 漂移
  族销号）；四象限 `model_invocable`/`user_invocable`。
- 判据：`checks/phase3_provider_selfcheck.py`。

## Consequences

换来：重复件删除、churn 隔离、D-4 最高危消解。付出：自研 codeact/visibility
继续维护；dsh 侧 Provider 无真宿主可测（Phase 5 补）。

## Alternatives considered

**否决：现在就接 dsh ctx.jobs/spillStore。** 无宿主进程，接了是假接线（L6
反面）；local 出厂默认才是"软件不依附 dsh"的落地。
**否决：visibility 用危险分级（L1/L2/L3）。** 分级表就是 D-4 的病根；可见性
过滤与审批是两个正交机制，不混。

## 后续（2026-09-25 收口轮）

**`ToolRestriction` / `VisibilityPolicy`（`visibility.py`）已删除**，本篇的
Decision 里"autonomy op 名表 → ToolRestriction"那一条**不再成立**——但**被撤销的是
机制，不是理由**：

- **理由仍然成立且更强**：D-4/D-5 的实质承诺是"框架不替项目给动词判级"。删掉可见性
  分级之后，框架里**根本没有任何分级机制**，承诺由行为保证：**注册即可见、可执行，
  没有第二份"可见性真值"**。
- **为什么删**：该机制**建好未接线**——`Surface.register` 从不调 `visibility.declare`、
  `ToolRegistry.schemas()` 不消费、全仓无第二处引用 ⇒ **零消费者**（R1）。
- **判据改动（R10）**：`phase3_provider_selfcheck.py` 的 `check_visibility_default_readable`
  验的是**机制自身的语义**（declare/restrict/交集/disposer），机制一删就没有对象；
  已换成**同一承诺的行为级判据** `check_registration_is_the_only_visibility`
  （自有动词 `register` 即可分派 ＋ Surface 上无可见性/分级 API ＋ `schemas()` 不过滤），
  不是把覆盖一起删掉。
- **升格触发**：出现"同一宿主要对不同 actor 暴露不同工具集"的真实需求时再加（届时写 ADR）。
- 记录在 ADR `两层传输上提` 的收口表第 4 行。
