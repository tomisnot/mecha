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

/** 标准页的**页签文案覆盖**（可选；不填则用共享默认「飞行记录仪」/「配置态」/「仪表盘」）。 */
export interface PanelTabOverrides {
  rec?: string
  cfg?: string
  /** ⭐ 仪表盘页（token 计量）。第三个标准页。 */
  dash?: string
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

/**
 * ⚠ **多项目同时挂**：注入态是**一个栈**，`panelConfig()` 读**栈顶**。
 *
 * 为什么必须是栈（2026-10 实测抓到的真缺陷）：原先这里是**模块级单例**
 * `let current: ResolvedPanelConfig | undefined` ⇒ ⭐ **三个项目插件装进同一个 dsh 时，
 * 后加载的会把前一个覆盖掉**：N 个 tab 都在，但它们**全都读同一个项目的**
 * `routePath`/`portFile`/`title` ⇒ 现象是"三个面板都列同一个项目的账"，**且不报任何错**
 * （最坏的一类）。
 *
 * ⇒ 改法：**参数跟着"那一次注册"走**。每个调用方（曾经的 host 半骨架 `mountHostPlugin`、
 * 现在**只有**框架自己的 client 助手 `mountClientPanel`）每次注册都
 * :func:`openPanelConfig` 推一份、并在自己的收尾里弹掉；注册期间发生的所有读取
 * （路由注册、页面组件取 `routePath`/`tabs`）都在**它自己的那一层**上。
 * ⚠ 项目侧**零改动**——它们本来就把参数交进来了。
 * ⚠ **2026-10-10**：那段里提到的 `mountHostPlugin`（资产 host 半骨架）**已删**（方案 A）。
 */
const stack: ResolvedPanelConfig[] = []

/**
 * ⚠ **单槽**（"最后注入的那一份"），给**非注册场景**用（单测、独立页面路由等）。
 *
 * 为什么栈之外还要有它：`configurePanel` 的既有契约是"**注入 → 收尾清回未注入**"
 * （本目录单测就是靠"`dispose()` 之后 `panelConfig()` 必须抛"来守**响亮失败**那条纪律的）。
 * 若让 `configurePanel` 也往栈上推，那个"清回未注入"就永远不成立（模块加载时的
 * 中性注入还留在栈里）⇒ ⭐ 会**把那条纪律的判据弄坏**。
 * ⇒ 分工：`stack` 管"**跟着一次注册走**"的多份并存；`fallback` 管"单份可清回"。
 */
let fallback: ResolvedPanelConfig | undefined

/** 未注入时抛的错误（**响亮失败**，不是"用一个中性默认"）。 */
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

function resolve(options: PanelOptions): ResolvedPanelConfig {
  return {
    routePath: required('routePath', options.routePath),
    portFile: required('portFile', options.portFile),
    title: required('title', options.title),
    ...(options.tabs !== undefined ? { tabs: options.tabs } : {}),
  }
}

/**
 * ⭐ **把这一份参数推上栈**，返回它自己 + 收尾器。**没有**"全局只许一份"的语义。
 *
 * 与 :func:`configurePanel` 的区别：它**把解析后的那份交回给调用方**（调用方拿它当
 * "**我这次注册**的参数"用），而不是让调用方事后再去问"现在栈顶是谁"——
 * 后者在多项目下会读到**别人**那份。⚠ 字段留空同样当场抛（响亮失败）。
 */
export function openPanelConfig(options: PanelOptions): {
  config: ResolvedPanelConfig
  dispose: Disposer
} {
  const config = resolve(options)
  stack.push(config)
  return {
    config,
    dispose: () => {
      const i = stack.lastIndexOf(config)
      if (i >= 0) stack.splice(i, 1)     // ⚠ 按身份弹，不 pop：防"弹掉别人的"
    },
  }
}

/**
 * 注入面板参数。返回 **disposer**（把这一份从栈上弹掉）。
 *
 * ⚠ 为什么要 disposer（而不是"测试专用清除函数"）：这是本仓**到处都在用**的可逆副作用形状
 * （`register(...) → disposer`、`subscribe(...) → disposer`），所以它**不是测试脚手架**——
 * 任何"临时注入、用完还原"的真实消费者（含本目录的单测）都用同一个口子。
 * ⚠ **多份可以并存**：后推的在栈顶（`panelConfig()` 读到的是它）；各弹各的。
 */
export function configurePanel(options: PanelOptions): Disposer {
  fallback = resolve(options)
  return () => {
    fallback = undefined            // ⚠ 清回**未注入**（单份语义；判据守的就是这条）
  }
}

/**
 * 读**当前这一层**的参数。**未注入即抛**（响亮失败）——这是本模块存在的理由那一半：
 * 宁可当场炸，也不许静默用一个中性默认把"忘了注入"演成"面板空的"。
 *
 * ⚠ 多项目下它读的是**栈顶**（= 正在注册的那一次）；**要拿"我这次"的那一份，
 * 请用 :func:`openPanelConfig` 的返回值**，不要在事后问栈顶。
 */
export function panelConfig(): ResolvedPanelConfig {
  // ⚠ 顺序：**注册期的那一层优先**（`openPanelConfig` 推的），没有才回落到单槽。
  //   多项目同时挂时，各 tab 的读取都发生在**自己那次注册**的作用域里 ⇒ 读到自己的。
  const top = stack[stack.length - 1] ?? fallback
  if (top === undefined) throw new Error(NOT_CONFIGURED)
  return top
}

/**
 * 该项目的**中性默认路由**（`routes.ts` 的唯一来源）。项目通常直接传它，
 * 也可以传自己的路由——但**必须显式传**（不许由资产悄悄回落）。
 */
export { DEFAULT_ROUTE_PATH }
