# 否决：把 autonomy 分级绑到 dsh permission-presets

> **状态**：rejected — presets 平面 3 周内自我推翻过一次，太新
> **作者**：mecha v2
> **日期**：2026-09-23

## Problem

v1 的 `autonomy.classify()` 靠硬编码 op 名表判级（D-4，最高危）。要换成 dsh 的哪一件？

## Decision

**定案用 `ToolRestriction`**（`{allow,deny}` + `restrict(): () => void`，dsh 定案件，§4.4 可当地基）。**permission-presets 只参考不绑定**——profile/bundle/preset 平面 3 周内自我推翻过一次（§4.4 只能观察档）。

## Consequences

换来：分级机制落在稳定件上，消 D-4。付出：不能白拿 presets 的 UX 组合。

**重新考虑条件**：preset 平面连续两个 release 稳定、且其 `workspace-write`/`danger-full-access` 语义与我们的域写权模型对齐时。

## Alternatives considered

**否决：绑 presets。** churn 越过 Provider 层。
**否决：保留 op 名表。** D-4 原样复发，新项目只读动词恒判 L3。
