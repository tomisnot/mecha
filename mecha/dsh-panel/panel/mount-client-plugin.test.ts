/**
 * 阶段①判据：**参数跟着那次注册走** + **头部按钮恒为一个**。
 *
 * ## 为什么这两条必须一起测、且必须"真装两份"
 *
 * 2026-10 实测：三家各自手写 client 半 ⇒
 * ① `title: () => panelConfig().title`（**渲染时重读的 thunk**）⇒ 三个 tab 的标题与
 *    取数路由**全落到"最后注册那一份"**（`ca2a05d` 只修了 host 半，浏览器侧是**另一个
 *    模块实例**，没修到）；
 * ② 三家各注册一个会话头部按钮 ⇒ **N 个项目 = N 个按钮**（用户要的是"唤起所有"）。
 *
 * ⇒ 判据就按这两条钉：**真装两份**（两个 kind / 两个 id），断言各读各的、按钮只有一个。
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { mountClientPanel, GUIDE_KIND, HEADER_ACTIONS_SLOT } from './mount-client-plugin.ts'

/** 记一笔的假 ctx：只实现本助手用到的那几片（**结构化**，与真 ctx 同形）。 */
function fakeCtx() {
  const calls: { slot: string; def: Record<string, unknown> }[] = []
  const opened: string[] = []
  const openedTabs: string[] = []
  const tabs: {
    id: string
    kind: string
    title: (a: string) => string
    guide?: readonly { id: string; order: number; title: () => string }[]
  }[] = []
  const ctx = {
    sidebarRightTabs: {
      register: (def: {
        id: string
        kind: string
        title: (a: string) => string
        guide?: readonly { id: string; order: number; title: () => string }[]
      }) => {
        tabs.push({ id: def.id, kind: def.kind, title: def.title, guide: def.guide })
        return () => { tabs.pop() }
      },
    },
    slots: {
      register: (def: Record<string, unknown>) => {
        calls.push({ slot: String(def.name), def })
        return () => {}
      },
      inject: (name: string, fn: () => () => void) => fn(),
    },
    layout: { openRightbar: (open: boolean) => { opened.push(String(open)) } },
    //: ⭐ 右栏服务：按钮的正确动作是 `openTab('guide')`（见 `openMonitorGuide`）
    sidebarRight: { openTab: (kind: string) => { openedTabs.push(kind) } },
  }
  return { ctx, calls, opened, openedTabs, tabs }
}

const OPTS = (svc: string, title: string, route: string) => ({
  panelConfig: { routePath: route, portFile: `${svc}.port`, title },
  kind: `mecha:${svc}`,
  id: `mecha-panel:${svc}`,
  body: { name: 'Body' },
})

test('⭐ 两份注册并存：标题各读各的（不落到"最后注册那一份"）', () => {
  const a = fakeCtx()
  const b = fakeCtx()
  // ⚠ 注入同一份干净的按钮登记表 ⇒ 两份"同进程"（模拟三家在同一个 dsh 里）
  const flags: Record<string, boolean> = {}
  const optsA = { ...OPTS('AAA', '甲的标题', '/a'), buttonFlags: flags }
  const optsB = { ...OPTS('BBB', '乙的标题', '/b'), buttonFlags: flags }
  const disA = mountClientPanel(a.ctx, optsA)
  const disB = mountClientPanel(b.ctx, optsB)

  // ① 类型注册各归各的 kind / id
  assert.deepEqual(a.tabs.map((t) => t.kind), ['mecha:AAA'])
  assert.deepEqual(b.tabs.map((t) => t.kind), ['mecha:BBB'])

  // ② ⭐ 标题**闭包捕获自己那份**：A 注册时给的是什么，就永远是什么
  const titleOf = (calls: { slot: string; def: Record<string, unknown> }[]) => {
    const body = calls.find((c) => c.slot === 'sidebar.right.pane.tab')
    const inject = body?.def.inject as (() => { config: { title: string } }) | undefined
    return inject!().config.title
  }
  assert.equal(titleOf(a.calls), '甲的标题', 'A 的正文必须拿到甲那份')
  assert.equal(titleOf(b.calls), '乙的标题', 'B 的正文必须拿到乙那份')
  // ⭐ 关键对偶：**B 注册之后，A 的读数不许改变**（这正是"落到栈顶"那个 bug）
  assert.equal(titleOf(a.calls), '甲的标题', 'B 注册后 A 仍读自己那份')

  // ③ 取数路由同样是各自那份（正文拿 config 去取数）
  const routeOf = (calls: { slot: string; def: Record<string, unknown> }[]) => {
    const body = calls.find((c) => c.slot === 'sidebar.right.pane.tab')
    const inject = body?.def.inject as (() => { config: { routePath: string } }) | undefined
    return inject!().config.routePath
  }
  assert.equal(routeOf(a.calls), '/a')
  assert.equal(routeOf(b.calls), '/b')

  disB()
  disA()
})

