"""按需生成 docs/capabilities.md，供阅读角色与能力关系。

python scripts/gen_capability_map.py          # 刷新静态快照
python scripts/gen_capability_map.py --check  # 可选：比较快照是否最新

来源是 build_roles；不含运行时动态注册或宿主自身能力。
快照新鲜度不是日常开发门禁。render 接口供项目侧复用。
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
