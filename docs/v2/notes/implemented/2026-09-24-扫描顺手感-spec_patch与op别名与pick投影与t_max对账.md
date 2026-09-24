# 扫描顺手感：spec_patch / op 别名 / pick 投影 / t_max 对账

> **状态**：implemented
> **作者**：mecha v2（回应《mecha-v2-实测第二轮-以实验者身份》§4）
> **日期**：2026-09-24

## Problem

第二轮实测确认上轮 P0/P1 全修好后，剩下的摩擦**不在"能不能用"，在"做扫描还是啰嗦"**：

- **4-1**：`run_actions` 的 `spec` 是整表替换语义——扫 9 点每点手写完整 config，
  单次调用一大坨 JSON 且易漏键（作者差点漏 `decay_index`）。"这轮唯一还让我累的地方"。
- **4-2**：顶层工具叫 `estimate_run`/`run_sim`，编排 op 叫 `estimate`/`run`——两套账，
  "每次都得试一次"（报错列合法值只是止损，不是免错）。
- **4-3**：`t_max` 不跟随脉冲时长——终态布居读的是"脉冲后又自由演化一段"（周期整除
  时看不出来，换 Ω 就读错）。作者点名"接口可以帮助实验者避免它"。
- **4-4**：扫描结果要自己从 N 个完整 output 里抠关注量；50 点就撑爆上下文。

## Decision

全部落在 **EL Provider 层的编排入口**（`mecha_v2/tools.py`），CodeAct 内核与框架零改动：

1. **`spec_patch`（4-1）**：run/estimate 步的配置三种给法——`args.spec` 整表替换
   （原样保留）；`args.spec_patch` 在当前配置（或顶层 spec）上只覆写列出的键；
   都不给=当前配置。顶层 `spec_patch` 参数同义（步没给才生效）。patch 只进本次
   run 的 spec，**不写快照**——扫描不改域状态。
2. **op 别名（4-2）**：`_OP_ALIAS = {estimate_run→estimate, run_sim→run}` 在编排
   入口映射——工具名与 op 名不再两套账，且不动 Surface 注册名（CodeAct 的合法 op
   集是内核事实，别名是 EL 侧的入口便利）。
3. **`pick` 投影（4-4）**：键路径列表投影每步 output（**整键优先**——JSON 键可含点，
   如 `final_top_pop.60P_{3/2} m_J=-0.5` 本身是一个键；匹配不到再点分层下钻）。
   缺失键如实列 `_missing`，绝不静默 null（D5）。
4. **t_max 对账（4-3）**：`set_drive` 回执加 `pulse_end_ns`（offset+平台长）；与当前
   `dynamic.t_max` 不等给 `t_max_note` 一句方向性提示（晚于→"终态含脉冲后自由演化"；
   早于→"脉冲被截断"）。**提示不拦止**——自由演化/截断都可能是故意设计，接口只保证
   "看得见"，物理判断归实验者。SKILL 坑列表补 13/14 两条（沿面积吃 π 精度、t_max 陷阱）。

## Consequences

换来：扫描从"每点全量 spec + 事后自己抠"变成"每点 patch 变化量 + pick 关注量"；
op 名免试错；4-3 的静默实验错误变显式对账。付出：`run_actions` 参数面从
`{actions, spec}` 扩到 `{actions, spec, spec_patch, pick}`（三个都可省，旧调用形状不变）；
`set_drive` 回执多两键（消费方判据/SKILL 同批更新）。

## Alternatives considered

- **否决扫描原语 `scan(axis, values)`**（报告的第二候选）：n=1——9 点扫描 spec_patch+
  pick 已把成本压到"每步一个键值对"；封闭的 axis 语法（点路径？数组索引？）在真出现
  二维/嵌套扫描需求前是猜。变化速率分叉时再拆（Don't split preemptively）。
- **否决把 op 名直接改成工具名**：Surface QUERY 注册名与 CodeAct 内置 op 名是内核
  既有事实（phase3 判据钉着），改内核名波及面大于入口别名，收益相同。
- **否决 t_max 不匹配就拦止**：Gate 只管"值合法"，不管"实验设计对不对"——拦止=框架
  替实验者做物理决定，越界；且自由演化窗口是合法设计（衰减测量就要它）。
