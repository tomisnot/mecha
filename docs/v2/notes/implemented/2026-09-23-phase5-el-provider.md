# Phase 5：EL 作为第一个 Provider + 第一份消费配置

> **状态**：implemented
> **作者**：mecha v2
> **日期**：2026-09-23

## Problem

Phase 5 要"改 EL 适配器"。但三条纪律相互拉扯：
- **不被旧代码干扰**（用户强调 + §11）：EL 仓里 v1 的 `src/adapter/EngineAdapter`、
  `mecha.app_bridge` 就是旧形状，读了会锚定。
- **只提交自己路径**（L9）：跨仓改 EL 的 `app_entry.py` 会污染 EL 暂存区。
- **主战场质量**（用户强调）：架构成不成，看真实项目能不能被它接住、AI 调用实不实用。

实测事实（本会话探针，非猜测）：EL 的 `src.core.protocol` 是**领域无关框架无关**
的纯领域层——docstring 明写"本协议不 import mecha（守 core ↛ mecha）"，可无 Qt
headless 导入；cache 建 13 s、单体小配置 evolve 2.4 s、40 个可写键、
`estimate` 单体**不返回 est_sec**（只有两体才有）。

## Decision

**EL 的 Provider 参考实现 + 真引擎判据住 mecha 仓 `examples/energy_level/`，
不改 EL 仓任何文件。** 理由：

1. **只 import `src.core.protocol`，绝不碰 `src.adapter`/`app_bridge`**（v1 旧代码）。
   EL core 是干净素材，包装它即证明"架构能被真实项目接住"。
2. **EL 根路径经构造参数/环境变量注入**（D-6 精神：库不写死路径）。判据显式传。
3. **真实部署接线**（把 EL 的 `app_entry` 从 v1 换到 v2）是 EL 仓的改动，
   本 ADR 给出接线契约，但**不擅自动跨仓文件**——需用户显式授权后单独进行。

**Provider 适配框架契约的两处领域现实**（隐式契约键显式化，D-15）：

- `estimate`：EL 单体无 `est_sec` ⇒ Provider 补一个**保守秒级估算**（基于
  n_states 线性，note 里明说"单体粗估、两体用 cost.py 标定"），使
  `Surface.estimate` 的 `est_sec` 必填契约成立。
- `run`：EL `result_summary` 无 `method` 键 ⇒ Provider 从 `run['method']` 补进
  回执，使 `run_output_required=['ok','method']` 成立。

**工具面**（AI 手脚，命名守 Phase 4 守卫：动词_宾语、无视图名、无前缀）：
`set_config` / `read_config` / `estimate_run` / `preview_run` / `run_sim` /
`query_levels` / `query_params`。每个 `output.schema` 显式声明 required 键；
description 只含任务概念（L4），不含"choke point/读回面"等实现词汇。

## Consequences

换来：真引擎装配判据（不 mock）、架构可消费性被真实项目证实、工具面质量有判据
兜底、EL 仓暂存区干净。付出：真实部署接线延后到用户授权；examples 与 EL 仓有
一份"接线契约"要人维护对齐。

## Alternatives considered

**否决：直接改 EL 仓 app_entry 接 v2。** 违反 L9（跨仓暂存污染）+ 会撞 v1 的
`AppBridge`/`EngineAdapter` 旧形状（正是"不被旧代码干扰"要避免的）。
**否决：把 EL core 复制进 mecha 仓。** 制造第二真值；EL core 是 EL 的资产，
只能 import 不能搬。
**否决：用 mock 引擎测 Phase 5。** 假绿（L3）——主战场的意义就是真引擎，
mock 掉等于没做这一步。
