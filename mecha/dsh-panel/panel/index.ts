/**
 * **`panel` 入口**（浏览器安全）——面板侧的全部公开面。
 *
 * ## 这一半的硬边界
 *
 * 本目录（`panel/`）里的任何件**不许 import**：
 * - 任何 `node:*`（顶层就炸，不是运行时才炸）；
 * - `../host/*`（那一半是 node-only：MCP SDK 会话、端口文件读取、插件骨架）。
 * ⇒ **"把 node-only 带进浏览器 bundle"在入口上不可表达**：
 * 包只发布 `./panel/*` 与 `./host/*` 两个面，`panel` 侧够不到 `host`；
 * 反向（host 用 panel）是**允许**的——`host` 是上面那一层。
 *
 * ⚠ 本文件是**入口**（`exports` 里的 `./panel`）。逐文件子路径 `./panel/*` 也照常可用，
 * 项目沿用原来的写法即可。
 */
export { configurePanel, openPanelConfig, panelConfig } from './panel-config.ts'
export type { Disposer, PanelOptions, PanelTabOverrides, ResolvedPanelConfig } from './panel-config.ts'
export { BASIC_ROUTES, DEFAULT_ROUTE_PATH } from './routes.ts'
export {
  classifyRoute,
  displayTier,
  fetchMonitorBase,
  isNonDegenerate,
  isOriginLike,
  monitorStats,
  panelState,
  readJson,
  statsLine,
  type DoFetch,
  type FetchResult,
  type MonitorFailure,
  type MonitorFailureTier,
  type PanelState,
} from './monitor-client.ts'
export {
  CONFIG_PATH,
  CONFIG_ROUTE,
  HISTORY_PATH,
  HISTORY_ROUTE,
  configOf,
  eventsOf,
  loadPanel,
  modeOf,
  okIsNonDegenerate,
  selfCheckLine,
  stateBadge,
  stateText,
  tabLabel,
  type ExtraRoute,
  type PanelLoad,
  type StandardPage,
} from './panel-data.ts'
export {
  TAB_CSS,
  badgeHtml,
  cfgPageHtml,
  esc,
  extraPageHtml,
  fmtVal,
  recPageHtml,
  tabsHtml,
  type ExtraPageSpec,
} from './panel-view.ts'
export {
  fmtMs,
  fmtNum,
  tokenPageHtml,
  type ContextBreakdownWire,
  type ContextPressureWire,
  type SessionStatsWire,
  type TokenUsageWire,
  type TokenViews,
} from './panel-token.ts'
// ⚠ 地址路由（`monitor-url.ts`）**不在本入口**：它是 node-only（`node:fs`/`http`/`path`），
//   归 `host` 面。面板侧要它，请从 `@mecha/dsh-panel/host/monitor-url.ts` 取。
export { MonitorTabBody } from './MonitorTabBody.tsx'

export { mountClientPanel, HEADER_BUTTON_ID, PANE_TAB_SLOT, PANE_TAB_TITLE_SLOT, HEADER_ACTIONS_SLOT } from './mount-client-plugin.ts'
export type { ClientContext, MountClientPanelOptions } from './mount-client-plugin.ts'

// ⭐ 阶段②：**框架自己的 client 插件**（`@mecha/dsh-panel/auto`）与它消费的那份参数。
// ⚠ 两个模块**都在 panel/ 内**（叶子层）：`auto.ts` 只 import 本目录的件 ⇒ 浏览器安全
//   那条结构守卫（`monitor-client.test.ts` 的 import 闭包检查）覆盖得到它们。
// ⚠ `Disposer` **只从 panel-config.ts 出**（一处家）；services.ts 的 `export type { Disposer }`
//   是同一份类型，这里不重复列出。
export {
  PANEL_SERVICES_ROUTE,
  NEUTRAL_EMPTY_TEXT,
  NEUTRAL_UNREADABLE_TEXT,
  contributeExtraPageRender,
  extraPageRenderFor,
  extraPageRenderKey,
  extraPageSpecsFrom,
  fieldText,
  isPanelServicesPayload,
  type DeclaredExtraColumn,
  type DeclaredExtraPage,
  type DeclaredPanelService,
  type ExtraPageRender,
  type PanelServicesPayload,
} from './services.ts'
export {
  TITLE_PREFIX,
  PLUGIN_NAME,
  PLUGIN_INJECT,
  applyMechaPanels,
  loadPanelServices,
  type ApplyOptions,
  type AutoContext,
} from './panels.ts'
// ⚠ 入口（`auto.ts`）**不在本索引里**：它静态 import `.tsx` 壳，而 `.tsx` 不能进
//   `node --test`（资产既有边界）⇒ 本项目索引（会被单测 import）不许把它拖进来。
//   项目侧照 §7.3.8 的原文从**显式子路径** import 它。
