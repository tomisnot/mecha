/**
 * **监控面参数**的 wire 形状 + 「声明 → 面板页」的**纯转换**（浏览器安全，可 `node:test` 直接测）。
 *
 * ## 这份参数从哪来（三层架构 §7.3.6 + §7.3.8 第 1 条）
 *
 * ```
 * mecha_site.py 的 Service.monitor（声明 = 唯一那份真值）
 *   ⇒ mecha/dsh_panel.py 生成参数（⚠ 不含端口号）
 *   ⇒ env: MECHA_PANEL_ROUTE / MECHA_PANEL_SERVICES
 *   ⇒ 框架的 host 插件（host-plugin/mecha-panel-host.mjs，node 侧能读 env）
 *   ⇒ 同源只读路由（浏览器 fetch）
 *   ⇒ 本模块把它变成「每个服务一份 PanelOptions + 它自己的附加页」
 * ```
 *
 * ⚠ **为什么不让 client 半读 env**：它跑在**浏览器**里（§7.3.2 坑 2：client 半是另一个
 * 模块实例），那里**没有** `process.env`。§7.3.6 原话"经 env 递给自己那份 client 插件"
 * 字面做不到 —— §7.3.8 已据实更正为上面这条路。
 *
 * ## 命名（§7.3.3 定案，**由框架生成**，资产这边只消费）
 *
 * | 项 | 值 | 为什么 |
 * |---|---|---|
 * | `kind` | `mecha:<svc_id>` | dsh 契约拒绝**同 tier 同 kind** 二次注册 ⇒ kind 必须逐服务区分 |
 * | `tabId` | `mecha-panel:<svc_id>` | 同上，契约拒绝**重复 id**（也是正文/标题槽位的 key） |
 *
 * ⚠ 这两个**在这里不是拼出来的**，而是**参数给的** —— 拼法只有一处家（`mecha/dsh_panel.py`）。
 */
import type { ExtraPageSpec } from './panel-view.ts'
import type { Disposer } from './panel-config.ts'

/** 参数所在的那个**同源只读路由**。
 *
 * ⚠ **这是镜像**：权威在 `mecha/dsh_panel.py` 的 `PANEL_SERVICES_ROUTE`（浏览器读不到
 * Python，只能从自己那份 bundle 拿字面量）⇒ 由判据钉住两者相等。
 * 写歪的症状是"fetch 404 ⇒ 一个 tab 都没有"（最该被钉住的那类假绿）。 */
export const PANEL_SERVICES_ROUTE = '/mecha/panels'

/** 附加页表格的一列（**纯数据**：取哪个字段，不含"怎么格式化"）。 */
export interface DeclaredExtraColumn {
  label: string
  field: string
}

/** 声明的附加页（项目词；**只有数据** —— §7.3.8 第 4 条）。 */
export interface DeclaredExtraPage {
  id: string
  label: string
  path: string
  title: string
  emptyText: string
  unreadableText: string
  note: string
  columns: DeclaredExtraColumn[]
}

/** 一个声明了监控面的服务（= 右栏里的一份 tab）。 */
export interface DeclaredPanelService {
  id: string
  kind: string
  tabId: string
  routePath: string
  portFile: string
  title: string
  extraPages: DeclaredExtraPage[]
}

/** 框架生成、经同源只读路由交过来的**整份**参数。 */
export interface PanelServicesPayload {
  routePath: string
  services: DeclaredPanelService[]
}

/** 空表时的**中性**文案（框架级；项目可覆盖）。
 *  ⚠ 与"读不到"**必须分开**（铁律 8）——见 :data:`NEUTRAL_UNREADABLE_TEXT`。 */
export const NEUTRAL_EMPTY_TEXT = '（这一页没有数据）'

/** 读不到时的**中性**文案：⚠ 明说"这不是没有数据"（铁律 8：读不到 ≠ 确实没有）。 */
export const NEUTRAL_UNREADABLE_TEXT =
  '这一页读不到（⚠ 不是「没有数据」，是**没读到**——看上面的原因）'

/** 收尾器（本仓到处在用的可逆副作用形状；⚠ 类型**只有一处家**：`panel-config.ts`）。 */
export type { Disposer }

