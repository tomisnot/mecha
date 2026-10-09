# dsh 集成**标准件**（共享资产）

> ## ⚠ 先读这三句
>
> 1. **这不是框架能力。** 它是**宿主（dsh）侧的共享资产**——一份可被项目**逐字复制**的
>    标准件。**框架不拥有它**：既有 ADR 已裁定
>    「**消费面归宿主，库不 import UI**」
>    （`docs/v2/notes/archived/2026-09-24-写权模式UI面-Authority通知缝.md:24-25`）——
>    库不 import 任何 UI 库，**它是"不是框架能力"的依据**。
> 2. ⚠ **「→GUI 交权」不违反"软件不依附 dsh"，反而是它的执行者**（**2026-10-01 裁决，
>    取代本处旧述**）。旧述引同一份 ADR 的 `:48-50` 否决「把驾驶舱面板塞回 dsh 插件当 web 按钮」，
>    并据此说"这个面板不许有写口/控制口"。**那条否决已过时，不适用于本资产**：
>    它否的是"**控制权依附 dsh**"（dsh 一死就没法控制软件）；而「交权给桌面 GUI」做的是
>    **相反的事**——请 dsh **退出**、把控制权交给**独立进程**。
>    ⇒ 「→GUI 交权」是**合法的 opt-in 标准件**，不是禁区。
>    ⛔ **但必须有能力开关**：它旧形态依赖"另有看门人进程在轮询 `.mode-request`"
>    （⚠ 三层架构已废这条：切写权走控制端点、**不动进程**、不留意图文件）；
>    **没有接手的部署按了没人应** ⇒ **开关关着时按钮不该出现在界面上**
>    （不许留"按了没反应"的按钮）。
> 3. **框架仓里没有运行时读取方。** 见下面「R1 的诚实立场」——这句话说的是
>    **"框架自己不会在运行时读它"**（与"三家项目在用它"**不矛盾**）。
> 4. ⭐ **靠依赖，不靠复制**（2026-10-01 起）：项目以
>    `"@mecha/dsh-panel": "file:<本目录>"` 装进来，npm 建的是**junction** ⇒
>    **磁盘上只有一份代码**。项目专有的值**注入**进来（`configurePanel(...)`），
>    **不在资产里**。详见下文「依赖约定」。
>    ⭐ **默认件 / opt-in 件的划分与参数表见下文「它是什么 / 解决什么」之后那几节。**

---

## 它是什么 / 解决什么

dsh 插件的 client 半**不能自己读磁盘**，而监控端点的端口是**动态分配**的
（`mecha.cockpit` 用 `port=0` 起端点，把实际端口写进项目根的一个裸端口文件）。
于是"地址"必须由 **host 半**转一次：host 读端口文件 → 同源只读路由回 `{base}` →
client 半 fetch 同源路由拿到地址。本目录就是这一层 + 取数骨架的参考实现。

**三家项目各自写过一份，而且已经分叉**：其中一家今天**坏在静默空白**（地址来源被硬编码/
回落，面板打不开却什么都不说），另一家的 host 半是前一家 `host/*` 的近似逐字复制、**唯独
漏掉了地址路由这一个文件**。⇒ 本资产的存在理由不是"少写几行"，是**让"漏一个文件"这件事
变成机械可抓**。

## ⭐ 标准件的两类：**默认件** 与 **opt-in 件**

| 类 | 件 | 谁用 |
|---|---|---|
| **默认件**（面板 + 地址路由） | `panel/panel-config.ts`（**注入点**）/ `panel/routes.ts` / `panel/panel-view.ts` / `panel/panel-data.ts` / `panel/MonitorTabBody.tsx` / `panel/monitor-client.ts` / **`host/monitor-url.ts`**（地址路由的 handler **住 host 面**，因为它 node-only；但**每个**要用面板的项目都要它）+ 各 `.test.ts` | **所有要"人看监控面"的项目**——这是"面板进 dsh"的最小闭环 |
| **opt-in 件**（自愈桥 + 工具注册 + **host 半骨架**） | `mcp-bridge.ts`（根）/ `host/mcp-session-http.ts` / `host/config.ts` / `host/register-tools.ts` / `host/mount-host-plugin.ts` / `host/mcp-sdk-shims.d.ts` | **要让 dsh 里的 AI 原生调自家工具**的项目；**不调就不用**（装了包也不进 bundle） |

⚠ **按总纲 §5.3「nothing ships enabled」：桥不许默认带。** 实测已有一家明确**不**用它
（ML 的 `dsh/src/index.ts:29-31` 自述）。⇒ 判定标准是"这个项目**用不用**"，
不是"资产里有没有"——**装进来但没被 import 的文件不进 bundle**（实测过）。

## ⭐ host 半骨架：`mountHostPlugin(...)`（2026-10-01 上提）

**为什么有它**：资产做成依赖包后 `client 半` 已共用，但每个项目的 `src/index.ts`（host 半）
**仍各手写一遍同一套骨架**——建桥、`onTools → syncTools` 两阶段 swap、logger 转发、
`onStatus` 三态日志、监控地址路由、可选的 command、teardown 顺序，连 `host/*-hub.ts`
都几乎逐字同形。⇒ 那段固化成一份，项目侧只剩**参数**与自身分支。

**项目侧现在长这样**（`apply` 的全部内容）：

```ts
import type { Context } from '@deepseek-ai/cordis'          // 项目自己的宿主类型（peer）
import {
  hostPluginInject,
  mountHostPlugin,
  type HostContext,                                        // 骨架的**结构化最小接口**
} from '@mecha/dsh-panel/mount-host-plugin.ts'
import { MY_BRIDGE, MY_FEATURES, MY_PANEL } from './my-params.ts'
import { myResolveUrl } from './host/my-hub.ts'

export const name = MY_BRIDGE.serverName
export const inject = hostPluginInject()   // → ['tools']（地址路由由骨架内部条件处理）

export async function apply(ctx: Context, config: MyConfig = {}): Promise<void> {
  // ⚠ 这个 cast 是**"资产不认识 cordis"**的代价，刻意写在项目侧：
  //   骨架对 @deepseek-ai/* **零 import**（连类型都不 import），所以由项目把宿主 ctx 适配进来。
  await mountHostPlugin(ctx as unknown as HostContext, {
    serverName: MY_BRIDGE.serverName,
    logLabel: MY_BRIDGE.logLabel,
    projectRoot: config.projectRoot?.trim() || process.cwd(),
    resolveUrl: () => myResolveUrl(config),
    clientInfo: MY_BRIDGE.clientInfo,
    offlineHint: MY_BRIDGE.offlineHint,
    panelConfig: MY_PANEL,
    // 可选：项目自己的 command。⚠ 能力关着时**不要传** ⇒ 骨架什么都不注册（不留死缝）
    // ⚠ 回执形状由**宿主**定（骨架只创建、不解释命令）⇒ 这里怎么写都行，资产不设约束。
    ...(MY_FEATURES.switchToGui ? {
      registerCommand: (c) => c.commands!.register({
        name: '…', description: '…', handler: async () => ({ kind: 'success', text: '…' }),
      }),
    } : {}),
  })
}
```

