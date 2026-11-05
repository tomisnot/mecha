"""``AIHostAdapter`` 的 dsh 实现。"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ..errors import MechaError
from ..host_adapter import HostPlan, HostPreparation, HostWiring, RefreshResult
from ..site import DSH_CHILD_ID
from ..supervise import ChildSpec
from .launch import (make_client_refresh, make_overlay_refresh, merge_dsh,
                     warn_if_port_taken)
from .runtime import make_prepare_hook


class DshHostAdapter:
    """把现有 dsh 行为包装成宿主适配器；默认启动路径保持一字不变。"""

    name = "dsh"
    managed = True
    child_id: str | None = DSH_CHILD_ID

    def child(self, plan: HostPlan) -> ChildSpec | None:
        return merge_dsh(plan.sites)[0]

    def spec(self, plan: HostPlan) -> object | None:
        return merge_dsh(plan.sites)[1]

    def validate(self, plan: HostPlan) -> None:
        merge_dsh(plan.sites)

    def warn(self, plan: HostPlan) -> None:
        warn_if_port_taken(plan.sites)

    def prepare(self, plan: HostPlan) -> HostPreparation:
        return HostPreparation(notes=("dsh 由 supervisor 托管；overlay 在 spawn 前注入",))

    def install(self, plan: HostPlan, wiring: HostWiring) -> None:
        if wiring.runtime is None or not plan.sites:
            return
        wiring.supervisor.add_prepare_hook(make_prepare_hook(
            wiring.runtime, plan.sites[0], lambda: int(wiring.mcp_port or 0) or None,
            runtime_root=plan.runtime_root))

    def overlay_refresh(self, plan: HostPlan,
                        wiring: HostWiring) -> Callable[[], object] | None:
        return make_overlay_refresh(plan.sites, wiring.mcp_port)

    def client_refresh(self, plan: HostPlan,
                       wiring: HostWiring) -> Callable[[], object] | None:
        return make_client_refresh(plan.sites, wiring.supervisor)

    def refresh_catalog(self, plan: HostPlan, revision: int) -> RefreshResult:
        return RefreshResult(True, "dsh MCP 客户端需要按当前聚合端点重建连接", revision)

    def capabilities(self, plan: HostPlan) -> Mapping[str, object]:
        try:
            spec = merge_dsh(plan.sites)[1]
            port = spec.port_preference
        except MechaError:
            port = 0
        return {"label": self.name, "managed": True, "child_id": self.child_id,
                "external": False, "port_preference": port}

    def shutdown(self, plan: HostPlan) -> None:
        return None


__all__ = ["DshHostAdapter"]
