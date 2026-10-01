/**
 * 数据层单测（**共享测试**：随资产一起被复制）。
 *
 * 跑：`node --test mecha/dsh-panel/panel-data.test.ts`
 *
 * 钉死两件事：
 *  1. **"面板不空白"**：每个状态档位都映射到**非空可读文案**（`ok` 档例外：那一档画数据）；
 *  2. **键只有一处**：`eventsOf` / `configOf` 必须用**基础路由键**（`/history`），
 *     而不是带查询串的请求路径——否则页面永远"暂无写事件"，而自证行仍报"事件行=N"
 *     （真实踩过的分叉）。
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  configOf,
  eventsOf,
  HISTORY_PATH,
  HISTORY_ROUTE,
  loadPanel,
  modeOf,
  okIsNonDegenerate,
  selfCheckLine,
  stateBadge,
  stateText,
  tabLabel,
} from './panel-data.ts'
// ⚠ **本测试也是一个消费者**：参数必须由它**自己注入**（资产不再持有项目专有的值）。
//   用中性值：路由取资产的默认、端口文件名用判据自己造的名字（**不是**任何项目的约定名）。
import {
  DEFAULT_ROUTE_PATH,
  configurePanel,
  panelConfig,
  type Disposer,
} from './panel-config.ts'
import { panelState, type FetchResult } from './monitor-client.ts'

/**
 * 注入中性参数（每个用例自己决定要不要改；`tabs` 由需要它的用例单独注入）。
 * 返回 disposer ⇒ 用例可以"临时注入、用完还原"，**不需要任何测试专用函数**。
 */
function inject(extra: { tabs?: { rec?: string; cfg?: string } } = {}): Disposer {
  return configurePanel({
    routePath: DEFAULT_ROUTE_PATH,
    portFile: 'probe.port',
    title: '监控',
    ...extra,
  })
}
inject()

const OK_BASE: FetchResult<string> = { ok: true, data: 'http://127.0.0.1:1' }
const okData = (d: unknown): FetchResult<unknown> => ({ ok: true, data: d })
const fail = (tier: 'address' | 'offline' | 'route', detail = '因为某原因') =>
  ({ ok: false as const, failure: { tier, detail } })

const okState = (events: unknown[], groups: unknown = { f: [] }) => panelState({
  base: OK_BASE,
  basic: {
    '/history': okData({ events, mode: 'ai' }),
    '/config': okData({ groups, has_schema: true }),
  },
})

test('stateText：每个非 ok 档都有**非空可读文案**；ok 档为空串（那一档画数据）', () => {
  assert.equal(stateText(okState([{ seq: 1 }])), '')
  const texts = [
    stateText(panelState({})),
    stateText(panelState({ base: fail('address') })),
    stateText(panelState({ base: OK_BASE, basic: { '/history': fail('offline') } })),
    stateText(panelState({ base: OK_BASE, basic: { '/history': okData({ events: [] }) },
                           extra: { '/x': fail('route') } })),
    stateText(panelState({ base: OK_BASE, basic: { '/history': okData({ events: [] }) } })),
  ]
  for (const t of texts) assert.ok(t.length > 0, '非 ok 档必须给出可读文案（空白面板的入口）')
})

test('stateBadge：首拍连接中；ok 档按写权模式；非 ok 档按档位', () => {
  assert.deepEqual(stateBadge(null), { cls: 'ok', text: '连接中…' })
  assert.equal(stateBadge(okState([])).text, '写权 AI')
  assert.equal(stateBadge(okState([])).cls, 'ai')
  const human = panelState({ base: OK_BASE, basic: {
    '/history': okData({ events: [{ seq: 1 }], mode: 'human' }), '/config': okData({ groups: {} }) } })
  assert.equal(stateBadge(human).text, '写权在人')
  assert.equal(stateBadge(panelState({ base: fail('address') })).text, '权威离线')
  assert.equal(stateBadge(panelState({ base: fail('address') })).cls, 'error')
  const route = panelState({ base: OK_BASE, basic: { '/history': okData({ events: [{ seq: 1 }] }) },
                             extra: { '/x': fail('route') } })
  assert.deepEqual(stateBadge(route), { cls: 'warn', text: '路由异常' })
  const empty = panelState({ base: OK_BASE, basic: { '/history': okData({ events: [] }) } })
  assert.deepEqual(stateBadge(empty), { cls: 'warn', text: '连上但无数据' })
})

