"""Phase 10 判据：Monitor 契约（两层信任、Claim 对账、原始赢）。

总纲 §5.2④ / 硬纪律 5：Monitor 是人看的读面，分两层——

- **概括层**（derived）：我们写的、给人看的、**可能出错**。值可以是自由文本，
  也可以是 ``Claim(value, target=原始键)``——可被原始层证伪的最小单位。
- **原始层**（authoritative）：``History`` 折叠出的快照（``fold``），append-only，
  不随概括层的死活。

本判据逐条钉住 ``mecha/monitor.py`` 的**真实语义**（不是理想语义）：

  A 原始赢：Claim 与原始折叠值不等 ⇒ ``disputed=True``，且**权威值只从
    ``raw_snapshot`` 取**（那里是原始值）。概括里的谎话既不进 ``raw_snapshot``，
    也不被静默改写成"对的"——Monitor 只标记存疑、不改写概括（这是实现行为，
    判据照实现断言；"原始赢"落在 raw_snapshot + disputed 上，不落在改概括上）。
  B 对账一致不误报：Claim 与原始值相等 ⇒ 不 disputed，``dispute_reason`` 为空；
    用 ``target`` 显式指向原始键的**人话概括**同样可对账、同样不误报。
  C 无 target / target 不存在：省略 ``target`` 时用概括键名本身；目标键不在
    原始史里（省略 target 的键名不在史里，或显式 target 缺失）⇒ disputed。
    自由文本（值不是 ``Claim``）**不对账**⇒ 不触发 disputed，但它也**不获权威
    地位**——它进不了 ``raw_snapshot``，权威值仍只在原始层（机械不可判自由文本
    的属实性，归人审）。
  D 原始层独立可读：summarizer 抛错时 ``read()`` **原样抛出**（fail loud：不吞错、
    不把概括故障伪装成 disputed、不返回半份读数）；原始层不经 Monitor 也能读
    （``History.events()`` + ``fold`` 原样可用，换一个概括器立刻能读）。
    Monitor **未提供**"概括失败仍返回 raw_snapshot"的降级读数——本判据把这条
    如实登记为未支持，不用假绿替代。
  E L7 对偶：一份完全据原始史派生的概括全绿（不误报）。
  F 能抓坏：同一条 Claim 在原始史被改写后变 disputed（判据不是哑的，且"原始赢"
    跟的是原始层，不是判据里的硬编码期望）。

用法：python checks/phase10_monitor_contract_selfcheck.py
退出码：0 全绿；1 有失败。

## Known Limitations and Deferred Work

- 只测 Monitor 读面本身（对账口径、两层信任、disputed 裁决）；**不测**任何
  真宿主的概括器质量——概括函数的领域正确性归各宿主自己的判据（本模块只注入
  中性玩具概括器）。
- 不测 UI/呈现层如何显示 disputed（Monitor 是后端读面，呈现归项目）。
- D 组的"概括失败降级读数"是**未支持项**，判据里显式登记而不是断言为绿：
  要支持它得先有一个不抛错的读面形状（如 ``read_or_raw()``），那是新机制，
  n=0 不预建。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mecha.assembly import assemble  # noqa: E402
from mecha.authority import Mode  # noqa: E402
from mecha.gate import accept_all  # noqa: E402
from mecha.history import fold  # noqa: E402
from mecha.monitor import Claim, Monitor  # noqa: E402
from mecha.surface import Engine  # noqa: E402

#: 本判据如实登记的未支持项（打印出来，不算失败，也不假绿）。
UNSUPPORTED: list[str] = []


class ToyEngine(Engine):
    def schema(self):
        return {"run_output_required": ["ok"], "keys": ["power", "gain"]}

    def run(self, spec, *, context, **opts):
        return {"ok": True}

    def summary(self):
        return {"ok": True}

    def estimate(self, spec):
        return {"est_sec": 0.1}


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _fail(out: list[str], msg: str) -> None:
    print(f"  ✗ {msg}")
    out.append(msg)


def _software(summarizer):
    """装配一台玩具软件实例（Monitor 经真实装配点接线到同一份 History）。"""
    td = tempfile.mkdtemp(prefix="phase10-")
    return assemble(root=Path(td), data_dir_name="p10data", engine=ToyEngine(),
                    summarizer=summarizer, validate=accept_all, project="toyhost")


def _write(sw, key: str, value: object) -> None:
    """经 Gate 真实写一条原始史事件（不是直接 append，走装配后的唯一写口）。"""
    sw.authority.switch_mode(Mode.AI, side="human")
    sw.gate.set(sw.channels["ai"], key, value, "setpoint", call_id="p10")


def check_raw_wins_over_summary(out: list[str]) -> None:
    """A：概括与原始不一致 ⇒ disputed，权威值是原始值（原始赢）。"""
    with _software(lambda evs: {}) as sw:
        _write(sw, "power", 100)

        def lying(events):
            # 概括层谎报：说 power 是 999，且显式 target 指向真实原始键
            return {"power_reading": Claim(999, target="power")}

        view = Monitor(sw.history.events, lying).read()
        if view.disputed:
            _ok("Claim 与原始值不一致 ⇒ disputed")
        else:
            _fail(out, f"不一致的 Claim 竟不 disputed：{view}")
        if "power" in view.dispute_reason:
            _ok("dispute_reason 指名对不上的原始键（可教学）")
        else:
            _fail(out, f"dispute_reason 没指名键：{view.dispute_reason!r}")
        # 原始赢：权威值是原始层的 100，不是概括的 999
        if view.raw_snapshot["power"] == 100:
            _ok("裁决结果取原始值（raw_snapshot[power] == 100，不是概括的 999）")
        else:
            _fail(out, f"原始层被概括污染：{view.raw_snapshot}")
        # 如实记实现：Monitor 只标记存疑，**不改写**概括值
        if view.summary["power_reading"] == Claim(999, target="power"):
            _ok("概括值原样保留（只标记存疑，不静默改写——假话留作证据）")
        else:
            _fail(out, f"概括被悄悄改写了：{view.summary}")
        # 对照片：同一份原始史 + 诚实的概括 ⇒ 不 disputed（防"永远是红的"疯守卫）
        honest = Monitor(sw.history.events,
                         lambda evs: {"power_reading": Claim(100, target="power")}).read()
        if not honest.disputed and honest.raw_snapshot["power"] == 100:
            _ok("对照片：同一原始史上诚实概括不 disputed（不误报）")
        else:
            _fail(out, f"对照片不符：{honest}")


def check_consistent_claim_not_disputed(out: list[str]) -> None:
    """B：Claim 与原始值一致 ⇒ 不 disputed（含 target 显式指向的人话概括）。"""
    with _software(lambda evs: {}) as sw:
        _write(sw, "power", 100)
        _write(sw, "gain", 5)

        def derived(events):
            snap = fold(events)
            return {"读数.power": Claim(snap["power"], target="power"),
                    "读数.gain": Claim(snap["gain"], target="gain")}

        view = Monitor(sw.history.events, derived).read()
        if not view.disputed and view.dispute_reason == "":
            _ok("Claim 与原始值一致 ⇒ 不 disputed，dispute_reason 为空")
        else:
            _fail(out, f"一致的 Claim 被误报：{view}")
        if view.raw_snapshot == {"power": 100, "gain": 5}:
            _ok("原始层快照是原始史折叠（power=100 / gain=5）")
        else:
            _fail(out, f"原始层快照不符：{view.raw_snapshot}")
        pairs = [(k, v) for k, v in view.summary.items() if isinstance(v, Claim)]
        if len(pairs) == 2 and all(v.value == view.raw_snapshot[v.target or k]
                                  for k, v in pairs):
            _ok("人话键名 + 显式 target 的概括全部对得上（对账不靠猜键名）")
        else:
            _fail(out, f"人话概括对账不符：{view.summary}")


def check_missing_target(out: list[str]) -> None:
    """C：无 target / target 不存在 ⇒ disputed；自由文本不对账也不获权威。"""
    with _software(lambda evs: {}) as sw:
        _write(sw, "power", 100)

        # (1) 省略 target：用概括键名本身当目标键；键不在原始史里 ⇒ disputed
        blank = Monitor(sw.history.events, lambda evs: {"ghost.key": Claim(1)}).read()
        if blank.disputed and "ghost.key" in blank.dispute_reason:
            _ok("省略 target ⇒ 用概括键名当目标；键不在原始史里 ⇒ disputed")
        else:
            _fail(out, f"省略 target 的悬空键竟不 disputed：{blank}")

        # (2) 显式 target 不存在：哪怕值看起来合理也 disputed（不许编值）
        phantom = Monitor(sw.history.events,
                          lambda evs: {"别名": Claim(100, target="no.such.key")}).read()
        if phantom.disputed and "no.such.key" in phantom.dispute_reason:
            _ok("显式 target 在原始史里不存在 ⇒ disputed（不许编值）")
        else:
            _fail(out, f"不存在的 target 竟不 disputed：{phantom}")

        # (3) 自由文本不比对 ⇒ 不 disputed；但它也不进原始层（无权威地位）
        prose = Monitor(sw.history.events,
                        lambda evs: {"headline": "power 已经涨到 9999 了"}).read()
        if not prose.disputed:
            _ok("自由文本不对账 ⇒ 不 disputed（机械不可判其属实性，归人审）")
        else:
            _fail(out, f"自由文本竟触发了对账：{prose}")
        if "headline" not in prose.raw_snapshot and prose.raw_snapshot["power"] == 100:
            _ok("自由文本不获权威地位：进不了原始层，权威值仍是原始史")
        else:
            _fail(out, f"自由文本污染了原始层：{prose.raw_snapshot}")

        # (4) 裸值（不是 Claim）同样不对账——只有 Claim 是可证伪单位
        bare = Monitor(sw.history.events, lambda evs: {"power": 999}).read()
        if not bare.disputed and bare.raw_snapshot["power"] == 100:
            _ok("非 Claim 的值不参与对账（只有 Claim 是可证伪单位），原始层不变")
        else:
            _fail(out, f"裸值对账语义不符：disputed={bare.disputed} "
                       f"raw={bare.raw_snapshot}")


def check_raw_layer_is_independent(out: list[str]) -> None:
    """D：概括层故障不拖垮原始层；Monitor 的降级读数当前**未支持**（如实登记）。"""

    def boom(events):
        raise RuntimeError("summarizer exploded")

    with _software(boom) as sw:
        _write(sw, "power", 100)
        raw_before = fold(sw.history.events())

        # ⚠ **2026-09-26 行为变更**：概括层故障**不再整体抛**，改成**降级读数**
        # （原始层照给 + disputed=True + summary_error 明文）。
        # 旧断言（读 `read()` 原样抛出）连同上面那条"未支持"登记**一起退役**——
        # 退役理由不是"它错了"，而是**它守的那个契约不存在了**（那正是当时的诚实登记）。
        lowered = sw.monitor.read()                     # 概括器仍是那个会炸的
        if (lowered.disputed is True and lowered.summary == {}
                and "RuntimeError" in lowered.summary_error
                and lowered.raw_snapshot == {"power": 100}):
            _ok("概括层故障 ⇒ **降级读数**（raw 照给 / disputed=True / summary_error 带类型名）")
        else:
            _fail(out, f"故障降级读数不符：{lowered}")
        if lowered.dispute_reason and lowered.dispute_reason == lowered.summary_error:
            _ok("故障在 wire 上可读：dispute_reason 与 summary_error 同时给出原因")
        else:
            _fail(out, f"故障原因没透出：{lowered.dispute_reason!r}")

        # 原始层不经概括层也能读：同一份 History 直接折叠
        events = sw.history.events()
        if events and fold(events) == {"power": 100} == raw_before:
            _ok("原始层独立可读（History.events + fold 原样可用，与概括层死活无关）")
        else:
            _fail(out, f"原始层读不出来：{[e.target for e in events]}")

        # 原始层是唯一权威：换一个健全概括器，立刻读到原始真值
        healthy = Monitor(sw.history.events, lambda evs: {"power": Claim(100)}).read()
        if not healthy.disputed and healthy.raw_snapshot["power"] == 100:
            _ok("换回健全概括器即可读（原始层不依赖任何一次概括的成败）")
        else:
            _fail(out, f"健全概括器读不到原始真值：{healthy}")

        # 2026-09-26：上面那条"未支持"登记**已兑现**（降级读数已实现）⇒ 登记删除，
        # 否则它会变成一句假话（诚实登记的纪律：兑现了就撤）。


def check_l7_derived_summary_all_green(out: list[str]) -> None:
    """E：完全据原始史派生的概括全绿（L7 对偶：合规不误报）。"""
    with _software(lambda evs: {}) as sw:
        _write(sw, "power", 100)
        _write(sw, "gain", 5)

        def derived(events):
            snap = fold(events)
            out_summary: dict[str, object] = {
                "headline": f"原始史共 {len(events)} 条事件",
                "raw_keys": list(snap),
            }
            for k, v in snap.items():
                out_summary[f"读数.{k}"] = Claim(v, target=k)
            return out_summary

        view = Monitor(sw.history.events, derived).read()
        claims = [v for v in view.summary.values() if isinstance(v, Claim)]
        if not view.disputed and len(claims) == 2:
            _ok("派生概括（每条可对账）全绿：不误报、dispute_reason 为空")
        else:
            _fail(out, f"合规派生概括被误报：{view}")
        if view.dispute_reason == "":
            _ok("不 disputed 时 dispute_reason 是空串（不留悬空理由）")
        else:
            _fail(out, f"不 disputed 却有理由：{view.dispute_reason!r}")


def check_catches_stale_claim_after_raw_change(out: list[str]) -> None:
    """F：能抓坏——原始史改写后同一条 Claim 必须变 disputed。"""
    with _software(lambda evs: {}) as sw:
        _write(sw, "power", 100)

        def freezes_first(events):
            # 冻结"第一条 power 事件"的值当概括（陈旧概括的典型形态）
            vals = [e.after for e in events if e.target == "power"]
            return {"power": Claim(vals[0], target="power")} if vals else {}

        first = Monitor(sw.history.events, freezes_first).read()
        if not first.disputed and first.raw_snapshot["power"] == 100:
            _ok("对照：原始史只有一条时，同一概括不 disputed（防判据恒红）")
        else:
            _fail(out, f"对照读数不符：{first}")

        _write(sw, "power", 999)        # 原始史改写（概括没跟着改）

        second = Monitor(sw.history.events, freezes_first).read()
        if second.disputed:
            _ok("原始值一改，同一条 Claim 立刻 disputed（判据不是哑的）")
        else:
            _fail(out, f"原始史改了 Claim 却没被抓到：{second}")
        if second.raw_snapshot["power"] == 999:
            _ok("抓坏后裁决仍取原始值（999），陈旧概括（100）不冒充权威")
        else:
            _fail(out, f"抓坏后原始层不符：{second.raw_snapshot}")
        if "999" in second.dispute_reason and "100" in second.dispute_reason:
            _ok("dispute_reason 同时给出概括值与原始值（对账可复核）")
        else:
            _fail(out, f"dispute_reason 缺对账细节：{second.dispute_reason!r}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out: list[str] = []
    print("Phase 10 Monitor 契约判据（两层信任 / Claim 对账 / 原始赢）：")
    check_raw_wins_over_summary(out)
    check_consistent_claim_not_disputed(out)
    check_missing_target(out)
    check_raw_layer_is_independent(out)
    check_l7_derived_summary_all_green(out)
    check_catches_stale_claim_after_raw_change(out)
    for note in UNSUPPORTED:
        print(f"  – 未支持（如实登记，不计为绿）：{note}")
    if out:
        print(f"phase10_monitor_contract_selfcheck: FAIL（{len(out)} 条）")
        return 1
    print("phase10_monitor_contract_selfcheck: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
