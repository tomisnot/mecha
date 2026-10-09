/**
 * ⭐ 阶段② 的**三条硬判据**都在这里（每条都自带"扰动真的落上了"的红证）。
 *
 * | 判据 | 一句话 |
 * |---|---|
 * | ① **按钮恒为 1 个** | 装 N 个服务 ⇒ 会话头部只有**一个**监控按钮 |
 * | ② **N 份配置并存、各读各的** | **标题与取数路由都不许**落到别人那份 |
 * | ③ **新项目零 dsh 领域代码也能出 tab** | 声明里加一个服务 ⇒ 右栏多一个 tab、且它读自己那份 |
 *
 * ## ⚠ 夹具的形状**必须与真实声明一致**（红线的由来）
 *
 * 本仓刚栽过一次：**三家的探活串自带冒号而夹具没有** ⇒ 30 项判据全绿，
 * 而**真项目一接就炸**（`http://127.0.0.1::62499/mcp`）。⇒ 这里的夹具**不是编的**：
 * 它来自 `panel/real-panel-services.json`，而那份 JSON 由框架判据
 * `checks/phase13_panel_decl_selfcheck.py` **拿三家的真 `mecha_site.py` 对拍**
 * （改声明不改夹具 ⇒ 那条判据当场红）。
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

import { HEADER_ACTIONS_SLOT, HEADER_BUTTON_ID, mountClientPanel } from './mount-client-plugin.ts'
import {
  PANEL_SERVICES_ROUTE,
  contributeExtraPageRender,
  extraPageRenderFor,
  extraPageRenderKey,
  extraPageSpecsFrom,
  isPanelServicesPayload,
  type DeclaredPanelService,
  type PanelServicesPayload,
} from './services.ts'
import { applyMechaPanels, loadPanelServices } from './panels.ts'

/**
 * ⚠ 正文组件用**替身**：真正的壳是 `.tsx`（react），**`node --test` 加载不了** ——
 * 这是资产**既有**的边界（"壳不进框架门禁的执行面，只过 typecheck"，见
 * `MonitorTabBody.tsx` 的 docstring），不是本判据图省事。
 * ⭐ 被验的**形状**（`panelConfig`/`kind`/`id`/`extraPages`）全都是真的；只有那个组件是替身。
 */
const STUB_BODY = { stub: 'MonitorTabBody' }

/** 真夹具（⚠ 与三家真 `mecha_site.py` 对拍，见文件头）。 */
const REAL: PanelServicesPayload =
  JSON.parse(readFileSync(new URL('./real-panel-services.json', import.meta.url), 'utf8')) as PanelServicesPayload

/** 造一个**假 ctx**：把每次注册都记下来（判据据此数按钮、读各自那份参数）。 */
function fakeCtx() {
  const tabs: { id: string; kind: string; title: (address: string) => string }[] = []
  const slots: { name: string; id?: string; key?: string; props: unknown; component: unknown }[] = []
  const btns: { id: string; label: string }[] = []
  const ctx = {
    sidebarRightTabs: {
      register(def: { id: string; kind: string; title: (address: string) => string }) {
        tabs.push(def)
        return () => { tabs.splice(tabs.indexOf(def), 1) }
      },
    },
    slots: {
      inject(name: string, fn: () => unknown) {
        const before = slots.length
        const dispose = fn() as () => void
        return () => { if (slots.length > before) slots.length = before; dispose?.() }
      },
      register(def: Record<string, unknown>, component?: unknown) {
        const row = {
          name: String(def['name']),
          ...(def['id'] !== undefined ? { id: String(def['id']) } : {}),
          ...(def['key'] !== undefined ? { key: String(def['key']) } : {}),
          props: def['inject'],
          component,
        }
        slots.push(row)
        if (row.id !== undefined) btns.push({ id: row.id, label: String(def['label']) })
        return () => { slots.splice(slots.indexOf(row), 1) }
      },
    },
    layout: { openRightbar() { /* 判据只关心按钮**注册了几个** */ } },
    sidebarRight: { openTab() { /* 同上：不该被调用 */ } },
    logger: { error() { /* 取参数失败那条路由的判据自己看返回值 */ } },
  }
  // ⚠ 正文槽位的 `inject` 是**返回 bodyProps 的函数**（`mount-client-plugin` 就是这么注册的）
  //   ⇒ 读那一份必须**调它**（第一版直接当对象读 ⇒ 读出 `undefined`，判据假红过一次）。
  const propsOf = (key: string): Record<string, unknown> =>
    ((slots.find((s) => s.key === key)?.props as (() => Record<string, unknown>))?.() ?? {})
  return { ctx, tabs, slots, btns, propsOf }
}

