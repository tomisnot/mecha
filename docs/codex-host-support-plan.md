# mecha 对 Codex 的支持工程施工方案

> 基线：mecha 当前工作树；Codex `openai/codex` 提交 `93b4e5b4c6474cdbf12614c4ac3acbd006fbee89`（2026-11-04）。
> 本地 Codex 源码位于 `D:/code-nosync/codex`，为单提交浅克隆并检出集成相关目录；Apache-2.0 许可。
> 本方案只规定工程顺序、代码边界和验收事实，不改动项目领域语义。

## 0. 当前施工状态（2026-11-05）

已完成第一阶段：新增 `mecha/host_adapter.py`，dsh Python 逻辑迁入 `mecha/dsh_adapter/`，旧 `dsh_panel.py` 与 `aggregate_runtime.py` 的 dsh 名字保留兼容再导出；新增 `mecha/codex_adapter/` 的外部 MCP 配置、Hooks wire 转换与 `--ai-host codex|none`。dsh 默认路径的核心与受影响集成判据均保持通过。

已完成第二阶段：新增宿主无关的 `/monitor` 页面，直接读取各软件 Cockpit 的 `/history`，区分“未声明 / 读不到 / 零事件 / 有事件”四态，不依赖 dsh 面板资产。

已完成第三阶段：Codex `prepare()` 生成 `PostToolUse` 与 `PermissionRequest` Hook；`hook_entry` 把任务提交回执写入隔离的 `tasks.jsonl`，`/monitor` 显示这本账；`PermissionRequest` 只审计、不输出 decision，保留 Codex 原有 OS 审批流程，领域写权仍归 mecha Gate。

已完成第四阶段：dsh 面板 TS/TSX 资产整体迁入 `mecha/dsh_adapter/assets/`，package-data 已更新并用真实 wheel 核验了 `package.json`、patch、面板壳、host 插件与 `.d.mts` 均随包发出；npm 包名仍为 `@mecha/dsh-panel`，下游只改 `file:` 路径。

已完成第五阶段（真机验收）：在 `codex-cli 0.162.1` 上让 Codex 自己跑通了整条链路，而不是只检查我们生成的字符串。`checks/codex_live_selfcheck.py` 依次做三件事：App Server 初始化后由 `mcpServerStatus/list` 读出 mecha 端点与工具表；开一个 ephemeral 会话，用 `mcpServer/tool/call` 真的把工具打进聚合端点并回读本次随机标记；最后让 `codex doctor --json` 用自己的解析器裁决这份 `config.toml`（含 `[[hooks.PostToolUse]]` 段）是否可接受。对着一份用 `python -m mecha --ai-host codex` 起的真实运行时复核时，Codex 看到 20 个 EL 工具并成功调用 `el_read_config`；`codex doctor` 报 `config.toml parse=ok`、`mcp servers=1`、`streamable_http=1`。

尚未开始：managed App Server 客户端。以下两条限制是验收中撞出来的事实，不是待办清单的修辞：

- **Hook 任务账只在模型发起的工具回合里产生。** 源码里 `PostToolUse` 由 agent 的工具循环派发，`mcpServer/tool/call` 是客户端直连代理，不经过那层。所以 `tasks.jsonl` 的写入要用一次真实对话来验；本机到 `chatgpt.com` 的推理请求超时（与 mecha 配置无关），这一条留待可用网络或本地模型环境。
- **`codex exec` 端到端未在本机跑通**，原因同上：模型请求出不去。配置、MCP 发现、工具调用三层都已独立验过，缺的只有模型这一跳。

## 1. 施工结论

Codex 应作为 mecha 的**第二个 AI 宿主实现**，而不是把 dsh 改名，也不是把 Codex 代码嵌进 Gate/History。原因是 mecha 已经把领域状态、命令面、聚合 MCP 端点和宿主生命周期分开了：项目软件与 `CommandRegistry`、`Gate`、`History` 不需要知道 Codex 的存在；真正需要替换的是当前围绕 dsh 写的宿主接线。

本方案采用一个明确边界：**首个正式版本让 Codex 经 streamable HTTP 直接使用现有聚合 MCP 端点；不移植 dsh 侧栏，不依赖 MCP 工具热更新通知，不把 Codex 的审批机制变成第二套领域写权门。** 启动编排、监控面和长任务桥通过新增的宿主适配器分别接入。

目标调用链为：

