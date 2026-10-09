/**
 * ⭐ **框架自己的 client 插件入口**（`@mecha/dsh-panel/panel/auto.ts`）。
 *
 * ## 项目侧那一行（§7.3.7 裁决 A + §7.3.8 第 3 条：**这是原文，照抄即可**）
 *
 * ```ts
 * export { apply, name, inject } from '@mecha/dsh-panel/panel/auto.ts'
 * ```
 *
 * ⚠ **不能**写成裸 `import '@mecha/dsh-panel/panel/auto.ts'`：cordis 只认**项目模块自己的**
 * `name`/`inject`/`apply` —— 裸 import 只执行模块体、`apply` **不会被调**；
 * 而资产 `package.json` 是 `sideEffects:false`，整行还可能被**摇掉**。
 * ⚠ 用**显式子路径**（不是 `@mecha/dsh-panel/auto`）：三家的"浏览器安全闭包"判据按
 * **真实文件**解析 import，显式子路径它解析得到；这也正是三家既有的写法。
 *
 * ## 本文件为什么这么薄
 *
 * 行为（取参数 / 按服务挂 N 份 / 跨 bundle 幂等 / 按钮恒 1 个）全在同目录 `panels.ts`，
 * 因为那个模块**不含 `.tsx`** ⇒ `node --test` 能**真跑**它（三条硬判据都落在那里）。
 * 这里只做一件 `panels.ts` 做不到的事：**把 react 壳绑上去**
 * （`MonitorTabBody.tsx`；`.tsx` 不在 node 的类型剥离范围内 ⇒ 壳**不进**门禁的执行面，
 * 这是资产既有口径，见 `MonitorTabBody.tsx` 的 docstring）。
 */
import { MonitorTabBody } from './MonitorTabBody.tsx'
import {
  PLUGIN_INJECT,
  PLUGIN_NAME,
  applyMechaPanels,
  setDefaultPanelBody,
  type AutoContext,
} from './panels.ts'

/** ⭐ 把**资产自己的壳**装成默认正文组件（`panels.ts` 与 `.tsx` 解耦的那条线的另一端）。
 *  ⚠ 在**模块加载期**装：项目那一行 bootstrap 一执行就生效，早于 dsh 调 `apply`
 *  ⇒ 自定义入口（如 GP 那样自带按钮的项目）**不必再 import 壳**，直接调
 *  `applyMechaPanels(ctx)` 即可。 */
setDefaultPanelBody(MonitorTabBody)

/** cordis 插件名（诊断用）。 */
export const name = PLUGIN_NAME

/** 用到的那几片 dsh client 服务。 */
export const inject = PLUGIN_INJECT

/** cordis 入口：按声明挂 N 个 tab（正文 = 资产自己的壳）。 */
export async function apply(ctx: AutoContext): Promise<void> {
  const dispose = await applyMechaPanels(ctx)
  // ⚠ 有 `ctx.effect` 就交给它管寿命（与三家原写法一致）；没有就直接挂着
  //   （最小装配/判据里没有 effect 也能跑）。
  if (typeof ctx.effect === 'function') {
    await ctx.effect(() => dispose, 'mecha-panel: monitor tabs by declaration')
  }
}

// ⚠ 也把"给项目用"的那几个口子从入口转出去（项目只需记**一个** import 路径）：
//   · `applyMechaPanels`：**自带自定义 UI 的项目**（如 GP 有自己的按钮）用它 ——
//     它们的 `apply` 必须是自己的，所以不能只写那一行 `export { apply } from …`；
//   · `contributeExtraPageRender` / `extraPageRenderKey`：项目要**细化呈现**时用（如 ML
//     的主指标取值函数）——声明管页集与路由，这些只管"怎么渲染一行"。
export {
  PLUGIN_INJECT,
  PLUGIN_NAME,
  TITLE_PREFIX,
  applyMechaPanels,
  setDefaultPanelBody,
} from './panels.ts'
export type { ApplyOptions, AutoContext } from './panels.ts'
export { contributeExtraPageRender, extraPageRenderKey, extraPageSpecsFrom } from './services.ts'
export type { ExtraPageRender } from './services.ts'
