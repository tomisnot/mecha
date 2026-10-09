/**
 * `mecha-panel-host.mjs`（框架自己的 dsh host 插件）的**行为判据**。
 *
 * ⚠ 为什么这份判据是必需的：那一件是**手写 JS、零 import**（dsh 的 patch 只能指 node 能
 * 直接 import 的模块，而这份资产是 TS、没有构建步骤）⇒ 它**不进 `tsc` 的类型检查**。
 * ⇒ 它的正确性只能由**跑起来**的判据守（喂假 ctx / 假 req、res，数注册了几条路由）。
 */
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'

import type { PanelHostContext } from './mecha-panel-host.d.mts'
import {
  PORT_FILES_ENV,
  ROUTE_ENV,
  SERVICES_ENV,
  apply,
  makeMonitorUrlHandler,
  makePanelHandler,
  readPanelPayload,
  readPortFiles,
  resolveMonitorBase,
} from '../host-plugin/mecha-panel-host.mjs'
// ⭐ 等价判据要用**资产那一份**实现（TS）与被测的框架实现（纯 JS）对拍。
import { makeMonitorUrlHandler as assetMonitorUrlHandler } from '../host/monitor-url.ts'

/** 一份真形状的 env（与 `mecha/dsh_panel.py` 生成的那几个变量同名同形）。 */
function envOf(services: unknown[] = [], route = '/mecha/panels',
               portFiles: Record<string, string> = { AAA: '/tmp/.svc-a-monitor-port' }) {
  return {
    [ROUTE_ENV]: route,
    [SERVICES_ENV]: JSON.stringify({ routePath: route, services }),
    [PORT_FILES_ENV]: JSON.stringify(portFiles),
  }
}

/**
 * 假 res：两种实现用的是**两套 node 习惯**（资产那份走 `statusCode`+`setHeader`，
 * 框架那份走 `writeHead`）⇒ 替身两边都支持，这样才**喂得进同一个夹具**。
 */
