/**
 * 监控面板的**壳**：挂载 + 轮询 + CSS 注入 + 事件委托（**唯一需要 react 的地方**）。
 *
 * ## 为什么壳这么薄
 *
 * 呈现的**全部决策**都在 `panel-data.ts`（状态/文案/徽标/自证）与 `panel-view.ts`
 * （HTML 字符串生成）里，**它们是纯函数、被 `node:test` 逐档覆盖**。
 * 这里只做三件 React 才能做的事：
 *   1. 3s 轮询（展示刷新，**非正确性依赖**）；
 *   2. 注入样式表一次；
 *   3. 把生成的 HTML 塞进容器，并用**事件委托**处理页签切换与折叠态。
 *
 * ⚠ **壳不进框架门禁的执行面**（`node:test` 渲染不了 react）⇒ 它只过 typecheck
 * （消费者各自的 `npm run typecheck`）。**这条代价的前提是"壳保持薄"**：
 * 若壳开始长出决策逻辑，就要把它搬回 `.ts` 层（否则那段逻辑没有任何判据覆盖）。
 *
 * ## react 从哪来
 *
 * **宿主提供**：dsh 的模块表（PLATFORM_MODULES）注入 `react` / `react/jsx-runtime`，
 * 项目的打包配置把它**外置**（避免双 React）。本目录**不声明任何 npm 依赖**。
 */
import { useEffect, useRef, useState, type MouseEvent } from 'react'
import type { PanelState } from './monitor-client.ts'
import {
  configOf,
  eventsOf,
  loadPanel,
  selfCheckLine,
  stateBadge,
  stateText,
  tabLabel,
  type StandardPage,
} from './panel-data.ts'
import {
  badgeHtml,
  cfgPageHtml,
  esc,
  recPageHtml,
  tabsHtml,
  TAB_CSS,
} from './panel-view.ts'

/** 轮询间隔（展示刷新，非正确性依赖；**共享常量、不开旋钮**——三家实测都是这个值）。 */
const POLL_MS = 3000

/** 展开态与页签跨挂载保留（模块级）。**渲染只问谓词**，不直接读这个集合。 */
const openSet = new Set<string>()
let savedPage: StandardPage = 'rec'
const isOpen = (k: string) => openSet.has(k)

/** 标准两页（页签文案可在参数块 `TABS` 里覆盖）。 */
function standardPages(): { id: StandardPage; label: string }[] {
  return [{ id: 'rec', label: tabLabel('rec') }, { id: 'cfg', label: tabLabel('cfg') }]
}

export function MonitorTabBody() {
  const ref = useRef<HTMLDivElement>(null)
  const [page, setPage] = useState<StandardPage>(savedPage)
  // ⭐ **唯一状态**（`PanelState` 判别联合）：没有"取数失败就什么都不画"的分支——
  // 每一档都带 `detail` 或数据，空白在类型上不可表达。`null` = 首拍还没回来。
  const [state, setState] = useState<PanelState | null>(null)

  useEffect(() => {
    let dead = false
    const poll = async () => {
      const st = await loadPanel()
      if (dead) return
      setState(st)
      // R8 非退化自证：判"面板正常"必须同时证明"数据真的到了"（否则"空 vs 空"同样通过）。
      if (st.kind === 'ok') console.info('[mecha] 监控面板：' + selfCheckLine(st))
      else console.warn('[mecha] 监控面板非 ok：' + selfCheckLine(st))
    }
    poll()
    const t = setInterval(poll, POLL_MS)
    return () => { dead = true; clearInterval(t) }
  }, [])

  useEffect(() => {
    if (!document.querySelector('style[data-mecha-tab-css]')) {
      const s = document.createElement('style')
      s.setAttribute('data-mecha-tab-css', '1')
      s.textContent = TAB_CSS
      document.head.appendChild(s)
    }
  }, [])

  // 事件委托：页签切换 + 折叠态。React 委托挂在容器上，对 `dangerouslySetInnerHTML`
  // 重建的子树同样生效。
  const onClick = (e: MouseEvent) => {
    const tgt = e.target as HTMLElement
    const pg = tgt.closest?.('[data-page]')
    if (pg) {
      const p = pg.getAttribute('data-page') as StandardPage
      savedPage = p
      setPage(p)
      return
    }
    const sm = tgt.closest?.('summary')
    const key = sm?.parentElement?.getAttribute?.('data-key')
    if (key) { if (openSet.has(key)) openSet.delete(key); else openSet.add(key) }
  }

  const b = stateBadge(state)
  const head = tabsHtml(standardPages(), page, badgeHtml(b.cls, b.text))
  // ⚠ 取事件与配置**走 panel-data 里那两个被单测覆盖的纯函数**（键只有一处）：
  // 早先的写法直接拿请求路径 `HISTORY_PATH`（带 `?since_seq=0`）去索引 payloads，
  // 而 payloads 是按**基础路由键**存的 ⇒ 页面永远"暂无写事件"，自证行却报"事件行=N"。
  const body = state?.kind === 'ok'
    ? (page === 'rec' ? recPageHtml(eventsOf(state), isOpen) : cfgPageHtml(configOf(state), isOpen))
    : `<div class="mecha-page"><div class="mecha-msg muted">${
        esc(state ? stateText(state) : '连接中…')}</div></div>`

  return (
    <div ref={ref} onClick={onClick}
      dangerouslySetInnerHTML={{ __html: `<div class="mecha-tab-native">${head}${body}</div>` }} />
  )
}
