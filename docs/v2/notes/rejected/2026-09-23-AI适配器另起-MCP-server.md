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

**重新考虑条件**：出现第二个非 dsh 的 MCP-only 宿主，且其 ctx.tools 等价物缺失时——届时 MCP server 是**又一个平级操作者适配器**，不违反本决定。

## Alternatives considered

**否决：MCP server 为主形态。** 见本篇 Problem；且它把 v2 压扁回"一个适配器"（v2 定位 ADR 已述）。
**否决：HTTP 自建 RPC。** 重造 dsh 已有的注册/守卫/投影机制，回到 v1 的重复造轮子。
