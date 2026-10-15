# 否决：把 Authority 模式机整个删掉、全权委托 dsh approval

> **状态**：rejected — dsh approval 刻意窄（one-shot only），持续写权互斥必须自管
> **作者**：mecha v2
> **日期**：2026-09-23

## Problem

Phase 3 要吸收 dsh 成熟件。approval seam 存在，v1 的 `Authority` 模式机是不是该整个删掉、全交给 `ctx.approval`？

## Decision

**Authority 的模式机不该删，该瘦**：mode 状态机自留 + one-shot 授权委托给 Approval Provider（总纲 §5.2⑤硬约束）。dsh approval **无 allow-always / 无规则记忆 / 无撤销 / 无 grant store**，fail closed。"human 模式持续一段时间的写权互斥"这种**跨多次调用的状态**，dsh 结构上不提供，必须我们自己管。

## Consequences

换来：既复用 dsh 的 one-shot 决策，又不丢"持续模式"语义（消 D-4/D-5/D-7 的一部分）。付出：仍要维护一个瘦版 mode 状态机。

**重新考虑条件**：dsh approval 若新增 grant-store / allow-always 语义并进入可当地基档，mode 状态机可进一步收缩。

## Alternatives considered

**否决：全删 Authority。** "human 模式锁写"直接失效，AI 与人类写权互斥无依据。
**否决：全留 Authority 不委托。** 重复 dsh 已有件，回到 v1 的 15.7% 轮子。
