/**
 * 面板**取数骨架**（client 半）：四基础路由的取数 + **三档可区分错误态** +
 * **R8 非退化自证**。**参考实现**，不是框架能力；**只抽骨架**。
 *
 * ## 抽什么、不抽什么
 *
 * * **抽**：`readJson` 统一取数；把**附加路由失败**与"**权威离线**"**分开**；
 *   非退化自证计数（把"离线=false 事件行=N 配置族=M"打出来）。
 * * **不抽**：任何**领域页**（谁在 `/summary` 上画什么、`/runs` 怎么并列存档、
 *   哪些键是孤儿）——那是项目的领域呈现，**留在项目里**。
 *
 * ## 三档错误态（**档位共享，文案归项目**）
 *
 * | 档 | 含义 | 谁产生 |
 * |---|---|---|
 * | `address` | **地址解析失败**：端口文件缺失/非法，或地址路由本身 `503` | 地址路由 |
 * | `offline` | **权威离线**：**基础路由**（`BASIC_ROUTES`）连不上或非 2xx | 基础路由 |
 * | `route` | **附加路由失败**：权威在，但**某条附加路由**失败了 | 项目自己的路由 |
 *
 * ⚠ **为什么要分这三档**：把附加路由的失败混进"权威离线"，会让**整块面板**显示
 * 离线或一张空表——**真因被吃掉**，看起来像"权威没起来"（本工程最贵的一类假象）。
 * 档位分开之后，项目可以**自己决定怎么显示**（见 `displayTier`）。
 */
import { BASIC_ROUTES } from './monitor-url.ts'

export type MonitorFailureTier = 'address' | 'offline' | 'route'

/** 失败事实：**档位是机制、文案是项目**。 */
export interface MonitorFailure {
  tier: MonitorFailureTier
  detail: string
}

/** 取数结果：要么拿到数据，要么拿到一个**带档位**的失败。 */
export type FetchResult<T> = { ok: true; data: T } | { ok: false; failure: MonitorFailure }

export type DoFetch = typeof fetch

/** 该路径属于"基础路由"还是"附加路由"（决定失败落到哪一档）。 */
export function classifyRoute(path: string): 'basic' | 'extra' {
  const clean = path.split('?')[0]
  return (BASIC_ROUTES as readonly string[]).includes(clean) ? 'basic' : 'extra'
}

/**
 * 取监控端点基址：调**注入的** `resolveAddress()`（同源 fetch 地址路由）。
 *
 * 失败一律落 `address` 档。⚠ 调用方**可以**把它显示成"权威离线"（两者都意味着
 * 权威没起来），但**必须先经 `displayTier` 显式塌缩**，不许就地混为一谈。
 */
export async function fetchMonitorBase(
  resolveAddress: () => Promise<string>,
  _doFetch: DoFetch = fetch,
): Promise<FetchResult<string>> {
  try {
    const base = await resolveAddress()
    if (!base) {
      return { ok: false, failure: { tier: 'address', detail: '地址路由返回空地址' } }
    }
    return { ok: true, data: base }
  } catch (err) {
    return { ok: false, failure: { tier: 'address', detail: (err as Error).message } }
  }
}

/**
 * 统一取数：`GET {base}{path}` → JSON。
 *
 * * 非 2xx / 连不上 / 解析失败 ⇒ 失败，档位由 `classifyRoute(path)` 决定
 *   （基础路由 ⇒ `offline`；附加路由 ⇒ `route`）；
 * * **不做任何回落**：失败就是失败，不返回空对象冒充成功。
 */
export async function readJson(
  base: string,
  path: string,
  doFetch: DoFetch = fetch,
): Promise<FetchResult<unknown>> {
  const tier: MonitorFailureTier = classifyRoute(path) === 'basic' ? 'offline' : 'route'
  let res: Response
  try {
    res = await doFetch(`${base}${path}`, { cache: 'no-store' })
  } catch (err) {
    return { ok: false, failure: { tier, detail: `${path} 连不上：${(err as Error).message}` } }
  }
  if (!res.ok) {
    return { ok: false, failure: { tier, detail: `${path} 返回 ${res.status}` } }
  }
  try {
    return { ok: true, data: await res.json() }
  } catch (err) {
    return { ok: false, failure: { tier, detail: `${path} 正文不是 JSON：${(err as Error).message}` } }
  }
}

/** 显示档位：把 `address` **显式**塌缩成 `offline`（"权威没起来"是实情）。 */
export function displayTier(tier: MonitorFailureTier): MonitorFailureTier {
  return tier === 'address' ? 'offline' : tier
}

// ---------------------------------------------------------------- R8 自证

export interface HistoryPayload { events?: unknown[] }
export interface ConfigPayload { groups?: Record<string, unknown> }

export interface MonitorStats {
  offline: boolean
  events: number
  configFamilies: number
}

/**
 * 非退化自证计数（**R8**）：判"面板正常"的守卫必须**同时**证明"数据真的到了"——
 * 否则"空表 vs 空表""离线 vs 离线"同样会通过，绿得毫无信息。
 */
export function monitorStats(input: {
  offline?: boolean
  history?: HistoryPayload
  config?: ConfigPayload
}): MonitorStats {
  const events = Array.isArray(input.history?.events) ? input.history!.events!.length : 0
  const groups = input.config?.groups
  const configFamilies = groups && typeof groups === 'object' ? Object.keys(groups).length : 0
  return { offline: Boolean(input.offline), events, configFamilies }
}

/** 自证行（判据**必须把它打出来**，否则"非退化"只是声称）。 */
export function statsLine(stats: MonitorStats): string {
  return `离线=${stats.offline} 事件行=${stats.events} 配置族=${stats.configFamilies}`
}

/**
 * 非退化判定：**不在离线态**，且**至少拿到了一样东西**。
 *
 * ⚠ 这是给判据用的：`assert(isNonDegenerate(stats))` 挡的是"两边都空所以相同"。
 */
export function isNonDegenerate(stats: MonitorStats): boolean {
  return !stats.offline && (stats.events > 0 || stats.configFamilies > 0)
}
