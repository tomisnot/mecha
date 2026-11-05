"""dsh 的 overlay 与启动期 prepare hook。

这些函数只描述 dsh 的加载形状；``AggregateRuntime`` 通过兼容导入继续使用它们。
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from pathlib import Path

from ..errors import MechaError
from ..site import DSH_CHILD_ID, Site
from ..supervise import ChildSpec

#: 与聚合端点给 dsh 的 toolCallTimeoutMs 对齐；长同步调用不能按 60s 截断。
DEFAULT_TOOL_CALL_TIMEOUT_MS = 600_000
DSH_OVERLAY_FILENAME = "dsh-aggregate.patch.yml"
TASK_BRIDGE_FILE = ".page-port"


def site_runtime(site: Site) -> Path:
    """站点运行期根；当前 launcher 固定使用 cwd 下的 ``_mecha_runtime``。"""
    return Path.cwd() / "_mecha_runtime"


def overlay_path(runtime_root: Path) -> Path:
    return Path(runtime_root) / DSH_OVERLAY_FILENAME


def overlay_text(*, mcp_port: int, server_name: str,
                 timeout_ms: int = DEFAULT_TOOL_CALL_TIMEOUT_MS,
                 entry_id: str | None = None,
                 header: str = "# 由 mecha 生成（每次启动按**实际端口**重写）——**不入库**。") -> str:
    """生成 dsh 第三层 overlay；``- insert:`` 形态不可改成裸 ``- id:``。"""
    eid = entry_id or f"mcp-{server_name}"
    return (
        f"{header}\n"
        "# 用法：dsh --profile web --patch <本文件> --port <dsh 端口>\n"
        "# 注意：patch 条目必须用 `- insert:` 包住；裸 id 会被判「目标不存在」并跳过。\n"
        "- insert:\n"
        f"    - id: {eid}\n"
        "      name: '@deepseek-ai/dsh-mcp-client'\n"
        "      config:\n"
        f"        serverName: {server_name}\n"
        "        transport: streamable-http\n"
        f"        url: http://127.0.0.1:{int(mcp_port)}/mcp\n"
        "        failOnStartupError: true\n"
        f"        toolCallTimeoutMs: {int(timeout_ms)}\n"
    )


def write_dsh_overlay(runtime_root: Path, *, mcp_port: int, server_name: str,
                      timeout_ms: int = DEFAULT_TOOL_CALL_TIMEOUT_MS) -> Path:
    p = overlay_path(runtime_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(overlay_text(mcp_port=mcp_port, server_name=server_name,
                             timeout_ms=timeout_ms), encoding="utf-8")
    return p


def with_overlay_patch(child: ChildSpec, overlay: Path) -> ChildSpec:
    """把 overlay 作为完整的一对 ``--patch <path>`` 插到最后一个 patch 值之后。"""
    argv = list(child.argv)
    if "--patch" in argv:
        idx = len(argv) - 1 - argv[::-1].index("--patch")
        at = idx + 2 if idx + 1 < len(argv) else idx + 1
        argv[at:at] = ["--patch", str(overlay)]
    else:
        argv += ["--patch", str(overlay)]
    return dataclasses.replace(child, argv=tuple(argv))


def make_prepare_hook(runtime: object, site: Site,
                      mcp_port_getter: Callable[[], int | None],
                      runtime_root: Path | None = None) -> Callable:
    """spawn dsh 前把 overlay 塞进它的 argv，并注入任务桥端口文件。"""
    root = Path(runtime_root) if runtime_root is not None else site_runtime(site)

    def hook(spec: ChildSpec, child: object) -> ChildSpec | None:
        if spec.id != DSH_CHILD_ID:
            return None
        port = mcp_port_getter()
        if port is None:
            raise MechaError(
                "聚合端点还没起来，无法给 dsh 生成 overlay",
                kind="aggregate_port_unknown",
                hint="顺序必须是 svc* → aggregator → dsh（StartupPlan.ordered 已保证）",
            )
        overlay = runtime.prepare_dsh(site, port)  # type: ignore[attr-defined]
        out = with_overlay_patch(spec, overlay)
        env = dict(out.env)
        env["MECHA_TASK_BRIDGE_FILE"] = str(root / TASK_BRIDGE_FILE)
        return dataclasses.replace(out, env=env)
    return hook


__all__ = [
    "DEFAULT_TOOL_CALL_TIMEOUT_MS", "DSH_OVERLAY_FILENAME", "TASK_BRIDGE_FILE",
    "make_prepare_hook", "overlay_path", "overlay_text", "site_runtime",
    "with_overlay_patch", "write_dsh_overlay",
]