| 参数 | 必填 | 不给时 |
|---|---|---|
| `serverName` | ✅ | **抛**（工具命名空间 = `mcp__<serverName>__<tool>`） |
| `logLabel` | ✅ | **抛**。它**同时也是服务名**（`[x] x 离线…`）——拆成两个字段会造成第二处真值 |
| `projectRoot` | ✅ | **抛**（面板端口文件所在目录） |
| `resolveUrl` | ✅ | **抛**。端点怎么算（显式 URL > 端口文件 > 项目默认）是**项目知识** |
| `clientInfo` | ✅ | **抛**（`initialize` 上报的 `name`/`version`，两者都非空） |
| `panelConfig` | ✅ | **抛**（面板参数必须由项目注入；资产不存项目值） |
| `offlineHint` / `reconnect` / `requestInit` / `onStatus` | ❌ | 用桥自己的默认；`onStatus` 不给 ⇒ 骨架打默认三态日志 |
| `sessionFactory` | ❌ | **只给单测**：注入假会话 ⇒ 完全不碰 MCP SDK |
| `registerCommand` | ❌ | 不注册任何 command（**能力关着时就不该传**） |

**骨架替你保证的五件事**（别在项目里再手写一遍）：

1. **端点每次建会话现解**（端口漂移自愈）；骨架只保存 `resolveUrl` 的**引用**、不缓存结果
   （判据：挂载期解析次数必须为 0 —— 退化成"只解一次"会当场红）；
2. **没有 webServer 时跳过地址路由，而不是整个插件不激活**
   （顶层 `inject` 缺服务会让插件整个不激活 ⇒ headless 部署会连 AI 工具一起丢）；
3. **收尾顺序**：路由 → command → 工具 → 关桥 → 面板注入态，每步各自吞错
   （不因一处失败泄漏其余）；
4. **MCP SDK 只在"建真桥"时才被加载**：注入假会话（`sessionFactory`，**只给单测**）的路径**完全不碰**它。
   ⚠⚠ **但走骨架默认路径的项目一定需要它** —— 不传 `sessionFactory` 就是**建真桥**，
   而建桥会 `await import('./mcp-session-http.ts')`，那个文件**顶层 import SDK**。
   （2026-10-02 订正：本条原先写"**不挂桥**……不需要装 SDK"——**推理对、结论误导**：
   三家都走默认路径 ⇒ **全都需要**。见第 5 条。）
5. ⭐⭐ **SDK 由【框架仓】提供**，不是各项目自己装（2026-10-02 实测，**三家全踩过**）。
   规则是一句通用的：**资产里的代码只能用它【自己解析得到】的依赖** ——
   理由与修法见「迁移时必须知道的**四**件事」**第 4 条**。

⚠ ⭐ **代价（与第 4 条是一体两面，下游实测撞到）**：懒加载让**构建产物不再只有一个文件**。
EL 实测的 `lib/`：

```
index.mjs                       36607
mcp-session-http-<hash>.mjs      2294   ← 动态分包（`await import()` 的产物）
client.js                       40977
```

⇒ **部署 / Docker / 打包脚本「只拷 `index.mjs`」会在运行时才炸**（动态 import 找不到分包）。
**带产物就要整目录带。** 这一条只写"好处"是不够的：
*它换来"不装 SDK 也能只吃面板"，代价是"产物多一个文件、必须整目录带"* —— 两者必须一起写，
否则下一个人会在部署脚本里踩一个**只在运行时出现**的坑。

## ⭐ 顶层 `inject`：**只要 `tools`**

```ts
export const inject = hostPluginInject()   // → ['tools']
```

⚠ **恒不含 `webServer`**，这不是省事，而是**与骨架内部语义一致**：
顶层 `inject` 里列了 `webServer` ⇒ **缺该服务时整个插件不激活**；而地址路由是**可选**能力
（骨架内部用 `ctx.inject(['webServer'], …)` 条件注册）。两者不一致，就会把
"**面板没有 web 服务**"升级成"**AI 工具也没了**"（headless 部署实测踩过）。

⚠ 这里**曾经**是 `hostPluginInject({ needsWebServer })`，而默认恰好是**错的那一档**：
一家下游发现了、传 `false` 绕过；另一家没发现、继承了脆弱默认 ⇒ **默认值选错的典型**
（**参数的存在往往说明默认值选错了**）。参数已删——没有任何调用方需要它。

⚠ **"写文件请某某接手"那类东西不在骨架里**（旧形态的 `.mode-request` 就是它）：它依赖"有人在轮询"；
⚠ 三层架构下切写权走控制端点、不动进程 ⇒ 骨架只给**注册 command 的口子**，写什么文件留在项目。

⚠ **资产对 `@deepseek-ai/*` 零 import（连类型都不 import）**：out-of-tree 插件的
node_modules 里没有它，而**垫片也救不了**（实测：`preserveSymlinks` 只能救"装在消费者
node_modules 里的" peer，cordis 是宿主提供的、消费者那里根本没有它）。
⇒ 骨架对 `ctx` 只用**结构化最小接口** `HostContext`，项目侧一处
`ctx as unknown as HostContext` 适配；**这是刻意的解耦代价，不是待补的垫片**。

⚠ **那一处 cast 不提供适配器**（评估结论，不是省事）：
- cast 本身**消不掉**——真 `Context` 在结构上**不可能**等于 `HostContext`（它是宿主自有形状）；
- 写成 `asHostContext(ctx)` 只是把同一个 cast 从项目挪进资产，**没有减少任何东西**，
  却新增一个导出面（正是"能靠结构就别加机制"要避免的）。
⇒ **项目各自 cast 一次就够。**

⭐ **但项目的宿主类型垫片该自己补全**（实测：EL 的 `dsh/types/dsh-shims.d.ts` 的 `Context`
**没有声明 `ctx.inject`**）⇒ 那句 `ctx as unknown as HostContext` 里的 `unknown`
**掩盖的是"垫片漏声明"**，不是骨架缺陷。
⚠ **垫片该补**：把宿主真实提供的成员（至少 `inject`）声明上去；
**不要**为此放宽资产自己的接口——资产只认它真正用到的那一片，
那正是它敢**零 import 宿主类型**的前提。

## ⭐ 参数表：项目值**注入**进来（资产零项目字面量）

**面板参数**（默认件；每个进程 / 每个 bundle 调**一次**）：

```ts
import { configurePanel } from '@mecha/dsh-panel/panel-config.ts'
configurePanel({
  routePath: '/<项目>/monitor-url',  // host 半注册它、client 半 fetch 同一个串
  portFile: '.<项目>-monitor-port',  // 权威实际写的那个文件名（或绝对路径）
  title: '<项目>监控',
  tabs: { rec: '…', cfg: '…' },      // 可选：只覆盖标准两页的页签文案
})
```

| 字段 | 必填 | 未注入 / 留空时 |
|---|---|---|
| `routePath` | ✅ | **抛**（`panelConfig()` 未注入即抛；字段留空也抛） |
| `portFile` | ✅ | 同上。**资产不猜文件名**（同 `resolve_data_dir` 的纪律） |
| `title` | ✅ | 同上。纯展示；模型不读它 |
| `tabs` | ❌ | 不注入 ⇒ 用共享默认「飞行记录仪」/「配置态」 |

**opt-in 件的参数**（自愈桥那一侧：仍是"传给构造函数"，不是全局注入）：

