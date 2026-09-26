/**
 * 视图层单测（**共享测试**：随资产一起被复制）。
 *
 * 跑：`node --test mecha/dsh-panel/panel-view.test.ts`
 *
 * 这一层是**纯字符串生成** ⇒ 可以把"**面板不空白**"钉成**字符串级**的性质：
 * 每个分支都必须返回**非空且含可读文本**的 HTML，而不是空串。
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  badgeHtml,
  cfgPageHtml,
  esc,
  fmtVal,
  recPageHtml,
  tabsHtml,
  TAB_CSS,
  type Ev,
} from './panel-view.ts'

const ev = (seq: number, over: Partial<Ev> = {}): Ev => ({
  seq, kind: 'set', actor: 'ai', target: 'field.x', before: 1, after: 2,
  reason: '扫描', call_id: 'call-1', ...over,
})

test('esc：五个危险字符都转义（注入点必须过它）', () => {
  assert.equal(esc('<a href="x">&'), '&lt;a href=&quot;x&quot;&gt;&amp;')
  assert.equal(esc(null), '')
})

test('fmtVal：空值 / 指针 / 截断 / 长 JSON / 标量', () => {
  assert.equal(fmtVal(null), '∅')
  assert.equal(fmtVal(undefined), '∅')
  assert.equal(fmtVal({ $pointer: true, shape: [3, 4] }), '⟨数组 3×4⟩')
  assert.equal(fmtVal({ $truncated: true, kept: 512, total: 4096 }), '⟨截断 512/4096⟩')
  assert.equal(fmtVal('ok'), 'ok')
  assert.equal(fmtVal(7), '7')
  const long = fmtVal({ a: 'x'.repeat(200) })
  assert.ok(long.length <= 120, `长 JSON 应被截断，实得 ${long.length}`)
  assert.ok(long.endsWith('…'))
})

test('⭐ recPageHtml：展开态**换了结果就变**（R17：注入的缝必须真的生效）', () => {
  const evs = [ev(1)]
  const closed = recPageHtml(evs, () => false)
  const opened = recPageHtml(evs, () => true)
  assert.notEqual(closed, opened, '换掉 isOpen 结果没变 ⇒ 这个缝是死的')
  assert.ok(!closed.includes('" open>'), '闭合时不该有 open')
  assert.ok(opened.includes('" open>'), '展开时应当有 open')
})

test('recPageHtml：每条含五要素（seq/actor/kind/reason/target+before→after+call_id）', () => {
  const html = recPageHtml([ev(42, { actor: 'human', reason: '', after: { $truncated: true, kept: 1, total: 2 } })],
    () => false)
  for (const marker of ['#42', 'human', 'set', 'field.x', 'before → after',
                        '⟨截断 1/2⟩', 'call_id', 'call-1']) {
    assert.ok(html.includes(marker), `缺 ${marker}：${html.slice(0, 200)}`)
  }
})

test('recPageHtml：空表 ⇒ **可读文案**（不是空串、也不是空白）', () => {
  const html = recPageHtml([], () => false)
  assert.ok(html.length > 0)
  assert.match(html, /暂无写事件/)
})

test('cfgPageHtml：族→前缀二级树 + 计数 + (top) + orphans 表', () => {
  const html = cfgPageHtml({
    has_schema: true,
    groups: { field: [{ prefix: 'field', rows: [{ name: 'field.x', short: 'x', value: 1 }] },
                      { prefix: '', rows: [] }] },
    orphans: [{ name: 'zzz', short: 'zzz', value: 2, set: { actor: 'ai', seq: 9 } }],
  }, () => false)
  for (const marker of ['field', 'x', '(top)', '契约外（orphans）', 'zzz', 'ai #9']) {
    assert.ok(html.includes(marker), `缺 ${marker}：${html.slice(0, 240)}`)
  }
})

test('cfgPageHtml：没 schema ⇒ **可读说明**，不是一棵空树；且展开态同样随谓词变', () => {
  const noSchema = cfgPageHtml({ has_schema: false, groups: {} }, () => false)
  assert.match(noSchema, /无 schema/)
  const c = cfgPageHtml({ has_schema: true, groups: { f: [{ prefix: 'f', rows: [] }] } }, () => false)
  const o = cfgPageHtml({ has_schema: true, groups: { f: [{ prefix: 'f', rows: [] }] } }, () => true)
  assert.notEqual(c, o)
  assert.ok(o.includes('" open>'))
})

test('tabsHtml / badgeHtml：当前页有 on 标记；徽标类名原样带出', () => {
  const html = tabsHtml([{ id: 'rec', label: '甲' }, { id: 'cfg', label: '乙' }], 'cfg',
    badgeHtml('error', '权威离线'))
  assert.match(html, /data-page="rec"/)
  assert.match(html, /data-page="cfg" class="on">乙/)
  assert.match(html, /class="mecha-badge error">权威离线</)
})

test('TAB_CSS：只用**中性前缀**（共享资产里不许有项目前缀）', () => {
  assert.ok(TAB_CSS.includes('.mecha-tab-native'))
  assert.ok(TAB_CSS.includes('.mecha-ev'))
  for (const prefix of ['.mltb', '.re0']) {   // 这两个前缀只作为**反面语料**出现
    assert.ok(!TAB_CSS.includes(prefix), `CSS 里混进了项目前缀 ${prefix}`)
  }
})
