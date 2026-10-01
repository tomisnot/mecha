/**
 * host 半插件骨架：**挂桥 + 注册工具 + 注册监控地址路由 +（可选）注册一个 command + 收尾**。
 *
 * ## 为什么有它（2026-10-01）
 *
 * 资产做成依赖包之后 `client 半` 已共用，但**每个项目的 `src/index.ts`（host 半）仍各手写
 * 一遍同一套骨架**（实测两家各 155 / 177 行，其中通用机制约 100 行），连 `host/*-hub.ts`
 * 也几乎逐字同形 ⇒ "日志标签 / 服务名 / 生命周期 / teardown 顺序 / 端点每次现解"
 * 全是手抄。本模块把那段**固化成一份**，项目侧只剩参数与自身分支。
 *
 * ⚠ 形状与给桥做的参数化**同形**（`logLabel` / `offlineHint` 的先例），不是新概念。
 *
 * ## 骨架拥有什么 / 项目拥有什么
 *
 * | 骨架拥有（机制） | 项目拥有（内容） |
 * |---|---|
 * | `ctx.effect` 包裹、桥的参数装配、`onTools → syncTools` 两阶段 swap | `resolveUrl`（端点怎么算） |
 * | logger 转发、`onStatus` 三态日志、**端点每次现解的包装** | `logLabel` / `serverName` / `clientInfo` / `offlineHint` |
 * | 会话工厂（`httpSessionFactory` + clientInfo/requestInit） | 显式 URL / 端口文件 / 默认端点（**经它自己的 resolve**） |
 * | 监控地址路由的注册**与注销**（无 webServer 时跳过） | 面板参数（经 `configurePanel` 注入）、项目根 |
 * | 依次收尾：路由 → command → 工具 → 关桥 → 面板注入态 | （可选）一个自己的 command |
 *
 * ## ⚠ 硬约束（都不是风格问题）
 *
 * 1. **零项目字面量**：`logLabel` / `serverName` / `clientInfo` 全是参数。
 * 2. **未给必需参数即响亮失败**：构造期抛（不许留中性默认——那会把"参数没配对"
 *    演成"日志里没有名字"这种静默坏事）。
 * 3. **对 `@deepseek-ai/*` 零 import（连类型都不 import）**：out-of-tree 插件的
 *    node_modules 里**没有**它（宿主提供的 peer）⇒ 引用它会让"本资产能否独立类型检查"
 *    依赖宿主的安装形状。故此处对 `ctx` **只用结构化最小接口**（`HostContext`，与
 *    `register-tools.ts` 的 `HostToolPack` 同一姿势）——项目传 `Context` 时按结构化类型
 *    自然相容，无需本资产认识 cordis。
 * 4. **command 是可选的，关着时什么都不注册**（不留"按了没反应"的死缝）。
 * 5. ⚠ **`mode-request` 那类"写文件请某某接手"的东西不是骨架的一部分**：它依赖"有人在轮询"，
 *    只有有看门人的项目才有 ⇒ 骨架只给**注册 command 的口子**，写什么文件留在项目。
 * 6. ⚠ **MCP SDK 是懒加载的**：只有真的需要建会话时才 `import()` 它 ⇒ 只吃面板（不挂桥）
 *    的部署、以及注入假会话工厂的单测，都不必装 SDK。
 */
import { MechaMcpBridge } from '../mcp-bridge.ts'
import type { BridgeLogger, BridgeOptions, BridgeStatus, SessionFactory, ToolDef } from '../mcp-bridge.ts'
import { syncTools, type ToolDisposers } from './register-tools.ts'
import { makeMonitorUrlHandler } from './monitor-url.ts'
import { type PanelOptions, configurePanel, panelConfig } from '../panel/panel-config.ts'

