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
export { configurePanel, panelConfig } from './panel-config.ts'
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
