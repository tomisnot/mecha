/**
 * 监控面板的**壳**：挂载 + 轮询 + CSS 注入 + 事件委托（**唯一需要 react 的地方**）。
 *
 * ## 为什么壳这么薄
 *
 * 呈现的**全部决策**都在 `panel-data.ts`（状态/文案/徽标/自证）与 `panel-view.ts`
 * （HTML 字符串生成，含**项目的附加页**）里，**它们是纯函数、被 `node:test` 逐档覆盖**。
 * 这里只做 React 才能做的事：3s 轮询、注入样式、把生成的 HTML 塞进容器 + 事件委托。
 *
 * ⚠ **壳不进框架门禁的执行面**（`node:test` 渲染不了 react）⇒ 它只过 typecheck
 * （**消费者各自的 `npm run typecheck` 是验收义务**）。**这条代价的前提是"壳保持薄"**：
 * 壳一旦长出决策逻辑，就要把它搬回 `.ts` 层（否则那段逻辑没有任何判据覆盖）。
 *
 * ## 项目怎么用
 *
 * ```tsx
 * // 项目自己的 client/index.ts 里：填参数块 + 声明附加页 + 挂载
 * const extraPages: ExtraPageSpec[] = [{ id: '…', label: '…', path: '/…?limit=50',
 *                                        title: '…', columns: [...],
 *                                        emptyText: '…', unreadableText: '…' }]
 * <MonitorTabBody extraPages={extraPages} />
 * ```
 *
 * ## react 从哪来
 *
 * **宿主提供**：dsh 的模块表（PLATFORM_MODULES）注入 `react` / `react/jsx-runtime`，
 * 项目的打包配置把它**外置**（避免双 React）。本目录**不声明任何 npm 依赖**。
 */
import { useEffect, useRef, useState, type MouseEvent } from 'react'
import type { FetchResult, PanelState } from './monitor-client.ts'
import {
  configOf,
  eventsOf,
  loadPanel,
  selfCheckLine,
  stateBadge,
  stateText,
  tabLabel,
  type PanelConfigLike,
  type StandardPage,
} from './panel-data.ts'
import {
  badgeHtml,
  cfgPageHtml,
  esc,
  extraPageHtml,
  recPageHtml,
  tabsHtml,
  TAB_CSS,
  type ExtraPageSpec,
} from './panel-view.ts'
import {
  tokenPageHtml,
  type ContextBreakdownWire,
  type ContextPressureWire,
  type SessionStatsWire,
  type TokenUsageWire,
} from './panel-token.ts'

/** 轮询间隔（展示刷新，非正确性依赖；**共享常量、不开旋钮**——三家实测都是这个值）。 */
const POLL_MS = 3000

/** 展开态与页签跨挂载保留（模块级）。**渲染只问谓词**，不直接读这个集合。 */
const openSet = new Set<string>()
let savedPage = 'rec'
const isOpen = (k: string) => openSet.has(k)

/** 标准三页（页签文案可在参数块 `TABS` 里覆盖）。 */
function standardPages(config?: PanelConfigLike): { id: string; label: string }[] {
  const ids: StandardPage[] = ['rec', 'cfg', 'dash']
  return ids.map((id) => ({ id, label: tabLabel(id, config) }))
}

/**
 * **仪表盘的取数面**：dsh 的 `useProjection`（会话槽位的标准 hook 座位之一）。
 *
 * ⚠ 它是**注入进来的**（不 import dsh 包）：本资产对 dsh **零依赖**，
 * 连类型都不 import ⇒ 这里用**结构化签名**描述"我们会怎么用它"。
 * 缺它（未在会话槽位挂载 / 宿主没提供）时整页显示"未接入"，**不假装是 0**。
 */
type UseProjectionLike = <K extends string>(key: K) => unknown