```text
Codex CLI / SDK / App Server
  -> [mcp_servers.mecha] streamable HTTP
  -> mecha Aggregator + McpEndpoint
  -> 各软件自己的 MCP 后端
  -> CommandRegistry / Gate / History
  -> 项目领域动作
```

这里没有新的领域执行器。Codex 只是另一个操作者，因此 `Gate.set`、`Gate.record`、`CommandRegistry.invoke` 和 History 的归因规则保持不变。

## 2.  Codex 源码给出的事实

以下结论来自本地 Codex 源码，不依赖产品宣传或文档转述。

| 事实 | 代码位置 | 对施工的含义 |
|---|---|---|
| `ConfigToml.mcp_servers` 是 `HashMap<String, McpServerConfig>` | `codex-rs/config/src/config_toml.rs:297-301` | 可以由 mecha 生成 `[mcp_servers.mecha]` 配置表 |
| HTTP MCP 由 `url` 触发，transport 是 untagged 枚举，没有显式 `transport = ...` 字段 | `codex-rs/config/src/mcp_types.rs`，`McpServerTransportConfig::StreamableHttp` | 配置只写 `url`，不发明 Codex 不认识的字段 |
| 单服务配置支持 `enabled`、`required`、`startup_timeout_sec`、`tool_timeout_sec`、`startup_readiness`、`default_tools_approval_mode`、`enabled_tools`、`disabled_tools`、`tools.<name>.approval_mode` | `codex-rs/config/src/mcp_types.rs:231-314, 389-459` | 可以把聚合端点设为启动必需，并让超时与 mecha 的 600000 ms 工具预算对齐 |
| `bearer_token` 内联值被明确拒绝，只允许 `bearer_token_env_var` | `codex-rs/config/src/mcp_edit.rs:33-49` | 任何凭据只走环境变量；首版本机回环无需凭据，不写死 token |
| `track_tool_list_changes` 在收到 `notifications/tools/list_changed` 时只递增 `AtomicU64` 版本号 | `codex-rs/rmcp-client/src/tool_list_changes.rs:57-67` | 不能把“服务端发了通知”等同于“Codex 已重拉并替换模型可见工具表” |
| `LoggingClientHandler::on_tool_list_changed` 只写日志 | `codex-rs/rmcp-client/src/logging_client_handler.rs:86-88` | 首版正确性必须靠“后端齐备后再启动/变化时显式重连”，不靠通知 |
| stdio、in-process、streamable HTTP、OAuth HTTP 客户端路径都包了 `track_tool_list_changes` | `codex-rs/rmcp-client/src/rmcp_client.rs:1589-1658` | 版本计数存在，但消费方仍需按版本真重连；本仓当前检出的相关模块没有提供可直接依赖的 refetch 保证 |
| Hooks 有 `PreToolUse`、`PermissionRequest`、`PostToolUse`；输入是 snake_case 的 `tool_name`、`tool_input`、`tool_response`、`tool_use_id`，输出是 camelCase 的 `hookSpecificOutput` | `codex-rs/hooks/src/schema.rs:102-126, 187-239, 276-343` | 长任务桥和 OS 审批能按稳定 wire 重写，不需要猜事件形状 |
| `PermissionRequest` 只接受 `hookSpecificOutput.decision.behavior = allow|deny`，任何 deny 优先，保留字段存在时 fail closed | `codex-rs/hooks/src/schema.rs:187-227`，`events/permission_request.rs:153-173` | 审批适配器只能输出这套结构；不允许把 mecha 的 `MechaError` 直接塞回 Codex |
| `PostToolUse` 提供 `tool_response`，可把 MCP 工具结果交给外部命令 | `codex-rs/hooks/src/events/post_tool_use.rs:24-41, 148-169` | 可以替代 dsh 的 `tools/post-execute`，把长任务提交回执转成 mecha 任务记录 |
| App Server 外部边界是 JSON-RPC over stdio，内部客户端可用 typed channels；支持 thread、turn、item 事件和服务端发起的审批请求 | `codex-rs/app-server-client/README.md`，`codex-rs/app-server-protocol/src/protocol/v2/` | 只有需要嵌入式人机界面时才做 App Server 客户端，首版不引入 |
| TypeScript SDK 包装 CLI 的 JSONL 事件；Python SDK 控制本地 App Server JSON-RPC，并自带固定 CLI runtime | `sdk/typescript/README.md:1-50`，`sdk/python/README.md:1-30` | managed 模式可优先用 Python SDK，不直接解析私有 TUI 输出 |

## 3. mecha 当前代码中的宿主耦合

