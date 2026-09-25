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
    # 每个能力至少一条声明；抽查已知脊椎件在表里
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
EL_ROOT = Path(os.environ.get("MECHA_EL_ROOT")
               or r"D:\学\BaiduSyncdisk\编程\code\python\Energy Level")
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
    if not EL_MAP.exists():
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
        _fail(out, str(e))
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
    "provider.spill.local",       # 大载荷回程待 dsh spillStore 接线
    "provider.artifacts.local",   # 宿主产物托管待 ML 宿主真实接入（无内置消费者）
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
    bases = [(ROOT / "mecha", "mecha/"), (EL_ROOT / "mecha_v2", "EL:mecha_v2/")]
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
    src = (EL_ROOT / "mecha_v2" / "bridge.py").read_text(encoding="utf-8")
    if ("surface=sw.surface" in src and "surface=self._surface" in src
            and "surface or Surface(engine)" in src):
        _ok("bridge 真路径穿装配点 surface（_assembled→serve_mcp→工具面同源）")
    else:
        _fail(out, "bridge 的 surface 穿线断了（漂回裸建 Surface ⇒ 双图复活）")

    # C：L7 对偶——裸建的表确实缺声明表的脊椎条目（守卫不是永远绿的哑守卫）
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
    if out:
        print(f"capability_map_selfcheck: FAIL（{len(out)} 条）")
        return 1
    print("capability_map_selfcheck: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
