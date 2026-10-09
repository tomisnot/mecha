/**
 * `mecha-panel-host.mjs` 的**类型声明**（实现是**手写 JS**：dsh 的 patch 只能指 node 能直接
 * import 的模块，而这份资产是 TS、没有构建步骤 ⇒ 那一件不能是 TS）。
 *
 * ⚠ **两份会不会漂**（声明 vs 实现）：会 —— 所以它由 `mecha-panel-host.test.ts` 的
 * **行为判据**（真 import 那个 `.mjs`、喂假 ctx/req/res）守，本声明只负责让 **`tsc`
 * 检查调用侧**（形状写错在类型检查期就红）。
 *
 * ⚠ 本文件**不 export 任何运行期值**，只是类型面。
 */

/** dsh 的 `webServer` 面（只声明本插件用到的那一个方法）。 */
export interface PanelWebServer {
  register(route: { kind: string; path: string; handler: unknown }): () => void
}

/** cordis 的 `ctx`：只声明**本插件真正用到的那几片**（结构化类型，接受真 `Context`）。 */
export interface PanelHostContext {
  logger?: { info?: (m: string) => void }
  /** ⚠ 用 `ctx.inject(['webServer'], …)`（**不在**顶层 `inject` 里写它）：
   *  写在那里 ⇒ 缺该服务时**整个插件不激活**。 */
  inject?: (names: string[], fn: (scoped: { webServer?: PanelWebServer }) => unknown) => unknown
  effect?: (fn: () => unknown, label: string) => unknown
}

/** 那份参数（`mecha/dsh_panel.py` 生成；⚠ **不含端口号**）。 */
export interface PanelPayload {
  routePath: string
  services: unknown[]
}

/** env 变量名（与 `mecha/dsh_panel.py` 同一处口径）。 */
export const ROUTE_ENV: string
export const SERVICES_ENV: string

/** 只显式声明的服务（本插件**不需要**任何服务依赖）。 */
export const inject: string[]

/** dsh 侧的行身份。 */
export const name: string

/** 读并校验那份参数（**缺失/坏 ⇒ 抛**）。 */
export function readPanelPayload(env?: Record<string, string | undefined>): PanelPayload

/** 那份参数的 JSON 回执函数。 */
export function makePanelHandler(env?: Record<string, string | undefined>): (
  req: unknown,
  res: { writeHead(status: number, headers: Record<string, string>): void; end(body: string): void },
) => void

/** 挂载：注册那**一个**同源只读路由。 */
export function apply(ctx: PanelHostContext): Promise<unknown>
