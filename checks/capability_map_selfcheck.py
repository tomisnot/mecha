"""§5.4 三件交付的机制判据（装配级，L6）。

验证"边界写机制不写分类"的三件东西**真的接上了**，不是文档口号：

  交付 1 角色声明：build_roles() 里每个能力都有 role ∈ {definition,
        provider, consumer}；出现无角色标注的扩展点 = 红。
  交付 2 生成 map：docs/capabilities.md 与 gen_capability_map.render() 逐字
        一致（手改 = 红）；且文件头带"禁止手工编辑"标记。
  交付 3 汇总就绪：读 EL 仓**真表**（EL scripts/gen_capability_map.py 生成入库），
        字段格式一致，机械 diff 出共有能力（不靠人读两份表）。

L7 对偶：合规装配全绿；并表在"共有能力存在"时给出非空交集。

用法：python checks/capability_map_selfcheck.py
退出码：0 全绿；1 有失败。

## Known Limitations and Deferred Work

- 交付 3 读 EL 真表（ADR `边界机制补三洞` 兑现了原"Phase 5 生成后复用"的承诺）；
  真表缺失即红，不回退合成表。第三项目接入时照 EL 方式出表、判据加一路 diff。
- 不校验"某能力该归 definition 还是 provider"（那是分类内容，n=1 推迟）；
  这里只校验"每个能力都声明了角色"（机制）。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mecha.assembly import build_roles  # noqa: E402
from mecha.roles import CapabilityEntry, Role, RoleRegistry  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
import gen_capability_map  # noqa: E402

MAP_FIELDS = ("name", "role", "providers", "consumers")


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _fail(out: list[str], msg: str) -> None:
    print(f"  ✗ {msg}")
    out.append(msg)


def check_roles_declared(out: list[str]) -> None:
    """交付 1：每个能力有合法角色；无标注 = 红。"""
    entries = build_roles().entries()
    if not entries:
        _fail(out, "角色表为空——机制没接（交付 1 未落地）")
        return
    valid = {Role.DEFINITION, Role.PROVIDER, Role.CONSUMER}
    unlabeled = [e.name for e in entries if e.role not in valid]
    if unlabeled:
        _fail(out, f"无合法角色标注的扩展点：{unlabeled}")
    else:
        _ok(f"全部 {len(entries)} 个能力都有 role（交付 1）")
    # 每个能力至少一条声明；抽查已知正职件在表里
    names = {e.name for e in entries}
    for spine in ("gate", "history", "surface", "monitor"):
        if spine in names:
            continue
        _fail(out, f"稳定脊椎件 {spine!r} 未声明角色")
    if all(s in names for s in ("gate", "history", "surface", "monitor")):
        _ok("Gate/History/Surface/Monitor 均入表（脊椎不缺件）")


def check_map_generated(out: list[str]) -> None:
    """交付 2：map 文件 = 生成结果；手改 = 红。"""
    path = ROOT / "docs" / "capabilities.md"
    if not path.exists():
        _fail(out, "docs/capabilities.md 不存在（交付 2 未落地）")
        return
    text = path.read_text(encoding="utf-8")
    if "禁止手工编辑" not in text:
        _fail(out, "map 文件缺'禁止手工编辑'标记")
    if text != gen_capability_map.render():
        _fail(out, "docs/capabilities.md 与角色声明漂移或被手改（§5.4 拒收）")
    else:
        _ok("map 与生成结果逐字一致（分类是生成的，交付 2）")


#: EL 仓根（真表来源）。与 phase5_* 判据同一约定；MECHA_EL_ROOT 可覆盖。
EL_ROOT = Path(os.environ.get("MECHA_EL_ROOT", ""))
#: WARN **缺 EL 时必须先问这个**。陷阱（本批差点踩）：把默认值删掉而不加守卫
#: ⇒ `Path("")` = 当前目录 ⇒ `EL_MAP` 指向 **mecha 自己的** docs/capabilities.md
#: ⇒ 比对变成「自己跟自己比」= **假绿**（比红更坏）。
EL_READY = (EL_ROOT / "mecha_v2").is_dir()
EL_MAP = EL_ROOT / "docs" / "capabilities.md"

_ROW_RE = re.compile(
    r"^\|\s*`([^`]+)`\s*\|\s*(\w+)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*$")


def _parse_map(md_text: str) -> list[CapabilityEntry]:
    """解析生成的 capability map（markdown 表）为条目列表。"""
    out: list[CapabilityEntry] = []
    for line in md_text.splitlines():
        m = _ROW_RE.match(line.strip())
        if not m:
            continue
        name, role, prov, cons = m.group(1), m.group(2), m.group(3), m.group(4)
        providers = tuple(p.strip() for p in prov.split(",") if p.strip() and p.strip() != "—")
        consumers = tuple(c.strip() for c in cons.split(",") if c.strip() and c.strip() != "—")
        out.append(CapabilityEntry(name, Role(role), providers, consumers))
    return out


def _el_project_entries() -> list[CapabilityEntry]:
    """读 EL 仓**真表**（EL scripts/gen_capability_map.py 生成入库）。

    审查建议 2：合成表退役——并表判据只证明"能 diff 我自己写的表"不算数，
    必须拿第二个真实项目生成的表来 diff。真表缺失即红（不假绿、不回退合成表）。
    """
    if not EL_READY or not EL_MAP.exists():
        raise FileNotFoundError(
            f"EL 真表不存在：{EL_MAP}\n"
            f"先在 EL 仓跑 python scripts/gen_capability_map.py 生成（ADR 边界机制补三洞）")
    return _parse_map(EL_MAP.read_text(encoding="utf-8"))


def _common_capabilities(a: list[CapabilityEntry], b: list[CapabilityEntry]) -> set[str]:
    """机械 diff 共有能力：同 (name, role) 即共有。"""
    ka = {(e.name, e.role.value) for e in a}
    kb = {(e.name, e.role.value) for e in b}
    return {n for n, _ in (ka & kb)}


def check_merge_ready(out: list[str]) -> None:
    """交付 3：两份**真实** map 能机械 diff 出共有能力。"""
    main = build_roles().entries()
    try:
        el = _el_project_entries()
    except FileNotFoundError as e:
        # WARN 外部依赖缺失只有一种语义 => 响亮跳过（严格模式由 --require-external 转失败）
        print(f"  · 跳过（缺 EL 仓）：读 EL 真表做并表 diff —— {e}")
        return
    # 字段格式一致（并表前提）
    for e in main + el:
        if not all(hasattr(e, f) for f in MAP_FIELDS):
            _fail(out, f"能力行 {e.name!r} 字段不全，无法并表")
            return
    common = _common_capabilities(main, el)
    if {"gate", "history", "surface", "monitor", "authority"} <= common:
        _ok(f"主表 × EL 真表机械 diff 出共有脊椎件：{sorted(common)}（交付 3·真表）")
    else:
        _fail(out, f"并表未抓到共有能力：{sorted(common)}")
    # EL 运行时才有的注册项（裸 engine / query:*）不误并入共有集（不误报）
    leaked = [n for n in common if n == "engine" or n.startswith("query:")]
    if not leaked:
        _ok("EL 独有注册项（engine/query:*）未被误并入共有集（不误报）")
    else:
        _fail(out, f"项目独有注册项被误判为共有：{leaked}")


#: 诚实推迟的 provider（无真实消费者，但显式登记——不是遗忘）。
#: 加新 provider 若不进这里又不声明 consumer ⇒ 守卫红（逼"推迟不许沉默"）。
DEFERRED_PROVIDERS = frozenset({
    "provider.approval.local",    # 细粒度每次写问人待 ctx.approval 接线
    "provider.skills",            # skill 目录待宿主 skill 面接线
    "provider.toolhost.local",    # 工具定义→宿主传输待可注入 ctx.tools 的真宿主
})

#: 有真实消费者的 provider → (实现符号, 自身模块路径子串)。
#: 守卫 grep 该符号在**库（mecha/）或真实项目（EL 仓 mecha_v2/）**下、排除
#: checks/ 与 assembly.py 与自身模块后的引用，独立于 consumer 标签证明
#: "真有人用"（审查 D-29）。examples/ 退役后（ADR 领域参考实现搬回所属项目），
#: 项目侧证据扫 EL 仓——**库机制被真项目 import 才算数，被框架自己的示例 import
#: 从来不算**（旧扫描面反而更弱）。
#: provider.log.local 的证据符号是 `assemble`：D-33 后 sink 只经具名装配点
#: 注入 History（依赖倒置，消费者不 import Provider 类）——真实路径
#: （EL mecha_v2/bridge、hub）import assemble 即证明整条 log 链被真消费；
#: 若谁退回手搭装配，该 import 消失、守卫当场红（D-33 的机械防回归）。
CONSUMER_SYMBOLS = {
    "provider.jobs.local": ("JobRegistry", ("providers/jobs.py",)),
    "provider.codeact": ("CodeAct", ("providers/codeact.py",)),
    "provider.log.local": ("assemble", ("mecha/assembly.py",)),
}


def _real_consumer_files(symbol: str, own_module_substrings) -> list[str]:
    """symbol 被**真实 import** 的文件：mecha 仓 mecha/ 与 EL 仓 mecha_v2/ 下，
    非 checks/、非 assembly、非 providers/ 子树（re-export 与兄弟模块不算消费者）。
    只认含 symbol 的 import 行——docstring/注释里的提及不算（防假绿）。"""
    hits: list[str] = []
    bases = [(ROOT / "mecha", "mecha/")]
    if EL_READY:
        bases.append((EL_ROOT / "mecha_v2", "EL:mecha_v2/"))
    else:
        print("  · 跳过（缺 EL 仓）：EL 侧的「真实消费者」扫描面")
    for d, prefix in bases:
        if not d.is_dir():
            continue
        for path in d.rglob("*.py"):
            rel = prefix + path.relative_to(d).as_posix()
            if rel.endswith("assembly.py"):
                continue
            if rel.startswith("mecha/providers/"):     # 自身/兄弟/__init__ 转出
                continue
            if any(s in rel for s in own_module_substrings):
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            for ln in lines:
                s = ln.strip()
                if s.startswith(("import ", "from ")) and symbol in s:
                    hits.append(rel)
                    break
    return hits


def check_real_consumers(out: list[str]) -> None:
    """审查 D-29（grep 式「有实际消费方」门禁）：声明的机制真被非判据代码引用。

    每个 CONSUMER_SYMBOLS 里的 provider 必须能在 checks/ 之外、assembly 实例化
    之外、自身模块之外找到引用；DEFERRED_PROVIDERS 里的豁免（诚实推迟）。
    这独立于 consumer 标签——标签写对了不代表有人 import，grep 才证明。
    L7 对偶：真符号（JobRegistry）必须非空、假符号必须空（守卫不永远返绿）。
    """
    if not EL_READY:
        # ⚠ 这一组证的是「**真项目**在消费」（扫 EL 仓的 import）⇒ 缺 EL 时无可证之物：
        # **响亮跳过这一组**，但 **L1/L2/L3/L3+ 一字不动**（守卫必须永远能跑）。
        print("  · 跳过（缺 EL 仓）：真实消费者 grep 证（需要 EL 仓作为第二个项目）")
        return
    for cap, (sym, own) in CONSUMER_SYMBOLS.items():
        files = _real_consumer_files(sym, own)
        if not files:
            _fail(out, f"{cap} 的实现符号 {sym!r} 在 checks/assembly/自身模块外"
                       f"零引用（声明有消费方却无人 import）")
        else:
            _ok(f"{cap} 有非判据真实消费（{sym} ← {files[0]} 等 {len(files)} 处）")
    # 不许误报 / 非哑守卫（L7）
    good = _real_consumer_files("JobRegistry", ("providers/jobs.py",))
    bogus = _real_consumer_files("NoSuchSymbol_zzz", ())
    if good and not bogus:
        _ok("grep 守卫自测：真符号命中、假符号 0 命中（非哑守卫）")
    else:
        _fail(out, f"grep 守卫自测失败：good={len(good)} bogus={len(bogus)}")


def check_consumer_truthfulness(out: list[str]) -> None:
    """审查勘误 §3/§4.1（升级为必须）：map 的「消费方」列不许说谎。

    两条机械断言：
      A 每条 PROVIDER 声明的 consumer 必须是**表内真实存在的能力名**——
        抓 `surface.run`/`tools` 这类"消费一个不存在的能力"的假声明。
      B 每条 PROVIDER 要么声明真实 consumer，要么在 DEFERRED_PROVIDERS
        显式登记——抓"加了 provider 忘了它有没有人用"（沉默推迟）。
    L7 对偶：合规声明 0 命中；合成假 consumer + 未登记 provider 必须被抓。
    """
    entries = build_roles().entries()
    names = {e.name for e in entries}
    providers = [e for e in entries if e.role is Role.PROVIDER]
    bad_consumer = []
    unregistered = []
    for e in providers:
        if e.consumers:
            for c in e.consumers:
                if c not in names:
                    bad_consumer.append(f"{e.name}→{c}")
        elif e.name not in DEFERRED_PROVIDERS:
            unregistered.append(e.name)
    if bad_consumer:
        _fail(out, f"PROVIDER 声明了表内不存在的 consumer（消费方说谎）："
                   f"{bad_consumer}")
    else:
        _ok("每条 PROVIDER 的 consumer 都是表内真实能力名（消费方不说谎）")
    if unregistered:
        _fail(out, f"PROVIDER 既无 consumer 又未登记推迟（沉默推迟）："
                   f"{unregistered}；确属推迟请加入 DEFERRED_PROVIDERS")
    else:
        _ok("每个 PROVIDER 要么真实消费、要么显式登记推迟（无沉默推迟）")
    # 不许误报（L7）：合成一条假 consumer + 一条未登记 provider，必须被抓
    fake = RoleRegistry()
    fake.register("surface", Role.DEFINITION, provider="x")
    fake.register("provider.foo.local", Role.PROVIDER, provider="f",
                  consumer="no_such_capability")
    fake.register("provider.bar.local", Role.PROVIDER, provider="b")
    fp = [e for e in fake.entries() if e.role is Role.PROVIDER]
    caught_bad = any(c not in {x.name for x in fake.entries()}
                     for e in fp for c in e.consumers)
    caught_unreg = any(not e.consumers and e.name not in DEFERRED_PROVIDERS
                       for e in fp)
    if caught_bad and caught_unreg:
        _ok("守卫自测：假 consumer + 未登记 provider 均被抓（非哑守卫）")
    else:
        _fail(out, f"守卫对合成违例漏抓：bad={caught_bad} unreg={caught_unreg}")


def check_runtime_graph_matches(out: list[str]) -> None:
    """审查缺口 B：服务面角色表 ⊇ 声明表（map 描述的图必须是运行时真实存在的图）。

    D-33 只修了一半的先例：`_assembled` 调用了 assemble，但 `serve_mcp` 又
    `Surface(engine)` 裸建丢掉带角色的那份 ⇒ 声明 19 条、运行时 3 条。本判据把
    "装配点建的 Surface 用的就是声明表"钉成永不再犯：
      A  `assemble()` 产出的 surface.roles ⊇ build_roles() 全部 (name, role)
      A2 且是**同一张表**（sw.surface.roles is sw.roles，不是复制）
      B  bridge 真路径源码断言：_assembled 穿 surface、serve_mcp 复用它
         （漂回裸建 ⇒ 红）
      C  L7 对偶（守卫载重）：裸建 Surface(engine) 的表不含 gate ⇒ 若谁把
         assemble 改回裸建，A 当场红。
    """
    import tempfile
    from pathlib import Path as _P

    from mecha.assembly import assemble
    from mecha.gate import accept_all
    from mecha.surface import Surface

    class Toy:
        def schema(self):
            return {"run_output_required": ["ok"]}

        def run(self, spec, *, context, **opts):
            return {"ok": True}

        def summary(self):
            return {"ok": True}

        def estimate(self, spec):
            return {"est_sec": 1.0}

    declared = {(e.name, e.role.value) for e in build_roles().entries()}
    sw = assemble(root=_P(tempfile.mkdtemp(prefix="capmap-")),
                  data_dir_name=".mecha-v2-check", engine=Toy(),
                  summarizer=lambda evs: {}, validate=accept_all,
                  project="energy-level")
    runtime = {(e.name, e.role.value) for e in sw.surface.roles.entries()}
    missing = declared - runtime
    if missing:
        _fail(out, f"运行时服务面缺声明条目（map 描述的是不存在的图）："
                   f"{sorted(missing)}")
    else:
        _ok(f"assemble 的 surface.roles ⊇ 声明表全部 {len(declared)} 条（缺口 B 已合）")
    if sw.surface.roles is sw.roles:
        _ok("surface 用的就是装配点那张角色表（同一对象，非复制）")
    else:
        _fail(out, "surface 持有另一张表——assemble 的 roles 注入漂了")

    # B：bridge 真路径（serve_mcp → _build_gui_tools）必须复用装配点的 surface。
    # （领域实现搬回 EL 仓后，bridge 在 EL_ROOT/mecha_v2/ —— 与 phase5 判据同源）
    if not EL_READY:
        print("  · 跳过（缺 EL 仓）：bridge 真路径穿线核对（需要 EL 的 bridge.py）")
        _ok("bridge 穿线核对**未执行**（缺 EL 仓，已响亮标注）")
        pass  # ⚠ 跳过分支到此为止：**不许**掉进下面的 else（上一版就是这么自伤的）
    if EL_READY:
        src = (EL_ROOT / "mecha_v2" / "bridge.py").read_text(encoding="utf-8")
        if ("surface=sw.surface" in src and "surface=self._surface" in src
                and "surface or Surface(engine)" in src):
            _ok("bridge 真路径穿装配点 surface（_assembled→serve_mcp→工具面同源）")
        else:
            _fail(out, "bridge 的 surface 穿线断了（漂回裸建 Surface ⇒ 双图复活）")
    else:
        print("  · 跳过（缺 EL 仓）：bridge surface 穿线核对（结论只能由 EL 的 bridge.py 证）")

    # C：L7 对偶——裸建的表确实缺声明表的正职条目（守卫不是永远绿的哑守卫）
    bare = {(e.name, e.role.value) for e in Surface(Toy()).roles.entries()}
    if "gate" not in bare and not declared <= bare:
        _ok("守卫自测：裸建 Surface 的表缺脊椎件（漂回裸建必被 A 抓）")
    else:
        _fail(out, f"守卫对『裸建』违例漏抓：bare={sorted(bare)[:5]}…")


def check_declaration_completeness(out: list[str]) -> None:
    """审查缺口 A：漏声明抓得到——每个装配进 Software 的机制都有角色条目。

    单一来源 = `Software` 数据类字段（D-33 后装配点是唯一具名组装处，新机制
    必然出现在这里），不是手写清单（L8）。规则：每个字段名必须是某能力名的
    一个 token（按 `.`/`_` 切分）——`jobs`→`provider.jobs`、`tools`→
    `tools.registry`、`commands`→`commands.registry`。
    豁免三个管线件（各自理由）：`roles`（map 机制自身，自我声明循环）、
    `channels`（Channel 的实例化，归 adapter.* 条目表达）、
    `layout`（路径解析管线，非能力 seam）。
    L7 对偶：合成一个未声明的字段（widget）必须被抓——守卫不哑。
    """
    import dataclasses
    import re

    from mecha.assembly import Software

    PLUMBING = {"roles", "channels", "layout"}
    tokens: set[str] = set()
    for e in build_roles().entries():
        tokens |= set(re.split(r"[._]", e.name))
    fields = {f.name for f in dataclasses.fields(Software)} - PLUMBING
    missing = sorted(fields - tokens)
    if missing:
        _fail(out, f"装配机制漏声明角色（无角色标注的扩展点=拒收）：{missing}；"
                   f"补进 declare_roles 或说明为何非能力")
    else:
        _ok(f"Software 全部 {len(fields)} 个机制字段都在角色表有条目（缺口 A 已合）")
    # 守卫自测（L7）：合成"漏声明"必须被抓
    fake_fields = fields | {"widget"}
    caught = sorted(fake_fields - tokens)
    if caught == ["widget"]:
        _ok("守卫自测：合成未声明机制（widget）被抓（非哑守卫）")
    else:
        _fail(out, f"完整性守卫对合成违例漏抓：{caught}")


# ---------------------------------------------------------------------------
# 声明 → 消费者（L1/L2/L3，2026-09-26）：把「声明了就得被消费」从人读变成机械
#
# 缘起：本会话抓到四处「声明与行为不一致」（approval_required 没人拦 / scope 项目没绑 /
# kind=read 却在写账 / CodeAct 文档的动作集比代码大），**四处都是人读代码发现的**。
# 本组把这个形态变成机械可查：**新声明必须表态**（有消费者 / 推迟 / 未定），
# **表里的消费者符号必须真的还在**，**每条"有消费者"必须点名一条判据**。
# ---------------------------------------------------------------------------

def _cmd_fields() -> set[str]:
    from mecha.commands import CommandSpec
    return set(CommandSpec.__dataclass_fields__)


def _tooldef_fields() -> set[str]:
    from mecha.tools import ToolDefinition
    return set(ToolDefinition.__dataclass_fields__)


def _mode_members() -> set[str]:
    from mecha.authority import Mode
    return set(Mode.__members__)


def _monitor_fields() -> set[str]:
    from mecha.monitor import MonitorView
    return set(MonitorView.__dataclass_fields__)


def _event_fields() -> set[str]:
    from mecha.history import Event
    return set(Event.__dataclass_fields__)


#: **L1 的枚举源**：从**活对象**取（`__dataclass_fields__` / `__members__`），**不手抄**。
#:
#: ⚠ **复核过的声明面清单**（哪些进了、哪些没进 —— 这块**必须能被人核**）：
#: * **进**：`CommandSpec`（命令声明）· `ToolDefinition`（工具声明）· `Mode`（写权模式）·
#:   `MonitorView`（监控读数形状）· **`Event`（域事件形状；2026-09-26 补）**
#: * **没进**（附理由）：`Action` / `SubCallLog` / `CodeActResult`（CodeAct 的**内部值对象**，
#:   由调用方构造，不是"项目声明面"）· `ToolOutput` / `Claim`（值形状，不是声明）·
#:   角色与能力面（走同文件的 `DEFERRED_PROVIDERS` / `CONSUMER_SYMBOLS` 那一套）
#: * ⚠ **这块永远是这套守卫的已知软肋**：**"哪些类算声明面"需要人判** ⇒
#:   **新加一个带声明字段的 dataclass / 枚举时必须人判并在此加一行**，否则**守卫看不见它**
#:   —— 同一个缺口会再来一次：本次 `Event.ts` / `Event.undoable` 就是这么漏掉的。
ENUM_SOURCES = (
    ("CommandSpec", _cmd_fields),
    ("ToolDefinition", _tooldef_fields),
    ("Mode", _mode_members),
    ("MonitorView", _monitor_fields),
    ("Event", _event_fields),
)

#: 三态表：`consumed`（有消费者 + 点名判据）/ `deferred`（今天没有消费者，**必须给理由**）
#: / `undecided`（被消费了，但**语义还没定**）。
#: ⚠ 三态都要求**有行**：**新声明没有行 ⇒ 红**（逼"要么接、要么明说推迟/未定"）。
DECLARATIONS: dict[str, dict[str, object]] = {}


def _declare(row_key: str, state: str, *, sym: str = "", files: tuple[str, ...] = (),
             crit: str = "", note: str = "") -> None:
    DECLARATIONS[row_key] = {"state": state, "sym": sym, "files": files,
                             "crit": crit, "note": note}


# ---- Event（域事件形状；2026-09-26 补进枚举源 —— 补的原因就是 ts/undoable 那次漏掉）----
_declare("Event.seq", "consumed", sym="last_seq",
         files=("mecha/providers/log_local.py",), crit="check_history_append_only_resume")
_declare("Event.kind", "consumed", sym="fold",
         files=("mecha/history.py",), crit="check_monitor_reconcile",
         note="\"state\" 才被 fold 折进快照；\"history\" 永不进（本轮的 bug 就在这一行）")
_declare("Event.op", "consumed", sym="append_history",
         files=("mecha/history.py",), crit="check_side_effect_audit",
         note="事件名住在这里（原先是 key；前缀只是命名习惯，不再承担判定）")
_declare("Event.target", "consumed", sym="fold",
         files=("mecha/history.py",), crit="check_history_append_only_resume")
_declare("Event.after", "consumed", sym="fold",
         files=("mecha/history.py",), crit="check_history_append_only_resume")
_declare("Event.before", "consumed", sym="append_state",
         files=("mecha/history.py",), crit="check_write_all_or_nothing",
         note="state 事件的前像（键此前不存在 ⇒ None）")
_declare("Event.actor", "consumed", sym="history_records",
         files=("mecha/cockpit.py",), crit="check_call_id_crossref")
_declare("Event.reason", "consumed", sym="config_tree",
         files=("mecha/cockpit.py",), crit="check_call_id_crossref")
_declare("Event.call_id", "consumed", sym="to_record",
         files=("mecha/history.py",), crit="check_call_id_crossref")
_declare("Event.ts", "consumed", sym="ts",
         files=("mecha/history.py",), crit="check_event_ts_and_undoable",
         note="⚠ **不许用它排序/去重**（seq 是唯一顺序）；判据已钉「ts 回拨也不重排」")
_declare("Event.undoable", "consumed", sym="undoable",
         files=("mecha/history.py",), crit="check_event_ts_and_undoable",
         note="⚠ **三态槽位**（None＝未声明），框架**不**据此实现撤销；"
              "**今天没有项目在传它** ⇒ 判据只钉「三态原样进史 + 老记录不冒充 False」")


# ---- CommandSpec（命令声明面：本会话三起事件在这里）----
_declare("CommandSpec.name", "consumed", sym="_require",
         files=("mecha/commands.py",), crit="check_spec_contract")
_declare("CommandSpec.description", "deferred",
         note="框架内只在构造期校验非空；命令→工具的投影面今天不存在（n=0）")
_declare("CommandSpec.parameters", "consumed", sym="_check_args",
         files=("mecha/commands.py",), crit="check_params_and_output")
_declare("CommandSpec.output_schema", "consumed", sym="_output_error",
         files=("mecha/commands.py",), crit="check_params_and_output")
_declare("CommandSpec.side_effect", "consumed", sym="_audit",
         files=("mecha/commands.py",), crit="check_side_effect_audit")
_declare("CommandSpec.scope", "consumed", sym="_scope_policy",
         files=("mecha/commands.py",), crit="check_scope_policy",
         note="⚠ 框架侧有消费者；**项目没绑 ScopePolicy 时它静默不生效**——"
              "那一类框架看不到，要靠项目侧判据（见 ADR）")
_declare("CommandSpec.estimate_sec", "consumed", sym="estimate",
         files=("mecha/commands.py",), crit="check_spec_contract")
_declare("CommandSpec.cancel_supported", "consumed", sym="cancel_supported",
         files=("mecha/providers/jobs.py",),
         crit="check_cancel_supported_is_a_real_contract")
_declare("CommandSpec.approval_required", "consumed", sym="approval",
         files=("mecha/commands.py",), crit="check_approval_required")
_declare("CommandSpec.wants_channel", "consumed", sym="wants_channel",
         files=("mecha/commands.py",), crit="check_handler_sees_channel")

# ---- ToolDefinition（工具声明面）----
_declare("ToolDefinition.name", "consumed", sym="MODEL_VISIBLE_FIELDS",
         files=("mecha/tools.py",), crit="check_wire_schema")
_declare("ToolDefinition.description", "consumed", sym="MODEL_VISIBLE_FIELDS",
         files=("mecha/tools.py",), crit="check_description_style")
_declare("ToolDefinition.parameters", "consumed", sym="MODEL_VISIBLE_FIELDS",
         files=("mecha/tools.py",), crit="check_wire_schema")
_declare("ToolDefinition.output", "consumed", sym="output",
         files=("mecha/tools.py",), crit="check_output_contract")
_declare("ToolDefinition.execute", "consumed", sym="execute",
         files=("mecha/tools.py",), crit="check_error_shape")
_declare("ToolDefinition.kind", "deferred",
         note="由 mecha/projection.py 的 project() 发出（一处声明、透传到底）；"
              "今天框架内没有消费者与判据（演示脚本不算判据）⇒ 如实登记 deferred")
_declare("ToolDefinition.scope", "deferred",
         note="同 kind：投影期按 ScopePolicy 过滤的依据；今天框架内无消费者")
_declare("ToolDefinition.reversible", "deferred",
         note="三态（None = 未声明）；今天框架内无消费者")

# ---- Mode（写权模式）----
_declare("Mode.HUMAN", "consumed", sym="switch_mode",
         files=("mecha/authority.py",), crit="check_gate_authority")
_declare("Mode.AI", "consumed", sym="switch_mode",
         files=("mecha/authority.py",), crit="check_gate_authority")
_declare("Mode.OPEN", "consumed", sym="OPEN",
         files=("mecha/authority.py",), crit="check_open_mode")
_declare("Mode.LOCKED", "consumed", sym="DEFAULT_MODE",
         files=("mecha/authority.py",), crit="check_gate_authority")

# ---- MonitorView（监控读数）----
_declare("MonitorView.summary", "consumed", sym="monitor_summary_payload",
         files=("mecha/cockpit.py",), crit="check_monitor_reconcile")
_declare("MonitorView.raw_snapshot", "deferred",
         note="⚠ 只有判据读它；**wire 投影（monitor_summary_payload）不含原始层** ⇒"
              " 今天没有产品路径消费它（「两层信任」里的原始层只到了函数，没到端点）")
_declare("MonitorView.disputed", "undecided", sym="monitor_summary_payload",
         files=("mecha/cockpit.py",),
         note="字段被透出（cockpit ✓），但「**概括器失败算不算存疑**」这一档未实现"
              "（phase10 的 UNSUPPORTED 已登记：summary 抛错时 read() 整体抛出）")
_declare("MonitorView.summary_error", "deferred", sym="summary_error",
         files=("mecha/cockpit.py",),
         note="⚠ **今天没有任何人能看见它**：投影会把它透出（透出≠显示），但**面板资产不消费**、"
              "GP 的端点只是原样带出 ⇒ 如实登记 deferred。⚠ 这是**刻意**的待办："
              "不许出现「框架开始标存疑了、但没人看得见」；等一个真会显示"
              "『概括层故障』的界面出现 ⇒ 转 consumed 并点名判据")
_declare("MonitorView.dispute_reason", "consumed", sym="monitor_summary_payload",
         files=("mecha/cockpit.py",), crit="check_monitor_reconcile")


def _iter_declared_names() -> list[str]:
    """L1：把声明面**从代码枚举**出来（`<Source>.<name>`）。"""
    out_names: list[str] = []
    for label, getter in ENUM_SOURCES:
        for name in sorted(getter()):
            out_names.append(f"{label}.{name}")
    return out_names


def _symbol_in_files(sym: str, files: tuple[str, ...]) -> list[str]:
    """L2：符号**还在**被点名的文件里吗（只认非注释行；注释里提一句不算）。

    ⚠ 诚实边界：它证明"点名的消费者**还在**"，**不证明它被调用**——
    "被调用"那一层由 L3 的**具名判据**兜（判据是真在跑代码的）。
    """
    hits: list[str] = []
    for rel in files:
        path = ROOT / rel
        if not path.is_file():
            continue
        for ln in path.read_text(encoding="utf-8").splitlines():
            s = ln.strip()
            if not s or s.startswith("#") or s.startswith('"""') or s.startswith("'''"):
                continue
            if sym in s:
                hits.append(f"{rel}:{ln.strip()[:40]}")
                break
    return hits


