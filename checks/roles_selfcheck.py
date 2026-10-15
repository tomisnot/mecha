"""角色注册表的公开行为与装配：贡献可独立撤销，软件共用一张表。

不扫描源码 import，不维护字段→消费者→判据的第二份登记表。
"""

from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mecha.assembly import assemble
from mecha.gate import accept_all
from mecha.errors import MechaError
from mecha.roles import Role, RoleRegistry


def main() -> int:
    roles = RoleRegistry()
    one = roles.register("compute", Role.PROVIDER, provider="a")
    two = roles.register("compute", Role.PROVIDER, provider="b")
    assert roles.names() == ["compute"]
    assert roles.entries()[0].providers == ("a", "b")
    one()
    one()
    assert roles.entries()[0].providers == ("b",)
    two()
    assert roles.entries() == []
    for name, role in (("", Role.DEFINITION), ("x", Role.PROVIDER), ("x", Role.CONSUMER)):
        try:
            roles.register(name, role)
        except MechaError as exc:
            assert exc.kind == "bad_registration"
        else:
            raise AssertionError(f"接受了不完整注册：{name!r}, {role}")

    class Engine:
        def schema(self): return {"run_output_required": ["ok"]}
        def run(self, spec, *, context, **opts): return {"ok": True}
        def summary(self): return {}
        def estimate(self, spec): return {"est_sec": 0}

    with tempfile.TemporaryDirectory() as td:
        with assemble(root=Path(td), data_dir_name=".test",
                      summarizer=lambda events: {}, validate=accept_all) as sw:
            assert sw.surface.roles is sw.roles
            undo = sw.roles.register("project-feature", Role.PROVIDER, provider="project")
            assert "project-feature" in sw.surface.roles.names()
            undo()
            assert "project-feature" not in sw.surface.roles.names()
    print("roles_selfcheck: PASS（注册、独立撤销、非法输入与装配共享）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
