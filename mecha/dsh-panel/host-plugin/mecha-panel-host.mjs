/**
 * **框架自己的 dsh host 插件**：把「监控面参数」从 **env** 递给**浏览器**。
 *
 * ## 为什么需要这一件（三层架构 §7.3.8 第 1 条，据实更正）
 *
 * §7.3.6 写的是"框架起 dsh 时生成那份参数、**经 env 递给自己那份 client 插件**"——
 * ⚠ **字面做不到**：client 半跑在**浏览器**里（§7.3.2 坑 2 早已说明它是**另一个模块实例**），
 * 那里**没有** `process.env`。⇒ 参数的真实通路是：
 *
 * ```
 * mecha_site.py 的声明
 *   ⇒ mecha/dsh_panel.py 生成一份参数（不含端口号）
 *   ⇒ MECHA_PANEL_ROUTE / MECHA_PANEL_SERVICES 两个环境变量
 *   ⇒ 【本插件】（node 侧，能读 env）
 *   ⇒ 同源只读路由（浏览器 fetch 得到 JSON）
 *   ⇒ 框架的 client 半（@mecha/dsh-panel/auto）按服务挂 N 个 tab
 * ```
 *
 * ⭐ 走**同源只读路由**不是新发明：三家项目的"监控地址路由"就是这条路
 * （`webServer.register({kind:'exact'})` + 浏览器 fetch 同一路径），已被真机验证。
 *
 * ## 为什么是**纯 JS、零 import**
 *
 * dsh 的 patch 只能指一个 **node 能直接 import 的模块**（`cordis.patch.yml` 的
 * `name: './lib/index.mjs'` 同族），而这份资产是 TS、**没有构建步骤**
 * （项目用它时由各自的 tsdown 打包）。⇒ 这一件必须**不依赖任何构建**：
 * 手写 JS、零 import（`ctx` 只用**结构化最小接口**，不 import cordis）。
 * ⚠ 代价如实记：它**不进** `tsc` 类型检查，靠 `mecha-panel-host.test.ts` 的
 * **行为判据**守（`node --test` 直接 import 它，喂假 ctx / 假 req、res）。
 *
 * ## 硬约束
 *
 * 1. ⚠ **参数缺失/坏 ⇒ 响亮失败**（抛）：不许"静默起一个没有面板的 dsh"，
 *    那会把"框架没把参数送到"演成"这个项目没有面板"（本工程最贵的那类假绿）；
 * 2. ⚠ **端口号不进这里**：本插件只转发声明里那几项（路由 / 端口**文件名** / 标题 /
 *    附加页）。端点地址由**各项目自己的 host 路由**现读端口文件得到（见 Known Limitations）；
 * 3. ⚠ **不吞异常**：注册失败要让 cordis 看见（插件加载失败是**响亮**的，不是静默的）；
 * 4. ⚠ 没有 `webServer` 时**跳过而不是不激活**：用 `ctx.inject(['webServer'], …)`，
 *    顶层 `inject` 里**不写** `webServer`（写在那里 ⇒ 缺该服务时**整个插件不激活**，
 *    与资产骨架 `hostPluginInject()` 同一条纪律）。
 */

/** 要显式声明的服务（缺它 ⇒ 本插件不激活 ⇒ 那不是我们要的语义，见硬约束 4）。 */
export const inject = []

/** dsh 侧的行身份（诊断/日志用）。 */
export const name = 'mecha-panel-host'

/** env 变量名（与 `mecha/dsh_panel.py` 同一处口径）。 */
export const ROUTE_ENV = 'MECHA_PANEL_ROUTE'
export const SERVICES_ENV = 'MECHA_PANEL_SERVICES'

/**
 * 读并校验那份参数（**缺失/坏 ⇒ 抛**）。
 *
 * @param {Record<string, string | undefined>} [env] 取参数的来源（判据注入假 env）。
 * @returns {{routePath: string, services: unknown[]}}
 */