def _criterion_exists(name: str) -> bool:
    """L3：这条判据**真的存在**吗（在 `checks/` 里找一个 `def <name>(`）。"""
    for path in (ROOT / "checks").glob("*.py"):
        if f"def {name}(" in path.read_text(encoding="utf-8"):
            return True
    return False


def _criterion_is_called(name: str) -> bool:
    """L3+：这条判据**真的被调用**吗（`def` 之外还要有 `<name>(out)` 的调用点）。

    ⚠ **一条没人调的判据是靠「缺席」而绿的** —— 那是最坏的一种绿（看起来有覆盖）。
    本仓今天就抓到一次：`check_event_ts_and_undoable` **定义了却没接进 `main`**
    （它自己不会报，因为**它根本没跑**）。
    ⚠ **边界**：只证「**被调用**」，**不证「被调用了并且真的在验」** ——
    后者只有**该判据自己的红证**能证（别当万能药）。
    """
    for path in (ROOT / "checks").glob("*.py"):
        for ln in path.read_text(encoding="utf-8").splitlines():
            # ⚠ **必须排除 `def` 那一行**：`def check_x(out: …)` 里也含 `check_x(out`
            # ⇒ 只认"调用行"（这条假绿是红证当场抓出来的：摘掉调用后守卫仍绿）。
            if f"{name}(out)" in ln and not ln.strip().startswith("def "):
                return True
    return False


