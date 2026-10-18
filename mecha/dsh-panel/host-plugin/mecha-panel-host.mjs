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
 *   ⇒ MECHA_PANEL_ROUTE / MECHA_PANEL_SERVICES（给浏览器）+ ⭐ MECHA_PANEL_PORT_FILES（只给本插件）
 *   ⇒ 【本插件】（node 侧，能读 env）
 *   ⇒ 同源只读路由（浏览器 fetch 得到 JSON / 地址）
 *   ⇒ 框架的 client 半（@mecha/dsh-panel/auto）按服务挂 N 个 tab
 * ```
 *
 * ⭐ 走**同源只读路由**不是新发明：三家项目的"监控地址路由"就是这条路
 * （`webServer.register({kind:'exact'})` + 浏览器 fetch 同一路径），已被真机验证。
 *
 * ## 为什么是**纯 JS、零 import**（node 内建除外）
 *
 * dsh 的 patch 只能指一个 **node 能直接 import 的模块**（`cordis.patch.yml` 的
 * `name: './lib/index.mjs'` 同族），而这份资产是 TS、**没有构建步骤**
 * （项目用它时由各自的 tsdown 打包）。⇒ 这一件必须**不依赖任何构建**：
 * 手写 JS、不 import 任何第三方（`ctx` 只用**结构化最小接口**，不 import cordis）。
 * ⚠ 代价如实记：它**不进** `tsc` 类型检查，靠 `mecha-panel-host.test.ts` 的
 * **行为判据**守（`node --test` 直接 import 它，喂假 ctx / 假 req、res），
 * 其中一条是**与资产 `host/monitor-url.ts` 的等价判据**（同一组夹具喂两边）。
 *
 * ## 硬约束
 *
 * 1. ⚠ **参数缺失/坏 ⇒ 响亮失败**（抛）：不许"静默起一个没有面板的 dsh"，
 *    那会把"框架没把参数送到"演成"这个项目没有面板"（本工程最贵的那类假绿）；
 * 2. ⚠ **端口号不进这里**：本插件只转发声明里那几项（路由 / 端口**文件名** / 标题 /
 *    附加页）——⭐⭐ **端口由它自己现读端口文件取**（`MECHA_PANEL_PORT_FILES` 给的是
 *    **绝对路径**，它**只给本插件**、不进给浏览器的那份参数）；
 * 3. ⚠ **不吞异常**：注册失败要让 cordis 看见（插件加载失败是**响亮**的，不是静默的）；
 * 4. ⚠ 没有 `webServer` 时**跳过而不是不激活**：用 `ctx.inject(['webServer'], …)`，
 *    顶层 `inject` 里**不写** `webServer`（写在那里 ⇒ 缺该服务时**整个插件不激活**，
 *    与资产骨架 `hostPluginInject()` 同一条纪律）。
 *
 * ## ⭐ 它注册**两类**路由（2026-10-09 起）
 *
 * | 路由 | 给谁 | 回什么 |
 * |---|---|---|
 * | `MECHA_PANEL_ROUTE`（`/mecha/panels`） | 浏览器（那份参数） | `{routePath, services:[…]}` |
 * | ⭐ **每个服务的 `routePath`** | 浏览器（取监控地址） | `200 {base}` / `503 {error}` |
 *
 * ⚠ 第二类是**上提**来的：原先"读端口文件现取端口"由**每个项目自己的 host 半**注册
 * （漏一个文件就静默空白）；现在框架照 `MECHA_PANEL_PORT_FILES` 替项目注册
 * ⇒ **新项目连 host 半都不用写**（§7.3.8 末段登记的下一步，已落地）。
 */
import { existsSync, readFileSync } from 'node:fs'
import { basename } from 'node:path'

/** 要显式声明的服务（缺它 ⇒ 本插件不激活 ⇒ 那不是我们要的语义，见硬约束 4）。 */
export const inject = []

/** dsh 侧的行身份（诊断/日志用）。 */
export const name = 'mecha-panel-host'

/** env 变量名（与 `mecha/dsh_panel.py` 同一处口径）。 */
export const ROUTE_ENV = 'MECHA_PANEL_ROUTE'
export const SERVICES_ENV = 'MECHA_PANEL_SERVICES'
/** ⭐ 第三个变量：`{svc_id: 端口文件**绝对**路径}` —— **只给本插件**（绝不交给浏览器）。 */
export const PORT_FILES_ENV = 'MECHA_PANEL_PORT_FILES'
export const MCP_REFRESH_ROUTE = '/mecha/mcp/refresh'
const AGGREGATE_MCP_ENTRY_ID = 'mcp-mecha'

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

/**
 * 读 `{svc_id: 绝对端口文件路径}`（⭐ **替项目取端口**那一条路由要用它）。
 *
 * ⚠ 缺 / 空 ⇒ 回 **空表**（**不是抛**）：一个服务都没声明 `monitor` 时框架注入的就是 `{}`
 * —— 那与"注入点断了"是**两件事**，不能混（铁律 8）。
 * ⚠ 而**有服务却没有它的路径** ⇒ 由 `mountRoutes` 在那一处**响亮记错**（不静默给个空路由）。
 *
 * @param {Record<string, string | undefined>} [env]
 * @returns {Record<string, string>}
 */
export function readPortFiles(env = process.env) {
  const raw = (env[PORT_FILES_ENV] ?? '').trim()
  if (!raw) return {}
  let parsed
  try {
    parsed = JSON.parse(raw)
  } catch (error) {
    throw new Error(`mecha-panel-host: ${PORT_FILES_ENV} 不是 JSON：${String(error)}`)
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error(
      `mecha-panel-host: ${PORT_FILES_ENV} 的形状必须是 {svc_id: 绝对路径}`
      + `（收到 ${JSON.stringify(parsed)?.slice(0, 200)}）`,
    )
  }
  return /** @type {Record<string, string>} */ (parsed)
}

/** 那份参数的 JSON 回执（⚠ **不含端口号 / 不含绝对路径**，见硬约束 2）。 */
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

// ---------------------------------------------------------------- 监控地址（⭐ 替项目取端口）

/**
 * 解析监控端点基址：`http://127.0.0.1:<port>`（读**权威写的那个端口文件**，每次现读）。
 *
 * ## ⚠ 与资产 `host/monitor-url.ts` 的 `resolveMonitorBase` **必须同形状**
 *
 * 两者是"同一件事的两份实现"（资产那份是 TS、由**项目**调用；这份是纯 JS、由**框架**调用，
 * 因为它要零 import、不依赖任何构建）。⇒ 由 `mecha-panel-host.test.ts` 的
 * **等价判据**钉住：同一组夹具喂两边，**成功回执必须逐字节相同**。
 *
 * ## 三条纪律（照抄资产那份 —— 它来自本工程跑得最久的那份实现）
 *
 * 1. ⭐ **绝不回落默认端口**：文件不在 / 内容非数字 / 端口越界 ⇒ **抛**，
 *    路由回 `503` + 可读错误（回落会把"权威没起来"演成"连上了但空面板"）；
 * 2. **每次调用现读**（端口漂移下一拍就落在新端口）；**不缓存**；
 * 3. ⚠ **错误文本里不出现本机绝对路径**（它会给**浏览器**）⇒ 只说**文件名**。
 */