/** cordis 的 `ctx`：只声明**我们真正用到的那一片**（结构化类型接受真 `Context`）。 */
export interface HostContext {
  logger?: BridgeLogger
  /** 工具注册表（顶层 `inject` 里声明 `tools` 后才在）。 */
  tools?: { register(def: unknown): () => void }
  /** 注册一个命令；返回 disposer。项目的能力开关关着时**不要**调它。
   *  ⚠ 入参与返回值都是 `unknown`：骨架**只创建、不解释**命令（"命令回执长什么样"是
   *  宿主的形状，不是本资产的契约）。写成 `(args) => Promise<{kind,text}>` 会凭空给
   *  两个真实消费者加一道**它们本来不需要的**类型约束（实测：cordis 传的是
   *  `{agent, rawInput}`、回执也是宿主自有形状）⇒ 那是假约束，不是契约。 */
  commands?: {
    register(command: {
      name: string
      description: string
      handler: (args: unknown) => unknown | Promise<unknown>
    }): () => void
  }
  /** 生命期包裹：回调返回的收尾函数在插件卸载时执行；其返回值即"挂载完成"的句柄。 */
  effect?(fn: () => Promise<() => Promise<void> | void>, label: string): Promise<void> | void
  /**
   * 只在某服务就绪时跑一段，**返回值即 disposer**。
   * ⚠ 无 webServer 的部署靠它不炸（顶层 `inject` 缺服务会让**整个插件不激活**，
   * 那会把"面板没有 web 服务"升级成"AI 工具也没了"）。
   */
  inject?(names: string[], fn: (scoped: unknown) => (() => void) | undefined): void
}

/** dsh 的 `webServer` 面（只声明 `register`）。 */
interface WebServerLike {
  register(route: {
    kind: 'exact'
    path: string
    // ⚠ `any` 是刻意的：`unknown` 与项目实现的 `IncomingMessage`/`ServerResponse` **逆变**不兼容
    handler: (req: any, res: any) => void
  }): () => void
}

/** 骨架默认的状态日志（项目可用 `onStatus` 整个覆盖）。`ready` 多给一个已解析端点。 */
export type HostStatusHandler = (status: BridgeStatus, resolvedUrl: string) => void

export interface MountHostPluginOptions {
  /** 工具命名空间：`mcp__<serverName>__<tool>`。**必填**。 */
  serverName: string
  /**
   * 日志前缀，日志一律 `[<label>] …`。**必填**——日志里没有名字的插件无法归因。
   * ⚠ 它同时也是**服务名**（`[paperpilot] paperpilot 离线…`）：拆成两个字段试过一版，
   * 结果是同一个词写两遍的第二处真值 ⇒ 并成一个。
   */
  logLabel: string
  /** 面板端口文件所在的项目根。**必填**。 */
  projectRoot: string
  /**
   * **每次建会话现解**端点（权威重启换端口后重连即连上新端口）。**必填**：
   * 端点怎么算（显式 URL > 端口文件 > 项目默认）是**项目知识**，骨架不猜。
   *
   * ⚠ 骨架**只保存这个函数的引用**，绝不在构造期调用它、也不把它算出的值缓存成字符串
   * ——"每条会话都重新现解"就是端口漂移自愈本身（`mountHostPlugin.test.ts` 用
   * "构造期解析次数 == 0" 钉住这条；退化成"只解一次"会当场红）。
   */
  resolveUrl: () => Promise<string>
  /** `initialize` 上报的客户端标识（项目名 + 版本）。**必填**。 */
  clientInfo: { name: string; version: string }
  /** 额外请求头 / RequestInit（如远程只读鉴权）。 */
  requestInit?: RequestInit
  /**
   * 注入假会话工厂（**只给单测用**）。给了它就**完全不走 SDK**、也不用 `resolveUrl`。
   * 生产不要传——那时由骨架用 `resolveUrl` + `clientInfo` 建真会话工厂。
   */
  sessionFactory?: SessionFactory
  /** 断联时给调用方看的那句指引（**项目知识**：怎么把软件重新起来）。 */
  offlineHint?: string
  /** 重连退避覆盖；**不给的项用桥自己的默认**（别在这里手抄一份默认值 = 第二处真值）。 */
  reconnect?: {
    initialDelayMs?: number
    maxDelayMs?: number
    maxAttempts?: number
  }
  /** 覆盖默认状态日志。 */
  onStatus?: HostStatusHandler
  /** 面板参数（**必须由项目注入**，骨架不存项目值）。 */
  panelConfig: PanelOptions
  /**
   * 可选：注册**项目自己的**一个 command（骨架负责它的收尾）。
   * ⚠ 能力关着时**不要传**——骨架不会注册任何东西（不留死缝）。
   */
  registerCommand?: (ctx: HostContext) => (() => void) | null | undefined
}

function required(name: string, value: string | undefined): string {
  const v = value?.trim()
  if (!v) {
    throw new Error(
      `mountHostPlugin 的 ${name} 不能为空。`
      + '⚠ 骨架不许替项目猜名字：日志标签 / 工具命名空间 / 客户端标识都是**项目专有**的值，'
      + '给一个中性默认会把"参数没配对"演成"日志里没有名字"这种静默坏事。',
    )
  }
  return v
}