必须改造的不是核心四件，而是以下 dsh 专用位置：

| 位置 | 现状 | 改造方向 |
|---|---|---|
| `mecha/site.py:420-484` | `TaskSpec`/`DSHSpec` 把宿主名写进了注释和字段语义 | 保留数据类兼容名，文档改为宿主无关；新增 `AIHostKind` |
| `mecha/launch.py:206-295` | `merge_dsh`、`_dsh_child`、`build_children` 默认把 dsh 加为最后一个 child | 抽成 `DshHostAdapter`，由启动配置决定是否产生 child |
| `mecha/launch.py:922-1138` | `build_aggregate_wiring` 内含 dsh overlay、dsh 刷新路由、dsh prepare hook | 聚合端点本体保留，host 相关闭包移到适配器 |
| `mecha/launch.py:1118-1123` | 第二次 `agg.wire(...)` 没有传 `resync_fn`，会覆盖 `AggregateRuntime.__init__` 在 `aggregate_runtime.py:266-278` 设置的自愈回调 | 本工程第一步修正为 `resync_fn=runtime.sync_backends`；这是现有 bug，不留给 Codex 分支绕开 |
| `mecha/aggregate_runtime.py:88-121, 567-590` | `overlay_text`、`make_prepare_hook` 和 `MECHA_TASK_BRIDGE_FILE` 是 dsh 形态 | 保留 dsh 实现，另增加宿主无关的任务桥配置出口 |
| `mecha/dsh_panel.py` | 框架 patch、host 插件、`MECHA_PANEL_*`、`MECHA_TASK_SPECS` 全是 dsh 插件接线 | Codex 路径不复用这些 env；监控数据仍走 `cockpit` |
| `mecha/providers/status_page.py` | 页面有固定 dsh 行和 dsh 存活滑块 | 改成按当前 `AIHostAdapter` 显示宿主行；未托管外部 Codex 时显示“外部连接”，不画假开关 |
| `mecha/dsh_adapter/assets/host-plugin/mecha-panel-host.mjs` | dsh `tools/post-execute` 与 `jobs.start` 包装长任务 | 另写 Codex Hook 适配器，不修改 dsh 插件 |

## 4. 目标抽象

新增 `mecha/hosts/` 包，使用 `Protocol` 而不是具名 ABC，符合本项目“框架只放自己会调用的机制”的原则：

```python
class AIHostAdapter(Protocol):
    name: str
    def child(self, plan: HostPlan) -> ChildSpec | None: ...
    def prepare(self, plan: HostPlan) -> HostPreparation: ...
    def refresh_catalog(self, revision: int) -> RefreshResult: ...
    def capabilities(self) -> Mapping[str, object]: ...
    def shutdown(self) -> None: ...
```

`HostPlan` 只携带框架事实：聚合端点 URL、实际回读端口、全部 `Service` 声明、runtime 目录、任务桥端口和当前工具表版本。它不携带 Codex 的模型、会话或 UI 概念。

启动选择是全 launcher 级参数，不写入 `Site`：

```text
python -m mecha --ai-host dsh       # 保持现有行为
python -m mecha --ai-host codex     # 生成隔离 Codex 配置，不启动 dsh
python -m mecha --ai-host none      # 只起软件、聚合端点和控制台
```

项目 `mecha_site.py` 不增加 Codex 字段。宿主是启动者选择，不是项目领域声明。

### 4.1 宿主代码的目录边界

通用宿主契约只放框架机制，宿主实现按目录彻底分开：

```text
mecha/
  host_adapter.py          # HostPlan / AIHostAdapter Protocol / 选择函数
  dsh_adapter/             # 所有 dsh 专用 Python 逻辑与资产入口
    launch.py              # dsh 声明合并、child、overlay、刷新、prepare hook
    panel.py               # dsh 面板参数与框架 patch 接线
    runtime.py             # dsh overlay 文本与挂接函数
    assets/                # 后续迁入 dsh 面板 TS/TSX 与 host 插件
  codex_adapter/           # 所有 Codex 专用逻辑
    config.py              # config.toml / CODEX_HOME / 工具白名单
    hooks.py               # PostToolUse / PermissionRequest 配置
    app_server.py          # 后续 managed 客户端
    host.py                # CodexHostAdapter
```

