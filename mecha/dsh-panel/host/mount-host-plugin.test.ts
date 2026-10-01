/**
 * host 半插件骨架单测（`mount-host-plugin.ts`）。
 *
 * 跑：`node --test mecha/dsh-panel/mount-host-plugin.test.ts`
 *
 * 本文件的每条判据都要能回答**"结构为什么保证不了它"**（用户口径：能靠结构消灭的错误
 * 类别别加判据）。这里保留的都是**运行时才成立**的性质：
 *
 *  1. **必需参数缺失 ⇒ 构造期抛**——TypeScript 能挡"没传字段"，但挡不住**传了空串**
 *     （`serverName: config.serverName || ''` 是真实会发生的写法），也挡不住
 *     `clientInfo: { name: '', version: '' }`。⇒ 必须运行时校验。
 *  2. **teardown 真的把注册过的东西全撤了**——"注册了但没注销"是**资源泄漏**，
 *     类型上完全看不出来（`dispose` 拿不拿都被调都不报错）。⇒ 两个方向都钉。
 *  3. **opt-in 的 command 关着时什么都不注册**——"关着"是运行期条件，类型上两种都合法。
 *  4. **无 webServer 时跳过但不炸**——部署形态差异，类型上看不出。
 *  5. **端点每次现解**——它是"端口漂移自愈"的关键，而"只解析一次"在类型上完全合法
 *     （把 `url` 收成一个字符串就行）⇒ 这条**必须**有判据，否则退化无声。
 *
 * ⚠ 全部用**注入的假会话**：不需要 MCP SDK、不连网络（骨架对 SDK 是懒加载，这也顺带证明它）。
 * ⚠ 本文件不许出现任何项目字面量（中性名 `probe.port` / `/probe/monitor-url`）。
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mountHostPlugin, hostPluginInject, type HostContext } from './mount-host-plugin.ts'
import type { McpSession, ToolDef } from '../mcp-bridge.ts'

// ---------------------------------------------------------------- 测试替身

/** 记下注册了什么，以便证明"注册过"与"注销干净"两个方向。 */
class FakeTools {
  readonly registered = new Map<string, unknown>()
  register(def: unknown): () => void {
    const name = String((def as { name?: unknown })?.name ?? '?')
    this.registered.set(name, def)
    return () => { this.registered.delete(name) }
  }
}

class FakeWebServer {
  readonly routes = new Map<string, unknown>()
  register(route: { path: string }): () => void {
    this.routes.set(route.path, route)
    return () => { this.routes.delete(route.path) }
  }
}

interface CtxOptions {
  tools?: FakeTools
  commands?: {
    register(cmd: { name: string; description: string;
      handler: (a: unknown) => unknown }): () => void
  }
  /** 不给 ⇒ 完全模拟"没有 webServer 的部署"。 */
  webServer?: FakeWebServer
  logs?: string[]
}

/**
 * 按 cordis 的最小形状造 ctx（骨架只认结构化接口，不 import @deepseek-ai/*）。
 * `webServer` 不给 ⇒ 模拟"没有 web 服务的部署"（`ctx.inject` 的 scoped 里就没有它）。
 */
function makeCtx(o: CtxOptions = {}): { ctx: HostContext; logs: string[] } {
  const logs = o.logs ?? []
  const push = (m: string) => { logs.push(m) }
  const ctx: HostContext = {
    ...(o.tools !== undefined ? { tools: o.tools } : {}),
    ...(o.commands !== undefined ? { commands: o.commands } : {}),
    logger: { info: push, warn: push, error: push },
    async effect(fn) { await fn() },
    inject(names, fn) {
      void names
      const scoped: Record<string, unknown> = {}
      if (o.webServer !== undefined) scoped.webServer = o.webServer
      void fn(scoped)
    },
  }
  return { ctx, logs }
}

function tool(name: string): ToolDef {
  return { name, description: 'd', inputSchema: {} }
}