| 文件 | 参数 | 默认值 | 用途 |
|---|---|---|---|
| `mcp-bridge.ts` | `BridgeOptions.logLabel` | `'mcp-bridge'` | 日志前缀（`[<label>] …`）；项目填自己的名字 |
| `mcp-bridge.ts` | `BridgeOptions.offlineHint` | `'请确认软件已启动；工具仍在列表但暂不可用。'` | 断联指引；"怎么把软件重新起来"是**项目知识** |
| `mcp-bridge.ts` | `BridgeOptions.reconnect` | `initialDelayMs=300` / `maxDelayMs=15000` / `growFactor=1.5` / `maxAttempts=0`(无限) | 退避重连 |
| `host/mcp-session-http.ts` | `HttpSessionOptions.clientInfo` | `{ name: 'mcp-bridge-dsh', version: '0.1.0' }` | initialize 上报的客户端标识 |
| `host/config.ts` | `HubUrlConfig.defaultUrl` | `''`（**不猜端口**） | 端口文件读不到时的默认端点（**项目值**） |
| `host/config.ts` | `HubUrlConfig.path` | `'/mcp'` | 端点路径 |
| `host/config.ts` | `HubUrlConfig.mcpPortFile` | 未给（不读文件） | 端口文件路径（**项目值**：文件名归项目） |
| `host/register-tools.ts` | `syncTools(..., serverName)` | 必填 | 工具公开名 = `mcp__<serverName>__<rawName>` |
| `host/register-tools.ts` | `SyncToolsOptions.logLabel` | `'mcp-bridge'` | 工具注册失败的日志前缀（**通常与桥传同一个值**） |

## ⚠ 类名不是项目参数（**别每个项目改一次**）

**类名 `MechaMcpBridge` 是资产自己的标识，不是品牌位**——本资产就叫 mecha。
项目要自己的名字，改的是**参数**：`logLabel`（日志前缀）与 `offlineHint`（断联指引）。
⇒ 这样"逐字复制"才成立；否则每抄一次都要动文件内容，比对立刻失效。
（已有项目按自己的桥名（如 `PaperPilotMcpBridge`）复制，那是**它们的选择**，不是本资产的约定。）
> **唯一权威在本文件**（D1）：`mcp-bridge.ts` 头部只留链接，不复述这段。

## ⚠ 依赖口径（如实写；**2026-10-02 订正**）

* **用桥的项目**需要 `@modelcontextprotocol/sdk`（`host/mcp-session-http.ts` 是**唯一** import 它的地方）。
* **框架仓【装】它，且必须有**（`mecha/package.json` 的 `devDependencies`）。
  ⚠ 这条原先写"**框架仓不装它**"——**那个说法在运行期是错的**：
  资产经 junction 被依赖时，Node 是按**框架仓**那条路径去解析的
  ⇒ 框架仓不装，**三家消费侧的插件全部加载失败**（2026-10-02 实测三家都中）。
  机制与修法见「迁移时必须知道的**四**件事」**第 4 条**。
* **但它与 `host/mcp-sdk-shims.d.ts` 不冲突、也不是替代** —— 两者管**不同的阶段**：

  | | 它管什么 | 何时生效 | 缺了会怎样 |
  |---|---|---|---|
  | **垫片**（`mcp-sdk-shims.d.ts`，纯 `declare`） | **类型检查期**的签名 | `tsc --noEmit` | 少一处签名就 `TS2307`（但这是"口径依赖"：**比真 SDK 宽松**，真 SDK 改签名**它不会自动红**） |
  | **框架仓装的真 SDK** | **运行期**的包解析 | dsh 加载插件时 | `ERR_MODULE_NOT_FOUND`（**且 `tsc` 照样全绿** —— 最难查的形态） |

* ⇒ **兜底仍然是**：用桥项目的 `npm run typecheck` **加上真机冒烟**。
  ⚠ 后者**不能省**：2026-10-02 这次事故里，`typecheck` 是绿的，**只有真机加载才暴露**。

## 入口与目录（**按入口分开**，2026-10-01）

两个入口 + 一个根：

| 入口 | 谁 import | peer | 里面有什么 |
|---|---|---|---|
| **`@mecha/dsh-panel/panel`** | **浏览器 client 半**（面板） | `react` | 面板的全部（见下表，**全部浏览器安全**） |
| **`@mecha/dsh-panel/host`** | dsh 插件的 **host 半** | `@modelcontextprotocol/sdk` | 插件骨架 / 工具注册 / 端点解析 / SDK 会话 |
| 根（`@mecha/dsh-panel/mcp-bridge.ts`） | **两半都会用** | 无 | 自愈桥（纯逻辑：零 import） |

⭐ **"把 node-only 带进浏览器 bundle"在入口上不可表达**：包只发布 `./panel/*` 与 `./host/*`
两个面，`panel` 侧**够不到** `host`。反向是**允许**的——`host` 是上面那一层。

> 临时消费者实测（junction 装包 + `--strict --noUncheckedIndexedAccess` + `preserveSymlinks`）：
> `'@mecha/dsh-panel/panel'` ⇒ **0 错**；`'@mecha/dsh-panel/host/mount-host-plugin.ts'` ⇒ **0 错**；
> `'@mecha/dsh-panel/panel/config.ts'` ⇒ **TS2307**（那条路**不可表达**）。

⚠ **`panel/` 与 `host/` 里不许有同名文件**：那样 `@mecha/dsh-panel/panel/x.ts` 会解析到 panel 那份，
消费者可能"以为自己 import 的是 host 那个"。⇒ **文件唯一命名域**。⚠ 但这**不是全序**：
`config.ts` 事实上唯一（只有 host 有），而**测试文件名在根与 panel 面重复**
（`monitor-client.test.ts` 等）——它们各自只被本目录引用，不构成歧义。
⇒ 这条不建判据（件很少、真重名会在 import 处立刻暴露），**但新件要放进它正确的那一半**。

### `panel/`（浏览器安全；host 与客户端都用）

| 文件 | 是什么 |
|---|---|
| `panel/panel-config.ts` | ⭐ **参数注入点**：`configurePanel({routePath, portFile, title, tabs?})` + `panelConfig()`（**未注入即抛**）。⚠ 两半都用它（host 注入面板参数也走这里）⇒ 住 panel 面（它本身浏览器安全），**不单开第三个入口**——那只会让两半又多一处要同步的地方 |
| `panel/routes.ts` | **浏览器安全的共享常量**：`DEFAULT_ROUTE_PATH` / `BASIC_ROUTES`（零 import） |
| `panel/monitor-client.ts` | 管道：同源取址 `fetchMonitorBase` + `readJson` + **错误档位** + **R8 非退化自证** + **`panelState` 归约（让空白不可表达）** |
| **`panel/panel-data.ts`** | **面板数据层**：`loadPanel`（含附加页取数）+ `stateText`/`stateBadge`/`modeOf`/`selfCheckLine` + `eventsOf`/`configOf` + `tabLabel` |
| **`panel/panel-view.ts`** | **面板视图层（纯字符串生成）**：`esc`/`fmtVal`/`recPageHtml`/`cfgPageHtml`/`extraPageHtml`/`tabsHtml`/`badgeHtml` + `TAB_CSS` |
| **`panel/MonitorTabBody.tsx`** | **壳**（~110 行）：轮询 + CSS 注入 + 事件委托 + 一次 `dangerouslySetInnerHTML`。**唯一需要 react 的地方** |
| `host/monitor-url.ts` | 地址路由：`resolveMonitorBase` / `sendJson` / `makeMonitorUrlHandler`。⚠ **node-only**（`node:fs` 读端口文件 + `node:path` + `node:http` 的类型）⇒ 归 host 面。**但它相对两个消费者都要用**：host 半注册路由用它，`monitor-client.ts` 的**注释**引用过它当年的缺陷；面板侧**运行时不 import 它** |
| `panel/index.ts` | **入口**（`exports` 的 `./panel`）：把上面这些重新导出一次，供"不带后缀"的写法 |

### `host/`（node-only；只有 host 半用）