def check_declarations_are_consumed(out: list[str]) -> None:
    """L1/L2/L3：声明的字段必须**表里有行**、消费者**还在**、且有**具名判据**。

    L1（枚举驱动，强）：`ENUM_SOURCES` 枚举出的每个名字都必须在 `DECLARATIONS` 里有行
      ⇒ **新声明没表态 ⇒ 红**（"声明了没人喂"这一类从此机械可见）。
    L2（grep，强）：`consumed` 行的消费者符号必须**仍在**点名文件里（非注释行）。
    L3（存在性，中）：`consumed` 行必须点名一条**真实存在的判据**；空栏即红。
    `deferred` / `undecided` ⇒ 进**待办清单**（**既不放行、也不红**——避免为"还没决定"造假红）。
    ⚠ **抓不到的**（写清，别当万能药）：项目侧没绑（如 `scope` 没注入 policy）、
      "有引用但不可达"、文档承诺比代码大、以及"消费的语义对不对"（那要靠行为判据）。
    """
    names = _iter_declared_names()
    missing = [n for n in names if n not in DECLARATIONS]
    if missing:
        _fail(out, f"声明了却没表态（既没消费者、也没登记推迟/未定）：{missing}")
    else:
        _ok(f"L1：{len(names)} 个声明字段全部表内有行（新声明没表态就会红）")

    bad_sym: list[str] = []
    bad_crit: list[str] = []
    todo: list[str] = []
    consumed = 0
    for key, row in sorted(DECLARATIONS.items()):
        state = str(row["state"])
        if state == "deferred" or state == "undecided":
            todo.append(f"{key}（{state}：{row['note']}）")
            continue
        consumed += 1
        sym = str(row["sym"])
        files = tuple(str(f) for f in row["files"])          # type: ignore[union-attr]
        if not _symbol_in_files(sym, files):
            bad_sym.append(f"{key}→{sym} @ {list(files)}")
        crit = str(row["crit"])
        if not crit or not _criterion_exists(crit):
            bad_crit.append(f"{key}→{crit or '(空)'}")
    if bad_sym:
        _fail(out, f"L2：点名的消费者符号已不在（改名/删除？）：{bad_sym}")
    else:
        _ok(f"L2：{consumed} 条「有消费者」的符号都还在点名文件里")
    if bad_crit:
        _fail(out, f"L3：有消费者却没点名真实判据（空栏即红）：{bad_crit}")
    else:
        _ok(f"L3：{consumed} 条「有消费者」都点名了真实存在的判据")
    uncalled = [str(row["crit"]) for row in DECLARATIONS.values()
                if str(row["state"]) == "consumed" and str(row["crit"])
                and not _criterion_is_called(str(row["crit"]))]
    if uncalled:
        _fail(out, f"L3+：点名的判据**没被调用**（靠缺席而绿）：{sorted(set(uncalled))}")
    else:
        _ok(f"L3+：{consumed} 条点名的判据**都在 run_all 的调用树里**（不是只定义不调用）")
    for item in todo:
        print(f"    · 待办（既不放行也不红）：{item}")
    if todo:
        _ok(f"推迟/未定共 {len(todo)} 条**已列出**（推迟不许沉默）")

    # L7 对偶：守卫不是哑的 —— ① 合成一个没登记的名字必须被 L1 抓到；
    # ② 假符号在真文件里必须 0 命中。
    synthetic = "CommandSpec.__no_such_field__"
    picks_up = synthetic not in DECLARATIONS
    bogus = _symbol_in_files("NoSuchSymbol_zzz", ("mecha/commands.py",))
    if picks_up and not bogus:
        _ok("L7 对偶：未登记的名字会被 L1 抓、假符号 0 命中（非哑守卫）")
    else:
        _fail(out, f"守卫自测失败：picks_up={picks_up} bogus={len(bogus)}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out: list[str] = []
    print("§5.4 边界机制判据：")
    check_roles_declared(out)
    check_map_generated(out)
    check_merge_ready(out)
    check_consumer_truthfulness(out)
    check_real_consumers(out)
    check_runtime_graph_matches(out)
    check_declaration_completeness(out)
    check_declarations_are_consumed(out)
    if out:
        print(f"capability_map_selfcheck: FAIL（{len(out)} 条）")
        return 1
    print("capability_map_selfcheck: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
