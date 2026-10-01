/**
 * 面板参数**注入点**（取代原来的"参数块"文件）。
 *
 * ## 为什么从"参数块"改成"注入点"（2026-10-01 结构性改造）
 *
 * 原设计把项目专有的值（路由 / 端口文件名 / 标题）放在**资产目录里**的一个
 * `panel-config.ts`，靠"复制一份到项目、只改那个文件"来用。**依赖化之后这条不成立**：
 * 项目经 junction 指向**同一份**资产 ⇒ 三个项目会**互相覆盖**同一个文件。
 * ⇒ 参数必须**住进项目自己**，由项目在启动时**注入一次**。
 *
 * ## 形状：`configurePanel(...)` 注入，读取侧经 `panelConfig()` 取
 *
 * - **不设默认值**：三个字段全必填。资产**不替项目猜**路由名、端口文件名、标题
 *   （同 `mecha/data_port` 那条纪律：*库拥有规则，宿主拥有名字*）。
 * - ⚠ **未注入即响亮失败**：`panelConfig()` 在没人注入时**抛**，不做"中性默认关卡"——
 *   默认关卡会把"新项目忘了注入"显示成"面板连上了但是空的"，正是本工程最贵的那类假绿。
 * - ⚠ **可选值（`tabs`）与必填值分开**：`panelConfig().tabs` 的类型就是可选的，
 *   于是 `tabLabel()` **不需要任何守卫**（守卫本身也是脚手架，能省则省）。
 * - ⚠ **资产不反向 import 项目**：注入是**推**进来的（项目调资产），资产只持有值。
 *
 * ## 谁来调
 *
 * **消费方半**（host 半与 client 半**各一次**，两个进程/两份 bundle）：
 * 在**用面板之前**调一次 `configurePanel({ routePath, portFile, title })`。
 * 本目录的单测也是一个消费者 ⇒ 它们各自注入自己的中性值。
 *
 * ## 边界
 *
 * ⚠ **本模块必须浏览器安全**（client 半会 import 它）⇒ 只许 import `./routes.ts`
 * 这类零 `node:*` 的模块。
 */
import { DEFAULT_ROUTE_PATH } from './routes.ts'

/** 可逆副作用的收尾函数（本仓通用形状：注册/订阅都返回它）。 */
export type Disposer = () => void

/** 标准页的**页签文案覆盖**（可选；不填则用共享默认「飞行记录仪」/「配置态」）。 */
export interface PanelTabOverrides {
  rec?: string
  cfg?: string
}

/** 注入用的完整参数（**三个必填**，没有默认值可回落）。 */
export interface PanelOptions {
  /** host 半注册的只读路由路径；client 半 fetch **同一个字符串**。 */
  routePath: string
  /** 权威写的**端口文件名**（相对项目根）或绝对路径。**必填**。 */
  portFile: string
  /** 面板标题（纯展示；模型不读它）。 */
  title: string
  /** 标准两页的页签文案覆盖（可选）。 */
  tabs?: PanelTabOverrides
}

/** 注入后对外暴露的读数形状（`tabs` 仍是可选的 ⇒ 读取侧不需要守卫）。 */
export interface ResolvedPanelConfig {
  readonly routePath: string
  readonly portFile: string
  readonly title: string
  readonly tabs?: PanelTabOverrides
}

let current: ResolvedPanelConfig | undefined

/** 未注入时抛的错误（**响亮失败**，不是"用一个中性默认"). */
const NOT_CONFIGURED =
  '面板参数未注入：请在使用面板前调用一次 configurePanel({ routePath, portFile, title })。'
  + '⚠ 资产不提供默认值——路由名 / 端口文件名 / 标题都是**项目专有**的值，'
  + '猜一个会把"忘了注入"伪装成"连上了但面板是空的"。'

function required(name: string, value: string | undefined): string {
  const v = value?.trim()
  if (!v) {
    throw new Error(
      `configurePanel 的 ${name} 不能为空。`
      + '⚠ 留空等于"没有这个名字"，而资产不许替项目猜（同"库拥有规则、宿主拥有名字"）。',
    )
  }
  return v
}

/**
 * 注入面板参数。返回 **disposer**（把注入态清回"未注入"）。
 *
 * ⚠ 为什么要 disposer（而不是"测试专用清除函数"）：这是本仓**到处都在用**的可逆副作用形状
 * （`register(...) → disposer`、`subscribe(...) → disposer`），所以它**不是测试脚手架**——
 * 任何"临时注入、用完还原"的真实消费者（含本目录的单测）都用同一个口子。
 * ⚠ 重复调用会覆盖（最后一次为准）。
 */
export function configurePanel(options: PanelOptions): Disposer {
  current = {
    routePath: required('routePath', options.routePath),
    portFile: required('portFile', options.portFile),
    title: required('title', options.title),
    ...(options.tabs !== undefined ? { tabs: options.tabs } : {}),
  }
  return () => {
    current = undefined
  }
}

/**
 * 读注入后的参数。**未注入即抛**（响亮失败）——这是本模块存在的理由那一半：
 * 宁可当场炸，也不许静默用一个中性默认把"忘了注入"演成"面板空的"。
 */
export function panelConfig(): ResolvedPanelConfig {
  if (current === undefined) throw new Error(NOT_CONFIGURED)
  return current
}

/**
 * 该项目的**中性默认路由**（`routes.ts` 的唯一来源）。项目通常直接传它，
 * 也可以传自己的路由——但**必须显式传**（不许由资产悄悄回落）。
 */
export { DEFAULT_ROUTE_PATH }