| 文件 | 是什么 |
|---|---|
| `host/mount-host-plugin.ts` | ⭐ **插件骨架** `mountHostPlugin(...)`（见上文） |
| `host/register-tools.ts` | **工具注册**：`syncTools` / `buildDefinition` / `publicToolName`（两阶段 swap；**零 `@deepseek-ai/*`**，用结构化最小接口吃宿主 ctx） |
| `host/config.ts` | **端点解析**：`resolveHubUrl`（显式 URL > 端口文件 > 项目默认，**缺省不猜端口**）+ `buildRequestInit`；`node:fs/promises` |
| `host/mcp-session-http.ts` | ⭐ **唯一 import `@modelcontextprotocol/sdk` 的地方**：`httpSessionFactory`（每次建会话都拿**新** session） |
| `host/mcp-sdk-shims.d.ts` | **口径依赖**：MCP SDK 的最小**类型垫片**（纯 `declare`，运行时零依赖） |
| `host/index.ts` | **入口**（`exports` 的 `./host`） |

⚠ **`mcp-bridge.ts` 住根**：它是**纯逻辑**（零 import），两半都会用；放任何一半都会逼另一半
"穿过"边界去取它。⇒ **"纯"与"node-only"不是一回事**：`host/mcp-session-http.ts` 也**不碰 `node:`**
（只碰 SDK），结构上浏览器安全，但它只会把 SDK 拖进 bundle ⇒ **归 host 面**（这是设计选择，写在这里定死）。

| 测试 | 归哪一半 | 为什么 |
|---|---|---|
| `panel/*.test.ts` | **panel** | 按"**被测的件在哪一半**"归置，不按"用什么跑"——它们用 `node:test` 跑，但测的是浏览器安全的件 |
| `host/mount-host-plugin.test.ts` | **host** | 被测的件在 host |
| `mcp-bridge.test.ts` | **根** | 被测的件在根 |

## ⭐⭐ 谁能在浏览器里 import：**这条边界是硬约束**

> **client 半（浏览器 bundle）只许 import `@mecha/dsh-panel/panel` 下的东西，以及宿主注入的
> `react` / `react/jsx-runtime`。**
> **不许** import `../host/*`（那是 node-only 的一半），也不许从根上除桥以外的件取值。
> ⚠ **其它裸包名一律不许**。

**为什么把这条写成硬约束**（2026-09-26 的真实缺陷，原生消费者报回来的）：
`panel/monitor-client.ts` 原先从 `monitor-url.ts`（**当年**那个版本自己读文件、是 node-only 的）
取值导入 `BASIC_ROUTES`，于是**浏览器 bundle 里进了 `node:fs` ⇒ 加载即失败**
（`esbuild --bundle --platform=browser` 直接 `Could not resolve "node:fs"`）。

⚠ **另一个消费者当时没炸，是工具链的运气，不是资产的性质**（它的原话：
"**我的安全是『碰巧没用那几个导出』换来的，不是结构保证的**"）。实测：**同一个缺陷下，
用 esbuild 打一个"完全不用那条链"的入口，一样红**（`node:fs` / `node:path` 都解析不了）——
它当时之所以绿，是它的打包器**把常量内联、把整个模块摇掉了**。⇒ **不要依赖树摇**。

**这条边界现在有**两层**保证（⚠ 两层管的不是同一件事，别拿一层替另一层）**：

1. **入口层（2026-10-01 新增）**：`exports` 只发布 `./panel/*` 与 `./host/*` 两个面
   ⇒ 从 panel 入口**够不到** host（实测：`'@mecha/dsh-panel/panel/config.ts'` ⇒ **TS2307**）。
   代价是"**未来任何新文件意外成为公开面**"这类错误也没了（通配只剩面内两层，根上只有桥）。
2. **机械守卫（在）**：`panel/monitor-client.test.ts` 的**导入闭包检查**——从 client 半入口出发，
   沿**相对 import** 走一遍，断言闭包里**没有任何 `node:`**（类型导入不算，它会被剥掉），
   并**自证闭包真的走过了若干文件**。

⚠ **第 2 层不能因为有了第 1 层就删**：第 1 层管的是**发布出去的那条路**，而
`panel/x.ts` 里写 `import '../host/y.ts'` 是**相对路径**——`exports` 根本管不到它。
这正是"切分**减少了**这类错误、但**没有让它不可表达**"的地方，如实写在这里。
红证（两层各自可重放）：
- **入口层**：从 panel 入口 import host 件 ⇒ `TS2307`（实测见上文）；
- **第 2 层**：在 `panel/monitor-client.ts` 里加一句 `import … from '../host/monitor-url.ts'`
  ⇒ 该检查**必红**（实测：报"panel 面出现了向外的相对 import"，且**自证扰动真的落上了**）。
  ⚠ 这条**以前抓不到**：旧版本只跟 `./`，`../` 会被**静默忽略**（2026-10-01 修）。

## ⭐ 严格 tsconfig 下**必须能过**（可移植性，不是风格）

**资产要能在最严的消费者 tsconfig 下编译**——**不许让项目为它放松类型严格度**。
真实代价（2026-09-26）：`classifyRoute` 里的 `path.split('?')[0]` 在
**`noUncheckedIndexedAccess`** 下是 `string | undefined` ⇒ 报 **TS2345**；
**`exclude` 挡不住**（TypeScript 仍会检查被 import 到的文件，而 client 半必须 import 它）
⇒ 一个消费者**只能**在"改资产"与"关掉那个旗标"之间选，**它关了**（并在注释里写了恢复条件）。

**现在的状态（已实测）**：用消费者装的 `tsc` 跑
`--strict --noUncheckedIndexedAccess --allowImportingTsExtensions`（ESM + bundler 解析）
⇒ **0 错误**；把 `?? ''` 拿掉 ⇒ **复现 TS2345**（两向都自证过扰动落上）。

⭐ **框架自己现在也会跑类型检查**（`checks/dsh_panel_selfcheck.py` 的第二关）：
它**借接入项目装的那份 `tsc`**（`<项目>/dsh/node_modules/typescript`，缺即红，
与 `phase5_*` 硬编码 EL 仓根同族先例），对本目录**全部 `.ts` + `.tsx`**（数量以判据打印为准）跑
`--strict --noUncheckedIndexedAccess`。
⚠ **要看到这一关真的跑，必须先给 `tsc` 一个来源**：设
`$env:MECHA_TSC_ROOT='<某个接入项目的 dsh 目录（含 node_modules 的那一层）>'`。
**没设时它 SKIP**（外部依赖缺失只有一种语义），**容易让人误以为"类型检查默认在跑"**——
末行会写 `SKIP: 缺一份可用的 tsc…`，看到它就别当成绿。
**为什么必需**：`node --test` **只剥类型、不做检查** ⇒ **只在类型位置的错它一个都看不见**。
真实缺口（2026-09-26，第一个跑 `tsc` 的消费者抓到的）：
```
panel-data.ts(166,53): error TS2304: Cannot find name 'Ev'.
panel-data.ts(173,53): error TS2304: Cannot find name 'ConfigWire'.
```
`Ev`/`ConfigWire` 是 `panel/panel-view.ts` 的 export，而 `panel/panel-data.ts` 拿它们当返回类型时**漏了 import**
⇒ **自测全绿、消费者一跑就红**。⚠ 而这条守卫**第一次跑就抓到本判据作者自己的 5 处类型错**
（`panel-data.test.ts` 里 `extras.ok` 在严格模式下是 `… | undefined`）。
⇒ **消费者的 `npm run typecheck` 仍然是验收义务**（本判据是**底线**；消费者口径更严时它仍可能先红）。

