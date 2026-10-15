# 否决：AI 适配器另起 MCP server

> **状态**：rejected — 多一层进程边界，且 Resources/Prompts 不桥接
> **作者**：mecha v2
> **日期**：2026-09-23

## Problem

AI 工具怎么暴露给 dsh？v1 有 server/hub 一族的 MCP 形态。

## Decision

工具**注册进 `ctx.tools`，不另起 MCP server**（总纲 §7 Phase 4 表首行）。注册、声明（`defineTool` 契约 + `output.schema` 显式）、命名（动词_宾语）、错误（`ToolFailure {message, info}` 形状）、投影（`schemas()` 白名单）全部照 dsh 原生工具的规矩长。

## Consequences

换来：工具与 `bash`/`edit` 同构，"像自己的手脚"；无进程边界；schema 单一来源。付出：v2 的 AI 适配器形态绑定 dsh 宿主（但这是**操作者适配器层**，允许被替换；Provider 层之外不损失独立性）。

**真实约束（审查 2.3 补，2026-09-23——原 Decision 的字面说法与实现不符，是
D-27 失败类在 ADR 层的复发，据实更正）**：dsh 是 Node、mecha v2 是 Python，
**跨语言边界上不可能从 Python 直接注册进 Node 进程的 `ctx.tools`** ⇒ 对 dsh
宿主，**MCP 传输不可避免，不是"另起"**。本决定的实质精神仍守住：

- 工具**声明**单一来源（v2 `ToolRegistry`，`schemas()` 白名单投影）；
- dsh 侧经 TS 插件 `register-tools.ts` 把 MCP 工具 `ctx.tools.register(def)`
  （返回 disposer），命名对齐 dsh `mcp-client`（`mcp__<server>__<raw>`）。

⇒ **"不另起 MCP server"指声明不重复，不指传输层不存在。** 独立进程 Hub
（`examples/energy_level/hub.py`）与同进程 `serve_mcp` 都是这唯一声明的 MCP
投影，不是第二套工具面。

**重新考虑条件**：出现第二个非 dsh 的 MCP-only 宿主，且其 ctx.tools 等价物缺失时——届时 MCP server 是**又一个平级操作者适配器**，不违反本决定。

## Alternatives considered

**否决：MCP server 为主形态。** 见本篇 Problem；且它把 v2 压扁回"一个适配器"（v2 定位 ADR 已述）。
**否决：HTTP 自建 RPC。** 重造 dsh 已有的注册/守卫/投影机制，回到 v1 的重复造轮子。
