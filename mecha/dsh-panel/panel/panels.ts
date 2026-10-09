/**
 * ⭐ **框架自己的 client 插件逻辑**（按声明挂 N 个 tab）—— **不含 `.tsx`** ⇒ `node --test` 能真跑它。
 *
 * ## 为什么要与 `auto.ts` 分开（一条**资产既有**的边界，不是新发明）
 *
 * `MonitorTabBody.tsx` 是 react 壳，**`node --test` 加载不了**（`.tsx` 不在 node 的类型剥离
 * 范围内）——资产的既有口径就是："壳**不进**门禁的执行面，它只过 typecheck"
 * （见 `MonitorTabBody.tsx` 的 docstring）。⇒ 本模块把**行为**（取参数、按服务挂 N 份、
 * 幂等、按钮）与**壳的绑定**分开：
 *
 * | 模块 | 含 `.tsx`？ | 谁测 |
 * |---|---|---|
 * | `panels.ts`（本模块） | ✗ | ⭐ 资产单测**真跑**（三条硬判据：按钮 1 个 / 各读各的 / 零 dsh 代码出新 tab） |
 * | `auto.ts`（入口） | ✓（静态 import 壳） | 消费者各自的 `npm run typecheck` + 三家在浏览器里真跑 |
 *
 * ## 项目侧那一行（§7.3.7 裁决 A + §7.3.8 第 3 条：**原文，照抄即可**）
 *
 * ```ts
 * export { apply, name, inject } from '@mecha/dsh-panel/panel/auto.ts'
 * ```
 *
 * ⚠ **不能**写成裸 `import '@mecha/dsh-panel/panel/auto.ts'`：cordis 只认**项目模块自己的**
 * `name`/`inject`/`apply` —— 裸 import 只执行模块体、`apply` **不会被调**；
 * 而资产 `package.json` 是 `sideEffects:false`，整行还可能被**摇掉**。
 *
 * ## 它做什么（三件事，一件不多）
 *
 * 1. 从**同源只读路由**取那份参数（`services.ts` 的 `PANEL_SERVICES_ROUTE`）；
 * 2. 按服务**各挂一份** tab：每份的标题/取数路由/附加页都是**它自己那一份**
 *    （经 `mountClientPanel` 的 `panelConfig` 显式传参，⚠ 不读任何全局）；
 * 3. 头部按钮**恒为 1 个** —— 由 `mountClientPanel` 的**跨 bundle 全局登记**保证。
 *
 * ## ⚠ 幂等必须**跨 bundle**（不是"同一模块 import 两次"那么简单）
 *
 * 三家项目各自 `file:` 依赖同一份资产 ⇒ 浏览器里**每个项目的 bundle 各有一份本模块**
 * （三个模块实例）。若各自都挂一遍，dsh 契约会当场拒绝（**重复 id / 同 tier 同 kind
 * 二次注册**）。⇒ 与头部按钮同一条办法：**登记在 `globalThis` 上**，按 `tabId` 记账。
 *
 * ## ⚠ 取不到参数时：**出声 + 不挂**（不是抛）
 *
 * 参数取不到（路由 404 / 网络错 / 形状不对）时**记一条 error 日志、一个 tab 都不挂**，
 * 且**不**把异常抛给 cordis。理由：`apply` 是同**一个**项目插件的入口，抛出去会连带
 * 把该项目自己的**自定义 UI**（如 GP 的「☀ 评审今日」按钮）一起打掉 ——
 * 那是把"面板没接上"升级成"项目在 dsh 里没了"。⚠ 这是**权宜的爆炸半径取舍**，
 * 所以它**必须出声**（`logger.error`，日志里能查到"为什么没有 tab"），
 * **不是**静默降级（本工程最忌那种）。
 */

import { mountClientPanel } from './mount-client-plugin.ts'
import {
  PANEL_SERVICES_ROUTE,
  extraPageSpecsFrom,
  isPanelServicesPayload,
  type DeclaredPanelService,
  type PanelServicesPayload,
} from './services.ts'

/** cordis 插件名（诊断用）。 */
export const PLUGIN_NAME = 'mecha-panel'

/** 用到的那几片 dsh client 服务（slots = tab 正文/按钮、layout = 开右栏、两个 sidebarRight*）。 */
export const PLUGIN_INJECT = ['slots', 'layout', 'sidebarRight', 'sidebarRightTabs']

/** 头部按钮的标题前缀（与阶段① 三家自己写的那份**逐字一致** ⇒ 观感不变）。 */
export const TITLE_PREFIX = '◈ 监控 · '

/** 跨 bundle 的登记键（浏览器里真正的全局；⚠ 与 `mount-client-plugin.ts` 的按钮登记同族）。 */
const AUTO_KEY = '__mechaPanelAuto__'

interface AutoState {
  /** 已挂上的服务（`tabId` → 收尾器）。**跨 bundle 共享** ⇒ 谁先跑谁挂，其余跳过。 */
  mounted: Record<string, () => void>
  /** 那份参数（取一次，三个 bundle 复用；⚠ 只在成功时缓存）。 */
  payload?: Promise<PanelServicesPayload>
}