test('modeOf / selfCheckLine / okIsNonDegenerate', () => {
  const ok = okState([{ seq: 1 }], { field: [{ prefix: 'field', rows: [] }] })
  assert.equal(modeOf(ok), 'ai')
  assert.equal(modeOf(panelState({})), '')
  assert.equal(selfCheckLine(ok), '离线=false 事件行=1 配置族=1')
  assert.match(selfCheckLine(panelState({})), /连不上权威/)
  assert.equal(okIsNonDegenerate(ok), true)
  assert.equal(okIsNonDegenerate(panelState({ base: OK_BASE, basic: { '/history': okData({ events: [] }) } })), false)
})

test('⭐ eventsOf / configOf：用**基础路由键**（不是带查询串的请求路径）', () => {
  assert.notEqual(HISTORY_PATH, HISTORY_ROUTE, '请求路径与键本来就不同（这是本条的由来）')
  const st = okState([{ seq: 7 }], { a: [] })
  assert.deepEqual(eventsOf(st).map((e) => e.seq), [7])
  assert.deepEqual(configOf(st), { groups: { a: [] }, has_schema: true })
  // 反面：把 payloads 只按**请求路径**键存 ⇒ 取不到（这正是早先那个 bug 的形状）
  const wrongKeyed = { kind: 'ok' as const, stats: { offline: false, events: 1, configFamilies: 1 },
                       payloads: { [HISTORY_PATH]: { events: [{ seq: 7 }] } } }
  assert.deepEqual(eventsOf(wrongKeyed as never), [], '这条钉住"不许用请求路径取事件"')
  assert.deepEqual(eventsOf(null), [])
  assert.equal(configOf(null), undefined)
})

test('⭐ 未注入即**响亮失败**（不给中性默认关卡——那会把"忘注入"演成"空面板"）', () => {
  // 这是本次改造的**核心要求**：资产不提供默认值，忘了注入必须当场炸。
  // 若哪天有人"为方便"加了一个默认关卡，本用例**立刻红**（R7：它守的就是这个）。
  const dispose = inject()
  dispose()                                  // 清回"未注入"
  // 读参数 ⇒ 抛，且消息要说清"该怎么办"（可教学错误，L4）
  assert.throws(() => panelConfig(), /未注入/)
  assert.throws(() => panelConfig(), /configurePanel/)
  // 用面板的入口也抛（不是"读参数才抛"）
  assert.throws(() => tabLabel('rec'), /未注入/)
  // 字段留空同样抛（空串不等于"没有这个名字"）
  assert.throws(() => configurePanel({ routePath: '   ', portFile: 'p', title: 't' }), /routePath/)
  assert.throws(() => configurePanel({ routePath: '/x', portFile: '', title: 't' }), /portFile/)
  assert.throws(() => configurePanel({ routePath: '/x', portFile: 'p', title: '  ' }), /title/)
  inject()                                   // 还原本文件其余用例的中性注入
})

test('tabLabel：**共享默认**（不注入 tabs）与**注入后结果就变**（R17）', () => {
  // ⚠ 这里**不许假定"项目没注入 tabs"**：`tabs` 是文档允许项目注入的可选参数，
  // 任何注入过的项目都会让"写死中性默认"的断言变红（真实发生过：某项目填了
  // `{rec: …}` ⇒ 共享自测 pass 41 / fail 1）。
  // ⇒ 正确做法：**不注入 tabs** 来测默认那一半，注入了 tabs 另测。
  // ⚠ 结构上的好处：`tabs` 在这个测试里**根本没被设过**，不需要"临时 delete 再恢复"
  //   ——那是旧"可变参数块"留下的脚手架；现在靠 disposer 还原注入态即可。
  const disposeDefault = inject()                       // 注入但**不带 tabs**
  assert.equal(tabLabel('rec'), '飞行记录仪')
  assert.equal(tabLabel('cfg'), '配置态')
  assert.equal(tabLabel('whatever'), 'whatever', '未知 id 原样返回')

  const disposeBoth = inject({ tabs: { rec: '甲', cfg: '乙' } })
  assert.equal(tabLabel('rec'), '甲')
  assert.equal(tabLabel('cfg'), '乙')
  disposeBoth()                                         // 还原成"不带 tabs"

  const disposeOne = inject({ tabs: { rec: '操作流水' } })
  assert.equal(tabLabel('rec'), '操作流水')
  assert.equal(tabLabel('cfg'), '配置态')
  disposeOne()

  // ⭐ 再证一次「未注入即响亮失败」：disposer 把注入态清掉之后，读它必须抛
  disposeDefault()
  assert.throws(() => tabLabel('rec'), /未注入/)

  inject()                                              // 还原本文件其余用例的中性注入
})