**同类隐患的纪律**：**越界索引（`x[0]` / `m[1]` / `split(...)[0]` / `Record` 索引）一律写 `?? ''` 或先取出再断言**
（本目录已扫过：实现、单测各有数处，都已修）。

⚠ 一条**约束**（如实写）：**共享单测要求 ESM**——它用 `import.meta.url` 定位自身目录。
若某个消费者的 tsconfig 把 `src/panel/` 当 **CommonJS** 编译，会报 `TS1470`。
（两个消费者的 dsh 插件都是 ESM，实测无此问题；这里写清是因为它是**口径依赖**，不是资产缺陷。）

## ⭐ 依赖约定：**一个包、一份真值、没有副本**（2026-10-01 取代旧的「复制约定」）

**项目不再复制本目录**，而是把它当依赖装进来：

```jsonc
// 项目的 package.json
"dependencies": { "@mecha/dsh-panel": "file:../mecha/mecha/dsh-panel" }
```

`npm install` 之后，`node_modules/@mecha/dsh-panel` 是**指向本目录的 junction**
（实测：`LinkType = Junction`）⇒ **磁盘上只有一份代码**，改资产就是改所有项目用的那份。

**参数不再靠"改副本里的参数块"**：`panel/panel-config.ts` 现在是**注入点**，
项目在启动时调一次 `configurePanel({ routePath, portFile, title, tabs? })`。
⇒ **三个项目不可能再互相覆盖**：项目专有的值住在**项目自己**的代码里（那一次注入调用），
资产里**没有任何**项目值可改。

**旧「逐字副本 + 指纹表」整套随之作废**（连同它的全部代价）：

* 那个模型存在的唯一理由是"项目会拿到一份**可变**的拷贝" ⇒ 现在**没有拷贝**，
  比对的对象不存在了（**不是**"我们再也不想比对"，是**结构上无可比对**）；
* 因此**指纹表 / `REFERENCE_REVISION` / "重抄 N 个文件"全部删除**——它们守的
  "副本有没有漂"这件事，已被"单一副本"**结构性地消灭**；
* 旧模型还逼出过"消费者照文档填 `TABS` ⇒ 破坏共享判据"那类事故（共享自测 pass 41 / fail 1）：
  现在参数**本来就不在资产里**，共享单测自己注入 ⇒ **那类事故也没有了**。

## ⚠ 迁移时必须知道的**四**件事（实测）

1. **`exports` 支持带 `.ts` 后缀的子路径**：项目写 `@mecha/dsh-panel/mcp-bridge.ts`
   （沿用原来的写法）。实测：`--strict --noUncheckedIndexedAccess` +
   `moduleResolution: bundler` + `allowImportingTsExtensions` ⇒ **0 错**。
2. ⭐ **tsconfig 必须开 `preserveSymlinks: true`**（否则经 junction 时，
   TypeScript 会用**资产的真实路径**去找依赖 ⇒ 找不到装在**项目**里的
   `@modelcontextprotocol/sdk`，报 `TS2307`；实测：加上它 ⇒ **0 错**）。
   原因：TS 的资源解析是 **Windows 原生**的（不跟随 junction），与本包无关，
   但**必须由消费者显式开启**——写在这里，免得下一个人以为资产坏了。
3. ⭐⭐ **运行期绝不要给 `node` 加 `--preserve-symlinks`**（它是第 2 条的**反面陷阱**）：
   它让模块标识**停在 `node_modules/@mecha/…`**，而 Node ≥ 22.6 的原生剥类型
   **拒绝处理 `node_modules` 下的 `.ts`** ⇒ **`ERR_UNSUPPORTED_NODE_MODULES_TYPE_STRIPPING`**
   （资产全是 `.ts`，于是**一条也跑不起来**）。
   ⇒ **只有 `tsconfig` 那一侧**该开 `preserveSymlinks`（**类型检查期**）；
   项目的 `npm test` 用**裸跑** `node --test …`，**不需要 `tsx`**。
   🔬 **本仓自测（消费者形状，经 junction 用包名 import）**：
   ```
   裸跑                     ⇒ ℹ tests 1 / pass 1 / fail 0
   --preserve-symlinks      ⇒ ERR_UNSUPPORTED_NODE_MODULES_TYPE_STRIPPING
                              （… node_modules/@mecha/dsh-panel/mcp-bridge.ts）
   ```
   ⚠ 它与 `tsx` 是**同一权宜之计的两半**：当初正是为了绕这个旗标才不得不装 `tsx`
   ⇒ **两样一起去掉才干净**（三家实测都中过、现已全清）。
   ⚠ 这条**只有真在消费者位置上才复现**：本仓自己 `node --test mecha/dsh-panel/*.test.ts`
   用的是相对路径、不走 junction，加了旗标照样绿——**别拿仓内绿来否证它**。
4. ⭐⭐ **框架仓必须装它自己的 node 侧依赖**（`mecha/package.json` 的 `@modelcontextprotocol/sdk`
   / `react`）。**这是 2026-10-02 三家同时踩的坑**，机制一句话：

   > **Node 找包，是从【文件真实所在目录】往上翻 `node_modules`。**
   > 资产经 junction 被依赖时，资产里的 `import '@modelcontextprotocol/sdk'`
   > **是按【框架仓】那条路径去翻的** —— 而项目自己 `node_modules` 里那份**它看不见**。

   ⇒ 实测：**框架仓根**下没有 `node_modules` 时，**三家全部**在
   `host/mcp-session-http.ts` 上报 `ERR_MODULE_NOT_FOUND`（桥的会话实现加载不了 ⇒ 插件不激活）。
   **EL / GP / ML 都中，一个不落** —— 因为三家的桥都走骨架默认路径。
   ⇒ 修法：在**框架仓根**放 `package.json` 并 `npm install`（装出来的在框架仓根 `node_modules`）。

   ⚠⚠ **别把它当成第 2 条（`preserveSymlinks`）的重复**：第 2 条是**类型检查期**的
   （不加就 `TS2307`），这条是**运行期**的（不加就 `ERR_MODULE_NOT_FOUND`）。
   第 2 条能"让 tsc 看见项目里的 SDK"⇒ **它恰好把这个运行期问题掩盖住了**，
   于是 `tsc` 全绿、到运行期才炸 —— **这是本条最值得记的地方**。

   ⭐ **由此得一条通用规则**（以后资产里加任何 import 都按它判）：
   **资产里的代码只能用【它自己解析得到】的依赖** —— 也就是由框架仓提供的那些。
   项目侧的依赖（含项目自己装的同一个包）**对资产不可见**，别指望。

## ⚠ 仍然有效的两条纪律（与旧模型无关，别一起删）

* ⭐ **R17：每个注入点都要配一条「换掉它、结果就变」的用例**。
  这种"看起来能注入、其实注入不进去"的缝（尤其被 `_` 前缀标成"故意不用"的形参）
  会在 review 里过关。**资产自己犯过一次**（`fetchMonitorBase` 的第二个形参曾是死的、零覆盖）；
  旧模型下它是"三份副本一起错"，现在它**只错一次、也只修一次**。