export function readPanelPayload(env = process.env) {
  const routePath = (env[ROUTE_ENV] ?? '').trim()
  const raw = (env[SERVICES_ENV] ?? '').trim()
  if (!routePath) {
    throw new Error(
      `mecha-panel-host: 缺 ${ROUTE_ENV} —— 框架没有把「监控面板路由」交过来。`
      + '⚠ 这一件由**框架的 patch** 带上（`mecha-panel.patch.yml`），参数由'
      + ' `mecha/dsh_panel.py` 按站点声明生成；缺它说明那条路断了，'
      + '**不是**"这个项目没有面板"（不许静默降级）。',
    )
  }
  if (!routePath.startsWith('/')) {
    throw new Error(`mecha-panel-host: ${ROUTE_ENV} 要以 '/' 开头，收到 ${JSON.stringify(routePath)}`)
  }
  if (!raw) {
    throw new Error(
      `mecha-panel-host: 缺 ${SERVICES_ENV}（那份按服务列的参数）。`
      + '⚠ 框架**恒**注入它（没有任何服务声明 monitor 时是空数组）⇒ 缺它 = 注入点断了。',
    )
  }
  let parsed
  try {
    parsed = JSON.parse(raw)
  } catch (error) {
    throw new Error(`mecha-panel-host: ${SERVICES_ENV} 不是 JSON：${String(error)}`)
  }
  if (parsed === null || typeof parsed !== 'object' || !Array.isArray(parsed.services)) {
    throw new Error(
      `mecha-panel-host: ${SERVICES_ENV} 的形状必须是 {routePath, services: [...]}`
      + `（收到 ${JSON.stringify(parsed)?.slice(0, 200)}）`,
    )
  }
  // ⚠ routePath 以**环境变量**为准（它就是浏览器要 fetch 的那个串）；payload 里那份是
  //   **给人看的同一份**，两者不一致是接线错误 ⇒ 响亮失败（不猜哪个对）。
  if (parsed.routePath !== undefined && parsed.routePath !== routePath) {
    throw new Error(
      `mecha-panel-host: 两份路由不一致（${ROUTE_ENV}=${JSON.stringify(routePath)} vs `
      + `payload.routePath=${JSON.stringify(parsed.routePath)}）——改一处必须改两处`,
    )
  }
  return { routePath, services: parsed.services }
}

/** 那份参数的 JSON 回执（⚠ **不含端口号**，见硬约束 2）。 */
export function makePanelHandler(env = process.env) {
  const payload = readPanelPayload(env)
  const body = JSON.stringify(payload)
  return (_req, res) => {
    res.writeHead(200, {
      'content-type': 'application/json; charset=utf-8',
      // ⚠ 参数是**启动期**决定的，但别让浏览器缓存住旧的那份（改声明重启 dsh 后要立刻生效）。
      'cache-control': 'no-store',
    })
    res.end(body)
  }
}

/**
 * 挂载：把那**一个**同源只读路由注册上。
 *
 * @param {object} ctx cordis 的 `ctx`（只用到 `logger` / `webServer` / `inject` / `effect`）。
 */
export async function apply(ctx) {
  const payload = readPanelPayload()          // ⚠ 先校验、再动手（坏参数不留半挂载态）
  ctx.logger?.info?.(
    `[mecha-panel] 框架面板插件：${payload.services.length} 个服务声明了监控面`
    + `（路由 ${payload.routePath}）`,
  )
  const mount = () => {
    let dispose
    // ⚠ `ctx.inject(['webServer'], …)`：没有 web 服务时**跳过**（不是整个插件不激活）。
    ctx.inject?.(['webServer'], (scoped) => {
      const webServer = scoped?.webServer
      if (webServer === undefined) return undefined
      dispose = webServer.register({
        kind: 'exact',
        path: payload.routePath,
        handler: makePanelHandler(),
      })
      return dispose
    })
    return () => {
      try { dispose?.() } catch { /* 收尾失败不掩盖其余收尾（与资产骨架同口径） */ }
    }
  }
  if (typeof ctx.effect === 'function') return await ctx.effect(mount, 'mecha-panel-host: panel services route')
  mount()
}