/** 参数取数的**替身**（同源 fetch；判据自证它真被调过）。 */
function fetchOf(payload: unknown) {
  const calls: string[] = []
  const impl = (async (url: string) => {
    calls.push(String(url))
    return { ok: true, status: 200, json: async () => payload } as unknown as Response
  }) as unknown as typeof fetch
  return { impl, calls }
}

/** 每次用例都从**干净**的跨 bundle 登记开始（否则用例之间互相污染 —— 实测踩过：
 *  上一个用例注册过的**头部按钮**会留在全局登记里，下一个用例就数到 0 个按钮）。 */
function resetGlobalAuto(): void {
  delete (globalThis as Record<string, unknown>)['__mechaPanelAuto__']
  delete (globalThis as Record<string, unknown>)['__mechaPanelHeaderButton__']
}

test('夹具：形状合法，且**就是三家真实声明**那一份（含三家真路由）', () => {
  assert.ok(isPanelServicesPayload(REAL), '夹具形状不合法')
  const ids = REAL.services.map((s) => s.id)
  assert.deepEqual(ids, ['ML', 'EL', 'GP'], `夹具的服务顺序/身份变了：${ids.join('、')}`)
  const routes = REAL.services.map((s) => s.routePath)
  // ⚠ 这三条是**真值**（与三家 `dsh/src/*-params.ts` 一致）——它们就是"夹具失真"的防线。
  assert.deepEqual(routes, [
    '/ml-toolbox/monitor-url', '/re0-mecha/monitor-url', '/paperpilot/monitor-url',
  ])
  assert.equal(REAL.routePath, PANEL_SERVICES_ROUTE, '夹具里的路由与 TS 镜像常量不一致')
})

// ------------------------------------------------------------------ 判据 ① 按钮恒为 1 个

test('① 按钮恒为 1 个：装 3 个服务 ⇒ 会话头部只有**一个**监控按钮', () => {
  resetGlobalAuto()
  const { ctx, btns } = fakeCtx()
  const { impl } = fetchOf(REAL)
  return applyMechaPanels(ctx, { body: STUB_BODY, fetchImpl: impl }).then(() => {
    const header = btns.filter((b) => b.id === HEADER_BUTTON_ID)
    assert.equal(header.length, 1,
      `头部按钮应当是 1 个，实际 ${header.length} 个（每个服务各注册一个 = 用户明确不要的形态）`)
    assert.equal(btns.length, 1, `会话头部动作槽位里不该有别的（收到 ${JSON.stringify(btns)}）`)
    assert.equal(header[0]?.label, '◈ 监控')
  })
})