/** 假会话：自发报出一个工具，callTool 直接回显。
 *  ⚠ **不用 TS 参数属性**（`constructor(private x)`）：Node 原生剥类型**不支持**它
 *  （`ERR_UNSUPPORTED_TYPESCRIPT_SYNTAX`），与 `mcp-bridge.ts` 里同一条备注同因。 */
class FakeSession {
  onClose?: () => void
  closed = false
  private readonly tools: ToolDef[]
  constructor(tools: ToolDef[]) { this.tools = tools }
  async listTools(): Promise<{ tools: ToolDef[] }> { return { tools: this.tools } }
  async callTool(name: string): Promise<unknown> { return { ok: true, name } }
  async close(): Promise<void> { this.closed = true }
}

function fakeSessionFactory(sessions: FakeSession[]): () => Promise<McpSession> {
  let i = 0
  return async () => {
    const s = sessions[i] ?? sessions[sessions.length - 1]
    i++
    return s as unknown as McpSession
  }
}

// ⚠ routePath 用**中性字面量**（不借资产的默认值）：本文件断言的是"骨架把我给的路由
//   原样注册了"，而不是"骨架用了某个特定默认"——后者是另一件事，归 panel 面的单测。
const PROBE_ROUTE = '/probe/monitor-url'
const PANEL = { routePath: PROBE_ROUTE, portFile: 'probe.port', title: 'probe' }

/** 一份齐全的最小参数（各个用例按需覆盖/删除字段）。 */
function baseOptions(over: Record<string, unknown> = {}) {
  return {
    serverName: 'probe',
    logLabel: 'probe',
    projectRoot: '/probe-root',
    clientInfo: { name: 'probe-client', version: '0.0.0' },
    resolveUrl: async () => 'http://127.0.0.1:4001/mcp',
    panelConfig: PANEL,
    sessionFactory: fakeSessionFactory([new FakeSession([tool('read_thing')])]),
    ...over,
  } as Parameters<typeof mountHostPlugin>[1]
}

// ---------------------------------------------------------------- 必需参数

test('⭐ 必需参数缺失 / 留空 ⇒ **构造期抛**（运行期校验：类型挡不住空串）', async () => {
  const cases: Array<[string, Record<string, unknown>]> = [
    ['serverName 留空', { serverName: '   ' }],
    ['logLabel 留空', { logLabel: '' }],
    ['projectRoot 留空', { projectRoot: '  ' }],
    ['clientInfo.name 留空', { clientInfo: { name: '', version: '1' } }],
    ['clientInfo.version 留空', { clientInfo: { name: 'x', version: '  ' } }],
    ['缺 resolveUrl', { resolveUrl: undefined }],
    ['panelConfig 缺失', { panelConfig: undefined }],
  ]
  for (const [label, over] of cases) {
    const { ctx } = makeCtx({ tools: new FakeTools() })
    await assert.rejects(
      () => mountHostPlugin(ctx, baseOptions(over)),
      (err: Error) => {
        assert.match(err.message, /不能为空|需要/, `${label}：消息不像参数错：${err.message}`)
        assert.match(err.message, /mountHostPlugin/, `${label}：消息没说清是谁在报错`)
        return true
      },
      label,
    )
  }
})

test('参数错时**不留半挂载状态**（也没把面板参数注进去）', async () => {
  const tools = new FakeTools()
  const web = new FakeWebServer()
  const { ctx } = makeCtx({ tools, webServer: web })
  await assert.rejects(() => mountHostPlugin(ctx, baseOptions({ logLabel: '' })))
  assert.equal(tools.registered.size, 0, '不该注册任何工具')
  assert.equal(web.routes.size, 0, '不该注册任何路由')
})

// ---------------------------------------------------------------- 挂载 / 收尾

