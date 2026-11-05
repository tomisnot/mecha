/**
 * 面板**仪表盘页**：token 计量的呈现（**纯字符串生成**，无 react、无 DOM、无 node）。
 *
 * ## 数据从哪来（决定这个文件为什么长这样）
 *
 * 数字全部来自 **dsh 的原生投影**，由壳（`MonitorTabBody.tsx`）经 `useProjection(key)`
 * 取到后传进来。本文件**不认识 dsh 的类型** —— 它只认下面那几个**结构化接口**
 * （与投影的 wire 形状同构）。⇒ 三个好处：
 *
 * 1. **可被 `node:test` 逐档覆盖**（本工程最想钉住的那类性质：不空白、口径不撒谎）；
 * 2. 资产**不 import 任何 dsh 包**（连类型都不），保持"零 dsh 依赖"这条既有口径；
 * 3. 投影换版本时，坏形状在**一处**显形（`tokenPageHtml` 的判空），不在渲染里散开。
 *
 * ## ⚠ 三条口径纪律（本文件存在的理由那一半）
 *
 * 1. **实测与启发式分开标**：`tokenUsage` / `sessionStats` 是**提供方上报的精确值**；
 *    `contextBreakdown` 是**启发式的"构成"**（dsh 原话：*"approximations of composition,
 *    never as a total"*，且它**系统性低估 CJK 与 JSON schema**）。两者混在一起标同一个词
 *    就是撒谎 ⇒ 本文件**用不同的措辞与提示**分开它们。
 * 2. **口径要写出来**：前缀写 `整会话`/`本轮`。**不写 = 读的人会以为都是本项目的。**
 * 3. **空不是错**：投影未就绪时显示"尚未计量"，**不显示 0** ——
 *    "没有数据"与"值就是 0"必须看得出区别（同 `empty` 档不伪装成成功）。
 */

/** 提供方上报的用量（`tokenUsage` 投影；**精确值**）。 */
export interface TokenUsageWire {
  uncachedInputTokens?: number
  outputTokens?: number
  cacheReadTokens?: number
  cacheWriteTokens?: number
}

/** 启发式的上下文构成（`contextBreakdown` 投影；**近似值**）。 */
export interface ContextBreakdownWire {
  systemTokens?: number
  toolsTokens?: number
  messageTokens?: number
}

/** 最新的请求压力（`contextPressure` 投影；提供方上报 + 增量估计）。 */
export interface ContextPressureWire {
  pressureTokens?: number
  projectedTokens?: number
  contextWindow?: number
}

/** 整会话的轮次/步计数与墙钟（`sessionStats` 投影；**来自完整持久日志**）。 */
export interface SessionStatsWire {
  turns?: number
  steps?: number
  llmMs?: number
  toolMs?: number
  ttftMs?: number
  ttftSteps?: number
  decodeMs?: number
  decodeTokens?: number
}

/** 仪表盘的全部输入（壳按 key 取好，缺哪个就是 `undefined`）。 */
export interface TokenViews {
  usage?: TokenUsageWire
  breakdown?: ContextBreakdownWire
  pressure?: ContextPressureWire
  stats?: SessionStatsWire
}

/** 数字格式：千分位；非有限数**不猜**（回 `—`，与"0"区分）。 */
export function fmtNum(v: unknown): string {
  return typeof v === 'number' && Number.isFinite(v) ? v.toLocaleString('en-US') : '—'
}

/** 毫秒 → 人读时长（`45.2s` / `2m42s` / `1h05m`）。 */
export function fmtMs(v: unknown): string {
  if (typeof v !== 'number' || !Number.isFinite(v)) return '—'
  const s = v / 1000
  if (s < 60) return `${Math.round(s * 10) / 10}s`
  const whole = Math.round(s)
  if (whole < 3600) return `${Math.floor(whole / 60)}m${String(whole % 60).padStart(2, '0')}s`
  return `${Math.floor(whole / 3600)}h${String(Math.floor((whole % 3600) / 60)).padStart(2, '0')}m`
}

/** 转义（与 `panel-view.ts` 的 `esc` 同源纪律：类名与文案都中性）。 */
const esc = (x: unknown): string =>
  String(x ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c] as string))

/** 一格读数：`{label, value, hint?}`。 */
interface Cell {
  label: string
  value: string
  hint?: string
}

/** 把一组读数画成卡片行（`.mecha-cards`）。 */
function cardsHtml(title: string, note: string, cells: Cell[]): string {
  const body = cells.map((c) =>
    `<div class="mecha-card"><div class="mecha-card-k">${esc(c.label)}</div>`
    + `<div class="mecha-card-v">${esc(c.value)}</div>`
    + (c.hint ? `<div class="mecha-card-h">${esc(c.hint)}</div>` : '') + '</div>').join('')
  return `<h4>${esc(title)}</h4><div class="mecha-note">${esc(note)}</div>`
    + `<div class="mecha-cards">${body}</div>`
}

