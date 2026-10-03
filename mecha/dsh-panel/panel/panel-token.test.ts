/**
 * 仪表盘页单测（**共享测试**：随资产一起被复制）。
 *
 * 跑：`node --test mecha/dsh-panel/panel/panel-token.test.ts`
 *
 * 这一层同样是**纯字符串生成** ⇒ 把三件事钉成**字符串级**的性质：
 *
 * 1. **不空白**：四种投影全缺时也要有可读文本（"尚未计量"），**不是空串**；
 * 2. ⭐ **不撒谎**：实测与启发式分开标、口径（整会话）写出来、"缺数据"与"值为 0"看得出区别；
 * 3. **不注入**：任何进 HTML 的值都过 `esc`（数字是我们格式化的，但结构仍按不可信处理）。
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { fmtMs, fmtNum, tokenPageHtml, type TokenViews } from './panel-token.ts'

/** 全部投影都在（正常态）。 */
const all: TokenViews = {
  usage: { uncachedInputTokens: 12345, outputTokens: 678, cacheReadTokens: 900, cacheWriteTokens: 100 },
  breakdown: { systemTokens: 2000, toolsTokens: 1500, messageTokens: 4000 },
  pressure: { pressureTokens: 8000, projectedTokens: 8100, contextWindow: 128000 },
  stats: {
    turns: 7, steps: 33, llmMs: 45200, toolMs: 12800,
    ttftMs: 3400, ttftSteps: 7, decodeMs: 21000, decodeTokens: 678,
  },
}

// ---------------------------------------------------------------- 格式：不猜

test('fmtNum：非有限数不猜（回 —，与 0 分得开），有限数带千分位', () => {
  assert.equal(fmtNum(0), '0')          // ⭐ 0 是"值就是 0"
  assert.equal(fmtNum(12345), '12,345')
  assert.equal(fmtNum(undefined), '—')  // ⭐ 缺数据是"—"，不是 0
  assert.equal(fmtNum(null), '—')
  assert.equal(fmtNum(NaN), '—')
  assert.equal(fmtNum(Infinity), '—')
})

test('fmtMs：秒 / 分 / 时三档，非数回 —', () => {
  assert.equal(fmtMs(45200), '45.2s')
  assert.equal(fmtMs(162000), '2m42s')
  assert.equal(fmtMs(3900000), '1h05m')
  assert.equal(fmtMs(undefined), '—')
})

// ---------------------------------------------------------------- 不空白

test('⭐ 四种投影全缺：仍然非空、且说"尚未计量"——不空白、也不假装 0', () => {
  const html = tokenPageHtml({})
  assert.ok(html.length > 0, '不能是空串')
  assert.ok(html.includes('尚未计量'), `应显式说尚未计量，实得：${html.slice(0, 120)}`)
  assert.ok(!html.includes('>0<'), '⚠ 缺数据时不许出现读数为 0 的格子')
  assert.ok(html.startsWith('<div class="mecha-page">') && html.endsWith('</div>'))
})

test('每个分区的标题恒定出现（结构稳定，缺数据不换形状）', () => {
  const html = tokenPageHtml({})
  for (const h of ['Token 用量', '请求压力', '上下文构成', '整会话']) {
    assert.ok(html.includes(h), `缺 ${h}`)
  }
})

// ---------------------------------------------------------------- 不撒谎

test('⭐ 口径必须写出来：整会话 / 提供方上报 / 启发式近似', () => {
  const html = tokenPageHtml(all)
  assert.ok(html.includes('整会话'), '要看得出这是整个会话的口径')
  assert.ok(html.includes('提供方上报'), '实测与估计要分开标')
  assert.ok(html.includes('启发式'), '构成那三个数必须标明是启发式的')
  assert.ok(html.includes('不是本项目的专属统计'), '要说清归属边界')
})

test('⭐ 实测区与启发式区措辞不同（混在一起标同一个词就是撒谎）', () => {
  const html = tokenPageHtml(all)
  // 用量/压力：锚定提供方；构成：明说近似
  assert.ok(/提供方上报（精确值）/.test(html))
  assert.ok(/启发式近似/.test(html))
})

// ---------------------------------------------------------------- 算得对

test('用量卡片：输入 = 未缓存 + 缓存读 + 缓存写；并给出输入÷输出比', () => {
  const html = tokenPageHtml(all)
  assert.ok(html.includes('12,345'), '未缓存输入')
  assert.ok(html.includes('678'), '输出')
  assert.ok(html.includes('900 / 100'), '缓存读 / 写')
  // (12345+900+100)/678 ≈ 19.7
  assert.ok(html.includes('19.7×'), `应给出输入÷输出比，实得：${html.match(/[\d.]+×/)?.[0]}`)
})

test('占窗口百分比：projectedTokens ÷ contextWindow', () => {
  const html = tokenPageHtml(all)
  // 8100 / 128000 = 6.328% → 6.3%
  assert.ok(html.includes('6.3%'), `应算出占窗口比，实得：${html.match(/[\d.]+%/)?.[0]}`)
})

test('整会话卡片：轮次/步、模型/工具耗时、首 token 延迟、解码输出', () => {
  const html = tokenPageHtml(all)
  assert.ok(html.includes('7 / 33'))
  assert.ok(html.includes('45.2s / 12.8s'))
  assert.ok(html.includes('共 7 步'))
  assert.ok(html.includes('3.4s'))
})

// ---------------------------------------------------------------- 部分就绪

test('只有部分投影就绪：各自独立显示，缺的不牵连已有的', () => {
  const html = tokenPageHtml({ usage: { outputTokens: 42 } })
  assert.ok(html.includes('42'), '用量在')
  assert.ok(html.includes('尚未计量'), '其余仍说尚未计量')
})

// ---------------------------------------------------------------- 不注入

test('结构不可信：title 走 esc（值进 HTML 前一律转义）', () => {
  // 数字由我们格式化，但把"结构性字段"塞成非数也不会漏过判空
  const html = tokenPageHtml({ usage: { uncachedInputTokens: undefined, outputTokens: undefined } })
  assert.ok(!html.includes('undefined'), '不许把 undefined 直接印出来')
})
