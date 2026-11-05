import assert from 'node:assert/strict'

const mod = await import(new URL(
  '../mecha/dsh-panel/host-plugin/mecha-panel-host.mjs', import.meta.url,
).href)
const { coerceTaskValue } = mod

assert.equal(coerceTaskValue({
  is_error: false,
  text: JSON.stringify({ ok: true, job_id: 'job-1', command: 'run_custom', state: 'running' }),
}, 'job_id').job_id, 'job-1', 'JSON text payload')

assert.equal(coerceTaskValue({
  is_error: false,
  text: "{'ok': True, 'job_id': 'job-7', 'command': 'run_custom', 'state': 'running'}",
}, 'job_id').job_id, 'job-7', 'Python repr payload')

assert.equal(coerceTaskValue({
  content: [{ type: 'text', text: JSON.stringify({ job_id: 'job-3' }) }],
}, 'job_id').job_id, 'job-3', 'content[] payload')

assert.equal(coerceTaskValue({
  structuredContent: { job_id: 'job-4' },
}, 'job_id').job_id, 'job-4', 'structuredContent payload')

assert.equal(coerceTaskValue({ job_id: 'job-5' }, 'job_id').job_id,
  'job-5', 'plain object payload')

const nested = {
  content: [{
    type: 'text',
    text: JSON.stringify({
      is_error: false,
      text: JSON.stringify({ ok: true, job_id: 'job-11', command: 'run_custom', state: 'running' }),
    }),
  }],
}
const value = coerceTaskValue(nested, 'job_id')
assert.equal(value.job_id, 'job-11', 'nested two-level payload')
assert.equal(value.command, 'run_custom', 'nested label survives')
assert.equal(value.state, 'running', 'nested state survives')

console.log('bridge_host_plugin_selfcheck: PASS')
