"""§5.4 三件交付的机制判据（装配级，L6）。

验证"边界写机制不写分类"的三件东西**真的接上了**，不是文档口号：

  交付 1 角色声明：build_roles() 里每个能力都有 role ∈ {definition,
        provider, consumer}；出现无角色标注的扩展点 = 红。
  交付 2 生成 map：docs/capabilities.md 与 gen_capability_map.render() 逐字
        一致（手改 = 红）；且文件头带"禁止手工编辑"标记。
  交付 3 汇总就绪：合成第二个项目（EL）的能力表，字段格式一致，能机械
        diff 出共有能力（不靠人读两份表）。

L7 对偶：合规装配全绿；并表在"共有能力存在"时给出非空交集。

用法：python checks/capability_map_selfcheck.py
退出码：0 全绿；1 有失败。

## Known Limitations and Deferred Work

- 交付 3 用**合成**的第二项目表测并表算法；真实 EL 表 Phase 5 生成后，同一
  diff 函数直接复用（字段格式已锁）。
- 不校验"某能力该归 definition 还是 provider"（那是分类内容，n=1 推迟）；
  这里只校验"每个能力都声明了角色"（机制）。
"""

from __future__ import annotations

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


def _el_project_entries() -> list[CapabilityEntry]:
    """合成第二个项目（EL）的能力表——字段格式与主表一致，供并表判据。"""
    r = RoleRegistry()
    r.register("gate", Role.DEFINITION, provider="el")
    r.register("history", Role.DEFINITION, provider="el")
    r.register("surface", Role.DEFINITION, provider="el")
    r.register("engine.sim", Role.PROVIDER, provider="rydberg")  # EL 独有
    r.register("adapter.ai", Role.CONSUMER, consumer="ai", provider="el")
    return r.entries()


def _common_capabilities(a: list[CapabilityEntry], b: list[CapabilityEntry]) -> set[str]:
    """机械 diff 共有能力：同 (name, role) 即共有。"""
    ka = {(e.name, e.role.value) for e in a}
    kb = {(e.name, e.role.value) for e in b}
    return {n for n, _ in (ka & kb)}


def check_merge_ready(out: list[str]) -> None:
    """交付 3：两份 map 能机械 diff 出共有能力。"""
    main = build_roles().entries()
    el = _el_project_entries()
    # 字段格式一致（并表前提）
    for e in main + el:
        if not all(hasattr(e, f) for f in MAP_FIELDS):
            _fail(out, f"能力行 {e.name!r} 字段不全，无法并表")
            return
    common = _common_capabilities(main, el)
    if {"gate", "history", "surface"} <= common:
        _ok(f"两项目 map 机械 diff 出共有脊椎件：{sorted(common)}（交付 3）")
    else:
        _fail(out, f"并表未抓到共有能力：{sorted(common)}")
    # EL 独有能力不出现在共有集（不误报）
    if "engine.sim" not in common:
        _ok("项目独有能力未被误并入共有集（不误报）")
    else:
        _fail(out, "独有能力被误判为共有")


#: 诚实推迟的 provider（无真实消费者，但显式登记——不是遗忘）。
#: 加新 provider 若不进这里又不声明 consumer ⇒ 守卫红（逼"推迟不许沉默"）。
DEFERRED_PROVIDERS = frozenset({
    "provider.spill.local",       # 大载荷回程待 dsh spillStore 接线
    "provider.approval.local",    # 细粒度每次写问人待 ctx.approval 接线
    "provider.skills",            # skill 目录待宿主 skill 面接线
})


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


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out: list[str] = []
    print("§5.4 边界机制判据：")
    check_roles_declared(out)
    check_map_generated(out)
    check_merge_ready(out)
    check_consumer_truthfulness(out)
    if out:
        print(f"capability_map_selfcheck: FAIL（{len(out)} 条）")
        return 1
    print("capability_map_selfcheck: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