// ---------------------------------------------------------------- loadPanel

/** fetch 替身：按 path 回内容，并**记下调用**（证明注入真的生效）。 */
function fakeFetch(reply: (path: string) => Response | Error) {
  const calls: string[] = []
  const f = (async (input: any) => {
    const raw = String(input)
    calls.push(raw)
    const r = reply(raw.replace(/^https?:\/\/[^/]+/, ''))
    if (r instanceof Error) throw r
    return r
  }) as unknown as typeof fetch
  return { f, calls }
}

const jsonRes = (status: number, payload: unknown): Response => ({
  ok: status >= 200 && status < 300, status, json: async () => payload,
} as unknown as Response)

test('⭐ loadPanel：四态各走一次，且**注入的 doFetch 真的被调用**（含用哪条路径）', async () => {
  const good = fakeFetch((p) => {
    if (p === panelConfig().routePath) return jsonRes(200, { base: 'http://127.0.0.1:1' })
    if (p.startsWith('/history')) return jsonRes(200, { events: [{ seq: 1 }, { seq: 2 }], mode: 'locked' })
    if (p === '/config') return jsonRes(200, { groups: { f: [] }, has_schema: true })
    return jsonRes(404, {})
  })
  const { state: st } = await loadPanel(good.f)
  assert.equal(st.kind, 'ok')
  assert.equal(st.kind === 'ok' && st.stats.events, 2)
  assert.equal(st.kind === 'ok' && st.stats.configFamilies, 1)
  assert.equal(modeOf(st), 'locked')
  assert.equal(good.calls[0], panelConfig().routePath, '第一跳必须是地址路由')
  assert.ok(good.calls.some((c) => c.includes('/history?since_seq=0')), '请求路径带全量尾')
  assert.ok(good.calls.length >= 3)

  const addrFail = fakeFetch(() => jsonRes(503, { error: '监控端点未知' }))
  assert.equal((await loadPanel(addrFail.f)).state.kind, 'address')

  const offline = fakeFetch((p) =>
    p === panelConfig().routePath ? jsonRes(200, { base: 'http://127.0.0.1:1' }) : jsonRes(500, {}))
  assert.equal((await loadPanel(offline.f)).state.kind, 'offline')

  const empty = fakeFetch((p) =>
    p === panelConfig().routePath ? jsonRes(200, { base: 'http://127.0.0.1:1' })
      : p === '/config' ? jsonRes(200, { groups: {}, has_schema: true })
        : jsonRes(200, { events: [] }))
  assert.equal((await loadPanel(empty.f)).state.kind, 'empty')
})

test('⭐ 附加页：各自取数；**附加路由失败不牵连标准两页**', async () => {
  const { f, calls } = fakeFetch((p) => {
    if (p === panelConfig().routePath) return jsonRes(200, { base: 'http://127.0.0.1:1' })
    if (p.startsWith('/history')) return jsonRes(200, { events: [{ seq: 1 }] })
    if (p === '/config') return jsonRes(200, { groups: { f: [] }, has_schema: true })
    if (p.startsWith('/extra-ok')) return jsonRes(200, { rows: [{ a: 1 }] })
    return jsonRes(500, {})
  })
  const { state, extras } = await loadPanel(f, [
    { id: 'ok', path: '/extra-ok?limit=1' },
    { id: 'bad', path: '/extra-bad' },
  ])
  assert.equal(state.kind, 'ok', '附加路由失败**不许**把整个面板打成 route 档（那会吃掉标准两页）')
  // ⚠ 严格 tsconfig（noUncheckedIndexedAccess）下 `extras.ok` 是 `FetchResult | undefined`
  // ⇒ 先取出来再断言（这条是被**类型检查守卫**当场抓到的：`node --test` 看不见）。
  const okExtra = extras['ok']
  const badExtra = extras['bad']
  assert.ok(okExtra, '声明过的附加页必须有结果')
  assert.ok(badExtra, '声明过的附加页必须有结果')
  assert.equal(okExtra.ok, true)
  assert.equal(badExtra.ok, false)
  assert.equal(badExtra.ok === false && badExtra.failure.tier, 'route')
  assert.ok(calls.some((c) => c.includes('/extra-ok?limit=1')), '附加路由按声明里的路径取')
  assert.deepEqual((await loadPanel(f, [])).extras, {}, '没声明附加页 ⇒ 空对象')
})