test('⭐ 每个监控 tab 都往右栏 guide 页**投一个入口**（那才是"一个按钮、N 个面板"的原语）', () => {
  const a = fakeCtx()
  const b = fakeCtx()
  const flags: Record<string, boolean> = {}
  mountClientPanel(a.ctx, { ...OPTS('AAA', '甲的标题', '/a'), buttonFlags: flags, guideOrder: 0 })
  mountClientPanel(b.ctx, { ...OPTS('BBB', '乙的标题', '/b'), buttonFlags: flags, guideOrder: 1 })

  // ⚠ 不投 `guide` ⇒ 这个 tab 类型**不上那一页**；而 dsh 的 `defaultSeed()` 只在
  //   "0 个或多个 guide 条目"时才把默认页算成 guide 页 ⇒ 投了条目，"一个按钮唤起全部"才成立。
  const ga = a.tabs[0]?.guide?.[0]
  const gb = b.tabs[0]?.guide?.[0]
  assert.ok(ga && gb, `两个 tab 类型都该带 guide 条目：${JSON.stringify([a.tabs[0], b.tabs[0]])}`)
  assert.equal(ga!.id, 'mecha-panel:AAA')
  assert.equal(ga!.order, 0, 'guideOrder 应当按声明顺序传进来')
  assert.equal(gb!.order, 1)
  // ⭐ 标题与 tab 标题**同一份**（前缀也一致）⇒ 一览里看到的就是面板名
  const label = (t: { title: (a: string) => string }) => t.title('ignored')
  assert.equal(ga!.title(), label(a.tabs[0]!))
  assert.equal(gb!.title(), label(b.tabs[0]!))
})

test('⭐ 兜底：宿主没有 `sidebarRight.openTab` ⇒ 退回 `openRightbar`（不静默什么都不做）', () => {
  const a = fakeCtx()
  delete (a.ctx as { sidebarRight?: unknown }).sidebarRight
  mountClientPanel(a.ctx, OPTS('AAA', '甲', '/a'))
  const header = a.calls.find((x) => x.slot === HEADER_ACTIONS_SLOT)!
  const inj = header.def.inject as () => { openCockpit: () => void }
  inj().openCockpit()
  assert.deepEqual(a.opened, ['true'], '没有 openTab 时应当退回 openRightbar(true)')
})

test('⭐ 头部按钮恒为**一个**（后注册的不再注册第二个）', () => {
  const a = fakeCtx()
  const b = fakeCtx()
  const c = fakeCtx()
  const flags: Record<string, boolean> = {}
  const dis = [
    mountClientPanel(a.ctx, { ...OPTS('AAA', '甲', '/a'), buttonFlags: flags }),
    mountClientPanel(b.ctx, { ...OPTS('BBB', '乙', '/b'), buttonFlags: flags }),
    mountClientPanel(c.ctx, { ...OPTS('CCC', '丙', '/c'), buttonFlags: flags }),
  ]
  const btnCount = (calls: { slot: string }[]) =>
    calls.filter((x) => x.slot === HEADER_ACTIONS_SLOT).length
  const total = btnCount(a.calls) + btnCount(b.calls) + btnCount(c.calls)
  assert.equal(total, 1, `三个服务插件总共只该注册 1 个按钮，实际 ${total} 个`)
  assert.equal(btnCount(a.calls), 1, '第一个注册的拿到按钮')

  // ⭐ 按钮语义 = **把监控面一整页带到眼前**（打开右栏那个内置的 guide 页）
  //   ⚠ 本判据**改过**（2026-10-09）：原先是"调 `layout.openRightbar(true)`"，而用户实测
  //   **点了什么都没开** —— 因为 `openRightbar` 只开栏、**不选 tab** ⇒ 用户看到**空栏**。
  const header = a.calls.find((x) => x.slot === HEADER_ACTIONS_SLOT)!
  const inj = header.def.inject as () => { openCockpit: () => void }
  inj().openCockpit()
  assert.deepEqual(a.openedTabs, [GUIDE_KIND], '按钮应当打开内置的 guide 页（那一页列出所有监控面板）')
  assert.deepEqual(a.opened, [], '有 openTab 时**不该**退回 openRightbar')

  // ④ 收尾之后**允许别人再注册**（不是永久占位）
  dis[0]!()
  const d = fakeCtx()
  mountClientPanel(d.ctx, { ...OPTS('DDD', '丁', '/d'), buttonFlags: flags })
  assert.equal(btnCount(d.calls), 1, '注销后应能重新注册按钮')
  dis[1]!()
  dis[2]!()
})

test('⚠ 参数留空 ⇒ **当场抛**（响亮失败，不许静默用别人那份）', () => {
  const a = fakeCtx()
  assert.throws(
    () => mountClientPanel(a.ctx, { ...OPTS('AAA', '甲', ''), panelConfig: {
      routePath: '', portFile: 'p', title: 't' } }),
    /routePath/,
  )
  assert.throws(
    () => mountClientPanel(a.ctx, { ...OPTS('AAA', '甲', '/a'), panelConfig: {
      routePath: '/a', portFile: 'p', title: '   ' } }),
    /title/,
  )
})

test('⭐ 标题 **thunk 闭包捕获自己那份**（渲染时不许读全局）', () => {
  // ⚠ 这条是"渲染时读到栈顶"那个 bug 的**正面**对偶：`title` 是契约要求的 thunk
  //   （每次使用时重读），所以断言必须**调用它**——只看"正文拿到了什么"抓不到这个 bug
  //   （正文的 config 是显式 prop，退化 A 里它照样对 ⇒ 那条断言会假绿，实测过）。
  const a = fakeCtx()
  const b = fakeCtx()
  const flags: Record<string, boolean> = {}
  const disA = mountClientPanel(a.ctx, { ...OPTS('AAA', '甲的标题', '/a'), buttonFlags: flags })
  const disB = mountClientPanel(b.ctx, { ...OPTS('BBB', '乙的标题', '/b'), buttonFlags: flags })
  assert.equal(a.tabs[0]!.title('whatever'), '甲的标题')
  assert.equal(b.tabs[0]!.title('whatever'), '乙的标题')
  // ⭐ 对偶：**B 注册之后**再问 A 的 thunk，仍然必须是甲的（不许变成乙）
  assert.equal(a.tabs[0]!.title('whatever'), '甲的标题', 'B 注册后 A 的标题不许被顶掉')
  disB()
  disA()
})