test('① 红证：**各算各的账**（每个服务一份空登记）⇒ 按钮立刻变成 3 个', () => {
  resetGlobalAuto()
  const { ctx, btns } = fakeCtx()
  const { impl } = fetchOf(REAL)
  return applyMechaPanels(ctx, { body: STUB_BODY, fetchImpl: impl }).then(() => {
    const good = btns.filter((b) => b.id === HEADER_BUTTON_ID).length
    assert.equal(good, 1, '装配路径本身应当是 1 个按钮')
    // ⚠ **扰动**：同一个 helper，但每次注册**各给一份空登记** —— 那正是"模块级变量"
    //   那种坏实现的形状（三家各一份 bundle 时它就会发生）⇒ 按钮必须**变成 3 个**。
    const naive = fakeCtx()
    for (const svc of REAL.services) {
      mountClientPanel(naive.ctx as never, {
        panelConfig: { routePath: svc.routePath, portFile: svc.portFile, title: svc.title },
        kind: svc.kind, id: svc.tabId, body: STUB_BODY,
        buttonFlags: {},                    // ⚠ 每份**各一份** = 各自都以为"我是第一个"
      })
    }
    const bad = naive.btns.filter((b) => b.id === HEADER_BUTTON_ID).length
    assert.equal(bad, 3,
      `红证没红：各算各的账时按钮应当是 3 个（收到 ${bad}）——`
      + '说明判据① 抓不住"按钮变成 N 个"这种故障（R7：扰动必须先自证落上）')
  })
})

// ------------------------------------------------- 判据 ② N 份配置并存、各读各的

test('② N 份配置并存：标题与取数路由**各读各的**（不许落到别人那份）', () => {
  resetGlobalAuto()
  const { ctx, tabs, propsOf } = fakeCtx()
  const { impl } = fetchOf(REAL)
  return applyMechaPanels(ctx, { body: STUB_BODY, fetchImpl: impl }).then(() => {
    assert.equal(tabs.length, 3, `应当挂 3 个 tab，实际 ${tabs.length}`)
    for (const svc of REAL.services) {
      const tab = tabs.find((t) => t.id === svc.tabId)
      assert.ok(tab, `没有 ${svc.tabId} 这份 tab`)
      assert.equal(tab?.kind, svc.kind, `${svc.id} 的 kind 串了`)
      // ⭐ 标题：thunk 每次使用都重读 ⇒ 它必须**闭包捕获自己那份**
      assert.equal(tab?.title('x'), '◈ 监控 · ' + svc.title,
        `${svc.id} 的标题落到了别人那份（收到 ${tab?.title('x')}）`)
      // ⭐ 取数路由：正文收到的 config 必须是**自己那份**
      const props = propsOf(svc.tabId) as { config?: { routePath?: string; portFile?: string } }
      assert.equal(props.config?.routePath, svc.routePath, `${svc.id} 的取数路由串了`)
      assert.equal(props.config?.portFile, svc.portFile, `${svc.id} 的端口文件名串了`)
    }
    // ⚠ 三份标题互不相同（否则"各读各的"用"全都一样"也能过）
    const shown = tabs.map((t) => t.title('x'))
    assert.equal(new Set(shown).size, 3, `三份标题应当互不相同：${shown.join(' | ')}`)
  })
})

test('② 红证：**渲染时才去问全局**（老写法 `() => panelConfig().title`）⇒ 三份标题全一样', () => {
  // ⚠ 这是阶段① 修掉的那个真缺陷的**复现件**：把标题 thunk 写成"去读一个共享的当前值"，
  //   三个 tab 的标题就全落到**最后注册那一份**。本判据必须能抓住它。
  const shared = { title: '' }                       // = 老写法的"栈顶/单槽"
  const twins: { title: () => string }[] = []
  for (const svc of REAL.services) {
    shared.title = svc.title
    twins.push({ title: () => shared.title })        // ⚠ 渲染时才读 ⇒ 读到的是**最后**那份
  }
  const shown = twins.map((t) => t.title())
  assert.equal(new Set(shown).size, 1, '红证没红：老写法本应让三份标题完全相同')
  assert.equal(shown[0], REAL.services[REAL.services.length - 1]?.title,
    '红证没红：老写法应当落到**最后注册**那一份')
})

// ------------------------------------- 判据 ③ 新项目零 dsh 领域代码也能出 tab