* ⭐ **"共享默认"要用「不注入那一项」来测，不许用「假定项目没注入」来猜**：
  共享单测写死中性默认（`tabLabel('rec') === '飞行记录仪'`）时，**任何注入了 `tabs` 的项目
  都会让共享自测变红**（真实发生：某项目填 `{rec: …}` ⇒ 共享自测 **pass 41 / fail 1**）。
  ⇒ 正确做法：**不注入 `tabs`** 测默认那一半，注入了另测（本目录已这么改）。
  ⚠ 这顺带**删掉了一处脚手架**：旧写法要 `delete PANEL_CONFIG.TABS` 再恢复
  （在改**共享可变状态**）；现在 `tabs` 在类型上就是可选的，**不需要任何清空动作**。

⚠ **不要改共享单测**。项目要加"自家历史默认端口"这类**反面语料**，请**另建一个
项目自己的测试文件**（共享单测里不许出现任何项目字面量）。

## ⭐ 面板形态：**原生页面板是默认，也是唯一推荐**

**资产现在自带面板**（`panel/MonitorTabBody.tsx` + `panel/panel-view.ts` + `panel/panel-data.ts`）⇒ 项目不用再自己渲染：

| 形态 | 判断 | 说明 |
|---|---|---|
| ⭐ **原生页面板（资产自带）** | **默认、推荐** | 项目只需：填参数块 + 声明附加页 + **一处挂载**。取数、错误档位、自证、布局、文案全在资产里 |
| **iframe / 两跳**（挂一个内层页） | **例外**，需说明理由 | 只有当"内层是项目自己的整页"时才合理。它**拿不到**四基础路由的取数结果 ⇒ **`panelState` 用不上**（硬套只会恒落 `empty` 档：那是**误用，不是覆盖**）、R8 自证也打折；只能靠"挂之前探一次内层可达"兜住（见下） |

⚠ **`panelState` 的适用条件**（写清，防误用）：**只适合"自己做取数的原生页面板"**。
iframe 形态的状态输入是**另一组**（地址 + 内层可达 + 视图 + 容器是否在）。
**但模式可以照搬**：**可移植的是「判别联合 + 没有空白档 + `assertNever` 兜底」这个模式，
不是那个函数**——已有一个 iframe 消费者自己写了个渲染层判别联合（`ready | error`，
明写"**空白不是一种状态**"）并明确拒用 `panelState`。
⇒ **不要**把 `panelState` 的签名改成"什么形态都能塞"（R1 / R17）。

## ⚠ 仍然归**项目**的两件事（资产结构上覆盖不了，**不许假装它覆盖了**）

> ⚠ **2026-09-26 更正**：本节原写"资产不含 DOM、不含渲染、**渲染与文案归项目**"——
> 那句话**已作废**（面板与文案现在**都在资产里**）。**仍然归项目的只有下面两件**：

1. ⭐ **挂载**：资产给的是**组件**；在哪个右栏容器里挂、什么时候挂、容器找不到怎么办，
   **都是项目的**。⚠ **挂载失败必须可见**：找不到容器/挂载点时**禁止静默 `return`**
   （那会渲染出一片空白且不抛错，"面板打不开"与"面板没数据"从此无法区分）；
   **必须**呈现成可读文本，或至少抛/记录到能看见的地方。
2. **面板形态的选择**（原生 vs iframe）与**它带来的后果**（见上一节）。
3. **两跳（iframe）必须可诊断**：若你选了 iframe，内层服务的失败要能被外层看见——
   不许出现"外层 200、内层空白"这种**静默空白**。
   ⚠ 本条与 `panelState` 的 `empty` 档是**两件事**：`empty` 管**数据**层，"外层 200 内层空白"
   是**渲染/挂载**层；**两层都要防**。
   **参考形状**（**约定，不是代码**——它的路径与语义是项目值）：**挂之前先探一次内层可达**：
   对**你项目自己的健康端点**发一个 **`no-cors`** 请求（`no-cors` 下读不到状态与正文，
   所以它只回答"**那个地址上有人讲 HTTP 吗**"），**绝不抛**（异常一律折成"不可达"），
   探不到就**渲染可读错误**，而不是挂一个注定空白的 iframe。
   ⚠ **诚实边界**（别假装覆盖）：它**不能证明内层页渲染出了内容** ⇒"内层页自己永远不空白"
   那一条由**内层页**负责（缺数据时如实渲染"未接监控面"这类文本，而不是空白）。

> 一句话：**资产的 `panelState` 让"没数据"无法伪装成成功；"挂载"这一层靠项目兜住**
> （渲染已经进资产，不再归项目）。

## ⭐ 项目的附加页：**声明式列表**（不是插件/hook 框架）

三家都可能有**自己的附加数据面**（框架的 `MonitorEndpoint` 本就留了 `extra_routes` 注入缝），
共享面板用一个**声明**来渲染它：

```ts
// 项目自己的 client 代码里（领域词留在项目）：
<MonitorTabBody extraPages={[{
  id: '…', label: '…', path: '/…?limit=50', title: '…',
  columns: [{ label: '…', cell: (row) => … }],
  emptyText: '…', unreadableText: '…', summaryLine: (payload) => …, note: '…',
}]} />
```

**数据契约（共享面板定 wire 形状）**：附加路由**必须**回 `{ rows: [...] }`——
表格行取 `payload.rows`，其余键由 `summaryLine` 自己解释（它拿到整个 payload）。
⇒ 这样 spec **不需要"行提取器"字段**。

**字段冻结在 8 个**（`id` / `label` / `path` / `title` / `columns` / `emptyText` /
`unreadableText` / `summaryLine` / `note` 中的可选那两个不计入冻结的"必须"）。
⚠ **要加字段，先有第二个页面型消费者**；否则就是为一个消费者把 spec 撑大。

⚠ **三条边界**：
1. **`emptyText` 与 `unreadableText` 必须分开**：空表与"读不到"是两件事，判据要能区分；
2. **附加页的失败只影响那一页**：标准两页照常（所以 `loadPanel` 把附加页结果**单独**返回，
   **不塞进** `panelState` 的 `extra`——那会把整个面板打成 `route` 档，吃掉本来好的两页）；
3. **资产里零路由字面量**：`path` 由项目给（词表守卫在扫 `.ts`/`.tsx`）。

## 一句**判据**（给插件的作者，不是给面板）：`inject` 怎么写

**各项目自决**，但判据是这一句：
> **若你的插件还提供 web 服务之外的入口（AI 工具 / 命令 / 别的能力），
> 就别把 `webServer` 写成顶层硬 `inject`** —— 顶层 `inject` 缺一个服务会让**整个插件不激活**，
> 那会把"**面板没有 web 服务**"升级成"**AI 工具也没了**"。用**作用域注入**
> （只把**路由注册**包在里面），让面板缺服务不影响插件其余能力。

（真实差异：三家当前的 `inject` 形态**各不相同**，且**都合理**——单服务的插件硬 `inject`
就对；多服务的插件才需要作用域注入。**这是插件架构的事，不是面板的事**：
不要借"迁移面板"顺手改掉某项目的插件激活语义。）

## 纪律：参考实现里**不许出现任何项目字面量**

`checks/vocabulary_selfcheck.py` 已把 `.ts` / `.tsx` / `.md` 纳入扫描面，词表末节列了
**三个项目的插件名与各自的端口文件名** ⇒ **写进参考实现就红**（连本 README 也不例外：
它自己就是被扫的资产之一）。
这是"参数块必须是唯一差异"的另一半保证（前一半是逐字比对）。

## 怎么跑判据（**零 npm 依赖**）

```powershell
node --test mecha/dsh-panel/*.test.ts
```
Node ≥ 22.6 **原生剥类型**，不需要 `tsx`、不需要 `npm install`（老版本加
`--experimental-strip-types`）。测试用 `node:test` + `node:assert/strict`，只用内建模块。

