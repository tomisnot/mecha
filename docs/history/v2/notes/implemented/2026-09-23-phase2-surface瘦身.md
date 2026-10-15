# Phase 2 Surface 瘦身：必实现 4 个 + 分派目标全部变注册项

> **状态**：implemented
> **作者**：mecha v2
> **日期**：2026-09-23

## Problem

v1 的 31 方法 EngineProtocol 里约 7 个"框架从不调、只是 getattr 分派目标"
（D-13/D-14/D-15/D-16/D-17/D-18 同族）；隐式契约键（`ok`/`est_sec`/`method`）
只在 spec 散文里，ABC 签名完全不提示。

## Decision

- `Engine` 必实现恰好 4 个：`schema` / `run` / `summary` / `estimate`。
  判据常量 `FRAMEWORK_CALLS` 与 `Engine.__abstractmethods__` 子集校验同源
  （守卫读实际 ABC，不读手抄数字——D-25 销号）。
- 其余能力全部 `Surface.register(kind, name, fn, called_by=…) → disposer`；
  `called_by` 必填（谁调它写明——D-18 的"钩子"混合概念废除，三角色取代）。
- 隐式键变显式：`schema()` 必须声明 `run_output_required`，`Surface.run` 逐条
  校验回执（D-15 销号）；`estimate` 回执必须含 `est_sec`。
- 取消与进度是 `ExecutionContext` 的显式字段（D-16 销号），不是数组三槽约定。
- Definition 无默认值（D-17 销号）：`Surface` 不藏任何回退；缺注册项就是
  UnknownOp 报错。
- 判据：`checks/phase2_surface_selfcheck.py`（含"往 Definition 塞分派目标
  → 判据必须红"的 L7 对偶）。

## Consequences

换来：无运行概念的项目不必实现 run 管线全家（D-13 销号）；契约从散文变校验。
付出：注册项多了 `called_by` 声明成本（这正是 D-18 要的答案，不是税）。

## Alternatives considered

**否决：保留可选 ABC 方法（带默认实现的钩子）。** 见 rejected/具名-ABC-方法膨胀。
**否决：用 TypedDict/Protocol 替代 ABC。** Definition 必须是可实例化校验的具体
  服务（glossary：绝不是 interface）；Protocol 无法承载 register 语义。