/**
 * ⭐ **项目可选的呈现细化**（"自定义 UI"那条路；§7.3.3 已定"保留为可选"）。
 *
 * ⚠ 为什么需要它：声明是**数据**，装不下"怎么取值/怎么格式化"那种**代码**
 * （ML 的 `metricCell` 要按来源各取一次 `score` / `metrics[primary_metric]`）。
 * ⇒ 分工：**声明管页集与路由**（一处真值），项目**只在需要时**细化呈现。
 * ⚠ 不给 ⇒ 资产用**中性**取值（`rows[i][field]`）——那对"只放数据"的新项目就够用。
 */
export interface ExtraPageRender {
  /** 覆盖声明里的列（项目自己的 `cell` 函数）。 */
  columns?: { label: string; cell: (row: unknown) => string }[]
  /** 顶部概括一行（项目自己的词与拼法）。 */
  summaryLine?: (payload: unknown) => string
}

/** 项目注册的呈现细化（键 = ``<svcId>:<pageId>``；⚠ 页 id 只在本服务内唯一 ⇒ 必须带服务）。 */
const renders = new Map<string, ExtraPageRender>()

/** 呈现细化的键（**只有这一处拼**）。 */
export function extraPageRenderKey(svcId: string, pageId: string): string {
  return `${svcId}:${pageId}`
}

/** ⭐ 注册一份呈现细化（自定义 UI）。返回收尾器。 */
export function contributeExtraPageRender(key: string, render: ExtraPageRender): Disposer {
  renders.set(key, render)
  return () => {
    // ⚠ 按身份删（不 `delete` 无条件）：后注册的那份若已覆盖，别把**它**删掉。
    if (renders.get(key) === render) renders.delete(key)
  }
}

/** 取某一份呈现细化（没有 ⇒ `undefined`，调用方回落到中性取值）。 */
export function extraPageRenderFor(key: string): ExtraPageRender | undefined {
  return renders.get(key)
}

/** 中性取值：`rows[i][field]` → 文本（缺字段给空串，**不渲染 `undefined`**）。 */
export function fieldText(row: unknown, field: string): string {
  const v = (row ?? {}) as Record<string, unknown>
  const got = v[field]
  return got === null || got === undefined ? '' : String(got)
}

/** 严格判形状：形状不对 ⇒ 响亮失败（不静默挂一个空面板）。 */
export function isPanelServicesPayload(v: unknown): v is PanelServicesPayload {
  if (v === null || typeof v !== 'object') return false
  const o = v as Record<string, unknown>
  if (typeof o['routePath'] !== 'string' || !Array.isArray(o['services'])) return false
  return o['services'].every((s) => {
    if (s === null || typeof s !== 'object') return false
    const x = s as Record<string, unknown>
    return ['id', 'kind', 'tabId', 'routePath', 'portFile', 'title']
      .every((k) => typeof x[k] === 'string')
      && Array.isArray(x['extraPages'])
  })
}

/**
 * 一个服务的声明 → 正文组件要的那份 `ExtraPageSpec[]`
 * （声明给数据 + 项目注册的呈现细化给函数；两处**不重叠**）。
 */
export function extraPageSpecsFrom(svcId: string, declared: DeclaredExtraPage[]): ExtraPageSpec[] {
  return declared.map((page) => {
    const render = extraPageRenderFor(extraPageRenderKey(svcId, page.id))
    return {
      id: page.id,
      label: page.label,
      path: page.path,
      title: page.title || page.label,
      // ⚠ 先声明里的数据列，再让**这一份**的呈现细化覆盖（覆盖是按身份，不污染别人）。
      columns: render?.columns
        ?? page.columns.map((c) => ({ label: c.label, cell: (row: unknown) => fieldText(row, c.field) })),
      emptyText: page.emptyText || NEUTRAL_EMPTY_TEXT,
      unreadableText: page.unreadableText || NEUTRAL_UNREADABLE_TEXT,
      note: page.note,
      ...(render?.summaryLine !== undefined ? { summaryLine: render.summaryLine } : {}),
    }
  })
}
