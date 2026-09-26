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
import { PANEL_CONFIG } from './panel-config.ts'
import { panelState, type FetchResult } from './monitor-client.ts'

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

test('tabLabel：共享默认；参数块覆盖后**结果就变**（R17）', () => {
  assert.equal(tabLabel('rec'), '飞行记录仪')
  assert.equal(tabLabel('cfg'), '配置态')
  assert.equal(tabLabel('whatever'), 'whatever')
  const original = PANEL_CONFIG.TABS
  try {
    PANEL_CONFIG.TABS = { rec: '甲', cfg: '乙' }
    assert.equal(tabLabel('rec'), '甲')
    assert.equal(tabLabel('cfg'), '乙')
  } finally {
    PANEL_CONFIG.TABS = original
  }
  assert.equal(tabLabel('rec'), '飞行记录仪')
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
    if (p === PANEL_CONFIG.ROUTE_PATH) return jsonRes(200, { base: 'http://127.0.0.1:1' })
    if (p.startsWith('/history')) return jsonRes(200, { events: [{ seq: 1 }, { seq: 2 }], mode: 'locked' })
    if (p === '/config') return jsonRes(200, { groups: { f: [] }, has_schema: true })
    return jsonRes(404, {})
  })
  const st = await loadPanel(good.f)
  assert.equal(st.kind, 'ok')
  assert.equal(st.kind === 'ok' && st.stats.events, 2)
  assert.equal(st.kind === 'ok' && st.stats.configFamilies, 1)
  assert.equal(modeOf(st), 'locked')
  assert.equal(good.calls[0], PANEL_CONFIG.ROUTE_PATH, '第一跳必须是地址路由')
  assert.ok(good.calls.some((c) => c.includes('/history?since_seq=0')), '请求路径带全量尾')
  assert.ok(good.calls.length >= 3)

  const addrFail = fakeFetch(() => jsonRes(503, { error: '监控端点未知' }))
  assert.equal((await loadPanel(addrFail.f)).kind, 'address')

  const offline = fakeFetch((p) =>
    p === PANEL_CONFIG.ROUTE_PATH ? jsonRes(200, { base: 'http://127.0.0.1:1' }) : jsonRes(500, {}))
  assert.equal((await loadPanel(offline.f)).kind, 'offline')

  const empty = fakeFetch((p) =>
    p === PANEL_CONFIG.ROUTE_PATH ? jsonRes(200, { base: 'http://127.0.0.1:1' })
      : p === '/config' ? jsonRes(200, { groups: {}, has_schema: true })
        : jsonRes(200, { events: [] }))
  assert.equal((await loadPanel(empty.f)).kind, 'empty')
})
