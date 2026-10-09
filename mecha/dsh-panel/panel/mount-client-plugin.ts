/**
 * **client 半**的挂载助手：把"面板参数"与"右栏 tab"**绑在一次注册上**。
 *
 * ## 为什么需要它（与 host 半 `mountHostPlugin` 对称）
 *
 * 三家项目原先是**各自手写** client 半：注册 `sidebarRightTabs`、注册正文/标题槽位、
 * 再自己注册一个会话头部按钮。三份手写代码意味着三份**各自可能走歪**的地方，
 * 而其中一条在 2026-10 真的走歪了：
 *
 * ```ts
 * title: () => panelConfig().title        // ⚠ thunk —— **每次使用都重读**
 * ```
 *
 * `title` 是**渲染时**才求值的 thunk（dsh 契约如此），而 `panelConfig()` 读的是
 * **当时的作用域/单槽** ⇒ 三个项目装进同一个 dsh 时，**三个 tab 的标题与取数路由
 * 全都落到"最后注册那一份"**（`ca2a05d` 只修了 host 半的 node 侧，浏览器侧是**另一个
 * 模块实例**，没被修到）。
 *
 * ## 本助手的做法：**显式绑定**，不靠"渲染时恰好还在作用域里"
 *
 * ⭐ 注册时 `openPanelConfig()` 拿到**自己那份** `config`，然后：
 * - **标题**：闭包**捕获** `config.title`（不再问任何全局）；
 * - **正文**：把 `config` 作为 **prop** 交给 `MonitorTabBody`（正文取数路由由它用）。
 *
 * ⇒ "各读各的"不再依赖作用域时序，而是**一次显式传参**——这也让它**可判**（R7）。
 *
 * ## ⚠ 按钮：**框架只注册一个**（跨"模块实例"也要成立）
 *
 * 会话头部那个「◈ 监控」按钮原先**每家注册一个** ⇒ 3 个项目 = 3 个按钮。
 * 现在由本助手注册**唯一一个**，语义是 ⭐ **"开右栏"**（`openRightbar`）——
 * **不再 `openTab(某个 id)`**：用户要的是"**唤起所有**监控面板"。
 *
 * ⚠ 去重不能用**模块级**变量：三家各 `file:` 依赖资产 ⇒ **各自的 bundle 里各有一份
 * 本模块**（模块级标志各算各的，按钮照样 3 个）。⇒ 用 **`globalThis` 上的一个键**
 * （浏览器里真正的全局），按要注册的那个 id 记账。
 *
 * ## 边界
 *
 * ⚠ 本模块**浏览器安全**（client 半）：不 import `node:*`，对 dsh **零依赖**
 * （`ctx` 用结构化签名描述"我们会怎么用它"）。
 */
import type { Disposer, PanelOptions, ResolvedPanelConfig } from './panel-config.ts'
import { openPanelConfig } from './panel-config.ts'

/** 会话头部按钮在**全局**登记键（跨"每仓一份 bundle"的唯一真值）。 */
const BUTTON_KEY = '__mechaPanelHeaderButton__'

function globalFlags(): Record<string, boolean> {
  const g = globalThis as unknown as Record<string, unknown>
  const cur = g[BUTTON_KEY]
  if (cur && typeof cur === 'object') return cur as Record<string, boolean>
  const fresh: Record<string, boolean> = {}
  g[BUTTON_KEY] = fresh
  return fresh
}

/** cordis 的 client `ctx`：只声明**本助手真正用到的那几片**。 */
export interface ClientContext {
  sidebarRightTabs?: {
    register(def: {
      id: string
      kind: string
      title: (address: string) => string
      patterns?: readonly string[]
      /** ⭐ **右栏「guide」页的入口**（dsh 的 `SidebarRightGuideEntry`）。
       *  ⚠ 不投条目 ⇒ 这个 tab 类型**不上那一页**；而 dsh 的 `defaultSeed()`
       *  规则是"**guide 条目 0 个或多个 ⇒ 右栏默认页 = guide 页**"
       *  ⇒ 这条正是我们"**一个按钮唤起所有监控面板**"的**唯一原语**
       *  （`layout.openRightbar` 只开栏、**不选 tab** ⇒ 用户看到的是**空栏**，实测踩到）。 */
      guide?: readonly {
        id: string
        order: number
        title: () => string
        description?: () => string
      }[]
    }): Disposer
  }
  slots?: {
    /** 注册一个 slot 座位；返回 disposer。 */
    register(def: Record<string, unknown>, component?: unknown): Disposer
    inject(name: string, fn: () => Disposer): Disposer
  }
  layout?: { openRightbar?: (open: boolean, focus?: boolean) => void }
  sidebarRight?: { openTab?: (kind: string) => void }
}

