/**
 * 会话头部那个**唯一的**「◈ 监控」按钮 —— 资产自带的**壳**。
 *
 * ## ⛔ 为什么必须有这个组件（2026-10-09 实测抓到的真因）
 *
 * 那个槽位（`conversation.session.header.actions`）是 **`kind: 'list'`**，
 * **条目的渲染靠第二个参数那个组件** —— dsh 官方给这个槽位的例子就是
 * `slots.register({…def…}, () => React.createElement(…))`，产品内的真实用法
 * （`ui-jobs` 的 `JobListAction`、`ui-agent-preset` 的 `AgentPresetLabel`）**全都传组件**。
 *
 * ⚠ 而我们此前**只注册了 def**（`{name, id, order, label, inject}`）⇒ **按钮根本不显示**
 * （用户报"「◈ 监控」按钮没显示"；而同一排 GP 自带的「评审今日」**给了组件** ⇒
 * **只有它显示** —— 那正是"看着像不对称"的现场）。
 * ⇒ 注册时**必须**把壳一起交上去；⚠ **没有壳 ⇒ 响亮失败**（`mount-client-plugin.ts`），
 * 不留"注册了但看不见"的静默态。
 *
 * ## 边界（与 `MonitorTabBody.tsx` **同一条**）
 *
 * 本文件是**壳**（含 `.tsx` ⇒ **不进** `node:test` 的执行面）：行为（要不要注册、
 * 注入什么、点了开什么页）都在 `panels.ts` / `mount-client-plugin.ts` 的 `.ts` 里，
 * 由判据逐条覆盖；本文件只画一个按钮。
 * ⚠ 对 `@deepseek-ai/*` **零 import**（**连类型都不 import**）：props 用**结构化最小接口**，
 * 这样资产仍然"不认识 cordis/dsh"。
 */
import type { CSSProperties } from 'react'

/** 这个按钮的 props（结构化：注入面由 `slots.register` 的 `inject` 给）。 */
export interface MonitorHeaderButtonProps {
  /** 点击时"**把监控面带到眼前**"（= 打开右栏那个内置的 guide 页；见 `openMonitorGuide`）。 */
  openCockpit?: () => void
}

/** 与三家原先自己写的按钮**同观感**（透明底、细边、跟随主题色）。 */
const BTN: CSSProperties = {
  padding: '2px 10px',
  borderRadius: 6,
  cursor: 'pointer',
  border: '1px solid rgba(128,128,128,0.35)',
  background: 'transparent',
  color: 'inherit',
  font: 'inherit',
  fontSize: '0.9em',
}

/** 会话头部按钮：**一个**，语义 = 打开监控面（不挑某一个项目的 tab）。 */
export function MonitorHeaderButton({ openCockpit }: MonitorHeaderButtonProps) {
  return (
    <button type="button" style={BTN} title="打开监控面（右栏）" onClick={() => openCockpit?.()}>
      ◈ 监控
    </button>
  )
}
