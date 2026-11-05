"""Codex 的首版宿主适配器：外部 MCP 模式。

mecha 只生成隔离的 ``CODEX_HOME`` 配置，不启动交互式 Codex，不改动用户全局配置。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ..errors import MechaError
from ..host_adapter import HostPlan, HostPreparation, HostWiring, RefreshResult
from ..supervise import ChildSpec
from .config import (MCP_SERVER_NAME, DEFAULT_TOOL_TIMEOUT_SEC, paths_for,
                     write_preparation)
from .tasks import ledger_path


class CodexHostAdapter:
    name = "codex"
    managed = False
    child_id: str | None = None

    def _url(self, plan: HostPlan) -> str:
        if plan.aggregate_url:
            return plan.aggregate_url
        if plan.aggregate_port > 0:
            return f"http://127.0.0.1:{plan.aggregate_port}/mcp"
        raise MechaError("聚合端点尚未就绪，不能生成 Codex MCP 配置",
                         kind="codex_aggregate_port_unknown",
                         hint="McpEndpoint.start() 必须先回读真实端口")

    def child(self, plan: HostPlan) -> ChildSpec | None:
        return None

    def spec(self, plan: HostPlan) -> object | None:
        return None

    def validate(self, plan: HostPlan) -> None:
        """启动前只校验声明；实际 URL 在聚合端点回读端口后再由 prepare 校验。"""
        return None

    def warn(self, plan: HostPlan) -> None:
        print("[codex] 外部 MCP 模式：不启动 dsh；请在软件就绪后按 "
              "codex-start.txt 启动 Codex", flush=True)

    def prepare(self, plan: HostPlan) -> HostPreparation:
        paths, command = write_preparation(
            plan.runtime_root, aggregate_url=self._url(plan),
            server_name=MCP_SERVER_NAME,
            tool_timeout_sec=DEFAULT_TOOL_TIMEOUT_SEC)
        notes = (
            "Codex 使用独立 CODEX_HOME，未修改用户全局配置",
            f"MCP 端点：{self._url(plan)}",
            "工具表变化后必须重连 Codex MCP 客户端，不依赖 list_changed 通知",
        )
        for note in notes:
            print(f"[codex] {note}", flush=True)
        return HostPreparation(
            config_path=paths.config, command=command, notes=notes,
            files=(paths.config, paths.start, paths.agents,
                   paths.catalog_revision))

    def install(self, plan: HostPlan, wiring: HostWiring) -> None:
        return None

    def overlay_refresh(self, plan: HostPlan,
                        wiring: HostWiring) -> Callable[[], object] | None:
        return None

    def client_refresh(self, plan: HostPlan,
                       wiring: HostWiring) -> Callable[[], object] | None:
        return None

    def refresh_catalog(self, plan: HostPlan, revision: int) -> RefreshResult:
        paths = paths_for(plan.runtime_root)
        paths.home.mkdir(parents=True, exist_ok=True)
        paths.catalog_revision.write_text(f"{int(revision)}\n", encoding="utf-8")
        return RefreshResult(
            True,
            "Codex 外部会话需重连 MCP 客户端；当前工具表版本已写入 catalog-revision",
            int(revision))

    def capabilities(self, plan: HostPlan) -> Mapping[str, object]:
        paths = paths_for(plan.runtime_root)
        return {"label": self.name, "managed": False, "child_id": None,
                "external": True, "config_path": str(paths.config),
                "start_file": str(paths.start),
                "task_ledger": str(ledger_path(plan.runtime_root)),
                "aggregate_url": self._url(plan)}

    def shutdown(self, plan: HostPlan) -> None:
        return None


__all__ = ["CodexHostAdapter"]