function autoState(): AutoState {
  const g = globalThis as unknown as Record<string, unknown>
  const cur = g[AUTO_KEY]
  if (cur && typeof cur === 'object' && 'mounted' in (cur as object)) return cur as AutoState
  const fresh: AutoState = { mounted: {} }
  g[AUTO_KEY] = fresh
  return fresh
}

/** 客户端 `ctx`：只声明**本模块真正用到的那一片**（结构化类型，接受真 `Context`）。 */
export interface AutoContext {
  slots?: { inject(name: string, fn: () => unknown): unknown }
  layout?: unknown
  sidebarRight?: unknown
  sidebarRightTabs?: unknown
  logger?: { error?: (m: string) => void; warn?: (m: string) => void }
  effect?: (fn: () => () => void, label: string) => unknown
}

/** `fetch` / 正文组件的注入点（判据喂替身；生产走同源 `fetch` + 资产自己的壳）。 */
export interface ApplyOptions {
  /** ⭐ 正文组件（**必填**：`panels.ts` 不认识 react 壳 —— 那条绑定在 `auto.ts`）。 */
  body: unknown
  fetchImpl?: typeof fetch
  /** 取参数的路由（缺省 = :data:`PANEL_SERVICES_ROUTE`）。 */
  route?: string
}

/**
 * 取那份参数（**同源**；⚠ 结果按 `globalThis` 缓存 ⇒ 三个 bundle 只取一次）。
 *
 * ⚠ `cache: 'no-store'`：改了声明重启 dsh 之后，别让浏览器拿旧的那份。
 */
export function loadPanelServices(opts: { fetchImpl?: typeof fetch; route?: string } = {}):
Promise<PanelServicesPayload> {
  const st = autoState()
  if (st.payload !== undefined) return st.payload
  const doFetch = opts.fetchImpl ?? fetch
  const route = opts.route ?? PANEL_SERVICES_ROUTE
  const p = (async () => {
    const res = await doFetch(route, {
      headers: { accept: 'application/json' },
      cache: 'no-store',
    } as RequestInit)
    if (!res.ok) {
      throw new Error(`${route} 回了 HTTP ${res.status}（框架的 host 插件应当注册了这个路由）`)
    }
    const body: unknown = await res.json()
    if (!isPanelServicesPayload(body)) {
      throw new Error(`${route} 的回执不是那份参数形状（{routePath, services:[…]}）`)
    }
    return body
  })()
  st.payload = p
  // ⚠ 失败**不缓存**：否则一次瞬时网络错会让这个页面**永远**没有面板。
  p.catch(() => { if (st.payload === p) delete st.payload })
  return p
}

/**
 * 按参数挂 N 个 tab。返回收尾器（把**本次**挂上的都收掉）。
 *
 * ⚠ 已挂过的 `tabId` **跳过**（跨 bundle 幂等）；收尾时把登记删掉，
 * 于是"重新 apply"（HMR 重载那个项目插件）能自愈地把它挂回来。
 */
export async function applyMechaPanels(ctx: AutoContext, opts: ApplyOptions): Promise<() => void> {
  let payload: PanelServicesPayload
  try {
    payload = await loadPanelServices(opts)
  } catch (error) {
    // ⚠ 出声：没有 tab 的**原因**必须在日志里查得到（不是静默降级，见模块 docstring）。
    ctx.logger?.error?.(
      `[mecha] 监控面板参数取不到 ⇒ 一个 tab 都没挂：${String(error)}`
      + `（⚠ 这不是「没有监控面」：那份参数由框架的 host 插件经 ${opts.route ?? PANEL_SERVICES_ROUTE} 交过来）`,
    )
    return () => { /* 什么都没挂 ⇒ 没有要收的 */ }
  }

  const st = autoState()
  const mine: string[] = []
  for (const svc of payload.services as DeclaredPanelService[]) {
    if (st.mounted[svc.tabId] !== undefined) continue      // ⭐ 别的 bundle 已经挂过这一份
    const dispose = mountClientPanel(ctx as never, {
      // ⭐ **这次注册自己那一份**参数（标题/取数路由都从这里进去，不读全局）
      panelConfig: { routePath: svc.routePath, portFile: svc.portFile, title: svc.title },
      kind: svc.kind,
      id: svc.tabId,
      body: opts.body,
      bodyProps: { extraPages: extraPageSpecsFrom(svc.id, svc.extraPages ?? []) },
      titlePrefix: TITLE_PREFIX,
    })
    st.mounted[svc.tabId] = dispose
    mine.push(svc.tabId)
  }
  return () => {
    for (const tabId of mine) {
      const dispose = st.mounted[tabId]
      delete st.mounted[tabId]       // 先删再收：收尾里抛了也不会留下"永远挂着"的假账
      dispose?.()
    }
  }
}