这条边界与“消除启动层分支”是同一件事：`launch.py` 只持有 `AIHostAdapter`，不出现 dsh 的 overlay、patch、env 或 Codex 的 TOML、Hook、SDK 细节。首版施工把 Python 逻辑迁入上述目录；现有 `mecha/dsh_panel.py` 与 `aggregate_runtime.py` 中的 dsh 函数改为兼容性再导出，`mecha/dsh_adapter/assets/` 资产路径先保留，以免一次改动牵动 package-data、面板判据和下游 `file:` 依赖。资产目录迁入 `dsh_adapter/assets/` 作为独立收口提交，不与协议接线混做。

## 5. 分阶段施工

### 阶段 A：先消除启动层的分支，不接 Codex

1. 新增 `mecha/host_adapter.py` 与 `mecha/dsh_adapter/`，同时建立空的 `mecha/codex_adapter/` 目录骨架。
2. 把 `merge_dsh`、`_dsh_child`、`with_panel_contributions`、`make_prepare_hook`、`refresh_dsh_overlay`、`refresh_dsh_client` 收进 `DshHostAdapter`，旧模块只留兼容再导出。
3. `LaunchController` 和 `build_children` 只认 `AIHostAdapter`；dsh 成为默认实现。
4. 修正 `launch.py:1118` 第二次 `agg.wire` 丢掉 `resync_fn` 的问题。
5. `StatusPage` 的宿主行、存活滑块和文案由 `adapter.capabilities()` 决定。

这一阶段结束后，现有 dsh 路径的行为必须逐字不变；新增的只是分支能力。判定方式是现有核心、集成和面板资产判据全部保持通过，并新增一条“dsh 适配器生成的 child、overlay、env 与原路径等价”的结构判据。

### 阶段 B：Codex 作为外部 MCP 宿主

新增 `mecha/hosts/codex.py`，首版选择 `external` 模式：mecha 不 spawn Codex TUI，只生成隔离配置和启动说明。这样不会把交互式终端、用户登录状态和 Supervisor 的无窗口进程模型纠缠在一起。

`prepare()` 在全部软件 ready、聚合工具表完成第一次 `sync_backends()` 之后执行，写入：

```toml
# <runtime>/codex-home/config.toml
[mcp_servers.mecha]
url = "http://127.0.0.1:<实际聚合端口>/mcp"
required = true
startup_timeout_sec = 30.0
tool_timeout_sec = 600.0
startup_readiness = "connection"
```

配置生成遵守以下事实约束：

- 使用独立 `CODEX_HOME`，不修改用户全局 `~/.codex/config.toml`；Codex 自身代码和测试已把 `CODEX_HOME` 作为凭据与配置根。
- 端口来自 `McpEndpoint.start()` 的回读值，不复制预选值。
- `required = true` 让聚合端点不可用时响亮失败，符合 `mcp_types.rs:247-251` 的语义。
- `tool_timeout_sec = 600.0` 与 `DEFAULT_TOOL_CALL_TIMEOUT_MS = 600000` 和 `Backend.DEFAULT_CALL_TIMEOUT_S = 600.0` 对齐。
- 不写 `bearer_token`；如果后续需要鉴权，只写 `bearer_token_env_var = "MECHA_MCP_TOKEN"` 并从框架环境注入。
- `enabled_tools` 显式排除 `set_write_authority` 与 `open_manual_surface`；聚合面本来就只给 `read_write_authority`，这里再加一道投影期防御。
- 启动命令打印为 `CODEX_HOME=<runtime>/codex-home codex`，并落成可复制文件，不把端口或凭据散进日志。

工具表变化的确定策略是：

1. Codex 启动前等待所有软件后端 ready，取得完整初始目录；
2. 软件重启或工具表版本变化时，`refresh_catalog()` 覆盖写 `<runtime>/codex-mcp/catalog-revision`；
3. `external` 模式在控制台明确报告“需要重连 Codex MCP 客户端”，而不是假设 `list_changed` 已被消费；
4. `managed` 模式后续通过 App Server 重建连接，或重启被 mecha 托管的 Codex 子进程。通知本身只作为触发信号，不作为一致性证明。

### 阶段 C：宿主无关的监控面

不移植 `mecha/dsh_adapter/assets/` 到 Codex。现有 `Monitor/Cockpit` 的 `/status`、`/activity`、`/history`、`/config` 已经是宿主无关的权威读面，直接复用。

新增 `mecha/monitor_page.py`，由 `StatusPage` 挂出 `/monitor`：

