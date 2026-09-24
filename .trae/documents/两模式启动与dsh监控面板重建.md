# 两模式启动 + dsh 监控面板重建

## Context

用户报告 dsh 升级后"驾驶室按钮没了"（实情：EL 仓 `e366de8` v1 净化时主动退役了 `dsh/src/client/`，与升级时间重合），并要求把使用性收拢成一个简单心智模型：

- **写权不搞三按钮 UI**——机制在内部保留（库的 Mode.LOCKED 不动），用户面只有两模式：**AI 模式**（写权自动在 AI）与 **GUI 模式**（写权自动在人）。
- **AI 模式** = headless 权威进程（不起 GUI 窗口）+ dsh 前台；dsh 界面恢复驾驶室按钮 + 侧边栏监控面板，驾驶室按钮旁加"切换至 GUI"按钮 → 按下才创建 GUI、关闭 dsh。
- **GUI 模式** = 纯本地软件，**不起 MCP/监控端点**（AI 物理看不见），无 AI 监视 dock；留一个"切换至 AI 模式"入口。
- 用户已拍板：①交接由**常驻看门人**编排；②GUI 模式完全断开 AI；③8767 **网页控制台砍掉**（只留接口给 dsh 面板轮询）；④全员禁写 UI 移除、机制保留。

## 目标形态（一张图）

```
能级.py ──▶ 看门人 launcher.py（常驻，无窗口，日志进控制台）
              │ 弹窗选模式（人=决策者，看门人代理人的切换意图）
              ├─ GUI 模式 ──▶ python app_entry.py --gui（纯 GUI，写权 HUMAN，无端点）
              └─ AI 模式 ──▶ python app_entry.py --authority（headless 权威，写权 AI，
                             MCP 端点 .mcp-port + 监控端点 :8767）
                          └─▶ dsh web --patch …（前台，驾驶室按钮+监控面板+切换按钮）

切换：任一进程写 <EL根>/.mode-request（内容 "gui"/"ai"）→ 看门人轮询发现
     → terminate 当前进程组（journal 写租约随进程释放）→ 按新模式 spawn → 删请求文件
```

## 实施批次（L9 一件一提交，EL 仓 + mecha 仓 + dsh 插件）

### 批 1：EL 权威 headless 化 + 看门人（EL 仓）

- `app_entry.py`：
  - 新增 `run_authority()`：`make_v2_bridge(PROJ_ROOT)`（start_services=True）→ `bridge.switch_mode(Mode.AI, actor='human')`（看门人代人的启动意图，ADR 记语义）→ 阻塞（`threading.Event().wait()`），Ctrl+C 退出。**不建 QApplication/MainWindow**。
  - `main()` 加 `--authority` 分支；**删 `run_ai()`**（职能并入看门人）。
  - `_choose_mode()` 弹窗文案改为两模式心智（"AI 模式：写权在 AI，你在 dsh 里监控/切回；GUI 模式：写权在人，AI 看不见这台软件"），删三态描述。
- 新建 `launcher.py`（EL 根）：
  - QApplication（仅弹窗用，无窗口）→ 选模式 → spawn 进程组 → 轮询 `.mode-request`（0.5s）→ 交接（terminate→wait 退出→spawn 新模式→删文件）→ 常驻直到用户 Ctrl+C/关控制台。
  - GUI→AI 交接时同时 spawn dsh（复用现 run_ai() 里的 dsh 启动逻辑：`_pick_dsh_port`、两个 patch、npm install 预检、等 `.mcp-port`）。
  - 模式状态文件 `.mode-state`（当前模式，供人看/判据查）。
- 判据：新建 `checks/launcher_selfcheck.py`（纯逻辑测状态机：请求文件→决策→mock 进程编排，不起真进程）；`gui_app_entry_selfcheck.py` 补 `--authority` 装配断言（无窗口、写权 AI、端点起）。

### 批 2：EL 监控端点收缩（EL 仓 `src/v2_bind.py`）

- 删 `_CONTROL_HTML` 网页、`GET /`、`POST /switch_mode`、`POST /write`、`POST /write_many`、token 机制（`.human-token`、`RE0_HUMAN_TOKEN`）。
- 保留/新增：`GET /status`（mode）+ **`GET /activity?since_seq=N`**（包装 `bridge.activity(since)`，返回 `{kind,actor,target,value,seq,call_id}` 事件流）——loopback-only 只读，鉴权随 POST 一起退役（读泄漏=操作流水，可接受；旧 :8766 同样无鉴权）。
- 函数改名 `start_human_control` → `start_monitor_endpoint`（语义：只读监控面，不再是控制面）。
- 判据：`human_control_selfcheck.py` 改测新面（/activity 事件流形状、since 增量、无 POST 路由 404）；`authority_selfcheck.py` 退役三按钮 UI 断言、保留 watch_mode 机制断言。

### 批 3：GUI 面简化（EL 仓）

