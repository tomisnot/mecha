# 否决：把 History 绑到 dsh session envelope / 存储实现

> **状态**：rejected — 违反"软件不依附 dsh"；session 存储是 churn 全场最高档
> **作者**：mecha v2
> **日期**：2026-09-23

## Problem

History 能不能直接复用 dsh 的 session 事件格式与存储，省掉自建 append-only 日志？

## Decision

**禁止**绑 dsh 的 session envelope 或存储实现。dsh session 存储与事件集是 2707 changes/40 天（快照前 11 天 SQLite provider 整套被删 + V3 envelopes，5 条 storage proposed note 仍 open，总纲 §4.4）。**只可绑 `session-persistence` seam，不绑 envelope**。History 是软件自己的资产，人操作时也写，必须能在 dsh 消失后独立工作（§5.2②）。

## Consequences

换来：History 不随 dsh 存储层重写而腐烂。付出：自建 append-only 日志 + `fold(events)→snapshot` 纯函数。

**重新考虑条件**：dsh session envelope 进入"有测试门禁背书 + 40 天变更极少"的可当地基档（§4.4），且我们确认它承载的是**域状态**而非**操作者行为**时，可重新评估共用事件词汇（仍不共用存储）。

## Alternatives considered

**否决：History 作 dsh session log 的投影。** 直接违反 §1.2 事实三，且制造第二份真值（§9 洞 2 的同类）。