test('③ 新项目零 dsh 领域代码：只在声明里加一个服务 ⇒ 右栏多一个 tab、且它读自己那份', () => {
  resetGlobalAuto()
  // ⚠ 这个"新项目"**没有任何 dsh 侧代码**：它只出现在那份参数里（= 声明里加了一行 `monitor=`）。
  const fresh: DeclaredPanelService = {
    id: 'NEW',
    kind: 'mecha:NEW',
    tabId: 'mecha-panel:NEW',
    routePath: '/new-project/monitor-url',
    portFile: '.new-monitor-port',
    title: '新项目',
    extraPages: [{
      id: 'runs', label: '运行记录', path: '/runs?limit=20', title: '运行记录',
      emptyText: '还没有记录', unreadableText: '读不到（不是空的）', note: '',
      columns: [{ label: '编号', field: 'run_id' }],
    }],
  }
  const before = fakeCtx()
  const { impl: implBefore } = fetchOf(REAL)
  return applyMechaPanels(before.ctx, { body: STUB_BODY, fetchImpl: implBefore }).then(() => {
    assert.equal(before.tabs.length, 3, '基线：三家 = 3 个 tab')
    // ⭐ 声明里多一个服务（**不写任何 dsh 代码**）⇒ 右栏多一个 tab
    resetGlobalAuto()
    const after = fakeCtx()
    const { impl: implAfter } = fetchOf({ ...REAL, services: [...REAL.services, fresh] })
    return applyMechaPanels(after.ctx, { body: STUB_BODY, fetchImpl: implAfter }).then(() => {
      assert.equal(after.tabs.length, 4, `加了声明之后应当 4 个 tab，实际 ${after.tabs.length}`)
      const tab = after.tabs.find((t) => t.id === 'mecha-panel:NEW')
      assert.ok(tab, '新项目没有出 tab（声明加了而 tab 没多 = 判据③ 不成立）')
      assert.equal(tab?.title('x'), '◈ 监控 · 新项目')
      const props = after.propsOf('mecha-panel:NEW') as
        { config?: { routePath?: string }; extraPages?: { path: string }[] }
      // ⭐ "读自己那份"：路由与附加页都是它自己的，不是三家里的任何一份
      assert.equal(props.config?.routePath, '/new-project/monitor-url')
      assert.equal(props.extraPages?.[0]?.path, '/runs?limit=20')
      // ⚠ 并自证**扰动真的落上了**：把新项目那份撤掉 ⇒ tab 数回到 3
      resetGlobalAuto()
      const undone = fakeCtx()
      const { impl: implUndone } = fetchOf(REAL)
      return applyMechaPanels(undone.ctx, { body: STUB_BODY, fetchImpl: implUndone }).then(() => {
        assert.equal(undone.tabs.length, 3, '撤掉声明后没回到 3 ⇒ 上面那次"多一个"可能不是声明带来的')
      })
    })
  })
})

test('③ 跨 bundle 幂等：第二份 bundle 再 apply 一次 ⇒ tab **不重复**、按钮仍 1 个', () => {
  resetGlobalAuto()
  const a = fakeCtx()
  const { impl, calls } = fetchOf(REAL)
  return applyMechaPanels(a.ctx, { body: STUB_BODY, fetchImpl: impl }).then(() => {
    const b = fakeCtx()
    return applyMechaPanels(b.ctx, { body: STUB_BODY, fetchImpl: impl }).then(() => {
      assert.equal(a.tabs.length, 3)
      assert.equal(b.tabs.length, 0,
        '第二份 bundle 又挂了一遍 ⇒ dsh 契约会拒（重复 id / 同 tier 同 kind 二次注册）')
      assert.equal(b.btns.filter((x) => x.id === HEADER_BUTTON_ID).length, 0)
      assert.equal(calls.length, 1, `那份参数应当只取一次（实际 ${calls.length} 次）`)
      assert.equal(calls[0], PANEL_SERVICES_ROUTE)
    })
  })
})

// --------------------------------------------------- 声明 → 附加页（数据 vs 自定义 UI）

