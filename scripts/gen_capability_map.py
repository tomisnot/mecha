"""生成 docs/capabilities.md（总纲 §5.4 交付 2：分类是生成的，不是手写的）。

调 ``mecha.assembly.build_roles`` 取角色表，渲染成表。字段格式（能力名 /
角色 / provider / 消费方）固定，使第二个项目的能力表能直接并表（交付 3）。

用法：
  python scripts/gen_capability_map.py            # 生成/覆盖 docs/capabilities.md
  python scripts/gen_capability_map.py --check    # 只校验现文件与生成结果一致（守卫）

--check 非零退出 = 文件被手改过或与声明漂移（§5.4 拒收标准"手工编辑过 ⇒ 红"）。

## Known Limitations and Deferred Work

- 只反映 ``build_roles`` 里的静态声明；运行时动态 register 的能力（如项目
  自己注册的查询动词）不进出厂 map——那是各项目自己的 map，Phase 5 接 EL 时
  由 EL 的装配点生成，两份 map 的并表判据在 checks/capability_map_selfcheck。
- 生成器不 import dsh（库边界）；dsh 侧能力由宿主 map 表达。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mecha.assembly import build_roles  # noqa: E402
from mecha.roles import Role  # noqa: E402

OUT = ROOT / "docs" / "capabilities.md"

HEADER = """<!-- 本文件由 scripts/gen_capability_map.py 从角色声明生成，禁止手工编辑。
     改能力归属请改 mecha/assembly.py 的 declare_roles，然后重新生成。
     字段格式固定，供第二个项目直接并表（总纲 §5.4 交付 2/3）。 -->
# Capability Map（生成）

| 能力 | 角色 | provider | 消费方 |
|---|---|---|---|
"""


def render(entries=None) -> str:
    """渲染 map。entries 缺省 = 本仓出厂声明；**项目侧（如 EL）传入自己的
    角色表条目复用同一函数**——字段格式单一来源，两份表并表才机械可比。"""
    if entries is None:
        entries = build_roles().entries()
    lines = [HEADER]
    for e in entries:
        prov = ", ".join(e.providers) or "—"
        cons = ", ".join(e.consumers) or "—"
        lines.append(f"| `{e.name}` | {e.role.value} | {prov} | {cons} |\n")
    return "".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    generated = render()
    if "--check" in sys.argv:
        if not OUT.exists():
            print("capability map 缺失，请运行 gen_capability_map.py")
            return 1
        if OUT.read_text(encoding="utf-8") != generated:
            print("docs/capabilities.md 与角色声明漂移或被手改（§5.4 拒收）")
            return 1
        print("capability map 与声明一致")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(generated, encoding="utf-8")
    print(f"已生成 {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
