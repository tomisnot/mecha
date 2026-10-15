# 作用域写权：ScopePolicy 是 mode 之外的正交过滤

> **状态**：implemented
> **作者**：ML×mecha 衔接（Lead）
> **日期**：2026-09-24

## Problem

Authority 只提供全局写权模式：HUMAN / AI / LOCKED。它对"此刻哪一侧能写域状态"
足够，但对第二个真实宿主（ML Toolbox）不够：

- 同一个 AI 模式下，AI 应能跑训练与对比；
- 但 AI 不应顺手导出数据、删除运行或触发外部命令；
- 这些都是"能写，但有作用域边界"的需求，不是 ML 特例：第三个、第四个宿主
  同样会问"这个操作者在这个域上有没有写权"。

现有 `CommandSpec.scope` 只是**声明字段**，核心不解释也不据此拦截（ADR
`通用命令面-命令是注册项` 明确写下这个缺口）。不补机制，宿主只能自己再建
一套权限过滤，框架的 scope 永远停在注释层。

## Decision

新增 additive 的 `mecha/scopes.py`，只写机制：

1. `ScopePolicy` 维护 `(side, scope)` 授予表；**默认拒绝**未列出的 scope；
   显式 `default_allow=True` 才放开（不静默把默认拒绝改掉）。
2. `CommandRegistry.bind_scope_policy(policy)`：注入后，`invoke` 对
   `spec.scope` 非空的命令，在**参数校验与 handler 调用之前**先
   `policy.check(channel.side, spec.scope)`；不通过即
   `scope_denied` 结构化失败，**不写审计、不调 handler**（无副作用）。
3. 未注入 policy 时不做 scope 过滤——单宿主（EL 等）行为不变，机制 opt-in。
4. scope 是不透明字符串；框架不解释领域含义。ML 用命令名当 scope（如
   `run_method`），未来 `export_prediction` 必须由装配点显式加授才给 AI。
5. 与 Approval 正交：scope 决定"能不能做"，Approval 决定"这一次要不要人点头"，
   两者不互相替代。

## Consequences

**换来**：mode 之外多了一层通用的作用域写权；`CommandSpec.scope` 从声明变成
可执行契约；宿主不必自建权限过滤；AI 默认只能做被显式授予的动作，新增危险
命令默认对 AI 关闭（fail closed）。

**付出**：宿主装配点必须显式维护授予表；`ScopePolicy` 主体只认 side，不认
角色/用户，粒度只到 scope 字符串。这些都写进模块 `Known Limitations`，
等第三个宿主出现再抽象。

**放弃了什么**：放弃把 scope 做成通配符/层级（`ml.*`）——当前只有命令名粒度
的真实压力，提前做层级是 n=1 的过度设计；放弃把权限判断塞进 `Authority`
（它管全局模式，两者职责不同）。

## Alternatives considered

**否决：扩展 Authority.gate 直接判 scope。** mode 是全局协调态，scope 是能力
粒度；混进同一状态机会让"谁能切模式"和"谁能跑某命令"互相污染，也让
`switch_mode` 的简单契约变复杂。

**否决：在 `VisibilityPolicy` 里做权限。** 那个 seam 管"工具可不可见"，
是投影层过滤；不可见不等于不可写，而且写路径（工具之外的命令调用）绕不过它。

**否决：让宿主 adapter 自己实现 scope 白名单。** 那是"第二套机制"：每个宿主
重复实现同一段判断，且核心的 `scope` 声明永远无法被执行；也不满足"薄适配"。

**否决：默认放开（只靠 deny 列表）。** 新增危险命令会默认对 AI 开放，违反
"nothing ships enabled" 的既有纪律；默认拒绝 + 显式授予才与 Authority 的
出厂 LOCKED 同向。
