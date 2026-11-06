"""AI 宿主的通用接缝。

这里只放框架会调用的机制：启动计划、宿主能力声明、准备结果和刷新结果。
具体宿主实现分别住在 ``dsh_adapter/`` 与 ``codex_adapter/``，``launch.py``
不持有任何 dsh/Codex 的 argv、patch、TOML、Hook 或 SDK 细节。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .site import Service, Site
from .supervise import ChildSpec


class AIHostKind(str, Enum):
    """一次 launcher 选择的 AI 宿主。"""

    DSH = "dsh"
    CODEX = "codex"
    NONE = "none"


@dataclass(frozen=True)
class HostPlan:
    """宿主适配器唯一能拿到的启动期事实。"""

    kind: AIHostKind
    sites: tuple[Site, ...]
    runtime_root: Path
    aggregate_port: int = 0
    aggregate_url: str = ""
    services: tuple[Service, ...] = ()

    @property
    def is_dsh(self) -> bool:
        return self.kind is AIHostKind.DSH

    @property
    def is_codex(self) -> bool:
        return self.kind is AIHostKind.CODEX


@dataclass(frozen=True)
class HostPreparation:
    """宿主准备结果；command 为空表示由用户从外部启动。"""

    config_path: Path | None = None
    command: str = ""
    notes: tuple[str, ...] = ()
    files: tuple[Path, ...] = ()


@dataclass(frozen=True)
class RefreshResult:
    """工具表变化后的宿主刷新事实。"""

    reconnect_required: bool
    message: str
    revision: int = 0


@dataclass
class HostWiring:
    """聚合端点起好后才存在的运行期对象；由 launcher 交给适配器。"""

    supervisor: Any
    aggregator: Any
    runtime: Any | None = None
    endpoint: Any | None = None
    controller: Any | None = None
    mcp_port: int = 0
    #: ⭐ 宿主自带界面（codex 的 Web UI）起来后要不要自动开浏览器。
    #: 语义与 `StatusPage` 的 `open_browser` 一致：`--no-browser` 时为 False。
    open_browser: bool = False


@runtime_checkable
class AIHostAdapter(Protocol):
    """宿主适配器。用 Protocol 而非 ABC：框架只按这些成员调用。"""

    name: str
    managed: bool
    child_id: str | None

    def child(self, plan: HostPlan) -> ChildSpec | None: ...
    def spec(self, plan: HostPlan) -> object | None: ...
    def validate(self, plan: HostPlan) -> None: ...
    def warn(self, plan: HostPlan) -> None: ...
    def prepare(self, plan: HostPlan) -> HostPreparation: ...
    def install(self, plan: HostPlan, wiring: HostWiring) -> None: ...
    def overlay_refresh(self, plan: HostPlan,
                        wiring: HostWiring) -> Callable[[], object] | None: ...
    def client_refresh(self, plan: HostPlan,
                       wiring: HostWiring) -> Callable[[], object] | None: ...
    def refresh_catalog(self, plan: HostPlan, revision: int) -> RefreshResult: ...
    def capabilities(self, plan: HostPlan) -> Mapping[str, object]: ...
    def shutdown(self, plan: HostPlan) -> None: ...


class NullHostAdapter:
    """不启动 AI 宿主；软件、聚合端点和人类控制台仍照常工作。"""

    name = "none"
    managed = False
    child_id: str | None = None

    def child(self, plan: HostPlan) -> ChildSpec | None:
        return None

    def spec(self, plan: HostPlan) -> object | None:
        return None

    def validate(self, plan: HostPlan) -> None:
        return None

    def warn(self, plan: HostPlan) -> None:
        return None

    def prepare(self, plan: HostPlan) -> HostPreparation:
        return HostPreparation(notes=("未启用 AI 宿主；聚合端点仍可供外部客户端连接",))

    def install(self, plan: HostPlan, wiring: HostWiring) -> None:
        return None

    def overlay_refresh(self, plan: HostPlan,
                        wiring: HostWiring) -> Callable[[], object] | None:
        return None

    def client_refresh(self, plan: HostPlan,
                       wiring: HostWiring) -> Callable[[], object] | None:
        return None

    def refresh_catalog(self, plan: HostPlan, revision: int) -> RefreshResult:
        return RefreshResult(False, "未启用 AI 宿主，无需重连", revision)

    def capabilities(self, plan: HostPlan) -> Mapping[str, object]:
        return {"label": self.name, "managed": False, "child_id": None,
                "external": False}

    def shutdown(self, plan: HostPlan) -> None:
        return None


def flat_site_services(sites: Sequence[Site]) -> tuple[Service, ...]:
    """站点服务平铺；与启动顺序同一约定，供适配器在不反向 import launch 时使用。"""
    return tuple(svc for site in sites for svc in site.services)


def make_host_plan(kind: AIHostKind | str, sites: Sequence[Site],
                   runtime_root: Path, *, aggregate_port: int = 0,
                   aggregate_url: str = "") -> HostPlan:
    """构造一份宿主计划；纯函数，不碰文件系统。"""
    value = AIHostKind(str(getattr(kind, "value", kind)))
    services = flat_site_services(sites)
    return HostPlan(kind=value, sites=tuple(sites),
                    runtime_root=Path(runtime_root),
                    aggregate_port=int(aggregate_port or 0),
                    aggregate_url=aggregate_url, services=services)


def select_host_adapter(kind: AIHostKind | str) -> AIHostAdapter:
    """按启动者选择构造宿主适配器；具体包只在命中时导入。"""
    value = AIHostKind(str(getattr(kind, "value", kind)))
    if value is AIHostKind.DSH:
        from .dsh_adapter import DshHostAdapter
        return DshHostAdapter()
    if value is AIHostKind.CODEX:
        from .codex_adapter import CodexHostAdapter
        return CodexHostAdapter()
    return NullHostAdapter()


__all__ = [
    "AIHostAdapter", "AIHostKind", "HostPlan", "HostPreparation",
    "HostWiring", "NullHostAdapter", "RefreshResult", "flat_site_services",
    "make_host_plan", "select_host_adapter",
]