export interface MountClientPanelOptions {
  /** 面板参数（**必填**；与 host 半同一份形状，字段名只有一处真值）。 */
  panelConfig: PanelOptions
  /** 这一份 tab 的类型判别符（⚠ 逐服务区分：契约拒绝同 tier 同 kind 二次注册）。 */
  kind: string
  /** 这一份 tab 的类型实现 id（⚠ 全注册表唯一）。 */
  id: string
  /** 正文组件（通常是资产自己的 `MonitorTabBody`；项目要自定义就传自己的）。 */
  body: unknown
  /** 透给正文的额外 props（例如项目的 `extraPages`）。 */
  bodyProps?: Record<string, unknown>
  /** 标题槽位组件（可选；不给则用 `title(address)` 的文本）。 */
  titleComponent?: unknown
  /** 标题前缀（如「◈ 监控 · ML」）；缺省只用 `panelConfig.title`。 */
  titlePrefix?: string
  /** ⭐ 这一份在**右栏 guide 页**里的排位（升序；缺省 `0`）。
   *  ⚠ 由调用方按**声明顺序**给（`panels.ts` 把那份参数的 index 传进来）⇒
   *  一览里的顺序与声明顺序一致，而不是"谁先注册谁在前"（后者会随 bundle 加载顺序漂）。 */
  guideOrder?: number
  /** ⭐ 是否注册那个**唯一的**会话头部按钮（缺省 `true`）。 */
  withHeaderButton?: boolean
  /** 跨"模块实例"的按钮登记表（⚠ 判据注入一份干净的；生产用 `globalThis` 上那份）。 */
  buttonFlags?: Record<string, boolean>
}

/** 会话头部按钮的 id（**全生态只有一个**）。 */
export const HEADER_BUTTON_ID = 'mecha-panel'

/** 正文槽位的 key（契约：keyed slot，键是**该类型的 `id`**）。 */
export const PANE_TAB_SLOT = 'sidebar.right.pane.tab'
/** 标题槽位的 key（同上）。 */
export const PANE_TAB_TITLE_SLOT = 'sidebar.right.pane.tab.title'
/** 会话头部动作槽位。 */
export const HEADER_ACTIONS_SLOT = 'conversation.session.header.actions'

/** ⭐ dsh 右栏那个**内置**的「guide」页的 kind（`sidebarRight` 的 `GUIDE_KIND`）。
 *  ⚠ 它**不是**我们能改的东西（宿主内置）⇒ 这里只把那个字面量收成**一处**，
 *  由判据钉住"按钮打开的是它"（而不是"随手开右栏"）。 */
export const GUIDE_KIND = 'guide'

/**
 * 把**监控面**带到眼前（那**唯一**那个按钮的动作）。
 *
 * ## 为什么不是 `layout.openRightbar(true, false)`
 *
 * 实测（2026-10-09，用户报"点了一个面板都没开启"）：`openRightbar` **只把栏打开**、
 * **不选任何 tab** ⇒ 用户看到的是**空栏**；而 dsh 的右栏**默认页是算出来的**
 * （`contract/seed.ts::defaultSeed`：**guide 条目 0 个或多个 ⇒ 默认页 = guide 页**）。
 * ⇒ 正确动作是**打开 guide 页**：它会列出**所有**投过 `guide` 条目的监控 tab
 * （每个项目一个入口，按 `guideOrder` 升序），点哪个开哪个
 * —— 这就是"**一个按钮、N 个面板**"。
 *
 * ⚠ 兜底：宿主版本没有 `sidebarRight.openTab` 时退回 `openRightbar`（不静默什么都不做）。
 */