/**
 * 挂载 host 半插件：**机制全在这里**，项目只给参数与自身分支。
 *
 * @returns `ctx.effect(...)` 的结果（无 `effect` 的环境下，返回收尾函数本身）。
 */
export async function mountHostPlugin(
  ctx: HostContext,
  opts: MountHostPluginOptions,
): Promise<void> {
  // ⚠ **先校验、再动手**：所有必需参数在任何副作用**之前**校验完
  //   ⇒ 参数写错时**不留半挂载状态**（也不留下一次注入的面板态）。
  const serverName = required('serverName', opts.serverName)
  const logLabel = required('logLabel', opts.logLabel)
  const projectRoot = required('projectRoot', opts.projectRoot)
  const clientName = required('clientInfo.name', opts.clientInfo?.name)
  const clientVersion = required('clientInfo.version', opts.clientInfo?.version)
  if (opts.resolveUrl === undefined) {
    throw new Error(
      'mountHostPlugin 需要 resolveUrl（端点怎么算）。'
      + '⚠ 骨架不猜端点：端口文件名与默认端点是**项目专有**的值。'
      + '（单测若要绕过真实连接，得同时给一个假的 sessionFactory。）',
    )
  }
  if (opts.panelConfig === undefined) {
    throw new Error(
      'mountHostPlugin 需要 panelConfig（面板参数：routePath / portFile / title）。'
      + '⚠ 面板参数是**项目专有**的值，必须由项目注入（资产不存项目值）。',
    )
  }
  const tag = `[${logLabel}]`

  // 面板参数注入（可逆）：字段留空 ⇒ `configurePanel` 自己抛（响亮失败）。
  // 骨架**不解释**这些值，只透传 ⇒ 面板参数的字段名只有一处真值（panel-config.ts）。
  const disposePanel = configurePanel(opts.panelConfig)
  const panel = panelConfig()

  const mount = async (): Promise<() => Promise<void>> => {
    let disposers: ToolDisposers = new Map()
    let lastUrl = ''                 // 仅日志展示：最近一次解析到的端点
    let bridge: MechaMcpBridge

    // 端点**每次建会话现解**（不固化）：把它包成"重连工厂每次调用都会重新解析"，
    // 这是端口漂移自愈的关键一环；顺带把最近一次结果留给自己打日志。
    const sessionFactory: SessionFactory = opts.sessionFactory
      ?? (await buildHttpSessionFactory(
        async () => { lastUrl = await opts.resolveUrl(); return lastUrl },
        { name: clientName, version: clientVersion },
        opts.requestInit,
      ))

    const options: BridgeOptions = {
      sessionFactory,
      logLabel,
      // ⚠ 只有项目**显式给了**才覆盖（不给 = undefined ⇒ 用桥自己的默认）。
      ...(opts.offlineHint !== undefined ? { offlineHint: opts.offlineHint } : {}),
      ...(opts.reconnect !== undefined ? { reconnect: opts.reconnect } : {}),
      logger: {
        info: m => ctx.logger?.info?.(m),
        warn: m => ctx.logger?.warn?.(m),
        error: m => ctx.logger?.error?.(m),
      },
      // 桥发现/刷新工具 → 两阶段 swap 重注册（首连与每次重连后都走这里）。
      onTools: (tools: ToolDef[]) => {
        disposers = syncTools(
          { tools: requireTools(ctx), ...(ctx.logger !== undefined ? { logger: ctx.logger } : {}) },
          bridge,
          serverName,
          tools,
          disposers,
          { logLabel },
        )
      },
      onStatus: (status: BridgeStatus) => {
        if (opts.onStatus !== undefined) {
          opts.onStatus(status, lastUrl)
          return
        }
        // 默认三态日志：断联必须**显式**（人/AI 不该面对"工具在但静默坏"）。
        if (status === 'offline') {
          ctx.logger?.warn?.(`${tag} ${logLabel} 离线：工具暂不可用，后台重连中`)
        } else if (status === 'ready') {
          // ⚠ 端点只在"知道"时才打（不给 resolveUrl 就不编一个）。
          ctx.logger?.info?.(`${tag} 已连上 ${logLabel} MCP${lastUrl === '' ? '' : `：${lastUrl}`}`)
        } else if (status === 'reconnecting') {
          ctx.logger?.warn?.(`${tag} 与 ${logLabel} 的连接中断，重连中…`)
        }
      },
    }
    bridge = new MechaMcpBridge(options)

    ctx.logger?.info?.(
      `${tag} 挂载 ${logLabel} MCP 桥（端点每次建会话现解，只 connect 不 spawn 权威）`)

    // ---- 监控地址的同源只读路由 ----------------------------------------------
    // 每次被 fetch 都**现读**端口文件 ⇒ 权威重启换端口后下一拍落在新端口。
    // ⚠ 用 `ctx.inject(['webServer'])`：无 webServer 时**跳过而不是不激活**（见 HostContext）。
    let disposeRoute: (() => void) | undefined
    ctx.inject?.(['webServer'], (scoped: unknown) => {
      const webServer = (scoped as { webServer?: WebServerLike }).webServer
      if (webServer === undefined) return undefined      // 没有 web 服务：面板没地址路由，桥照活
      const dispose = webServer.register({
        kind: 'exact',
        path: panel.routePath,
        handler: makeMonitorUrlHandler({ root: projectRoot, portFile: panel.portFile }),
      })
      disposeRoute = dispose
      ctx.logger?.info?.(
        `${tag} 注册监控地址路由 ${panel.routePath}`
        + `（读 ${projectRoot} 的 ${panel.portFile}，不回落默认端口）`)
      return dispose
    })

    // ---- 项目自己的 command（可选；关着时什么都不注册）------------------------
    const disposeCommand = opts.registerCommand?.(ctx) ?? null

    await bridge.start()   // 服务未起也不抛：插件照常加载，桥后台重连，起来后自动注册工具

    return async () => {
      // 收尾顺序：路由 → command → 工具 → 关桥 → 面板注入态（与挂载大致相反）。
      // 每步各自吞错：一个注销失败不该让剩下的资源泄漏。
      if (disposeRoute !== undefined) { try { disposeRoute() } catch { /* ignore */ } }
      if (disposeCommand !== null) { try { disposeCommand() } catch { /* ignore */ } }
      for (const dispose of disposers.values()) {
        try { dispose() } catch { /* ignore */ }
      }
      await bridge.close()
      disposePanel()
    }
  }

  if (ctx.effect !== undefined) return await ctx.effect(mount, `${logLabel}: host MCP bridge`)
  await mount()
}