- `monitor_panel.py`：**不再 attach**（GUI 模式无 AI、AI 模式无 GUI——两模式下这面板都没有存在场景）。文件删除；`attach_monitor_dock` 调用点（`app_entry.py:114-115`）删。
- `app_entry.py` 菜单栏加"切换至 AI 模式"按钮（corner widget 或文件菜单）→ 写 `.mode-request` = "ai" + 提示"即将交接，本次会话状态已入账本"。
- `_mecha_bind.Binder` 的 `set_readonly` 路径保留（机制在库/桥里，GUI 模式恒 human 不触发）。
- 判据：`gui_bind_selfcheck.py` 只读门控断言改 in-process 直调 `bridge.switch_mode`（不经 UI 按钮）。

### 批 4：dsh 插件 client 半重建（EL 仓 `dsh/`）

参考 `git show e366de8~1:dsh/src/client/` 的注册形状，全部改接 v2 面：

- `src/client/index.ts`：`inject=['slots','layout','sidebarRight','sidebarRightTabs']`；
  - 驾驶室按钮：`ctx.slots.inject('conversation.session.header.actions', …)`（id `re0-cockpit`，order 200）→ 点击 `openRightbar` + `openTab('re0-cockpit')`（旧交互原样恢复）。
  - **切换至 GUI 按钮**：同 slot 再注册一个（id `re0-to-gui`）→ `ctx.remote.commands.execute(sessionId, '/re0-gui', [])`（ui-plan 先例：client 不能调 MCP 工具，slash command 到 host）。
  - 右侧栏 tab：`sidebarRightTabs.register` + `sidebar.right.pane.tab` 注册 `MonitorTabBody`——3s 轮询 `GET http://127.0.0.1:8767/activity?since_seq=`（增量拉事件流，渲染"谁/何时/改了什么"列表 + mode 徽标）。
- `src/host/`：注册 slash command `/re0-gui` → 写 `<EL根>/.mode-request` = "gui"（EL 根从 `.mcp-port` 所在目录解析，host 是 node 可写文件）；回执让 dsh 提示"即将切换"。
- `package.json` 恢复 `"./client"` 导出 + `"dsh":{"client":{...}}` 声明；`tsdown.config.ts` 恢复 client 入口块（cjs/browser/banner 包 ModuleLoader；注意 0.1.7 升级报告提过的 deps.alwaysBundle 正则坑）。
- 验证：`npm run typecheck` + `npm run bundle` 出双产物；dsh 加载冒烟（活体交用户）。

### 批 5：文档 + ADR + 记忆（mecha 仓）

- 新 ADR（implemented）：`两模式启动-看门人交接与写权自动落位`——含"看门人代理人的启动意图切 AI 写权"语义合法化、:8767 降级为只读监控面、三态 UI 收两态的裁决记录。
- `docs/v2/架构详解.md` 更新 §10 双入口数据流 → 看门人形态；`docs/v2/notes/implemented/` 对应改写权 UI 面 note（被取代关系按 ADR 制度第 3 条继承 rationale）。
- mecha 库侧**无代码改动**（Mode 三态、switch_mode、activity、watch_mode 全保留）。

## 关键文件

| 仓 | 文件 | 动作 |
|---|---|---|
| EL | `launcher.py` | 新建（看门人） |
| EL | `app_entry.py` | run_authority 新增、run_ai 删、弹窗改、GUI 切 AI 按钮 |
| EL | `src/v2_bind.py` | 端点收缩为只读监控 |
| EL | `src/ui/monitor_panel.py` | 删除 |
| EL | `dsh/src/client/*` | 重建（接 v2 面） |
| EL | `dsh/src/host/` | /re0-gui command |
| EL | `dsh/package.json`、`tsdown.config.ts` | client 入口恢复 |
| EL | `checks/` | launcher 新建 + 3 个改 |
| mecha | `docs/` | ADR + 详解更新 |

## 验证

1. **判据**：EL 全档 checks（含新 launcher_selfcheck、改后 human_control/authority/gui_bind/gui_app_entry）；mecha `python checks/run_all.py` 全绿（库无改动，防回归）。
2. **dsh 插件**：`npm run typecheck` 零错、`npm run bundle` 双产物。
3. **活体（交用户）**：`python launcher.py` → 选 AI → dsh 里驾驶室按钮/监控面板/切换按钮三件套可用 → 点"切换至 GUI" → GUI 起、dsh 关、journal 连续（seq 不断档）→ GUI 里点"切换至 AI" → 回到 AI 模式。

## 风险与对策

- **写租约交接窗口**：看门人 terminate 后必须等进程真退出（租约释放）再 spawn，否则新权威被 `writer_lease_held` 拒——launcher_selfcheck 测这个时序决策。
- **journal 连续性**：两模式共用 `.mecha-v2` 数据根，append-only + 单写者已保证；切换弹窗文案承诺"状态入账本"。
- **0.1.7 client 加载链未逐行核对**（探索结论：同版本内置包仍用同 API，风险低）——bundle 后 dsh 加载冒烟即证伪。
- **run_ai() 退役**：其 dsh 启动逻辑整体搬进 launcher.py，不重写。