test('附加页：声明给**数据**，呈现细化给**函数**（两处不重叠、各读各的）', () => {
  const declared = [{
    id: 'summary', label: '运行记录', path: '/summary', title: '运行记录（操作史）',
    emptyText: '', unreadableText: '', note: '脚注',
    columns: [{ label: '运行编号', field: 'run_id' }],
  }]
  // ① 没有呈现细化 ⇒ 中性取值（新项目的默认那条路）
  const plain = extraPageSpecsFrom('ML', declared)
  assert.equal(plain.length, 1)
  assert.equal(plain[0]?.columns[0]?.cell({ run_id: 7 }), '7')
  assert.equal(plain[0]?.columns[0]?.cell({}), '', '缺字段要给空串，不许渲染 undefined')
  // ⚠ 中性文案：声明没给 ⇒ **框架**给（且"读不到"与"空"分开）
  assert.ok(plain[0]?.emptyText && plain[0]?.unreadableText)
  assert.notEqual(plain[0]?.emptyText, plain[0]?.unreadableText)

  // ② 项目注册了自己的呈现（自定义 UI）⇒ 覆盖**自己那一份**的列，且不动别人
  const dispose = contributeExtraPageRender(extraPageRenderKey('ML', 'summary'), {
    columns: [{ label: '主指标', cell: (r) => `m=${String((r as { score?: number })?.score)}` }],
    summaryLine: () => '运行 3 次',
  })
  const rich = extraPageSpecsFrom('ML', declared)
  assert.equal(rich[0]?.columns.length, 1)
  assert.equal(rich[0]?.columns[0]?.label, '主指标')
  assert.equal(rich[0]?.columns[0]?.cell({ score: 0.5 }), 'm=0.5')
  assert.equal(rich[0]?.summaryLine?.({}), '运行 3 次')
  const other = extraPageSpecsFrom('EL', declared)
  assert.equal(other[0]?.columns[0]?.label, '运行编号',
    'ML 的呈现细化串到 EL 那份上去了（键里必须带服务 id）')
  dispose()
  assert.equal(extraPageRenderFor(extraPageRenderKey('ML', 'summary')), undefined, '收尾器没弹掉')
})

test('参数取不到 ⇒ **出声**且一个 tab 都不挂（不抛给 cordis）', () => {
  resetGlobalAuto()
  const errors: string[] = []
  const ctx = { ...fakeCtx().ctx, logger: { error: (m: string) => errors.push(m) } }
  const bad = (async () => { throw new Error('boom') }) as unknown as typeof fetch
  return applyMechaPanels(ctx, { body: STUB_BODY, fetchImpl: bad }).then((dispose) => {
    assert.equal(errors.length, 1, '取不到参数必须**出声**（否则没人知道为什么没有 tab）')
    assert.match(errors[0] ?? '', /一个 tab 都没挂/)
    assert.equal(typeof dispose, 'function')
  })
})

test('取参数失败**不缓存**：下一拍能自己恢复（不是"这个页面永远没面板"）', () => {
  resetGlobalAuto()
  let n = 0
  const flaky = (async () => {
    n += 1
    if (n === 1) throw new Error('first fails')
    return { ok: true, status: 200, json: async () => REAL } as unknown as Response
  }) as unknown as typeof fetch
  return loadPanelServices({ fetchImpl: flaky })
    .then(() => { throw new Error('第一次本应失败') })
    .catch(() => loadPanelServices({ fetchImpl: flaky }))
    .then((payload) => { assert.equal(payload.services.length, 3) })
})

test('没给正文组件 ⇒ **响亮失败**（不静默挂一个空白的 tab）', () => {
  // ⚠ `panels.ts` 与 `.tsx` 壳解耦（壳进不了 node 门禁）⇒ 默认正文由入口 `auto.ts` 装。
  //   这条钉住"两边都没有"那种接线错误：空白的 tab 看着像坏了，必须当场炸。
  //   ⚠ 本判据**不 import** 入口（那会把 `.tsx` 拖进来）⇒ 这里天然就是"没装默认"那一档。
  resetGlobalAuto()
  const { ctx } = fakeCtx()
  const { impl } = fetchOf(REAL)
  return assert.rejects(() => applyMechaPanels(ctx, { fetchImpl: impl }), /需要正文组件/)
})