/**
 * 顶层 `inject` 数组（cordis 要求它在**模块顶层**给出，故只能由项目自己 `export`）。
 *
 * ⚠ **恒为 `['tools']`，不含 `webServer`**——这不是省事，是**与骨架内部语义保持一致**：
 * 顶层 `inject` 里列了 `webServer` ⇒ **缺该服务时整个插件不激活**；
 * 而地址路由是**可选**能力（骨架内部用 `ctx.inject(['webServer'], …)`，没它就跳过注册）。
 * 两者若不一致，就会出现"**面板没有 web 服务**"被升级成"**AI 工具也没了**"（headless 部署实测踩过）。
 *
 * ⚠ 曾经这里是 `hostPluginInject({ needsWebServer })`，默认恰好是**错的那一档**：
 * 一家下游发现了并传 `false` 绕过，另一家没发现、继承了脆弱默认 ⇒ **默认值选错的典型**。
 * 参数已删：**没有任何调用方需要它**（要不要路由由骨架内部的条件处理，不由顶层硬要）。
 */
export function hostPluginInject(): string[] {
  return ['tools']
}

// ---------------------------------------------------------------------------

/**
 * 建真会话工厂。⚠ **`mcp-session-http.ts` 是懒加载的**：它顶层 import MCP SDK，
 * 而 SDK 是**用桥的项目**才装的 optional peer ⇒ 只吃面板、或注入假会话的单测
 * 都不该被它拖住（也就不会因为没装 SDK 而炸）。
 */
async function buildHttpSessionFactory(
  url: () => Promise<string>,
  clientInfo: { name: string; version: string },
  requestInit: RequestInit | undefined,
): Promise<SessionFactory> {
  const { httpSessionFactory } = await import('./mcp-session-http.ts')
  return httpSessionFactory({ url, clientInfo, requestInit })
}

function requireTools(ctx: HostContext): { register(def: unknown): () => void } {
  const tools = ctx.tools
  if (tools === undefined || typeof tools.register !== 'function') {
    throw new Error(
      'mountHostPlugin 需要 ctx.tools（工具注册表）。'
      + '⚠ cordis 里请在插件顶层 `inject` 里声明 `tools`（见 hostPluginInject()），'
      + '否则本插件不该被激活。',
    )
  }
  return tools
}