⚠ **"零 npm 依赖"只针对这份自测，别外推**：**插件本身**要 `@modelcontextprotocol/sdk`
与 `react`，由**框架仓**提供（见「依赖口径」）——那是运行期的事，跑自测用不上。

⭐ **框架已经替你跑它**：`checks/dsh_panel_selfcheck.py` 已挂进 `checks/run_all.py`
⇒ **mecha 的完整门禁现在是硬依赖 `node` 的**（缺 node **即红**，不软跳过——软跳过就是
"存在但从不执行"的假绿）。**为什么必须挂（R10）**：这份资产是**三份副本的源头**，框架
自己不跑它的自测 ⇒ 参考实现写错了，**三份副本一起错**，而"逐字一致"那条判据还会
**强制**那个错。

## R1 的诚实立场（**这一句是刻意的**）

> **框架仓里没有运行时读取方**：它的消费者是**三个项目的插件源码**（它们**经 junction 指向本目录**，
> 2026-10-01 起不再持有副本），而**新鲜度判据不算消费者**（本仓 D-29 同口径）。⇒ 若你要求
> "这份资产必须有运行时读取方"，**那它就不该进框架**。本资产进 mecha 的正当性来自
> **三个真实项目**（n=3），**不来自它的判据**。
> ⚠ 注意这句的**作用域**（R3）：它说的是"**框架自己**不会在运行时读它"——
> **不是**"没有人在用这份面板"。**三家都在用**（见下一节的实测）。

因此**不要**为它造运行时消费者，也不要把它做成 Python 的 extra/依赖（它是 TS，没有任何
Python 代码 import 它）。它随包分发的方式是 `pyproject.toml` 的 `package-data` 声明。

⚠ **但要与"框架仓根装 node 依赖"分清**（2026-10-02）：那条是给**资产自己**解析依赖用的
（`mecha/package.json` 的 `devDependencies`，见「依赖口径」），
**不是"把它做成 Python 依赖"**，也不进 wheel。

## ⚠ R1 的**灰区**：两半的消费者状态**不同**（**已于 2026-10-01 结清**）

2026-09-26（Get Paper 迁移落地后）实测的调用点分布：

| 半边 | 今天的消费者 | 状态 |
|---|---|---|
| **地址半边**（`host/monitor-url.ts` + `fetchMonitorBase` + `panel-config` + `assertNever`） | **有**：Get Paper 的 iframe 面板（真调用点已在生产路径上） | ✅ 今天就在用 |
| **数据半边**（`readJson` + `panelState` + `monitorStats`/`isNonDegenerate`/`statsLine`） | **有**：**EL 与 ML 的原生面板** | ✅ 今天就在用（**2026-10-01 结清**） |

**结清记录（2026-10-01，可复现）**：本节原写"数据半边 **0** 消费者，消费者是 EL/ML 的原生面板、
**只是迁移尚未执行**"，并附条件"若 EL/ML 的迁移被取消，数据半边随之撤出"。
实测：**迁移已执行完毕**，EL 与 ML 的 `panel/panel-data.ts` 都从**共享模块**取数据半边——
`import { … isNonDegenerate, panelState, readJson, statsLine … } from './monitor-client.ts'`，
并在 `loadPanel()` 里真的调用（`readJson` ×2 + `panelState` ×1）。
> 复现：在 EL / ML 两仓的 `dsh/src/dsh-panel/panel-data.ts` 里看同一个 `import`
> （两份**逐字节相同**，与参考实现同指纹）；行尾归一的 sha256 取 `panel/panel-data.ts` 即得。
> ⚠ **不写绝对路径**（R14/个人路径守卫：本机路径不许进仓）——按**符号名**找。
> ⚠ **行号不写在这里**（D-26：行号是全库最易漂的东西）——按**符号名**找。

⇒ **"今天的消费者 0"不成立**；**"若迁移被取消则撤出"这个条件已以相反方向结清**
（迁移完成了 ⇒ **保留**）。**不是删掉它、也不是假装它在用，而是把状态写清楚。**

## 三条设计点（为何这样写）

1. ⭐ **绝不回落**：端口文件读不到 / 内容非数字 / 越界 ⇒ **抛错**，路由回 `503` + 可读错误。
   回落会把"权威没起来"显示成"**连上了但空面板**"——本工程反复在防的假绿。
   **同一条纪律也适用于端口文件名**：所以 `portFile` 是**必填参数**，库**不提供默认文件名**
   （猜错名字是同一个病上一层）。
2. **每次调用现读**：端口漂移后下一拍就落在新端口上，不需要重启 dsh；本层**不缓存**。
3. **三档失败可区分**：`address`（地址拿不到）/ `offline`（**基础路由**失败 = 权威离线）/
   `route`（**附加路由**失败）。**档位共享、文案归项目**。把附加路由失败混进"权威离线"，
   会让真因被吃掉——`displayTier()` 让"塌缩成离线"成为一个**显式动作**，而不是就地混同。
4. ⭐ **`panelState` 让"面板空白"不可表达**：归约成**判别联合**
   （`ok` / `address` / `offline` / `route` / `empty`），**每个分支都带 `detail` 或数据**
   ⇒ 渲染方**没有"什么都不画"的档**；漏分支时 `assertNever` 在运行侧**抛**
   （跑了类型检查的项目里还会**编译不过**）。
   其中 **`empty` 档是刻意加的**：它堵的是"**连上了、也 200、但一点数据都没有**"——
   那是 R8 那一类**最难抓**的假绿（**有渲染，但内容是空的**）。
   ⚠ 它管的是**数据层**；"外层 200、内层空白"那类**渲染/挂载层**的空白靠上文的项目侧义务。

## 谁在用它 / 迁移顺序（不在本目录）

**顺序：EL → ML → Get Paper**（一个同步点，各自只抄一次）。
**进度（2026-10-01 更新）：资产已改成可依赖的包，三家迁移待做（顺序不变）。**
（2026-09-26 旧述写"三家都要重抄一次"——那次重抄已发生；**现在要做的是相反的**：
**删掉那些副本**，改为依赖本包。下面那张表就是各家旧副本的落点。）

| 序 | 谁 | 旧副本落点（**待删**） | 迁移内容 | 备注 |
|---|---|---|---|---|
| 1 | **EL** | `dsh/src/dsh-panel/` | **删掉该副本** ⇒ 装 `@mecha/dsh-panel`（junction）+ 把 `import '../dsh-panel/…'` 改成 `@mecha/dsh-panel/…` + 用它自己的值调一次 `configurePanel(...)` | 它是**源**（用户裁"就几乎完全复用 EL"）。⚠ **验收项**：迁移后「飞行记录仪」页**在有事件时必须显示事件行**（见下） |
| 2 | **ML** | `dsh/src/dsh-panel/` | 同上；**声明式附加页**保留它的"运行记录"能力 | ⚠ **行为变更（必须申报）**：① 由**一页并列**改成**两页**；② 附加路由按**共享 wire 契约**回 `{ rows: [...] }`；③ 它那份 `mltb-` CSS 丢弃（取资产的布局）。⚠ 它**不用桥**，所以只迁默认件 |
| 3 | **GP** | `dsh/src/panel/`（⭐ 目录名与其他两家**不同**） | 同上（含桥） | 它的 `/settings` 是**它自己 Web 应用**的页，**不进**这套机制 |

⚠ **"重抄 + 指纹表"整套已作废**（见「依赖约定」）：迁移现在是**删副本 + 装依赖**
+ 改 import + 加一次 `configurePanel(...)`。各家**不再需要维护指纹表**——
那份表的唯一职责（发现副本漂移）已被"单一副本"结构性地消灭。