- 数据源来自 `Service.monitor` 声明，不写死项目词；
- 保留 `address/offline/route/empty` 四档，不把读不到渲染成空表；
- 复用 `mecha/dsh_adapter/assets/panel/panel-view.ts` 中经过单测的纯渲染逻辑，可在 Python 状态页生成等价 HTML，或将其抽成宿主无关 JS 资产；
- 控制台同时显示聚合端点、当前 AI 宿主、各软件状态和最近操作记录。

这样 dsh 与 Codex 共用同一套监控后端，只有宿主插件层不同。App Server 自定义客户端只在阶段 E 需要，不作为首版前置条件。

### 阶段 D：长任务与审批

把 `TaskSpec` 从 dsh 语义改成宿主无关声明。首版不伪造 Codex 原生 job，而用两条确定的通道：

1. `PostToolUse` Hook 命令由 `CodexHostAdapter` 生成，匹配 `TaskSpec.submit_tool` 的规范工具名。Hook 读到 `tool_name`、`tool_input`、`tool_response`、`tool_use_id` 后，把 `task_id` 注册到 mecha 状态页的任务面板，并沿用 `AggregateRuntime.task_wait()` / `task_cancel()` 的短轮询。
2. 状态页通过 `/mecha/task/wait`、`/mecha/task/cancel` 显示任务终态；取消请求与真正取消分开表达，沿用 `JobRegistry.cancel()` 的真话契约。

`PermissionRequest` 只处理 Codex 的 OS 层审批。适配器输出必须是：

```json
{"hookSpecificOutput": {"hookEventName": "PermissionRequest",
  "decision": {"behavior": "deny", "message": "..."}}}
```

任何 deny 优先放行；`updatedInput`、`updatedPermissions`、`interrupt` 等当前 fail-closed 字段一律不写。项目领域写入仍只在 `CommandRegistry.invoke` 的 `ScopePolicy`、`Authority`、`LocalApproval`、`Gate` 上裁决。Codex 审批不会成为第二套领域写权真值。

### 阶段 E：托管宿主与嵌入式界面

只有在阶段 B-D 稳定后才引入 App Server 客户端：

- 用 Python SDK 或 `codex app-server` 的 JSON-RPC 启动、恢复 thread，订阅 `item/started`、`item/*/delta`、`item/completed`、`turn/completed`；
- 通过协议生成命令 `codex app-server generate-json-schema` 导出类型，不手抄 protocol 字符串；
- 把 mecha 监控页作为自定义客户端的一个面板，而不是把 dsh React 插件塞进 Codex；
- 初始化能力按需声明 `required_human_approval` 与 `experimental_api`，由适配器处理服务端审批请求。

这一阶段是 UI 完整性增强，不改变 Gate/History/命令链。

## 6. 安全边界

Codex 沙箱与 mecha Gate 是互补的两层，不互相替代：

- Codex 沙箱约束 shell、文件系统、网络与 OS 资源；
- mecha Gate 约束项目领域状态和 History；
- Codex 不能拿到控制端点地址，`set_write_authority`/`open_manual_surface` 始终不出现在 AI 能力面；
- 生成的 `AGENTS.md` 明确要求领域状态只经 `mcp__mecha__*` 工具修改，禁止直接用 shell 或项目 Python API 改写数据根、journal、数据库；
- `sandbox_mode` 默认从 `read-only` 起步；需要工作区写入时用 `workspace-write`，并把所有 `Service.data_root` 从可写根中排除；网络只允许本机聚合端点；
- 本机回环 HTTP 不加 OAuth 和 bearer token；若跨机部署，必须补真正鉴权，不把回环假设带过去。

这条边界直接对应 mecha 原本防的“洞 1”：AI 不能借通用 shell 绕过门的直写路径。

## 7. 文件交付清单

