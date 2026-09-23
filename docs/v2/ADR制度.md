# ADR 制度（Architecture Decision Record）

> **为什么第一步立这个**：v1 最大的痛点不是代码，是**决策无法追溯**。
> 主代理为弥补这个洞手写了三份交接文档。dsh 用 1907 条 note + 机械门禁解决同一问题，
> 且它**零运行时耦合**——这是最该先抄的东西。
>
> 我们从 dsh 抄**制度**，不抄它的规模。我们是单仓 + 一个新对话，不是 266 个包的 monorepo。

---

## 1. 目录与四态

```
docs/v2/notes/
  proposed/     未建。仍在争，不许当定论引用
  implemented/  已落地。必须随时事更新（但不许改决策本身，只能被新 note 取代）
  rejected/     想过并明确否决。**防止后人重提一遍**
  archived/     定案后封存。永久冻结，不是当前权威
```

**⚠️ 最重要的一条语义**：**`implemented/` ≠ 冻结**。它只表示"已发货且仍可能变"。
`archived/` 才是冻结。四态流转：

```
proposed ──落地──▶ implemented ──被完全取代──▶ archived
   │                    │
   └──否决──▶ rejected ─┴──被新 note 取代时删除（必须继承全部 rationale）
```

## 2. 命名与格式

**文件名**：`YYYY-MM-DD-短名.md`（日期 = 首次提出日，落地后不改名）。

**必填四节**（缺一节 = 格式不过，见 §5）：

```markdown
# <标题>

> **状态**：proposed | implemented | rejected | archived
> **作者**：<谁>
> **日期**：YYYY-MM-DD

## Problem
<不改会怎样？受影响的是谁？给出可观察的事实，不要观点。>

## Decision
<决定了什么。用可判定的措辞——"必须/禁止/只有…才…"，不要"尽量/倾向于"。>

## Consequences
<换来什么、付出什么、**放弃了什么**。代价要具体到可观察。>

## Alternatives considered
<认真考虑过并被否决的方案，每个附否决理由。
 ⚠️ 这一节是整份 ADR 最有价值的部分，不许写"无"。>
```

## 3. 五条硬规则

1. **一个非平凡改动一篇 ADR**（"非平凡" = 动 Definition / 改不变量 / 加 seam / 删能力 / 换 Provider）。
   纯机械改动（重命名、补注释、修 typo）豁免。
2. **`rejected/` 不是垃圾场**：每条必须写清"否决理由 + 什么条件下会重新考虑"。
   没有重新考虑条件的否决是懒否决。
3. **取代必须继承**：删除一条 `implemented/` note 只有一种正当理由——有一篇新 note
   完全取代它，且新 note 的 `Alternatives considered` 里引用了旧 note 并继承其全部 rationale。
4. **归档即冻结**：进了 `archived/` 的 note **永不修改**。冻结用 git 提交完成（commit 即 hash-lock），
   不引入额外 sidecar。
5. **与现实冲突时先改 note 再改码**。如果代码和 note 不一致，那是 note 过期了——
   **先更新 note**（或写取代 note），再让代码跟上。反过来做等于把 ADR 变成谎言。
6. **推迟必须绑可判定的触发事件，且触发/关门必须回登**（审查 §3 补，2026-09-23）：
   登记一条推迟（`rejected/` 的重新考虑条件、或 `implemented/` 的推迟项登记）时，
   **触发必须写成可判定的具体事件**（"某真实消费者出现""某能力被真用"），不许写
   "归 Phase N"这种会关门却无人回来复核的模糊锚点。**被绑定的里程碑（如某 Phase
   落地）发生时，必须回到该 note 复核并留下"已回登：状态如何、触发是否发生"的一句
   记录**——否则"推迟"沦为"遗忘"的合法外衣（本仓最该防的假绿变体）。回登是人的
   里程碑动作（Phase 关门由人判定），不建机械门禁；但推迟项登记 note 每条须含
   `**触发**` 标记，缺则视为懒推迟。

## 4. 什么时候必须写（清单）

- 动 `架构总纲.md` 的任何一节
- 新增/删除/缩小一个 Definition 或 seam
- 新增一个 Provider 或换 Provider
- 改变一条硬纪律或例外于它
- 销号一条 `遗留登记-D1-D27.md` 的缺陷
- 否决一个看起来合理的方案（→ 直接进 `rejected/`）

## 5. 机械门禁（Phase 0 交付，之后长期跑）

一条判据 `checks/adr_format_selfcheck.py`，五组：

| 组 | 断言 |
|---|---|
| A | 每篇 note 有必填四节；`Alternatives considered` 非空且不含"无" |
| B | 状态字面量合法；文件名日期与 front matter 一致 |
| C | `proposed/` 里的 note 没被 `implemented/` 的代码引用（防把提案当定论） |
| D | `rejected/` 每条有"重新考虑条件" |
| E | **不许误报**：合成一篇完全合规的 note 必须 0 命中（防哑守卫） |

## 6. 和 dsh 的差异（我们刻意简化的）

| dsh 做的 | 我们不做 | 理由 |
|---|---|---|
| 1907 条 note、266 包各自 `./invariant` 配套插件 | 单棵 note 树 + 每模块一个 Limitations 段 | 规模差 100 倍 |
| 归档用 sidecar hash + append-only manifest | **用 git commit 冻结** | git 本身就是不可变日志 |
| `verify-agent-note-format` 有 header/skeleton 双门禁 | 一条判据四组 | 够用即可 |
| class 封闭集（feature/bug-fix/simplification/architecture/process/testing） | 先不分类 | 等 note 超过 30 条再引入，避免过早分类 |

## 7. 仓库入口文档（审查 P4 补，2026-09-23）

每个仓库根必须有 `README.md`：它是什么 / 不是什么 / 仓库地图 / 怎么起 /
怎么验证 / 决策怎么查。**示例代码必须实测可跑**——文档说一套代码做一套是
D-27 失败类，入口文档尤其致命（新对话第一眼就读它）。README 的更新与代码
同提交（L9 的一件之内）。