test('⭐ 挂载确实注册了工具与监控地址路由（**不撤时确实在**）', async () => {
  const tools = new FakeTools()
  const web = new FakeWebServer()
  const { ctx, logs } = makeCtx({ tools, webServer: web })
  await mountHostPlugin(ctx, baseOptions())

  assert.deepEqual([...tools.registered.keys()], ['mcp__probe__read_thing'],
    '工具应按 mcp__<serverName>__<raw> 注册')
  assert.deepEqual([...web.routes.keys()], [PROBE_ROUTE], '监控地址路由应已注册')
  // 日志里必须出现**项目给的名字**（不是中性默认）
  assert.ok(logs.some(l => l.includes('[probe]') && l.includes('注册监控地址路由')),
    `日志里应出现项目前缀与路由：${JSON.stringify(logs)}`)
})

test('⭐ teardown 把**注册过的东西全撤了**（撤了之后确实不在了）', async () => {
  const tools = new FakeTools()
  const web = new FakeWebServer()
  const { ctx } = makeCtx({ tools, webServer: web })
  await mountHostPlugin(ctx, baseOptions())

  // `ctx.effect` 在本替身里立即执行了回调；要拿收尾函数得自己抓。
  // ⇒ 用"抓收尾函数"的替身重跑一次（替换 effect 实现）。
  const realTools = new FakeTools()
  const realWeb = new FakeWebServer()
  let teardown: (() => Promise<void>) | undefined
  const logs: string[] = []
  const ctx2: HostContext = {
    tools: realTools,
    logger: { info: m => logs.push(m), warn: m => logs.push(m), error: m => logs.push(m) },
    async effect(fn) { teardown = await fn() as () => Promise<void> },
    inject(_names, fn) { fn({ webServer: realWeb }) },
  }
  await mountHostPlugin(ctx2, baseOptions())
  assert.equal(realTools.registered.size, 1, '收尾前：工具在')
  assert.equal(realWeb.routes.size, 1, '收尾前：路由在')

  assert.ok(teardown !== undefined, 'ctx.effect 应给出收尾函数')
  await teardown!()
  assert.equal(realTools.registered.size, 0, '收尾后：工具必须被注销')
  assert.equal(realWeb.routes.size, 0, '收尾后：路由必须被注销')
})

test('⭐ 端点**每次建会话现解**：骨架不许在构造期解析、也不许把它缓存成值', async () => {
  // 为什么必须有这条判据：**"只解一次"在类型上完全合法**（把 resolveUrl 的结果存成
  // `const url = await resolveUrl()` 就行），但它会让端口漂移**静默失效**——
  // 权威重启换端口后桥一直对着旧端点重试。⇒ 结构保证不了，必须钉。
  let calls = 0
  const ctx: HostContext = {
    tools: new FakeTools(),
    logger: {},
    async effect(fn) { await fn() },
    inject(_n, fn) { fn({}) },
  }
  await mountHostPlugin(ctx, baseOptions({
    resolveUrl: async () => { calls++; return 'http://127.0.0.1:4001/mcp' },
  }))
  // 假会话工厂被注入 ⇒ 真工厂（也就只有它）才会调 resolveUrl。
  // 这里断言的是**边界**：构造/挂载期**一次都不许解析**
  //（解析只发生在"真的建会话"那一刻，而那一刻由工厂按需触发）。
  assert.equal(calls, 0,
    `挂载期不该解析端点（应由会话工厂按需触发），实际解析了 ${calls} 次 ⇒ 可能被缓存成了值`)
})

test('sessionFactory 与 resolveUrl 都必需（注入假会话也仍要 resolveUrl）', async () => {
  const { ctx } = makeCtx({ tools: new FakeTools() })
  await assert.rejects(
    () => mountHostPlugin(ctx, baseOptions({ resolveUrl: undefined })),
    /resolveUrl/,
  )
})