| 文件 | 施工内容 |
|---|---|
| `mecha/host_adapter.py` | `HostPlan`、`AIHostAdapter` Protocol、`HostPreparation`、`RefreshResult`、适配器选择 |
| `mecha/dsh_adapter/launch.py` | dsh 声明合并、child、overlay、刷新、prepare hook |
| `mecha/dsh_adapter/panel.py` | dsh 面板参数、patch、env 与 host 插件接线 |
| `mecha/dsh_adapter/runtime.py` | dsh overlay 文本与 `with_overlay_patch` 一类运行时挂接 |
| `mecha/codex_adapter/config.py` | 独立 `CODEX_HOME` 配置生成、端口回读、工具白名单、重连标记 |
| `mecha/codex_adapter/hooks.py` | 生成 `PostToolUse`/`PermissionRequest` hook 配置与桥接命令入口 |
| `mecha/codex_adapter/host.py` | `CodexHostAdapter` 与宿主能力声明 |
| `mecha/monitor_page.py` | 宿主无关监控页与任务面板 |
| `mecha/site.py` | `AIHostKind`、`TaskSpec` 语义泛化，保留旧名兼容 |
| `mecha/launch.py` | `--ai-host`、适配器选择、child 列表、修 `resync_fn`、generic refresh |
| `mecha/aggregate_runtime.py` | 宿主无关任务桥配置；dsh overlay 保留为 dsh 实现 |
| `mecha/providers/status_page.py` | 通用宿主行、`/monitor`、任务状态接口 |
| `checks/codex_host_selfcheck.py` | 配置形状、隔离目录、超时、白名单、无凭据泄漏 |
| `checks/codex_mcp_selfcheck.py` | 真 MCP 调用、失败透传、软件重启后的重连事实 |
| `checks/codex_hooks_selfcheck.py` | Hook wire 形状、deny 优先、长任务注册与取消真话 |
| `checks/codex_sandbox_selfcheck.py` | 数据根不可直写、控制工具不可见、只有 MCP 路径可改域状态 |
| `checks/run_all.py` | 新增 `codex` 套件；默认核心不因缺 Codex 可执行文件而假绿 |
| `docs/codex-host-support-plan.md` | 本施工方案 |
| `docs/接入指南.md`、`README.md` | 增加 `--ai-host` 的实际用法，不改领域接入契约 |

## 8. 验收判据

1. `--ai-host dsh` 的启动顺序、工具表、overlay、面板与当前实现等价；现有 dsh 判据全绿。
2. `--ai-host codex` 不产生 dsh child，不生成 dsh overlay，不注入 `MECHA_PANEL_*` 或 `MECHA_TASK_SPECS`。
3. 生成的 `config.toml` 可被 Codex `ConfigToml`/`McpServerConfig` 解析；`bearer_token` 不存在；端口等于 `McpEndpoint` 回读端口。
4. 经真实 MCP 协议调用一个聚合工具，回执经 `ToolHost.call` 到达项目命令；域失败仍带 `is_error=True` 和 `info.kind`。
5. 软件重启或工具表变化后，控制台明确给出重连事实；没有把 `notifications/tools/list_changed` 的发送记录冒充客户端刷新成功。
6. `/monitor` 在 address/offline/route/empty 四档都渲染可读文本；`/history` 仍展示 History 原始事件而非 AI 自述。
7. `PostToolUse` 能识别长任务提交回执并注册任务；取消请求、超时、终态分别可查。
8. `PermissionRequest` 的 allow/deny 与 fail-closed 字段行为符合 Codex schema；领域审批仍由 `CommandRegistry.invoke` 决定。
9. 沙箱判据证明 Codex 不能直写 `Service.data_root`/journal；控制端点与切换写权工具不在模型可见 schema 中。
10. Codex 可执行文件缺失时，默认核心套件照常验证框架机制；显式 `--suite codex` 必须响亮报告缺失，不以 SKIP 冒充通过。

## 9. 明确不做的事

- 不为 Codex 重写 `Gate`、`History`、`CommandRegistry`、`Monitor` 或 Artifacts。
- 不把 dsh React 插件改成 Codex 插件；监控后端共用，宿主 UI 分开。
- 不在 `Site` 或项目领域声明里写 Codex 的模型、API key、session 路径或界面词。
- 不依赖 `notifications/tools/list_changed` 作为首版正确性依据。
- 不用 Codex 的 shell 工具直接操作项目状态或 journal。
- 不把 Codex 审批升级成领域写权；`Authority` 仍是领域侧唯一闸门。
- 不修改用户全局 `~/.codex/config.toml`；所有生成物落在 mecha runtime 目录。
- 首版不实现 App Server 嵌入式客户端，避免把 UI 工程量压到 MCP 通路上。

## 10. 实施顺序

按以下提交顺序推进，每一步都能单独验收：

1. 宿主适配器骨架 + dsh 等价迁移 + `resync_fn` 修复；
2. Codex 配置生成与 `external` 启动模式；
3. 通用监控页与状态页宿主行；
4. Codex Hooks 长任务/审批桥；
5. `codex` 判据套件与文档；
6. 最后才评估 managed App Server 客户端。

这个顺序把“协议是否连通”与“界面是否完整”分开。只要第一步和第二步成立，mecha 就已经有了 dsh 之外的第二个正式 AI 操作者；后续阶段只补宿主体验，不再动摇软件状态所有权。
