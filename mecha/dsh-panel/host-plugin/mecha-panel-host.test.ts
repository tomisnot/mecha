/**
 * `mecha-panel-host.mjs`（框架自己的 dsh host 插件）的**行为判据**。
 *
 * ⚠ 为什么这份判据是必需的：那一件是**手写 JS、零 import**（dsh 的 patch 只能指 node 能
 * 直接 import 的模块，而这份资产是 TS、没有构建步骤）⇒ 它**不进 `tsc` 的类型检查**。
 * ⇒ 它的正确性只能由**跑起来**的判据守（喂假 ctx / 假 req、res，数注册了几条路由）。
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'

import type { PanelHostContext } from './mecha-panel-host.d.mts'
import {
  ROUTE_ENV,
  SERVICES_ENV,
  apply,
  makePanelHandler,
  readPanelPayload,
} from '../host-plugin/mecha-panel-host.mjs'

/** 一份真形状的 env（与 `mecha/dsh_panel.py` 生成的那两个变量同名同形）。 */
function envOf(services: unknown[] = [], route = '/mecha/panels') {
  return {
    [ROUTE_ENV]: route,
    [SERVICES_ENV]: JSON.stringify({ routePath: route, services }),
  }
}

/** ⚠ 用例里的服务名/路由/端口文件名一律**中性**（词表守卫扫本目录：
 *  共享资产里不许出现任何项目字面量）。"那份参数与真声明同形"那条由
 *  `checks/phase13_panel_decl_selfcheck.py` 拿三家真声明对拍（不在这里）。 */
const ONE_SVC = [{
  id: 'AAA', kind: 'mecha:AAA', tabId: 'mecha-panel:AAA',
  routePath: '/svc-a/monitor-url', portFile: '.svc-a-monitor-port',
  title: '甲监控', extraPages: [],
}]

test('读参数：形状对 ⇒ 原样给出（数量 + 路由）', () => {
  const p = readPanelPayload(envOf(ONE_SVC))
  assert.equal(p.routePath, '/mecha/panels')
  assert.equal(p.services.length, 1)
  assert.equal((p.services[0] as { tabId: string }).tabId, 'mecha-panel:AAA')
})

test('⭐ 参数缺失 ⇒ **响亮失败**（不许静默起一个没有面板的 dsh）', () => {
  for (const key of [ROUTE_ENV, SERVICES_ENV]) {
    const env = envOf(ONE_SVC)
    delete (env as Record<string, string | undefined>)[key]
    assert.throws(() => readPanelPayload(env), new RegExp(key), `缺 ${key} 时应当抛`)
  }
  assert.throws(() => readPanelPayload(envOf(ONE_SVC, 'no-slash')), /要以 '\/' 开头/)
  const bad = envOf(ONE_SVC)
  ;(bad as Record<string, string>)[SERVICES_ENV] = '{not json'
  assert.throws(() => readPanelPayload(bad), /不是 JSON/)
  const wrongShape = envOf(ONE_SVC)
  ;(wrongShape as Record<string, string>)[SERVICES_ENV] = JSON.stringify({ services: 'nope' })
  assert.throws(() => readPanelPayload(wrongShape), /形状/)
})

test('两份路由不一致 ⇒ 响亮失败（改一处必须改两处）', () => {
  const env = envOf(ONE_SVC)
  ;(env as Record<string, string>)[SERVICES_ENV] =
    JSON.stringify({ routePath: '/somewhere/else', services: [] })
  assert.throws(() => readPanelPayload(env), /两份路由不一致/)
})

test('handler：回 JSON、no-store、⚠ **不含端口号**', () => {
  const handler = makePanelHandler(envOf(ONE_SVC))
  let status = 0
  let headers: Record<string, string> = {}
  let body = ''
  handler({}, {
    writeHead(s: number, h: Record<string, string>) { status = s; headers = h },
    end(b: string) { body = b },
  })
  assert.equal(status, 200)
  assert.match(headers['content-type'] ?? '', /application\/json/)
  assert.equal(headers['cache-control'], 'no-store')
  const parsed = JSON.parse(body) as { services: Record<string, unknown>[] }
  assert.equal(parsed.services.length, 1)
  // ⚠ 端口号不许进这份参数（它会给浏览器、也可能进日志）：这里断言**一个数字端口都没有**。
  assert.doesNotMatch(body, /https?:\/\//, `参数里出现了完整地址：${body}`)
  assert.deepEqual(Object.keys(parsed.services[0] ?? {}).sort(),
    ['extraPages', 'id', 'kind', 'portFile', 'routePath', 'tabId', 'title'])
})

test('apply：注册**一条**精确路由，收尾时注销；没有 webServer 时跳过而不炸', async () => {
  // ⚠ `apply` 读的是**真** `process.env`（框架正是经 env 交参数的）⇒ 这一条必须把
  //   env 摆成真形状，否则它测的是"参数缺失"那条路（第一条版本就这么假红过）。
  const saved = { ...process.env }
  Object.assign(process.env, envOf(ONE_SVC))
  try {
    const registered: { path: string; kind: string }[] = []
    let disposed = 0
    const scoped = {
      webServer: {
        register(route: { path: string; kind: string }) {
          registered.push(route)
          return () => { disposed += 1 }
        },
      },
    }
    const effects: unknown[] = []
    const ctx: PanelHostContext = {
      logger: { info() { /* 日志不参与判据 */ } },
      inject(names, fn) {
        assert.deepEqual(names, ['webServer'])
        const d = fn(scoped)
        assert.equal(typeof d, 'function')
      },
      effect(fn, _label) {
        const cleanup = fn()
        effects.push(cleanup)
        return cleanup
      },
    }
    await apply(ctx)
    assert.equal(registered.length, 1, '应当只注册**那一个**路由')
    assert.equal(registered[0]?.path, '/mecha/panels')
    assert.equal(registered[0]?.kind, 'exact')
    // 收尾
    ;(effects[0] as () => void)()
    assert.equal(disposed, 1, '收尾必须把路由注销掉（否则 dsh 会攒下一堆旧路由）')

    // ⚠ 没有 webServer（headless 部署）：**跳过**而不是整个插件不激活，也不炸
    await apply({ logger: {}, inject(_n, fn) { fn({}) } })
  } finally {
    for (const k of [ROUTE_ENV, SERVICES_ENV]) delete process.env[k]
    Object.assign(process.env, saved)
  }
})

test('apply：参数坏 ⇒ 抛（不吞）', async () => {
  const saved = { ...process.env }
  try {
    delete process.env[ROUTE_ENV]
    delete process.env[SERVICES_ENV]
    await assert.rejects(() => apply({ logger: {} }), new RegExp(ROUTE_ENV))
  } finally {
    for (const k of [ROUTE_ENV, SERVICES_ENV]) delete process.env[k]
    Object.assign(process.env, saved)
  }
})