⚠ **每一家都要**（迁移清单）：
1. `"@mecha/dsh-panel": "file:<mecha 仓>/mecha/dsh-panel"` 装进来；
2. tsconfig 开 **`preserveSymlinks: true`**（否则经 junction 找不到 SDK，见「迁移时必须知道的**四**件事」第 2 条）；
   ⚠ **但只开这一处**：**别给 `node` 加 `--preserve-symlinks`**（那是反面陷阱 ⇒
   `ERR_UNSUPPORTED_NODE_MODULES_TYPE_STRIPPING`；同时**删掉 `tsx`**，见第 3 条）；
3. 各处 import 改包名（**保留 `.ts` 后缀**）；
4. 启动时调一次 `configurePanel(...)`（值就是原来写在参数块里的那三个）；
5. 删掉旧副本目录 + 旧指纹判据；
6. 跑自己的 **`npm run typecheck`**（`.tsx` 的验收义务）+ **重建**（`lib/` 陈旧那条陷阱）；
7. ⭐⭐ **确认框架仓装了 node 侧依赖**（`<mecha 仓>/node_modules` 里有
   `@modelcontextprotocol/sdk` 与 `react`）。**这一条不在项目侧做** ——
   它是框架仓的一次性安装（`npm install` 于框架仓根，见第 4 条）。
   ⚠ 漏了它的症状：`npm run typecheck` **照样全绿**，而 dsh 里插件**加载失败**
   （`ERR_MODULE_NOT_FOUND`，指向框架仓的真实路径）—— **2026-10-02 三家同时中招。**

⭐ **"并入"的直接收益（实例，值得记一笔）**：搬 EL 那份面板时抓到它一个**用户可见 bug**——
组件用**请求路径**（带 `?since_seq=0`）去索引按**基础路由键**存的 payloads ⇒
**「飞行记录仪」页永远"暂无写事件"，而自证行仍报"事件行=N"**（它自己的注释警告过同一个坑，
但只修了 stats 那一侧 ⇒ **"修了一处"不等于"修了这一类"**）。
这个 bug 只有在**"把面板搬进资产、被逐档单测 + 反面语料钉住"**时才暴露。
⇒ **"并入"本身就是一次最强的审查**；这也是"面板该共用"的最硬证据（比任何论证都硬）。

⭐ **"消费者找到框架门禁找不到的东西"已有三次**（都是**不同**的层）：
1. **可移植性**：EL 撞上 `noUncheckedIndexedAccess` 下的 TS2345（框架当时只跑 `node --test`）；
2. **判据更严**：某消费者的判据显式断言 `skipped 0`，比框架那条严 ⇒ 反过来当了规格；
3. **类型位置**：某消费者跑 `tsc` 抓到资产漏 `import type { ConfigWire, Ev }`（自测全绿）。
⇒ 这三次都是"**框架的判据没走到那条路径**"（R10），也是"为什么必须有真消费者"的证据。

## 出包实测（2026-10-01 复验 · **已验证**）

`pip wheel . -w %TEMP%\… --no-deps` 构建成功（`mecha-0.2.0-py3-none-any.whl`），
**用 `zipfile` 逐名核对**（不是肉眼看输出）：wheel 里**确实含**本目录全部 **19** 个文件
（含 `package.json`），**无缺、无多** ⇒ `pyproject.toml` 的 `package-data` 声明**经实测成立**。
（D-24：这条只有真出包才能证，读代码看不出来。）
⚠ ⭐ **2026-10-01 又抓到第三次同款**：`package.json` **不会**被 `*.ts` / `*.tsx` / `*.md` 匹配
⇒ 不加 `dsh-panel/*.json` 的话，**pip 装出来的 mecha 里没有它**，消费侧
`npm install file:<...>/mecha/dsh-panel` **直接装不上**（方案级失败）。已加并复验。
⚠ 这条核对**此前的两次**：① 一次探针把**真仓目录**当草稿目录，往里写了两个入口
文件，轮子清单当场从 7 变成 **9**；② ⭐ **`*.ts` 不匹配 `.tsx`** ⇒ 面板壳曾经**不会随包
发货**（`package-data` 里没单列 `.tsx`）——**三次都是"另一层"抓到的**。
⚠ **不写"现在共 N 个文件"这类手写计数**（L8）：以**判据打印的**文件数为准。
⇒ **模式清单的结论**：`package-data` 现在显式列了 `.ts` / `.tsx` / `.md` / `.json` 四种；
**将来若加第五种扩展名（例如 `.css`），必须同步加一条**——这是唯一会被漏的地方，
而**出包核验是唯一能发现它的动作**（不为它建机制）。

⚠ **实测顺带抓到一条**：`pip wheel` 会在**仓内**留下 `build/`（`build/lib/mecha/…`）。
它是 **gitignore 的**（`.gitignore:11`）⇒ **`git status` 看不见它**——"仓内无残留"若只按
`git status` 判，就是**假绿**。本工程已被 gitignore 骗过两次，**这是第三次，而且发生在
我们自己的验收动作里** ⇒ 结论：**核验残留必须直接看文件系统**。
本轮已清掉 `build/`；`mecha.egg-info/`（先前就存在、同样被 ignore）被构建刷新了内容，
属无害缓存，保留。

另：本地缺 `wheel` 包时，`--no-build-isolation` 会以 `invalid command 'bdist_wheel'`
失败——那是**环境缺口**，不是 `pyproject` 的问题（用默认的构建隔离即可）。

## 仍未验证（明确不宣称）

* ⭐ **`.tsx` 壳不在框架门禁的执行面里**：`node:test` 渲染不了 react ⇒ 壳（`panel/MonitorTabBody.tsx`）
  **只过 typecheck、不被执行**。它的代价由三件事兜住：
  1. **壳保持薄**（~110 行）——**这是代价可控的前提**；壳一旦长出决策逻辑，就要搬回 `.ts` 层；
  2. **消费者各自的 `npm run typecheck` 是验收义务**（不是建议）——文件被复制进 `src/`，
     `@types/react` 在他们的 devDeps 里；
  3. 全部**决策与 HTML 生成**都在 `.ts` 层，被 `node:test` 逐档覆盖（"面板不空白"是**字符串级**可测性质）。
* **参考实现本身**没有在任何真实 dsh 实例里跑过——我这边只有 **Node 单测 + 出包实测**。
  ⚠ 与之相对：**它的一个逐字副本已在某个项目的真 dsh 里实测过**
  （503 / 200 / 端口漂移 / 404 + 无进程孤儿，由该项目实测）——**那是有价值的间接证据，
  但不等于本目录的实现被直接验证**（不能说明"本目录这份源码与那份副本之间没有别的差异"）。

## 框架自己的门禁**真的在跑**这份自测（自证）

`checks/dsh_panel_selfcheck.py` 已挂进 `checks/run_all.py`（硬依赖 `node`，缺即红），
并且它**自证"用例真的跑了"**：解析 `node --test` 的**四个**计数
（`tests / pass / fail / skipped`），要求 **`skipped == 0` 且 `pass > 0`**。
⇒ `run_all` 的通过行会打出 `资产自测 21/21，skipped=0`；**"全被跳过"是红**，不是绿。
（这条自证本身来自一次实测缺陷：本判据第一版**漏了 `skipped`**，把"全 skip"判成了 PASS——
详见 `checks/dsh_panel_selfcheck.py` 模块 docstring 里的 R16 实例。）
