"""Phase 5 判据：EL 作为第一个 Provider 的端到端装配 + 工具面质量守卫。

真引擎、真演化、不 mock（主战场判据；mock 掉等于没做这步）。覆盖：

1. 装配闭环：ElEngine → Gate(validate 注入) → Surface → 工具面，全链路。
2. 写入门生效：set_config 写未知键 / 只读 engine.* → 拒且报错可教学。
3. 契约显式化：每个工具的 output.schema.required 在**真回执**上逐条成立。
4. 物理演化唯一入口：run_sim 经 Surface.run；回执含 ok/method（D-15 落地）。
5. estimate 适配层：单体回执被补出 est_sec（框架契约成立，note 说明粗估口径）。
6. L7 对偶：合法配置全链路绿；报错用例的 hint/suggest 非空（可教学）。

**缺 EL 即红，不假绿**：本判据是 EL 专属，EL 根不存在时 FAIL（不是 skip）。

用法：python checks/phase5_el_selfcheck.py
退出码：0 全绿；1 有失败。预计耗时 < 60 s（cache ~13 s + 小配置演化 ~3 s）。

## Known Limitations and Deferred Work

- 工具回执校验用真调用（单体小配置），不校验大数组载荷（pops 不进回执，
  摘要口径由 EL result_summary 决定）。
- 取消路径（cancel_after_sec）只测"设了不炸"，不强测引擎中途真停
  （numba 段内不响应取消，硬测会 flaky——L3 宁可少测也不测假的）。
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EL_ROOT = Path(os.environ.get("MECHA_EL_ROOT", ""))   # 只读环境变量，缺了不猜
# 领域实现归所属项目（ADR 领域参考实现搬回所属项目）：引擎/工具面从 EL 仓 import。
sys.path.insert(0, str(EL_ROOT))

from mecha.authority import Authority, Mode  # noqa: E402
from mecha.data_layout import resolve_data_dir  # noqa: E402
from mecha.errors import MechaError  # noqa: E402
from mecha.gate import Channel, Gate  # noqa: E402
from mecha.history import History  # noqa: E402
from mecha.providers.jobs import JobRegistry  # noqa: E402
from mecha.surface import EntryKind, ExecutionContext, Surface  # noqa: E402

from mecha_v2.engine import ElEngine, make_validator  # noqa: E402
from mecha_v2.tools import (  # noqa: E402
    DOMAIN_QUERY_OPS, build_tool_registry)


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _fail(out: list[str], msg: str) -> None:
    print(f"  ✗ {msg}")
    out.append(msg)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out: list[str] = []
    print("Phase 5 判据（真引擎，不 mock）：")

    if not (EL_ROOT / "src" / "core" / "protocol.py").exists():
        print(f"  ✗ EL 根不存在：{EL_ROOT}——本判据缺 EL 即红（不假绿）")
        return 1

    t0 = time.time()
    engine = ElEngine(EL_ROOT)
    _ok(f"ElEngine 构造 + ARC cache 就绪（{time.time() - t0:.1f}s）")

    # ---- 装配：Gate 注入 validator，AI 通道，出厂后人类开闸到 AI 模式 ----
    history = History()
    authority = Authority(Mode.LOCKED)
    gate = Gate(authority, history, validate=make_validator(engine))
    surface = Surface(engine)
    ai = Channel("ai")
    authority.switch_mode(Mode.AI, side="human")
    tools = build_tool_registry(engine, gate, surface, authority, ai,
                                JobRegistry())
    _ok("全链路装配完成（Gate/Surface/Authority/工具面）")

    # ---- 1. 工具面形状：命名守卫 + 投影白名单 ----
    names = [s["name"] for s in tools.schemas()]
    expect = {"set_config", "read_config", "query_params", "estimate_run",
              "preview_run", "run_sim", "query_levels",
              "submit_job", "read_job", "cancel_job", "run_actions",
              "set_drive", "read_waves", "reset_config"}
    if set(names) == expect:
        _ok(f"工具注册齐且过命名守卫：{sorted(names)}")
    else:
        _fail(out, f"工具集不符：{names}")
    for s in tools.schemas():
        if set(s.keys()) != {"name", "description", "parameters"}:
            _fail(out, f"工具 {s['name']} 投影泄漏字段")
    _ok("schemas() 投影全部只含模型可见字段")

    # ---- 1b. ②a 防复发：run_actions 声明的查询名必须真在 Surface QUERY 面 ----
    # （审查②a：description 说"或已注册的只读查询名"曾是假的——声明与能力面
    # 不许两套账，判据按 DOMAIN_QUERY_OPS 单一来源逐个验真。）
    qnames = {e.name for e in surface.entries(EntryKind.QUERY)}
    if set(DOMAIN_QUERY_OPS) <= qnames:
        _ok(f"Surface QUERY 面覆盖声明的查询名：{sorted(DOMAIN_QUERY_OPS)}")
    else:
        _fail(out, f"QUERY 面缺：{sorted(set(DOMAIN_QUERY_OPS) - qnames)}")
    ra_desc = next(s["description"] for s in tools.schemas()
                   if s["name"] == "run_actions")
    missing_in_desc = [q for q in DOMAIN_QUERY_OPS if q not in ra_desc]
    if not missing_in_desc:
        _ok("run_actions description 由单一来源生成（逐个查询名可见）")
    else:
        _fail(out, f"run_actions description 缺查询名：{missing_in_desc}")

    # ---- 2. 写入门：拒未知键 / 拒只读 engine.*，报错可教学 ----
    r = tools.execute("set_config", {"key": "dynamic.nope", "value": 1})
    if r["is_error"] and r["error"]["info"]["kind"] == "unknown_key" \
            and r["error"]["info"]["hint"] and r["error"]["info"]["suggest"]:
        _ok("未知键被拒 + hint/suggest 非空（L4 可教学）")
    else:
        _fail(out, f"未知键处理不符：{r}")
    eng_key = next(iter(engine.schema()["engine"]))
    r = tools.execute("set_config", {"key": eng_key, "value": 1})
    if r["is_error"] and r["error"]["info"]["kind"] == "readonly_key":
        _ok(f"只读引擎常量 {eng_key} 被拒（engine.* 不可覆写）")
    else:
        _fail(out, f"只读键处理不符：{r}")

    # ---- 3. 合法写 + 回读自证（实测报告 P1-4：写一次就核对，不必二次查询）----
    gate.set(ai, "field.B_gauss", 0.5, "seed-before")     # 造一个可对照的旧值
    r = tools.execute("set_config", {"key": "field.B_gauss", "value": 1.0})
    if not r["is_error"] and r["value"]["applied"]:
        v = r["value"]
        rr = tools.execute("read_config", {})
        if (rr["value"]["config"].get("field.B_gauss") == 1.0
                and v["before"] == 0.5 and v["resolved"] == 1.0
                and v["readback"] == 1.0):
            _ok("set→read 闭环 + 回执自带回读（before/resolved/readback 三口齐）")
        else:
            _fail(out, f"回读自证不符：value={v} read={rr}")
    else:
        _fail(out, f"合法 set_config 被拒：{r}")

    # 值域/枚举拒（不许误报合法值已在上一用例验证）
    r = tools.execute("set_config", {"key": "dynamic.method", "value": "quantum_torch"})
    if r["is_error"] and r["error"]["info"]["kind"] == "bad_enum":
        _ok("枚举外引擎方法被拒")
    else:
        _fail(out, f"枚举校验不符：{r}")

    # ---- 3b. 数值串收编 + reason 留痕（使用者 AI 实测坑 1/5 的回归钉） ----
    # 坑 1：宿主把未声明 type 的 value 转成 str ⇒ 工具层按键类型收编回数值。
    r = tools.execute("set_config", {"key": "field.B_gauss", "value": "2.5"})
    ok_coerce = (not r["is_error"]) and abs(
        gate.snapshot.get("field.B_gauss", 0) - 2.5) < 1e-9
    # 不许误伤：str 型键的字符串值原样过（method 上面已验合法值）
    r_bad = tools.execute("set_config", {"key": "field.B_gauss", "value": "abc"})
    # 坑 5：reason 进史（飞行记录仪折叠行的中文摘要来源）
    r_rr = tools.execute("set_config", {"key": "field.E_vpcm", "value": 1.0,
                                        "reason": "开 AC-Stark 看光移"})
    last_ev = history.events()[-1]
    if (ok_coerce and r_bad["is_error"]
            and not r_rr["is_error"] and last_ev.reason == "开 AC-Stark 看光移"):
        _ok("数值串收编（'2.5'→2.5）+ 非数字串仍拒 + reason 中文摘要进史")
    else:
        _fail(out, f"收编/reason 面不符：coerce={ok_coerce} "
                   f"bad={r_bad['is_error']} reason={last_ev.reason!r}")

    # ---- 4. 真配置骨架（groups/时长/网格/初态） ----
    for key, val in (
        ("groups.list", [{"n": 60, "l": 0, "j": 0.5}, {"n": 60, "l": 1, "j": 1.5}]),
        ("dynamic.mode", 0), ("dynamic.method", "mixed"),
        ("dynamic.t_max", 5.0), ("dynamic.npts", 50), ("dynamic.decay_index", 0),
    ):
        r = tools.execute("set_config", {"key": key, "value": val})
        if r["is_error"]:
            _fail(out, f"骨架键 {key} 被拒：{r['error']['message']}")
            return 1
    _ok("骨架配置经门写入成功（groups/mode/method/t_max/npts/decay）")

    # ---- 4b. ②a 防复发：声明的每个查询 op 经 run_actions 真跑通 ----
    r = tools.execute("run_actions",
                      {"actions": [{"op": q} for q in DOMAIN_QUERY_OPS]})
    if r.get("is_error"):
        _fail(out, f"run_actions 查询串报错：{r['error']['message']}")
    elif r["value"]["ok"] and r["value"]["stopped_at"] == -1:
        _ok(f"run_actions 逐个跑通声明查询：{[s['op'] for s in r['value']['steps']]}")
    else:
        _fail(out, f"run_actions 查询串未全通：{r['value']['steps']}")

    # ---- 5. query_levels → 真身份键 → 设初态 ----
    r = tools.execute("query_levels", {})
    if r["is_error"] or not r["value"]["states"]:
        _fail(out, f"query_levels 失败：{r}")
        return 1
    need = {"ok", "n_states", "states"}
    if not need <= set(r["value"]):
        _fail(out, f"query_levels 回执缺键：{set(r['value'])}")
    key0 = r["value"]["states"][0]["key"]
    r = tools.execute("set_config", {"key": "dynamic.psi_single", "value": key0})
    if r["is_error"]:
        _fail(out, f"设初态身份键被拒：{r['error']['message']}")
        return 1
    _ok("真身份键设进 dynamic.psi_single（语义寻址，不猜索引）")

    # ---- 6. estimate：est_sec 适配层成立 ----
    r = tools.execute("estimate_run", {})
    if not r["is_error"] and isinstance(r["value"].get("est_sec"), (int, float)):
        _ok(f"estimate 回执含 est_sec={r['value']['est_sec']}（单体粗估口径在 note）")
    else:
        _fail(out, f"est_sec 适配失败：{r}")

    # ---- 6b. summary：取 EL 公开声明面（methods 非空且与 enum 一致）----
    summ = surface.summary()
    if summ.get("engine") == "energy-level" and summ.get("methods"):
        _ok(f"summary 回引擎身份 + methods={summ['methods']}（公开声明面）")
    else:
        _fail(out, f"summary 不符：{summ}")

    # ---- 7. preview：basis_enough 契约 ----
    r = tools.execute("preview_run", {})
    if not r["is_error"] and isinstance(r["value"].get("basis_enough"), bool):
        _ok(f"preview 回执含 basis_enough={r['value']['basis_enough']}")
    else:
        _fail(out, f"preview 契约不符：{r}")

    # ---- 8. run_sim：真演化，回执含 ok/method（D-15 落地）----
    t0 = time.time()
    r = tools.execute("run_sim", {"cancel_after_sec": None})
    dt = time.time() - t0
    if r["is_error"]:
        _fail(out, f"run_sim 报错：{r['error']['message']}")
    elif r["value"].get("ok") and "method" in r["value"]:
        _ok(f"真演化 {dt:.1f}s 完成；回执 ok+method={r['value']['method']!r}"
            f"，top 布居 {list(r['value'].get('final_top_pop', {}))[:2]}")
    else:
        _fail(out, f"run_sim 回执缺 ok/method：{r}")

    # ---- 8b. ②b set_drive / read_waves：驱动手感端到端（顶层一步问到，P1-7）----
    # 用 intensity+duration 路径（不依赖具体 dme 符号，稳定）；写回单键、读回摘要。
    rl = tools.execute("query_levels", {})
    kk = [s["key"] for s in rl["value"]["states"][:2]]
    r = tools.execute("set_drive", {"src": kk[0], "tgt": kk[1],
                                    "intensity_mwcm2": 1.0, "pol": "σ+",
                                    "duration_ns": 10.0})
    if r["is_error"]:
        _fail(out, f"set_drive 被拒：{r['error']['message']}")
    elif r["value"]["key"] == "dynamic.waves_single" and r["value"]["wave"]["intensity"] == 1.0:
        rw = tools.execute("read_waves", {})      # 顶层工具直读（不再绕编排）
        waves = (rw["value"].get("waves")
                 if not rw["is_error"] else [])
        if waves and waves[0]["intensity_mwcm2"] == 1.0:
            _ok(f"set_drive 写波 + read_waves 顶层读回闭环（{len(waves)} 波，"
                f"freq={waves[0]['freq_mhz']:.1f}MHz）")
        else:
            _fail(out, f"read_waves 未读回刚写的波：{rw}")
    else:
        _fail(out, f"set_drive 回执不符：{r}")

    # ---- 8c. run_actions 结果带名字（实测报告 P1-6）----
    r = tools.execute("run_actions", {"actions": [{"op": "read_config"},
                                                  {"op": "no_such_action"}]})
    steps = r["value"]["steps"]
    if ("outputs" not in r["value"]
            and steps[0]["ok"] and isinstance(steps[0].get("output"), Mapping)
            and steps[0].get("detail")
            and not steps[1]["ok"] and steps[1]["detail"]):
        _ok("run_actions 每步自带 output+detail（成功有摘要、失败有原因，无位置数组）")
    else:
        _fail(out, f"run_actions 结果形状不符：{steps}")

    # ---- 8d. reset_config 清零入口（实测报告 P1-5）----
    r = tools.execute("reset_config", {"reason": "判据收台"})
    if (not r["is_error"] and r["value"]["n"] == len(r["value"]["cleared"])
            and r["value"]["n"] > 0
            and all(v is None for v in gate.snapshot.values())):
        _ok(f"reset_config 全键清空回默认（{r['value']['n']} 键，快照全 None）")
    else:
        _fail(out, f"reset_config 不符：{r}")

    # ---- 8e. 实测第二轮 §4 的顺手感（spec_patch / op 别名 / pick / t_max 对账）----
    # reset 已清空快照，重铺最小骨架（estimate 用得到 n_states/t_max/npts；
    # B 让 60S ±0.5 有 Zeeman 分裂，set_drive 的跃迁频率才非零）。
    for key, val in (
        ("groups.list", [{"n": 60, "l": 0, "j": 0.5}, {"n": 60, "l": 1, "j": 1.5}]),
        ("field.B_gauss", 1.0),
        ("dynamic.mode", 0), ("dynamic.method", "mixed"),
        ("dynamic.t_max", 5.0), ("dynamic.npts", 50), ("dynamic.decay_index", 0),
    ):
        tools.execute("set_config", {"key": key, "value": val})
    # 4-2 op 别名：顶层工具名 estimate_run/run_sim 直接当 op 用（不再"每次试一次"）
    r = tools.execute("run_actions", {"actions": [{"op": "estimate_run"}]})
    if not r["is_error"] and r["value"]["ok"] and r["value"]["steps"][0]["ok"]:
        _ok("run_actions 接受工具名 estimate_run 作 op 别名（4-2）")
    else:
        _fail(out, f"op 别名不符：{r}")
    # 4-1 spec_patch：在现有配置上只改 t_max（不重抄全量 spec），且**不写回快照**
    base = tools.execute("run_actions", {"actions": [{"op": "estimate"}]})
    patched = tools.execute("run_actions", {"actions": [
        {"op": "estimate", "args": {"spec_patch": {"dynamic.t_max": 10.0}}}]})
    b_sec = base["value"]["steps"][0]["output"]["est_sec"]
    p_sec = patched["value"]["steps"][0]["output"]["est_sec"]
    if (p_sec > b_sec and gate.snapshot.get("dynamic.t_max") == 5.0):
        _ok(f"spec_patch 只改变化量（est_sec {b_sec}→{p_sec}）且不污染域快照（4-1）")
    else:
        _fail(out, f"spec_patch 不符：base={b_sec} patched={p_sec} "
                   f"快照 t_max={gate.snapshot.get('dynamic.t_max')}")
    # 4-4 pick：只回关注量；写错的键如实进 _missing（绝不静默 null）
    r = tools.execute("run_actions", {
        "actions": [{"op": "estimate"}],
        "pick": ["est_sec", "no_such_key"]})
    po = r["value"]["steps"][0]["output"]
    if (po.get("est_sec") is not None and "n_states" not in po
            and po.get("_missing") == ["no_such_key"]):
        _ok("pick 投影每步 output（关注量在、非选键不回、错键列 _missing，4-4）")
    else:
        _fail(out, f"pick 不符：{po}")
    # 4-3 set_drive 回执对账 t_max（脉冲结束 vs 演化窗口）——不拦止、只提示。
    # 报告的实际陷阱是 t_max 晚于脉冲结束（终态含脉冲后自由演化）：快照 t_max=5，
    # 给一个 2ns 短脉冲 → 波结束 2ns < t_max 5ns → 应报"晚于"提示。
    rl = tools.execute("query_levels", {})
    kk = [s["key"] for s in rl["value"]["states"][:2]]
    r = tools.execute("set_drive", {"src": kk[0], "tgt": kk[1],
                                    "intensity_mwcm2": 1.0, "duration_ns": 2.0})
    v = r["value"]
    if (not r["is_error"] and v.get("pulse_end_ns") == 2.0
            and "t_max_note" in v and "晚于" in v["t_max_note"]):
        _ok("set_drive 回执带 pulse_end_ns + t_max 对账提示（4-3，脉冲 2ns< t_max 5ns→晚于）")
    else:
        _fail(out, f"set_drive t_max 对账不符：{v}")

    # ---- 9. History 可归因：所有写都带 actor=ai + 有 call 记录 ----
    evs = history.events()
    if evs and all(e.actor == "ai" for e in evs):
        _ok(f"History {len(evs)} 条全部归因 ai（通道钉死，Phase 5 侧证）")
    else:
        _fail(out, f"History 归因异常：{[(e.actor) for e in evs][:5]}")

    # ---- 10a. 缺初态：build 失败 → InvalidSpec 可教学（不静默兜底）----
    h2 = History()
    g2 = Gate(Authority(Mode.AI), h2, validate=make_validator(engine))
    g2.set(ai, "groups.list", [{"n": 60, "l": 0, "j": 0.5}], "r")
    g2.set(ai, "dynamic.mode", 0, "r")
    g2.set(ai, "dynamic.t_max", 5.0, "r")
    g2.set(ai, "dynamic.npts", 50, "r")
    try:
        surface.run(g2.snapshot, context=ExecutionContext())
        _fail(out, "缺初态竟跑成功")
    except MechaError as e:
        if e.kind == "build_failed" and e.hint:
            _ok("缺初态 → build_failed 可教学报错（EL 的'绝不静默回落'经门成立）")
        else:
            _fail(out, f"缺初态报错 kind/hint 不符：{e.kind}")

    # ---- 10b. 空演化：仍跑（EL 语义），但 check_run 的问题如实进 warnings（不静默）----
    g2.set(ai, "dynamic.psi_single", key0, "r")
    res = surface.run(g2.snapshot, context=ExecutionContext())
    warns = " ".join(res.get("warnings") or [])
    if res.get("ok") and "空演化" in warns:
        _ok("空演化配置：仍跑但 warnings 如实带出（告诉 AI，不静默——L3 反面）")
    else:
        _fail(out, f"空演化告警未如实带出：ok={res.get('ok')} warnings={res.get('warnings')}")

    if out:
        print(f"phase5_el_selfcheck: FAIL（{len(out)} 条）")
        return 1
    print(f"phase5_el_selfcheck: PASS（总耗时 {time.time() - t0:.0f}s 级）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
