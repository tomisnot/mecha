/**
 * 取数骨架单测（**共享测试**：随参考实现一起被复制）。
 *
 * 跑：`node --test mecha/dsh-panel/monitor-client.test.ts`
 *
 * 钉死的三件事：
 *  1. **三档错误态可区分**：基础路由失败 = `offline`；**附加路由失败 = `route`**
 *     （后者若混进前者，真因会被"权威离线"吃掉——这正是本骨架要防的）；
 *  2. **失败不冒充成功**：非 2xx / 非 JSON ⇒ `ok:false`，**绝不返回空对象**；
 *  3. **R8 非退化自证**：`isNonDegenerate` 必须**两个方向都能变**（空 ⇒ false、
 *     有数据 ⇒ true），否则它就是个恒定的观测量（R11）。
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  classifyRoute,
  displayTier,
  fetchMonitorBase,
  isNonDegenerate,
  monitorStats,
  readJson,
  statsLine,
} from './monitor-client.ts'

/** 造一个 fetch 替身：按 path 决定回什么。 */
function fakeFetch(reply: (path: string) => Response | Error) {
  return (async (input: any) => {
    const path = String(input).replace(/^https?:\/\/[^/]+/, '')
    const r = reply(path)
    if (r instanceof Error) throw r
    return r
  }) as unknown as typeof fetch
}

function jsonRes(status: number, payload: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  } as unknown as Response
}

test('classifyRoute：四基础路由 = basic，其余 = extra', () => {
  for (const p of ['/status', '/activity', '/history', '/config']) {
    assert.equal(classifyRoute(p), 'basic', p)
  }
  assert.equal(classifyRoute('/history?since_seq=0'), 'basic')   // 查询串不改变归属
  assert.equal(classifyRoute('/summary'), 'extra')
  assert.equal(classifyRoute('/runs?limit=50'), 'extra')
})

test('⭐ 附加路由失败 ⇒ route 档；基础路由失败 ⇒ offline 档（**必须分开**）', async () => {
  const f = fakeFetch((path) => jsonRes(path.startsWith('/summary') ? 500 : 503, {}))
  const extra = await readJson('http://127.0.0.1:1', '/summary', f)
  const basic = await readJson('http://127.0.0.1:1', '/history', f)
  assert.equal(extra.ok, false)
  assert.equal(basic.ok, false)
  assert.equal(extra.ok === false && extra.failure.tier, 'route')
  assert.equal(basic.ok === false && basic.failure.tier, 'offline')
  // 两者**不是同一档**——这条断言就是"不许把附加路由失败混成权威离线"的机器版
  assert.notEqual((extra as any).failure.tier, (basic as any).failure.tier)
})

test('连不上 / 非 JSON ⇒ ok:false，**不返回空对象冒充成功**', async () => {
  const dead = await readJson('http://127.0.0.1:1', '/status',
    fakeFetch(() => new Error('ECONNREFUSED')))
  assert.equal(dead.ok, false)
  const notJson = await readJson('http://127.0.0.1:1', '/config', fakeFetch(() => ({
    ok: true, status: 200, json: async () => { throw new Error('bad json') },
  } as unknown as Response)))
  assert.equal(notJson.ok, false)
  assert.equal(notJson.ok === false && notJson.failure.tier, 'offline')
})

test('取数成功时如实返回数据（对偶：不能永远失败）', async () => {
  const good = await readJson('http://127.0.0.1:1', '/config',
    fakeFetch(() => jsonRes(200, { groups: { a: {} } })))
  assert.equal(good.ok, true)
  assert.deepEqual((good as any).data, { groups: { a: {} } })
})

test('fetchMonitorBase：解析器抛错 / 返回空 ⇒ address 档', async () => {
  const threw = await fetchMonitorBase(async () => { throw new Error('读不到端口文件') })
  assert.equal(threw.ok, false)
  assert.equal(threw.ok === false && threw.failure.tier, 'address')
  const empty = await fetchMonitorBase(async () => '')
  assert.equal(empty.ok, false)
  assert.equal(empty.ok === false && empty.failure.tier, 'address')
  const ok = await fetchMonitorBase(async () => 'http://127.0.0.1:1')
  assert.equal(ok.ok, true)
})

test('displayTier：把 address **显式**塌缩成 offline；route 不塌缩', () => {
  assert.equal(displayTier('address'), 'offline')
  assert.equal(displayTier('offline'), 'offline')
  assert.equal(displayTier('route'), 'route')     // 附加路由失败**不许**被显示成离线
})

test('⭐ R8 非退化自证：两个方向都能变（空 ⇒ false，有数据 ⇒ true）', () => {
  const empty = monitorStats({ offline: false, history: { events: [] }, config: { groups: {} } })
  const real = monitorStats({
    offline: false,
    history: { events: [{ seq: 1 }, { seq: 2 }] },
    config: { groups: { field: {}, dynamic: {} } },
  })
  const offline = monitorStats({ offline: true, history: { events: [] }, config: {} })

  assert.equal(isNonDegenerate(empty), false, '空表不该被判为"正常"（否则就是假绿）')
  assert.equal(isNonDegenerate(real), true)
  assert.equal(isNonDegenerate(offline), false)

  // 自证行必须把三个数**打出来**（否则"非退化"只是声称）
  assert.equal(statsLine(real), '离线=false 事件行=2 配置族=2')
  assert.equal(statsLine(empty), '离线=false 事件行=0 配置族=0')
  assert.notEqual(statsLine(real), statsLine(empty), '自证行必须随数据变化（R11）')
})