export function resolveMonitorBase(portFile) {
  const p = String(portFile ?? '').trim()
  if (!p) {
    throw new Error(
      '监控端口文件未配置（声明里的 `monitor.port_file` 为空）：本插件不替项目猜文件名',
    )
  }
  const shown = basename(p)
  let raw = ''
  try {
    raw = readFileSync(p, 'utf8').trim()
  } catch (err) {
    throw new Error(`读不到监控端口（${shown} 不存在）：软件没在 AI 模式跑？(${err.message})`)
  }
  if (!/^\d+$/.test(raw)) {
    throw new Error(`监控端口文件内容非法：${JSON.stringify(raw)}（${shown}）`)
  }
  const port = Number(raw)
  if (!(port > 0 && port <= 65535)) {
    throw new Error(`监控端口越界：${port}（${shown}）`)
  }
  return `http://127.0.0.1:${port}`
}

/** 写一份 JSON 响应（⚠ `Content-Length` 用**字节数**：中文错误体按字符算会截断）。 */
export function sendJson(res, status, payload) {
  const body = Buffer.from(JSON.stringify(payload), 'utf8')
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': String(body.length),
    'cache-control': 'no-store',
  })
  res.end(body)
}

/** 一条服务的地址路由：能解析 ⇒ `200 {base}`；解析不了 ⇒ `503 {error}`（**不回落**）。 */
export function makeMonitorUrlHandler(portFile) {
  return (_req, res) => {
    try {
      sendJson(res, 200, { base: resolveMonitorBase(portFile) })
    } catch (err) {
      // ⚠ 失败体里**没有** `base` 字段：不许回半截地址（否则面板会拿它去 fetch）。
      sendJson(res, 503, { error: `监控端点未知：${err.message}` })
    }
  }
}