test('command 是可选的：**不传就什么都不注册**（不留"按了没反应"的死缝）', async () => {
  const registered: string[] = []
  const { ctx } = makeCtx({
    tools: new FakeTools(),
    commands: {
      register(cmd) {
        registered.push(cmd.name)
        return () => { registered.pop() }
      },
    },
  })
  await mountHostPlugin(ctx, baseOptions())          // 不传 registerCommand
  assert.deepEqual(registered, [], '关着时不许注册任何 command')
})

test('传了 registerCommand ⇒ 注册；收尾时**被注销**（且回执形状**不设约束**）', async () => {
  const live: string[] = []
  let teardown: (() => Promise<void>) | undefined
  const ctx: HostContext = {
    tools: new FakeTools(),
    commands: {
      register(cmd) {
        live.push(cmd.name)
        return () => { live.splice(live.indexOf(cmd.name), 1) }
      },
    },
    logger: {},
    async effect(fn) { teardown = await fn() as () => Promise<void> },
    inject(_n, fn) { fn({}) },
  }
  await mountHostPlugin(ctx, baseOptions({
    // ⚠ 刻意返回一种**宿主自有形状**（不是 `{kind,text}`）：骨架只创建、不解释命令
    //   ⇒ 它不该对回执形状设约束（那是宿主契约，不是本资产的）。
    registerCommand: (c: HostContext) => c.commands!.register({
      name: 'probe-command',
      description: 'd',
      handler: async () => ({ ok: true, rendered: 'anything' }),
    }),
  }))
  assert.deepEqual(live, ['probe-command'], '开着时应注册')
  await teardown!()
  assert.deepEqual(live, [], '收尾后必须注销')
})

test('无 webServer ⇒ **跳过路由但不炸**，桥与工具照活', async () => {
  const tools = new FakeTools()
  const web = new FakeWebServer()
  let teardown: (() => Promise<void>) | undefined
  /** scoped 里**没有** webServer（模拟 headless / 无 web 服务的部署）。 */
  const ctx: HostContext = {
    tools,
    logger: {},
    async effect(fn) { teardown = await fn() as () => Promise<void> },
    inject(_n, fn) { void fn({}) },            // 刻意不给 webServer
  }
  await mountHostPlugin(ctx, baseOptions())
  assert.equal(tools.registered.size, 1, '桥与工具注册不受"没有 web 服务"影响')
  assert.equal(web.routes.size, 0, '没给 webServer ⇒ 那条替身本就不该被用')
  // 收尾也不能因为"从没注册过路由"而炸
  await teardown!()
  assert.equal(tools.registered.size, 0, '收尾照常撤工具')
})

test('有 webServer ⇒ 路由照注册（**与上一条合起来 = "默认值 == 正确语义"**）', async () => {
  // ⭐ 上一条 + 这一条是 `hostPluginInject()` 恒返回 `['tools']` 的**两个方向**：
  //   顶层不再硬要 webServer，而"有没有路由"完全由骨架内部的 `ctx.inject` 条件决定。
  const tools = new FakeTools()
  const web = new FakeWebServer()
  const { ctx } = makeCtx({ tools, webServer: web })
  await mountHostPlugin(ctx, baseOptions())
  assert.equal(tools.registered.size, 1, '工具照注册')
  assert.deepEqual([...web.routes.keys()], [PROBE_ROUTE], '有 web 服务时路由要注册')
})

test('⭐ hostPluginInject **恒不含 webServer**（顶层硬要它会把"没 web 服务"升级成"工具也没了"）', () => {
  // 为什么要有这条：顶层 `inject` 里列了 `webServer` ⇒ **缺该服务时整个插件不激活**，
  // 而地址路由是**可选**能力（骨架内部用 `ctx.inject(['webServer'],…)` 条件注册）。
  // 两者不一致的后果有实测分叉：一家下游传 `false` 绕过、另一家继承了脆弱默认。
  // ⇒ 参数已删，默认值即正确语义。
  assert.deepEqual(hostPluginInject(), ['tools'])
  assert.ok(!hostPluginInject().includes('webServer'), '顶层不许硬要 webServer')
})
