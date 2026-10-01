/**
 * **`host` 入口**（node-only）——挂桥、注册工具、插件骨架、端点解析。
 *
 * ## 这一半允许什么 / 不许什么
 *
 * - **允许** import `panel/`（面板是下面那一层：`panel-config` 这类两半都用的件住那里，
 *   host 侧注入面板参数就是用它）；
 * - **会** 拖进 MCP SDK（`mcp-session-http.ts` 顶层 import 它）⇒ 用本入口的项目
 *   需要装 `@modelcontextprotocol/sdk`（optional peer）；**只吃面板的项目不要碰本入口**。
 *
 * ⚠ 本文件是**入口**（`exports` 里的 `./host`）。逐文件子路径 `./host/*` 也照常可用。
 */
export {
  hostPluginInject,
  mountHostPlugin,
  type HostContext,
  type HostStatusHandler,
  type MountHostPluginOptions,
} from './mount-host-plugin.ts'
export { buildDefinition, publicToolName, syncTools, type ToolDisposers } from './register-tools.ts'
export { makeMonitorUrlHandler, resolveMonitorBase, sendJson, type MonitorUrlOptions } from './monitor-url.ts'
export { buildRequestInit, resolveHubUrl, type HubUrlConfig, type RemoteConfig } from './config.ts'
export { httpSessionFactory, type HttpSessionOptions } from './mcp-session-http.ts'