// 聚合 MCP 客户端刷新也属于 mecha host 插件职责：不另起一个 DSH 插件入口。
function startMcpRefresh(ctx) {
  const argv = process.argv
  let file
  for (let i = 0; i < argv.length - 1; i++) {
    if (argv[i] === '--patch' && basename(String(argv[i + 1] ?? '')) === 'dsh-aggregate.patch.yml') {
      file = String(argv[i + 1])
      break
    }
  }
  if (!file) return undefined
  let inFlight
  let stopped = false
  const findEntry = (id) => {
    try { return ctx.loader?.resolve(id) } catch { /* nested entry ids may differ */ }
    const rows = [...(ctx.loader?.entries?.() ?? [])]
    return rows.find((row) => row.options?.name === '@deepseek-ai/dsh-mcp-client'
      && row.options?.config?.serverName === 'mecha')
  }
  const inspect = () => {
    let text = ''
    try { text = readFileSync(file, 'utf8') } catch (error) {
      return { found: false, error: `overlay unreadable: ${error.message}` }
    }
    const id = text.match(/^\s*- id:\s*([^\s#]+)/m)?.[1] ?? AGGREGATE_MCP_ENTRY_ID
    const requestedUrl = text.match(/^\s*url:\s*(\S+)\s*$/m)?.[1] ?? null
    const entry = findEntry(id)
    if (!entry) return { id, requestedUrl, found: false }
    return {
      id: entry.id,
      requestedUrl,
      found: true,
      fiberUid: entry.fiber?.uid ?? null,
      fiberState: entry.fiber?.state ?? null,
      currentUrl: entry.options?.config?.url ?? null,
    }
  }
  const refresh = async () => {
    if (stopped || !existsSync(file)) return
    let text
    try { text = readFileSync(file, 'utf8') } catch { return }
    const id = text.match(/^\s*- id:\s*([^\s#]+)/m)?.[1] ?? AGGREGATE_MCP_ENTRY_ID
    const url = text.match(/^\s*url:\s*(\S+)\s*$/m)?.[1]
    if (!url || inFlight === url) return false
    const entry = findEntry(id)
    const fiber = entry?.fiber
    if (!fiber?.uid) return false
    inFlight = url
    try {
      const config = { ...(entry.options?.config ?? {}), url }
      if (entry.options?.config?.url === url) {
        // 工具表变化时聚合地址通常不变；必须显式重启 MCP client，
        // 不能把“URL 未变”误当成“无需刷新”。
        await fiber.restart()
      } else {
        await fiber.update(config, true)
      }
      await fiber.await()
      ctx.logger?.info?.(`[mecha-panel] 已重新初始化 ${id}（${url}）`)
      return true
    } catch (error) {
      ctx.logger?.error?.(`[mecha-panel] 重初始化 ${id} 失败：${error.message}`)
      return false
    } finally {
      if (inFlight === url) inFlight = undefined
    }
  }
  const trigger = async (attempt = 0) => {
    const done = await refresh()
    if (done || stopped || attempt >= 20) {
      if (!done && !stopped) {
        ctx.logger?.error?.(`[mecha-panel] 找不到可重载的聚合 MCP Entry（id=${AGGREGATE_MCP_ENTRY_ID}）`)
      }
      return done
    }
    await new Promise(resolve => setTimeout(resolve, 250))
    return trigger(attempt + 1)
  }
  trigger()
  return { trigger, inspect, stop: () => {
    stopped = true
  } }
}

/**
 * 挂载：注册那**一个**同源只读路由（那份参数）+ ⭐ **每个服务一条地址路由**。
 *
 * ⭐ **地址路由归框架**（2026-10-09 上提，§7.3.8 末段登记的下一步）：
 * 原先每条 `routePath` 由**项目自己的 host 半**注册（"读端口文件现取端口"那段
 * 每家抄一遍，而且漏一个文件就静默空白）⇒ 现在框架拿
 * `MECHA_PANEL_PORT_FILES`（**绝对**路径）替项目注册 ⇒ **新项目连 host 半都不用写**。
 *
 * ⚠ **启动期配置错 ⇒ 抛**（响亮）：某个声明了监控面的服务**没有**端口文件路径，
 * 说明框架那两个变量不是同一次注入生成的 —— 那是接线错误，不许静默起一个
 * "有一半路由"的面板（硬约束 1）。⚠ 而**运行期**的"软件还没写端口文件"属另一种：
 * 那由路由回 `503` + 可读错误（面板如实显示"读不到"），**不是**同一个病。
 *
 * @param {object} ctx cordis 的 `ctx`（只用到 `logger` / `webServer` / `inject` / `effect`）。
 */
export async function apply(ctx) {
  const payload = readPanelPayload()          // ⚠ 先校验、再动手（坏参数不留半挂载态）
  const portFiles = readPortFiles()
  const missing = payload.services
    .filter((s) => typeof portFiles[s?.id] !== 'string' || portFiles[s.id].trim() === '')
    .map((s) => String(s?.id))
  if (missing.length > 0) {
    throw new Error(
      `mecha-panel-host: ${PORT_FILES_ENV} 里缺少这些服务的端口文件路径：${missing.join('、')}`
      + '。⚠ 它与那份参数由**框架同一个注入点**生成（`mecha/dsh_panel.py`）⇒ '
      + '缺 = 注入断了，**不是**"这个项目没有面板"（不许静默起一个只有一半路由的面板）。',
    )
  }
  ctx.logger?.info?.(
    `[mecha-panel] 框架面板插件：${payload.services.length} 个服务声明了监控面`
    + `（参数路由 ${payload.routePath}）`,
  )
  const mount = () => {
    const disposers = []
    // ⚠ `ctx.inject(['webServer'], …)`：没有 web 服务时**跳过**（不是整个插件不激活）。
    ctx.inject?.(['webServer'], (scoped) => {
      const webServer = scoped?.webServer
      if (webServer === undefined) return undefined
      disposers.push(webServer.register({
        kind: 'exact',
        path: payload.routePath,
        handler: makePanelHandler(),
      }))
      for (const svc of payload.services) {
        const portFile = portFiles[svc.id]
        disposers.push(webServer.register({
          kind: 'exact',
          path: svc.routePath,
          handler: makeMonitorUrlHandler(portFile),
        }))
        ctx.logger?.info?.(
          `[mecha-panel] 注册监控地址路由 ${svc.routePath}`
          + `（服务 ${svc.id}；现读 ${basename(portFile)}，不回落默认端口）`,
        )
      }
      if (mcpRefresh) {
        disposers.push(webServer.register({
          kind: 'exact',
          path: MCP_REFRESH_ROUTE,
          handler: (_req, res) => {
            // MCP 重连可能需要数秒；控制面只负责接收触发，不能把 DSH HTTP
            // 请求绑在重连生命周期上。结果写入 DSH 自己的日志。
            if (_req?.method === 'GET') {
              sendJson(res, 200, { ok: true, diagnostic: mcpRefresh.inspect() })
              return
            }
            ctx.logger?.info?.('[mecha-panel] 收到聚合 MCP 刷新请求')
            void mcpRefresh.trigger()
            sendJson(res, 202, { ok: true, accepted: 'mcp-refresh', target: mcpRefresh.inspect() })
          },
        }))
      }
      return () => {
        for (const d of disposers.splice(0)) {
          try { d?.() } catch { /* 收尾失败不掩盖其余收尾（与资产骨架同口径） */ }
        }
      }
    })
    return () => {
      for (const d of disposers.splice(0)) {
        try { d?.() } catch { /* 同上 */ }
      }
    }
  }
  const mcpRefresh = startMcpRefresh(ctx)
  if (typeof ctx.effect === 'function') {
    return await ctx.effect(() => {
      const stopRoutes = mount()
      return () => {
        stopRoutes?.()
        mcpRefresh?.stop()
      }
    }, 'mecha-panel-host: panel routes and aggregate MCP refresh')
  }
  mount()
}