export function openMonitorGuide(ctx: ClientContext): void {
  const openTab = ctx.sidebarRight?.openTab
  if (typeof openTab === 'function') {
    openTab(GUIDE_KIND)
    return
  }
  ctx.layout?.openRightbar?.(true, false)
}

/**
 * 挂一份面板 tab（**参数自己那份**）+ 那**唯一一个**头部按钮。
 *
 * @returns 收尾器（弹掉本份参数、注销 tab 与（若是它注册的）按钮）。
 */
export function mountClientPanel(
  ctx: ClientContext,
  opts: MountClientPanelOptions,
): Disposer {
  // ⭐ 拿到**自己那份**（字段留空 ⇒ `openPanelConfig` 当场抛，响亮失败）
  const opened = openPanelConfig(opts.panelConfig)
  const config: ResolvedPanelConfig = opened.config
  const disposers: Disposer[] = [opened.dispose]

  // ---- 类型注册：标题**闭包捕获自己那份**（不再问全局） ----
  // ⚠ 这是"渲染时读到栈顶"的正面解法：thunk 是契约要求的形状，
  //   但**它捕获什么**是我们的选择 ⇒ 捕获 `title` 的**值**，不捕获"去哪读"。
  const label = opts.titlePrefix ? `${opts.titlePrefix}${config.title}` : config.title
  const reg = ctx.sidebarRightTabs?.register?.({
    id: opts.id,
    kind: opts.kind,
    title: () => label,
    // ⭐ **投一个 guide 条目**：右栏那一页（「guide」）会把**所有**投过条目的 tab 类型列出来，
    //   dsh 的 `defaultSeed()` 又规定"条目 0 个或多个 ⇒ 默认页 = guide 页"
    //   ⇒ 这才是"**一个按钮唤起所有项目的监控面板**"的实现方式（实测：只 `openRightbar` 得到空栏）。
    guide: [{
      id: opts.id,
      order: opts.guideOrder ?? 0,
      title: () => label,
    }],
  })
  if (reg) disposers.push(reg)

  // ---- 正文：把**这一份** config 作为 prop 交给正文 ----
  // ⭐ 不靠"渲染时还在作用域里"：正文拿到的是**显式传参**（可判、可测）。
  const bodyProps = { ...(opts.bodyProps ?? {}), config }
  const body = ctx.slots?.inject?.(PANE_TAB_SLOT, () =>
    ctx.slots!.register!({ name: PANE_TAB_SLOT, key: opts.id, inject: () => bodyProps },
                         opts.body))
  if (body) disposers.push(body)

  if (opts.titleComponent !== undefined) {
    const tc = ctx.slots?.inject?.(PANE_TAB_TITLE_SLOT, () =>
      ctx.slots!.register!({ name: PANE_TAB_TITLE_SLOT, key: opts.id }, opts.titleComponent))
    if (tc) disposers.push(tc)
  }

  // ---- ⭐ 头部按钮：**全局唯一**（语义 = 把监控面**一整页**带到眼前） ----
  if (opts.withHeaderButton !== false) {
    const flags = opts.buttonFlags ?? globalFlags()
    if (!flags[HEADER_BUTTON_ID]) {
      const btn = ctx.slots?.inject?.(HEADER_ACTIONS_SLOT, () =>
        ctx.slots!.register!({
          name: HEADER_ACTIONS_SLOT,
          id: HEADER_BUTTON_ID,
          order: 200,
          label: '◈ 监控',
          // ⚠ 2026-10-09 改：**打开 guide 页**（= 那一页列出**所有**项目的监控面板），
          //   而不是 `layout.openRightbar(...)`。后者**只开栏、不选 tab** ⇒ 用户看到
          //   **空栏**，正是"点了一个面板都没开启"的现场（实测）。
          inject: () => ({
            openCockpit: () => { openMonitorGuide(ctx) },
          }),
        }))
      if (btn) {
        flags[HEADER_BUTTON_ID] = true
        disposers.push(() => {
          delete flags[HEADER_BUTTON_ID]        // 注销后允许别人再注册
          btn()
        })
      }
    }
  }

  return () => {
    // ⚠ 逆序收尾（后注册的先收）；⚠ **不吞异常**：收尾里抛的要带到调用方
    for (const d of disposers.reverse()) d()
  }
}