function fakeRes() {
  const seen = { status: 0, headers: {} as Record<string, string>, body: '' }
  return {
    seen,
    res: {
      // ⚠ 资产那份是 `res.statusCode = …`（赋值），框架那份是 `writeHead(…)` ⇒ 两者都收。
      get statusCode() { return seen.status },
      set statusCode(v: number) { seen.status = v },
      setHeader(name: string, value: string) { seen.headers[name.toLowerCase()] = value },
      writeHead(status: number, headers: Record<string, string>) {
        seen.status = status
        for (const [k, v] of Object.entries(headers)) seen.headers[k.toLowerCase()] = v
      },
      end(body: string | Uint8Array) {
        seen.body = typeof body === 'string' ? body : Buffer.from(body).toString('utf8')
      },
    },
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

test('apply：注册**那条参数路由 + 每个服务一条地址路由**，收尾全注销；没有 webServer 时跳过而不炸', async () => {
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
    // ⭐ 两类路由：① 那份参数（浏览器靠它发现 N 个服务）② **每个服务一条**地址路由
    //    （原先这一条由**项目自己的 host 半**注册 ⇒ 现在归框架，新项目不用写）。
    assert.deepEqual(registered.map(r => r.path).sort(),
      ['/mecha/panels', '/svc-a/monitor-url'], '路由集合不对')
    for (const r of registered) assert.equal(r.kind, 'exact')
    // 收尾
    ;(effects[0] as () => void)()
    assert.equal(disposed, 2, '收尾必须把两条路由都注销掉（否则 dsh 会攒下一堆旧路由）')

    // ⚠ 没有 webServer（headless 部署）：**跳过**而不是整个插件不激活，也不炸
    await apply({ logger: {}, inject(_n, fn) { fn({}) } })
  } finally {
    for (const k of [ROUTE_ENV, SERVICES_ENV, PORT_FILES_ENV]) delete process.env[k]
    Object.assign(process.env, saved)
  }
})

test('⭐ 缺某服务的端口文件路径 ⇒ **抛**（启动期配置错要响亮，不许起个"只有一半路由"的面板）', async () => {
  const saved = { ...process.env }
  Object.assign(process.env, envOf(ONE_SVC, '/mecha/panels', { /* 故意不给 AAA */ }))
  try {
    await assert.rejects(() => apply({ logger: {}, inject(_n, fn) { fn({}) } }),
      new RegExp(PORT_FILES_ENV))
  } finally {
    for (const k of [ROUTE_ENV, SERVICES_ENV, PORT_FILES_ENV]) delete process.env[k]
    Object.assign(process.env, saved)
  }
})

test('readPortFiles：缺 / 空 ⇒ **空表**（不是抛）——"没有监控面"与"注入断了"是两件事', () => {
  assert.deepEqual(readPortFiles({}), {})
  assert.deepEqual(readPortFiles({ [PORT_FILES_ENV]: '  ' }), {})
  assert.deepEqual(readPortFiles({ [PORT_FILES_ENV]: '{"AAA":"/x/.p"}' }), { AAA: '/x/.p' })
  assert.throws(() => readPortFiles({ [PORT_FILES_ENV]: '["nope"]' }), /形状/)
  assert.throws(() => readPortFiles({ [PORT_FILES_ENV]: '{bad' }), /不是 JSON/)
})

test('⭐⭐ 地址路由：与资产 `host/monitor-url.ts` **同夹具逐字节等价**（成功回执）', () => {
  // ⚠ 这是"同一件事两份实现"（资产 TS 那份给项目；这份纯 JS 给框架，因为它零 import）
  //   ⇒ 必须有一条判据钉住**两者等价**。喂同一组夹具、比状态码与**字节**。
  const dir = mkdtempSync(join(tmpdir(), 'mecha-panel-'))
  try {
    const portFile = join(dir, '.svc-a-monitor-port')
    writeFileSync(portFile, '61234\n', 'utf8')
    const mine = fakeRes()
    makeMonitorUrlHandler(portFile)({}, mine.res)
    const theirs = fakeRes()
    assetMonitorUrlHandler({ root: dir, portFile: '.svc-a-monitor-port' })({} as never, theirs.res as never)
    assert.equal(mine.seen.status, 200)
    assert.equal(theirs.seen.status, 200)
    assert.equal(mine.seen.body, theirs.seen.body, '两份实现的成功回执必须逐字节相同')
    assert.deepEqual(JSON.parse(mine.seen.body), { base: 'http://127.0.0.1:61234' })
    // no-store（面板不吃缓存：地址每次现取）
    assert.equal(mine.seen.headers['cache-control'], 'no-store')
    assert.equal(mine.seen.headers['content-length'], String(Buffer.byteLength(mine.seen.body)))
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('⭐ 地址路由：端口文件坏 ⇒ 503 + 可读错误，**不回落**；且**不外泄绝对路径**', () => {
  const dir = mkdtempSync(join(tmpdir(), 'mecha-panel-'))
  try {
    // ① 文件不存在
    const missing = fakeRes()
    makeMonitorUrlHandler(join(dir, '.nope'))({}, missing.res)
    assert.equal(missing.seen.status, 503)
    const body1 = JSON.parse(missing.seen.body) as { base?: string; error: string }
    assert.equal(body1.base, undefined, '失败体里**不许**有半截地址')
    assert.match(body1.error, /读不到监控端口/)
    // ② 内容非法（三种：非数字 / 越界 / 空）
    for (const bad of ['abc', '0', '70000', '']) {
      writeFileSync(join(dir, '.p'), bad, 'utf8')
      const r = fakeRes()
      makeMonitorUrlHandler(join(dir, '.p'))({}, r.res)
      assert.equal(r.seen.status, 503, `内容 ${JSON.stringify(bad)} 应当 503（不回落）`)
    }
    // ③ ⭐ **绝对路径不外泄**：错误文本只说**文件名**（它会进浏览器）
    const leak = fakeRes()
    makeMonitorUrlHandler(join(dir, '.nope'))({}, leak.res)
    assert.ok(!leak.seen.body.includes(dir), `错误体里出现了目录：${leak.seen.body}`)
    assert.ok(!leak.seen.body.includes(tmpdir()), `错误体里出现了临时根：${leak.seen.body}`)
    assert.ok(leak.seen.body.includes('.nope'), `错误体该说清是哪个文件：${leak.seen.body}`)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('⭐ 地址路由**每次现读**（端口漂移下一拍就落新端口；退化成缓存会红）', () => {
  const dir = mkdtempSync(join(tmpdir(), 'mecha-panel-'))
  try {
    const portFile = join(dir, '.p')
    writeFileSync(portFile, '60001', 'utf8')
    assert.equal(resolveMonitorBase(portFile), 'http://127.0.0.1:60001')
    writeFileSync(portFile, '60002', 'utf8')
    assert.equal(resolveMonitorBase(portFile), 'http://127.0.0.1:60002',
      '第二次读到的还是旧端口 ⇒ 缓存了（端口漂移就不自愈了）')
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('apply：参数坏 ⇒ 抛（不吞）', async () => {
  const saved = { ...process.env }
  try {
    delete process.env[ROUTE_ENV]
    delete process.env[SERVICES_ENV]
    delete process.env[PORT_FILES_ENV]
    await assert.rejects(() => apply({ logger: {} }), new RegExp(ROUTE_ENV))
  } finally {
    for (const k of [ROUTE_ENV, SERVICES_ENV, PORT_FILES_ENV]) delete process.env[k]
    Object.assign(process.env, saved)
  }
})
