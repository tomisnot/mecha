"""Phase 1 装配判据（L6：测装配不测单元；L7：含"不许误报"用例）。

真装配 Gate+History+Authority+Monitor+Provider，从公开入口跑，断言：

  洞1（写权门唯一 + actor 由通道决定）：AI 通道伪造不了 human、越不了 mode。
  门在执行器上：绕过 set 的直写无入口；validate 失败则快照一字节不动。
  call-id 互引：set 带 call_id 落进 History。
  History append-only + 从盘续：sink 重建快照一致。
  Monitor：概括被原始证伪时原始赢 + 存疑；一致时不误报（L7）。
  格式迁移链：相邻边可用、缺边拒、未来版本拒、拒识清单命中。
  数据目录：不注入报错、注入可用。

每个"能抓坏"用例都配一个"不许误报"对偶（L7：证明它不是永远返红的哑守卫）。

用法：python checks/phase1_assembly_selfcheck.py
退出码：0 全绿；1 有失败。

## Known Limitations and Deferred Work

- 用内存 LogSink（InMemorySink）装配，不碰真实文件——落盘 Provider 的接线由
  Phase 3 的 provider 判据覆盖。
- 不测多进程串行写（History 文档已声明 n=1 假设）。
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mecha.authority import Authority, Mode
from mecha.data_layout import resolve_data_dir
from mecha.errors import GateDenied, MechaError
from mecha.format_version import MigrationChain, FORMAT_VERSION
from mecha.gate import Channel, Gate, accept_all
from mecha.history import Event, History
from mecha.monitor import Claim, Monitor


class InMemorySink:
    """最小 LogSink Provider（装配用）。"""

    def __init__(self) -> None:
        self._records: list[dict] = []

    def append(self, record: dict) -> None:
        self._records.append(dict(record))

    def since(self, seq: int) -> Iterable[dict]:
        return [r for r in self._records if r["seq"] > seq]

    def last_seq(self) -> int:
        return self._records[-1]["seq"] if self._records else 0


def _make_gate(mode: Mode = Mode.AI, validate=accept_all, sink=None):
    auth = Authority(mode=mode)
    hist = History(sink)
    gate = Gate(auth, hist, validate=validate)
    return auth, hist, gate


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _fail(out: list[str], msg: str) -> None:
    print(f"  ✗ {msg}")
    out.append(msg)


def check_gate_authority(out: list[str]) -> None:
    """洞1：actor 由通道决定 + 门在执行器上。"""
    # 能抓坏：ai 通道在 HUMAN 模式写 → 拒
    _, hist, gate = _make_gate(Mode.HUMAN)
    ai = Channel("ai")
    try:
        gate.set(ai, "k", 1, "reason")
        _fail(out, "ai 通道在 HUMAN 模式竟写成功")
    except GateDenied:
        _ok("ai 通道在 HUMAN 模式被拒")
    # 不许误报：human 通道在 HUMAN 模式写 → 成
    human = Channel("human")
    ev = gate.set(human, "k", 1, "reason")
    if ev.seq == 1 and gate.snapshot.get("k") == 1:
        _ok("human 通道在 HUMAN 模式写入成功（不误报）")
    else:
        _fail(out, "human 通道合法写入未落地")

    # 能抓坏：伪造 actor —— Channel 不接受任意字符串
    try:
        Channel("human' or 1=1")
        _fail(out, "Channel 竟接受了伪造 actor")
    except GateDenied:
        _ok("Channel 拒绝未知 actor（无法伪造人类归因）")

    # 能抓坏：ai 通道切模式 → 拒（AI 不能给自己解锁）
    auth, _, _ = _make_gate(Mode.LOCKED)
    try:
        auth.switch_mode(Mode.AI, side="ai")
        _fail(out, "ai 侧竟能切模式")
    except GateDenied:
        _ok("ai 侧切模式被拒")
    # 不许误报：human 切模式 → 成
    auth.switch_mode(Mode.AI, side="human")
    if auth.mode is Mode.AI:
        _ok("human 切模式成功（不误报）")
    else:
        _fail(out, "human 合法切模式未生效")
    # 审查 2.4 根除验证：库层只认 side，细标签 'gui' 直传必被拒（同名不同义堵死）
    try:
        auth.switch_mode(Mode.HUMAN, side="gui")
        _fail(out, "细标签 'gui' 直传 authority 竟被接受（应只认 side）")
    except GateDenied:
        _ok("authority 只认 side：细标签 'gui' 直传被拒（2.4 陷阱根除）")

    # 标签通道：side 定写权、actor 进史做归因（EL 消费方依赖）
    _, hist2, gate2 = _make_gate(Mode.HUMAN)
    gui = Channel("gui", "human")           # 标签 gui，side human
    ev = gate2.set(gui, "power", 5, "r")
    if ev.actor == "gui" and gate2.snapshot.get("power") == 5:
        _ok("Channel('gui','human')：写权看 side、史记 actor=gui（来源归因保留）")
    else:
        _fail(out, f"标签通道归因不符：{ev}")
    # ai 侧标签不得借 human 闸：side=ai 在 HUMAN 模式被拒
    try:
        gate2.set(Channel("mcp", "ai"), "power", 9, "r")
        _fail(out, "ai 侧标签竟在 HUMAN 模式写成功")
    except GateDenied:
        _ok("标签不扩权：Channel('mcp','ai') 在 HUMAN 模式仍被拒")
    # 未知 side 拒
    try:
        Channel("gui", "admin")
        _fail(out, "Channel 竟接受未知 side")
    except GateDenied:
        _ok("未知 side 的通道构造被拒")


def check_gate_executor_not_wrapper(out: list[str]) -> None:
    """门在执行器上：绕过 set 的直写无入口；validate 失败快照不动。"""
    # 快照是私有 + 只读投影：外部改副本不回写
    _, _, gate = _make_gate(Mode.AI)
    gate.set(Channel("ai"), "a", 1, "r")
    snap = gate.snapshot
    snap["a"] = 999
    snap["injected"] = True
    if gate.snapshot.get("a") == 1 and "injected" not in gate.snapshot:
        _ok("外部改快照副本不回写门内状态（无绕过写口）")
    else:
        _fail(out, "快照副本可回写门内状态——门漏在包装层")

    # validate 失败：快照一字节不动 + History 不追加
    def bad_validate(key, value):
        if value == "forbidden":
            raise GateDenied("禁值", kind="validate", hint="h")

    hist = InMemorySink()
    _, memhist, gate = _make_gate(Mode.AI, validate=bad_validate, sink=hist)
    before = dict(gate.snapshot)
    try:
        gate.set(Channel("ai"), "x", "forbidden", "r")
        _fail(out, "validate 失败竟落地")
    except GateDenied:
        pass
    if gate.snapshot == before and memhist.last_seq == 0 and len(hist._records) == 0:
        _ok("validate 失败：快照未动 + History 未追加（报错时一字节不动）")
    else:
        _fail(out, "validate 失败仍有副作用")


def check_seed_also_validates(out: list[str]) -> None:
    """⭐ `seed` **也过 validate**：它免的是**写权门**，不免**校验**。

    ⚠ 2026-09-26 之前 `seed` **连 validate 也跳过** ⇒ "快照里只放校验过的值"这条
    不变量对 seed 不成立；而那句旧 docstring"仍走 validate **之外**的同一落地路径"
    很容易读反（字面"在 validate 之外"，读者却会读成"验证仍在"）。
    ⚠ **别改回去**：初值也是值，凭它绕过校验就是让快照可以装"非法值"。
    """

    def bad_validate(key, value):
        if value == "forbidden":
            raise GateDenied("禁值", kind="validate", hint="h")

    hist = InMemorySink()
    _, memhist, gate = _make_gate(Mode.LOCKED, validate=bad_validate, sink=hist)
    # ① 合法初值：LOCKED 下也能写（免的是写权门，不是校验）
    gate.seed("ok_key", "fine", actor="bootstrap")
    if gate.snapshot.get("ok_key") == "fine" and memhist.last_seq == 1:
        _ok("seed 免写权门：LOCKED 下装配期初值仍可落（并记史）")
    else:
        _fail(out, f"seed 在 LOCKED 下没落地：{gate.snapshot} seq={memhist.last_seq}")
    # ② 非法初值：**必须被 validate 拦下**，且快照/史都不动
    snap_before, seq_before = dict(gate.snapshot), memhist.last_seq
    try:
        gate.seed("bad_key", "forbidden", actor="bootstrap")
        _fail(out, "seed 把非法值写进了快照（validate 被跳过）")
    except GateDenied:
        if gate.snapshot == snap_before and memhist.last_seq == seq_before:
            _ok("seed 的非法初值被 validate 拦下：快照未动 + 史未追加")
        else:
            _fail(out, f"seed 被拒却留下痕迹：{gate.snapshot} seq={memhist.last_seq}")


class _FlakySink(InMemorySink):
    """可控失败替身：预检拒某个值 / 第 n 次 append 抛（模拟磁盘满那类**不可预见**失败）。"""

    def __init__(self, *, fail_precheck_for=None, fail_append_at=None) -> None:
        super().__init__()
        self.fail_precheck_for = fail_precheck_for
        self.fail_append_at = fail_append_at
        self.appends = 0

    def precheck(self, record: dict) -> None:
        if (self.fail_precheck_for is not None
                and record.get("after") == self.fail_precheck_for):
            raise MechaError("值不可编码（替身）", kind="bad_event_value")

    def append(self, record: dict) -> None:
        self.appends += 1
        if self.fail_append_at is not None and self.appends == self.fail_append_at:
            raise OSError("磁盘满（替身）")
        super().append(record)


def check_write_all_or_nothing(out: list[str]) -> None:
    """写口**全有或全无**：可预见失败被**预检**挡在落地前；不可预见失败靠**回滚**兜。

    ⚠ 目标不是"批全落"（append-only 日志**撤不回**），而是 **快照 == fold(史)** ——
    盲目"全回滚"会把这条不变量弄坏（快照会丢掉史里已经有的写）。
    """
    ai = Channel("ai")

    # ① 预检（单键）：值不可编码 ⇒ 快照/史一个字节都不动
    sink = _FlakySink(fail_precheck_for="BAD")
    _, memhist, gate = _make_gate(Mode.AI, sink=sink)
    gate.set(ai, "k", "ok", "r")
    before = (dict(gate.snapshot), memhist.last_seq, len(sink._records))
    try:
        gate.set(ai, "k2", "BAD", "r")
        _fail(out, "预检失败竟落地")
    except MechaError:
        pass
    if (dict(gate.snapshot), memhist.last_seq, len(sink._records)) == before:
        _ok("预检失败（可预见）⇒ 快照/史**一个字节都不动**（失败发生在落地之前）")
    else:
        _fail(out, f"预检失败仍留痕：{gate.snapshot} seq={memhist.last_seq}")

    # ①b 预检（批量）：任一键不过 ⇒ **一个都不落地**
    sink2 = _FlakySink(fail_precheck_for="BAD")
    _, memhist2, gate2 = _make_gate(Mode.AI, sink=sink2)
    try:
        gate2.set_batch(ai, {"a": 1, "b": "BAD", "c": 3}, "r")
        _fail(out, "批量预检失败竟落地")
    except MechaError:
        pass
    if gate2.snapshot == {} and memhist2.last_seq == 0 and sink2._records == []:
        _ok("批量预检失败 ⇒ **一个都不落地**（校验/预检阶段是原子的）")
    else:
        _fail(out, f"批量半落地：{gate2.snapshot} seq={memhist2.last_seq}")

    # ② 回滚（批量）：第 3 次 append 抛 ⇒ `a`（第 2 次）已进史、`b`/`c` 回滚
    #   ⚠ 计数要数准：`keep` 是第 1 次 ⇒ `fail_append_at=3` 落在 `b` 上
    #   （我第一版写 2，结果失败落在 `a` 上，`a` 本来就该回滚——**是判据的期望错了**）
    sink3 = _FlakySink(fail_append_at=3)
    _, memhist3, gate3 = _make_gate(Mode.AI, sink=sink3)
    gate3.set(ai, "keep", "v0", "r")
    try:
        gate3.set_batch(ai, {"a": 1, "b": 2, "c": 3}, "r")
        _fail(out, "append 失败竟没抛")
    except OSError:
        pass
    folded = {e.target: e.after for e in memhist3.events()}
    if gate3.snapshot == folded:
        _ok("落地期失败（不可预见）⇒ 快照 **== fold(史)**（不变量保住了）")
    else:
        _fail(out, f"快照与史不一致：snap={gate3.snapshot} fold={folded}")
    if gate3.snapshot.get("a") == 1 and "b" not in gate3.snapshot:
        _ok("回滚粒度对：已进史的 a 留着、没进史的 b/c 回滚（不是盲目全回滚）")
    else:
        _fail(out, f"回滚粒度不对：{gate3.snapshot}")

    # ②b 回滚（单键·新键）：**真的不在**快照里（不是留个 None）+ 内存史未推进
    sink4 = _FlakySink(fail_append_at=1)
    _, memhist4, gate4 = _make_gate(Mode.AI, sink=sink4)
    try:
        gate4.set(ai, "brand_new", "v", "r")
        _fail(out, "单键 append 失败竟没抛")
    except OSError:
        pass
    if "brand_new" not in gate4.snapshot and memhist4.last_seq == 0:
        _ok("单键回滚：新键**真的不在**快照里（不是留着 None）＋ 内存史未推进")
    else:
        _fail(out, f"单键回滚留下残迹：{gate4.snapshot} seq={memhist4.last_seq}")

    # ②c 回滚（单键·覆盖写）：回到**旧值**（"本来没有"与"本来是 None"必须能区分）
    sink5 = _FlakySink(fail_append_at=2)
    _, _memhist5, gate5 = _make_gate(Mode.AI, sink=sink5)
    gate5.set(ai, "k", "old", "r")
    try:
        gate5.set(ai, "k", "new", "r")
        _fail(out, "覆盖写 append 失败竟没抛")
    except OSError:
        pass
    if gate5.snapshot.get("k") == "old":
        _ok("单键回滚：原有键**回到旧值**（哨兵区分「本来没有」与「本来是 None」）")
    else:
        _fail(out, f"旧值没复原：{gate5.snapshot}")

    # ③ dry-run（A5）：两道闸都试、都不留痕
    def only_ok(key, value):
        if value == "BAD":
            raise GateDenied("禁值", kind="validate", hint="h")

    sink6 = _FlakySink(fail_precheck_for="UNSERIALIZABLE")
    _, memhist6, gate6 = _make_gate(Mode.AI, validate=only_ok, sink=sink6)
    passed = True
    try:
        gate6.validate_only("k", "fine")
    except Exception:                                   # noqa: BLE001 - 替身不该抛
        passed = False
    if passed and gate6.snapshot == {} and memhist6.last_seq == 0 and not sink6._records:
        _ok("validate_only 放行合法值：**不落地、不记史**（dry-run 无痕）")
    else:
        _fail(out, f"validate_only 留了痕：{gate6.snapshot} seq={memhist6.last_seq}")
    caught = []
    for label, val, exc in (("校验", "BAD", GateDenied),
                            ("预检", "UNSERIALIZABLE", MechaError)):
        try:
            gate6.validate_only("k", val)
            _fail(out, f"validate_only 没试出「{label}」那道闸")
        except exc:
            caught.append(label)
    if len(caught) == 2:
        _ok("validate_only **两道闸都试**（校验 + 预检），失败即抛、仍不留痕")


def check_open_mode(out: list[str]) -> None:
    """新增 `Mode.OPEN`：**两侧都能写**（2026-09-26）；出厂默认与既有三模式**一字不改**。

    ⚠ `OPEN` 的代价是**后写覆盖**（没有先到先得、没有 CAS）——它是给"人机低冲突"的
    项目去掉那道多余的闸，不是把写权变成没有闸。
    """
    human, ai = Channel("human"), Channel("ai")

    # ① 新模式：**两个都要**（只有一边过 = 没做到）
    #   ⚠ 用 try 收集而不是直接写：被测对象坏了时判据要**报红**，不是**崩**
    _, hist, gate = _make_gate(Mode.OPEN)
    wrote: list[str] = []
    for ch, key, val in ((human, "h", 1), (ai, "a", 2)):
        try:
            gate.set(ch, key, val, "r")
            wrote.append(ch.side)
        except GateDenied:
            pass
    if wrote == ["human", "ai"] and gate.snapshot == {"h": 1, "a": 2}:
        _ok("Mode.OPEN：human 与 ai **两侧都能写**（缺一不可）")
    else:
        _fail(out, f"OPEN 下不是两侧都能写：写成 {wrote}，快照 {gate.snapshot}")
    actors = [e.actor for e in hist.events()]
    if actors == wrote:
        _ok("OPEN 不模糊归因：写成的每笔都按通道记 actor（写权放开≠归因模糊）")
    else:
        _fail(out, f"OPEN 下归因不对：{actors}（写成 {wrote}）")

    # ② 对偶一：出厂 LOCKED **两侧仍都被拒**（安全默认没被削弱）
    _, _, locked = _make_gate(Mode.LOCKED)
    for ch in (human, ai):
        try:
            locked.set(ch, "k", 1, "r")
            _fail(out, f"LOCKED 下 {ch.side} 侧竟写成功")
        except GateDenied:
            pass
    if locked.snapshot == {}:
        _ok("对偶：出厂 LOCKED 下**两侧仍都被拒**（安全默认没被削弱）")
    else:
        _fail(out, f"LOCKED 下竟落了值：{locked.snapshot}")

    # ③ 对偶二：写权的**互斥一字未改**（三层架构下它仍是"同一时刻只一侧能写"）
    _, _, g_human = _make_gate(Mode.HUMAN)
    _, _, g_ai = _make_gate(Mode.AI)
    still_exclusive = True
    try:
        g_human.set(ai, "x", 1, "r")          # HUMAN 模式下 ai 仍应被拒
        still_exclusive = False
    except GateDenied:
        pass
    try:
        g_ai.set(human, "y", 1, "r")          # AI 模式下 human 仍应被拒
        still_exclusive = False
    except GateDenied:
        pass
    g_human.set(human, "x", 1, "r")           # 各自的正侧仍可写（不误伤）
    g_ai.set(ai, "y", 1, "r")
    if (still_exclusive and g_human.snapshot == {"x": 1}
            and g_ai.snapshot == {"y": 1}):
        _ok("对偶：HUMAN 下 ai 仍被拒、AI 下 human 仍被拒（既有语义零变化）")
    else:
        _fail(out, f"既有模式的互斥被改动：{(g_human.snapshot, g_ai.snapshot)}")

    # ④ 新模式**不能**被 ai 侧切进来（切模式仍只 human）
    auth = Authority(mode=Mode.LOCKED)
    try:
        auth.switch_mode(Mode.OPEN, side="ai")
        _fail(out, "ai 侧竟能切到 OPEN（AI 给自己开闸）")
    except GateDenied as e:
        if e.kind == "authority_switch_denied":
            _ok("OPEN 也只能由 human 侧切（ai 侧切被拒、kind 正确）")
        else:
            _fail(out, f"ai 切模式报错 kind 不对：{e.kind}")
    auth.switch_mode(Mode.OPEN, side="human")
    if auth.mode is Mode.OPEN:
        _ok("human 侧可切到 OPEN（新模式真的可用）")
    else:
        _fail(out, f"human 切 OPEN 未生效：{auth.mode}")


def check_call_id_crossref(out: list[str]) -> None:
    """call-id 互引：set 带 call_id 落进 History。"""
    _, hist, gate = _make_gate(Mode.AI)
    gate.set(Channel("ai"), "k", 5, "reason", call_id="call-42")
    ev = hist.events()[-1]
    if ev.call_id == "call-42" and ev.actor == "ai":
        _ok("call_id + actor 落进 History（可双向走查）")
    else:
        _fail(out, f"call_id 未互引：{ev!r}")


def check_history_append_only_resume(out: list[str]) -> None:
    """append-only + 从盘续：sink 重建快照一致。"""
    sink = InMemorySink()
    _, hist, gate = _make_gate(Mode.AI, sink=sink)
    gate.set(Channel("ai"), "a", 1, "r")
    # 用同一 sink 重建：模拟进程重启（dsh 消失后独立可用）
    gate2 = Gate(Authority(Mode.AI), History(sink), validate=accept_all)
    rebuilt = gate2.snapshot
    if rebuilt == {"a": 1}:
        _ok("从 sink 重建快照一致（dsh 消失后独立可用）")
    else:
        _fail(out, f"重建快照不符：{rebuilt}")
    # seq 续上：新写不撞已有 seq
    ev = gate2.set(Channel("ai"), "b", 2, "r")
    if ev.seq == 2:
        _ok("seq 从盘上续（不重置为 1）")
    else:
        _fail(out, f"seq 未续：{ev.seq}")


def check_monitor_reconcile(out: list[str]) -> None:
    """Monitor：原始赢 + 存疑；一致时不误报（L7 对偶）。"""
    _, hist, gate = _make_gate(Mode.AI)
    gate.set(Channel("ai"), "power", 100, "r")

    # 能抓坏：概括复述了一个与原始不符的值
    def wrong_summarizer(events):
        return {"power": Claim(999), "note": "自由文本不比对"}
    m_bad = Monitor(lambda: hist.events(), wrong_summarizer)
    v = m_bad.read()
    if v.disputed and v.raw_snapshot["power"] == 100:
        _ok("概括与原始不符 → 存疑 + 原始赢")
    else:
        _fail(out, f"对账未抓到不一致：{v}")

    # 不许误报：概括与原始一致
    def good_summarizer(events):
        return {"power": Claim(100)}
    v2 = Monitor(lambda: hist.events(), good_summarizer).read()
    if not v2.disputed:
        _ok("概括与原始一致 → 不存疑（不误报）")
    else:
        _fail(out, f"一致却报存疑：{v2.dispute_reason}")

    # 能抓坏：概括声称了原始没有的键
    def ghost(events):
        return {"nonexistent": Claim(1)}
    v3 = Monitor(lambda: hist.events(), ghost).read()
    if v3.disputed:
        _ok("概括声称原始没有的键 → 存疑")
    else:
        _fail(out, "幽灵键未被抓到")

    # 审查 P3-b：人话键名 + 显式 target 指向原始键 ⇒ 可对账
    def plain_ok(events):
        return {"功率": Claim(100, target="power")}
    v4 = Monitor(lambda: hist.events(), plain_ok).read()
    if not v4.disputed:
        _ok("人话键名 Claim(target=原始键) 与原始一致 → 可对账不存疑")
    else:
        _fail(out, f"target 对账失败：{v4.dispute_reason}")

    # 能抓坏：人话键名 + target 指向的原始值不符 → 原始赢
    def plain_lie(events):
        return {"功率": Claim(7, target="power")}
    v5 = Monitor(lambda: hist.events(), plain_lie).read()
    if v5.disputed and "power" in v5.dispute_reason:
        _ok("人话概括谎报 target 值 → 存疑且归因到原始键")
    else:
        _fail(out, f"target 谎报未被抓到：{v5}")


def check_validate_required(out: list[str]) -> None:
    """审查 P5-2：validate 必填，缺失当场拒（不静默放行一切值）。"""
    try:
        Gate(Authority(Mode.AI), History(), validate=None)
        _fail(out, "缺 validate 的 Gate 竟构造成功（静默放行）")
    except MechaError as e:
        if e.kind == "missing_validator":
            _ok("validate 必填：缺失当场拒，显式 accept_all 才放行（P5-2）")
        else:
            _fail(out, f"缺 validate 报错 kind 不对：{e.kind}")


def check_format_migration(out: list[str]) -> None:
    """格式迁移链：相邻边可用、缺边拒、未来版本拒、拒识清单命中。"""
    chain = MigrationChain()
    # v1 是当前版本，v0→v1 加一条边
    chain.add_stage(0, lambda evts: [{**e, "migrated": True} for e in evts])
    got = chain.upgrade("v0", [{"seq": 1}])
    if got and got[0].get("migrated"):
        _ok("相邻边 v0->v1 迁移可用")
    else:
        _fail(out, "相邻边迁移未执行")

    # 缺边拒：v1->v2 不存在（若把当前版本设为更高）——用未来版本测
    try:
        chain.upgrade(f"v{FORMAT_VERSION + 5}", [])
        _fail(out, "未来版本竟可读")
    except MechaError as e:
        if e.kind == "future_format":
            _ok("未来版本被拒（降级不支持）")
        else:
            _fail(out, f"未来版本报错 kind 不对：{e.kind}")

    # 拒识清单命中
    chain2 = MigrationChain()
    chain2.add_stage(0, lambda e: e)
    chain2.reject("v0", 0, "该来源是损坏导出，故意不支持")
    try:
        chain2.upgrade("v0", [{"seq": 1}])
        _fail(out, "拒识清单未生效")
    except MechaError as e:
        if e.kind == "rejected_conversion":
            _ok("corpus 拒识清单命中并给出确切原因")
        else:
            _fail(out, f"拒识报错 kind 不对：{e.kind}")


def check_data_layout(out: list[str]) -> None:
    """数据目录：不注入报错、注入可用、含分隔符报错。"""
    try:
        resolve_data_dir(Path("/tmp/x"), None)
        _fail(out, "不给目录名竟不报错")
    except MechaError as e:
        if e.kind == "missing_data_dir":
            _ok("不注入目录名明确报错（不猜路径）")
        else:
            _fail(out, f"缺目录名报错 kind 不对：{e.kind}")
    dl = resolve_data_dir(Path("/tmp/x"), "mydata")
    if dl.data_dir == Path("/tmp/x/mydata"):
        _ok("注入目录名可用（不误报）")
    else:
        _fail(out, f"注入解析不符：{dl.data_dir}")
    try:
        resolve_data_dir(Path("/tmp/x"), "a/b")
        _fail(out, "含分隔符目录名竟通过")
    except MechaError as e:
        if e.kind == "bad_data_dir":
            _ok("含路径分隔符的目录名被拒")


def check_event_ts_and_undoable(out: list[str]) -> None:
    """② 账本两样（2026-09-26）：`ts` 可选加成 + `undoable` **三态槽位**。

    ⚠ `ts`：**老记录（无 ts）读回空串**（＝"早于本特性"，不是"时间未知的错"）；
    ⛔ **不许用 `ts` 排序/去重** —— `seq` 仍是唯一顺序（同秒多条、时钟回拨都可能）。
    ⚠ `undoable`：`True`/`False`/**`None`＝未声明**；用布尔会把"没声明"冒充成"不可撤"。
    """
    _, hist, gate = _make_gate(Mode.AI)
    gate.set(Channel("ai"), "a", 1, "r", undoable=True)
    gate.set(Channel("ai"), "b", 2, "r", undoable=False)
    gate.set(Channel("ai"), "c", 3, "r")             # 不传 ⇒ None（未声明）
    evs = hist.events()
    if all(e.ts and "T" in e.ts for e in evs):
        _ok("新写事件都带 ts（UTC ISO；由 History.append 现取墙钟）")
    else:
        _fail(out, f"有新事件没有 ts：{[e.ts for e in evs]}")
    if [e.undoable for e in evs] == [True, False, None]:
        _ok("undoable **三态**原样进史（True / False / None＝未声明）")
    else:
        _fail(out, f"undoable 三态不对：{[e.undoable for e in evs]}")
    old = Event.from_record({"seq": 9, "key": "k", "value": 1,
                             "actor": "a", "reason": "r"})
    if old.ts == "" and old.undoable is None:
        _ok("**老记录照读**（无 ts ⇒ 空串；无 undoable ⇒ None，**不冒充 False**）")
    else:
        _fail(out, f"老记录读坏了：ts={old.ts!r} undoable={old.undoable!r}")
    h2 = History(InMemorySink())
    h2.append_state("x", 1, None, "ai", "r", ts="2026-01-01T00:00:00+00:00")
    h2.append_state("x", 2, None, "ai", "r", ts="2025-01-01T00:00:00+00:00")
    if [e.after for e in h2.events()] == [1, 2]:
        _ok("⛔ `seq` 仍是唯一顺序：**ts 回拨也不重排**（不许用 ts 排序/去重）")
    else:
        _fail(out, f"顺序被 ts 影响了：{[e.after for e in h2.events()]}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out: list[str] = []
    print("Phase 1 装配判据：")
    check_gate_authority(out)
    check_gate_executor_not_wrapper(out)
    check_seed_also_validates(out)
    check_open_mode(out)
    check_event_ts_and_undoable(out)
    check_write_all_or_nothing(out)
    check_call_id_crossref(out)
    check_history_append_only_resume(out)
    check_monitor_reconcile(out)
    check_validate_required(out)
    check_format_migration(out)
    check_data_layout(out)
    if out:
        print(f"phase1_assembly_selfcheck: FAIL（{len(out)} 条）")
        return 1
    print("phase1_assembly_selfcheck: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