/**
 * 仪表盘页。
 *
 * @param v - 按 key 取到的投影值；缺哪个都可以（显示"尚未计量"）。
 * @returns 该页的 HTML 片段。
 */
export function tokenPageHtml(v: TokenViews): string {
  const u = v.usage
  const b = v.breakdown
  const p = v.pressure
  const s = v.stats

  // ---- 用量（提供方上报，精确）----
  const hasUsage = !!u && (
    typeof u.uncachedInputTokens === 'number' || typeof u.outputTokens === 'number'
  )
  const input = (u?.uncachedInputTokens ?? 0) + (u?.cacheReadTokens ?? 0) + (u?.cacheWriteTokens ?? 0)
  const output = u?.outputTokens ?? 0
  const inOut = output > 0 ? (input / output) : undefined
  const usageCards = hasUsage
    ? cardsHtml('Token 用量', '整会话累计 · 提供方上报（精确值）', [
        { label: '输入（未缓存）', value: fmtNum(u?.uncachedInputTokens) },
        { label: '输出', value: fmtNum(u?.outputTokens) },
        { label: '缓存读 / 写', value: `${fmtNum(u?.cacheReadTokens)} / ${fmtNum(u?.cacheWriteTokens)}` },
        { label: '输入 ÷ 输出', value: inOut === undefined ? '—' : `${Math.round(inOut * 10) / 10}×`,
          hint: '每产出一个 token 要重发多少输入' },
      ])
    : `<h4>Token 用量</h4><div class="mecha-msg muted">尚未计量（提供方还没上报 usage）</div>`

  // ---- 请求压力（提供方锚定 + 增量估计）----
  const pressureCards = (p && (typeof p.pressureTokens === 'number' || typeof p.projectedTokens === 'number'))
    ? cardsHtml('请求压力', '最近一次请求 · 锚定在提供方上报值上', [
        { label: '最近请求提示词', value: fmtNum(p.pressureTokens) },
        { label: '下一次预估', value: fmtNum(p.projectedTokens), hint: '仅增量是估计的' },
        { label: '上下文窗口', value: fmtNum(p.contextWindow) },
        { label: '占窗口', value: (typeof p.projectedTokens === 'number' && typeof p.contextWindow === 'number' && p.contextWindow > 0)
            ? `${Math.round((p.projectedTokens / p.contextWindow) * 1000) / 10}%` : '—' },
      ])
    : `<h4>请求压力</h4><div class="mecha-msg muted">尚未计量</div>`

  // ---- 上下文构成（⚠ 启发式）----
  const hasBreak = !!b && (
    typeof b.systemTokens === 'number' || typeof b.toolsTokens === 'number' || typeof b.messageTokens === 'number'
  )
  const breakCards = hasBreak
    ? cardsHtml('上下文构成', '⚠ 启发式近似（**不是**总数、且系统性低估 CJK 与 JSON schema）', [
        { label: '系统提示词', value: fmtNum(b?.systemTokens) },
        { label: '工具 schema', value: fmtNum(b?.toolsTokens), hint: '这一项最可改：我们自己的工具面' },
        { label: '消息', value: fmtNum(b?.messageTokens) },
      ])
    : `<h4>上下文构成</h4><div class="mecha-msg muted">尚未计量</div>`

  // ---- 整会话（轮次/步/墙钟）----
  const hasStats = !!s && typeof s.turns === 'number'
  const statsCards = hasStats
    ? cardsHtml('整会话', '来自完整持久日志（分页与压缩不改变它）· 口径：整个会话', [
        { label: '轮次 / 步', value: `${fmtNum(s?.turns)} / ${fmtNum(s?.steps)}` },
        { label: '模型 / 工具耗时', value: `${fmtMs(s?.llmMs)} / ${fmtMs(s?.toolMs)}` },
        { label: '首 token 延迟', value: fmtMs(s?.ttftMs), hint: typeof s?.ttftSteps === 'number' ? `共 ${fmtNum(s.ttftSteps)} 步` : undefined },
        { label: '解码输出 token', value: fmtNum(s?.decodeTokens) },
      ])
    : `<h4>整会话</h4><div class="mecha-msg muted">尚未计量</div>`

  return `<div class="mecha-page">`
    + `<div class="mecha-note">数字来自 dsh 原生投影。**整会话**口径含本会话的全部工具与模型开销，`
    + `不是本项目的专属统计 —— 归属到本项目的只有"工具 schema"那一项。</div>`
    + usageCards + pressureCards + breakCards + statsCards
    + `</div>`
}