export function MonitorTabBody({ extraPages = [], useProjection, config }: {
  extraPages?: ExtraPageSpec[]
  useProjection?: UseProjectionLike
  /** ⭐ **这一次注册自己那份**面板参数（由 mountClientPanel 显式传入）。
   *  ⚠ 不给则回落到既有的作用域/单槽语义（老调用方仍能跑）。 */
  config?: PanelConfigLike
} = {}) {
  const ref = useRef<HTMLDivElement>(null)
  const [page, setPage] = useState<string>(savedPage)
  // ⭐ **唯一状态**（`PanelState` 判别联合）：没有"取数失败就什么都不画"的分支——
  // 每一档都带 `detail` 或数据，空白在类型上不可表达。`null` = 首拍还没回来。
  const [state, setState] = useState<PanelState | null>(null)
  /** 附加页各自的取数结果（按 id）。**它们的失败只影响各自那一页。** */
  const [extras, setExtras] = useState<Record<string, FetchResult<unknown>>>({})
  // 附加路由变化（项目换了声明）时重新起轮询；用字符串 key 避免每次渲染都重订阅。
  const extraKey = extraPages.map((p) => p.id + ':' + p.path).join('|')

  useEffect(() => {
    let dead = false
    const routes = extraPages.map((p) => ({ id: p.id, path: p.path }))
    const poll = async () => {
      const st: { state: PanelState; extras: Record<string, FetchResult<unknown>> } =
        await loadPanel(fetch, routes, config)
      if (dead) return
      setState(st.state)
      setExtras(st.extras)
      // R8 非退化自证：判"面板正常"必须同时证明"数据真的到了"（否则"空 vs 空"同样通过）。
      if (st.state.kind === 'ok') console.info('[mecha] 监控面板：' + selfCheckLine(st.state))
      else console.warn('[mecha] 监控面板非 ok：' + selfCheckLine(st.state))
    }
    poll()
    const t = setInterval(poll, POLL_MS)
    return () => { dead = true; clearInterval(t) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [extraKey])

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
      const p = pg.getAttribute('data-page') || 'rec'
      savedPage = p
      setPage(p)
      return
    }
    const sm = tgt.closest?.('summary')
    const key = sm?.parentElement?.getAttribute?.('data-key')
    if (key) { if (openSet.has(key)) openSet.delete(key); else openSet.add(key) }
  }

  // ⚠ **四个 hook 恒定按序调用**：`useProjection` 在组件生命周期里是恒定的
  //   （由槽位 kit 注入）⇒ `?.` 只影响"取不取得到值"，**不改变 hook 数量**。
  //   写成条件调用会破坏 hooks 规则（渲染期报错），所以这里刻意不分支。
  const tokenViews = {
    usage: useProjection?.('tokenUsage') as TokenUsageWire | undefined,
    breakdown: useProjection?.('contextBreakdown') as ContextBreakdownWire | undefined,
    pressure: useProjection?.('contextPressure') as ContextPressureWire | undefined,
    stats: useProjection?.('sessionStats') as SessionStatsWire | undefined,
  }

  const b = stateBadge(state)
  const pages = [...standardPages(config), ...extraPages.map((p) => ({ id: p.id, label: p.label }))]
  const head = tabsHtml(pages, page, badgeHtml(b.cls, b.text))

  // ⚠ 取事件与配置**走 panel-data 里那两个被单测覆盖的纯函数**（键只有一处）：
  // 早先的写法直接拿请求路径 `HISTORY_PATH`（带 `?since_seq=0`）去索引 payloads，
  // 而 payloads 是按**基础路由键**存的 ⇒ 页面永远"暂无写事件"，自证行却报"事件行=N"。
  const spec = extraPages.find((p) => p.id === page)
  let body: string
  if (spec) {
    const got = extras[spec.id]
    body = got
      ? extraPageHtml(spec, got)
      : `<div class="mecha-page"><div class="mecha-msg muted">${
          esc(state ? '该附加页还没取到数据' : '连接中…')}</div></div>`
  } else if (page === 'dash') {
    // ⭐ 仪表盘**不依赖那条只读端点**（它的数据来自 dsh 投影）⇒ 权威离线时它照样能显示。
    body = useProjection
      ? tokenPageHtml(tokenViews)
      : `<div class="mecha-page"><div class="mecha-msg muted">${
          esc('未接入会话投影（本页需要 dsh 的 useProjection；挂在会话槽位时由宿主注入）')}</div></div>`
  } else if (state?.kind === 'ok') {
    body = page === 'rec'
      ? recPageHtml(eventsOf(state), isOpen)
      : cfgPageHtml(configOf(state), isOpen)
  } else {
    body = `<div class="mecha-page"><div class="mecha-msg muted">${
      esc(state ? stateText(state) : '连接中…')}</div></div>`
  }

  return (
    //: ⚠⭐ **这个 div 必须带上 `mecha-tab-root`**（2026-10-10 实测：侧边栏**没有滚动条**）：
    //: dsh 的右栏正文容器（`.tabBody`）是 `display:flex;flex-direction:column;overflow:hidden`
    //: ⇒ 若本层不参与 flex（无类名 = `height:auto`），里面的
    //: `.mecha-tab-native{height:100%}` 就**没有可依据的高度**，内容一路长出去被
    //: `.tabBody` 的 `overflow:hidden` **裁掉** ⇒ 既滚不动、也看不见下半截。
    //: 类名由 `panel-view.ts` 给出（`flex:1 1 auto;min-height:0;display:flex`）。
    <div ref={ref} className="mecha-tab-root" onClick={onClick}
      dangerouslySetInnerHTML={{ __html: `<div class="mecha-tab-native">${head}${body}</div>` }} />
  )
}
