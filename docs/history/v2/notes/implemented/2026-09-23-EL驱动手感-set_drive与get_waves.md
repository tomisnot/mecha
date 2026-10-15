# EL 驱动手感：set_drive 具名工具 + get_waves 查询项（②b 拍板）

> **状态**：implemented
> **作者**：mecha v2（回应《待办-代替v1六件事》§2②b）
> **日期**：2026-09-23

## Problem

v1 的 EL 领域侧有两个工具 `set_drive` / `get_waves`（`src/adapter/mcp_tools.py`
自报，实现在 `src/adapter/fields.py`）。v2 把领域工具面整个交给通用 `set_config`
后，审查《待办-代替v1六件事》②b 把它列为"便利性退化，需人拍板"，两条路：
(a) 加具名包装保手感 / (b) 接受退化、skill 里教模型手搓 `wave_list`。

**实测把这条从"手感"升级为"能力"**（探针 `_probe_setdrive.py`，真 core，跑完即删）：
`set_drive` 的 `rabi_mhz` 入参要换算成光强，必须知道该跃迁的**偶极矩阵元**
`|d|`（`intensity_mwcm2_from_rabi(d_au, om_mhz, pol_frac)`）。而 v2 暴露的
`query_levels` 精简输出**不含 dme**（只回 label/key/E_mhz）——模型拿不到 `|d|`，
就无法经 `set_config` 表达"给 60S m_J=-0.5 → 60P m_J=-1.5 来一个 5 MHz 的 π 脉冲"。
探针实测：该跃迁 `|d|≈2127 a.u.`（里德堡巨偶极），须经 `build_states` +
`static_atom.dipole_amplitude` 现算才拿得到。

**精确边界**：用 `intensity_mwcm2` 表达驱动，v2 的 `set_config` 完全到得了
（intensity 是 wave dict 直接字段）；**用 `rabi_mhz` 表达**（原子物理的自然语言）
到不了。所以这不是"手脚变笨"，是"少了一种说话方式"——直接打在验收口号
"仿佛是自己的手脚"上。

## Decision

1. **新增具名工具 `set_drive`（写）**：内部**经 `gate.set` 写单键
   `dynamic.waves_single`/`waves_A`/`waves_B`**，门不旁路（与其余写工具同一条路）。
   入参：`src`/`tgt`（`query_levels` 的身份键）、`pol`、`rabi_mhz` 或
   `intensity_mwcm2`（二选一）、`pulse`（`pi`/`pi2`/方波）、`detuning_mhz`、
   `duration_ns`、`edge_ns`、`name`、`mode`（`append`/`replace`）。
   **物理换算复用 core 层**（`static_atom.dipole_amplitude` /
   `intensity_mwcm2_from_rabi`），**不重写物理、不读 v1 的 `src/adapter/`**。
   因换算要碰 ARC cache（线程亲和），换算逻辑落在 `ElEngine.drive_wave`，
   汇流引擎线程执行；工具层只做"读现有波 → 追加/替换 → 写回"。

2. **`get_waves` 不新增顶层工具，注册为 Surface QUERY 项**（`run_actions` 可达）：
   它是纯读（回各波摘要：name/freq/intensity/pol/包络形状/时长，不内联大数组），
   遵循既有原则"新查询经 `Surface.register` 暴露，不新增工具"（见
   `2026-09-23-EL能力面取舍.md` 勘误 §4.1）。复用 ②a 刚建的 QUERY 注册机制。

3. **两件都落在 provider 层**（`examples/energy_level/`），框架零改动。
   顶层工具数 11→12（只 +`set_drive`；`get_waves` 走 QUERY 不计数）。

## Consequences

换来：模型能用"Rabi 频率 + 脉冲形状"这种物理自然语言驱动，且 `get_waves` 读回
波形摘要经 `run_actions` 可达——补齐 v1 的领域读写两侧。付出：
① 顶层工具 +1（`set_drive`），偏离"11"但这是 ②b 拍板要付的；
② `set_drive` 是复合写（读快照→建态→算 dme→写回单键），须线程亲和，换算
   不能在工具闭包里就地跑（cache 认引擎线程）；
③ `get_waves` 走 QUERY ⇒ GUI 侧若要直读需另接（当前 GUI 有自己的波形面板，不依赖它）。

## Alternatives considered

- **否决 (b) 纯 skill 教模型手搓 `wave_list`**：探针证明 rabi 表达拿不到 dme，
  不是"多写几行 dict"的手感问题，是"这种说法根本表达不出"的能力问题。skill
  教不会模型一个它拿不到的物理量。
- **否决 `get_waves` 也做成顶层工具**：读操作占工具面与 `read_config`/`query_*`
  已折叠进投影的原则冲突；且刚在 ②a 建了 QUERY 注册机制，读就该走它。
- **否决把 dme 塞进 `query_levels` 输出让模型自己换算**：把 `intensity = f(|d|,Ω)`
  的物理常数换算推给模型（易算错单位），违背"仿佛手脚"——手脚的本分是替主人
  做掉该做的换算。
- **否决在框架里加"领域工具"这一类**：provider 层本就能往工具面加工具，框架不
  该知道"驱动/波形"是什么（硬纪律：边界写机制不写分类）。`set_drive` 是 EL
  Provider 的私有工具，不是 mecha 库的能力。
