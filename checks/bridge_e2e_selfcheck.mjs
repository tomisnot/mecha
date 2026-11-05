/**
 * 脱离 dsh，直接测 mecha 的长任务桥插件。
 *
 * 造一个假 cordis ctx，按真实 env 调用 apply(ctx)，手工触发 tools/post-execute，
 * 验证“事件 → 匹配 → 摊平 → 开 job”整条链路。测试不触碰真实网络和真实 dsh。
 */
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const mod = await import(new URL(
  '../mecha/dsh_adapter/assets/host-plugin/mecha-panel-host.mjs', import.meta.url,
).href)
const { apply } = mod

const dir = mkdtempSync(join(tmpdir(), 'mecha-bridge-'))
const monitorFile = join(dir, '.monitor-port')
const pagePort = join(dir, '.page-port')
writeFileSync(monitorFile, '12346')
writeFileSync(pagePort, '12347')

process.env.MECHA_PANEL_ROUTE = '/demo/monitor-url'
process.env.MECHA_PANEL_SERVICES = JSON.stringify({
  routePath: '/demo/monitor-url',
  services: [{ id: 'DEMO', title: 'Demo 监控', routePath: '/demo/monitor-url' }],
})
process.env.MECHA_PANEL_PORT_FILES = JSON.stringify({ DEMO: monitorFile })
process.env.MECHA_TASK_BRIDGE_FILE = pagePort
process.env.MECHA_TASK_SPECS = JSON.stringify([{
  tool: 'demo_submit',
  service: 'DEMO',
  status_tool: 'read_job',
  cancel_tool: 'cancel_run',
  task_id_field: 'job_id',
  label_field: 'command',
  state_field: 'state',
  terminal_states: ['done', 'failed', 'cancelled'],
  wait_field: 'wait',
  wait_value: 'terminal',
}])

const starts = []
const hooks = new Map()
const fakeJobs = {
  start(spec) {
    starts.push(spec)
    return 'mecha-1'
  },
}
const ctx = {
  logger: { info: () => {}, warn: () => {} },
  on(name, fn) {
    hooks.set(name, fn)
    return () => hooks.delete(name)
  },
  inject(deps, cb) {
    if (deps.includes('webServer')) return undefined
    return cb({ jobs: fakeJobs, on: ctx.on })
  },
  effect(fn) { return fn() },
}

globalThis.fetch = async () => ({
  ok: true,
  json: async () => ({ is_error: false, value: { state: 'done', job_id: 'job-1' } }),
})

await apply(ctx)
const handler = hooks.get('tools/post-execute')
assert.ok(handler, 'tools/post-execute must be registered')
const next = async () => ({ kind: 'accept' })

async function fire(name, value) {
  starts.length = 0
  return handler(
    { name, agent: { id: 'sess-1' }, callId: 'c1', rootCallId: 'c1' },
    { isError: false, value },
    next,
  )
}

await fire('mcp__mecha__demo_submit', {
  is_error: false,
  text: JSON.stringify({ ok: true, job_id: 'job-1', command: 'run_custom' }),
})
assert.equal(starts.length, 1, 'JSON payload starts one job')
assert.equal(starts[0].kind, 'mecha')
assert.equal(starts[0].label, 'run_custom')
assert.equal(starts[0].owner, 'sess-1')

await fire('mcp__mecha__demo_submit', {
  is_error: false,
  text: "{'ok': True, 'job_id': 'job-2', 'command': 'run_custom'}",
})
assert.equal(starts.length, 1, 'Python repr payload starts one job')
assert.equal(starts[0].label, 'mcp__mecha__demo_submit job-2')

await fire('pwsh', { is_error: false, text: 'hello' })
assert.equal(starts.length, 0, 'unrelated tools do not start a job')

console.log('bridge_e2e_selfcheck: PASS')
