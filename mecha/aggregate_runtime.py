"""把聚合端点**接上** supervisor，并让 dsh 连它（步骤 2b）。

## 这一层补的是"两件已经各自成立的事之间那根线"

- ``Aggregator``（2a）会合成工具表，但它**只是被动挂着**：谁在跑、端口是多少，它不知道；
- ``Supervisor``（步骤 1）管进程生死，但它**不知道聚合端点**的存在。

⇒ 本模块把两者接起来，并生成指向聚合端点的 **dsh 第三层 overlay**。

## ⚠ 铁律 7（单一真值）：**进程事实只有一个来源**

元工具报的"谁在跑"必须来自 ``supervisor.state()``（``state_fn``），
**不是**聚合端点自己记的账——它那份账只回答"我挂着谁、各有多少工具"。
两者是**不同的事实**，所以 ``query_software`` 分两栏报（``aggregate`` / ``process``）。
判据 2 就造一个"聚合端点以为在跑、supervisor 已停"的形态，断言**必须报停**。

## ⚠ "关软件"（存活滑块）与"切写权"是**两个不同操作**（三层架构 A3，别再混）

| 操作 | 动软件存活① | 动客户端连接② | 动写权③ | 该用谁 |
|---|---|---|---|---|
| **关软件**（存活滑块 off） | ✓ 停它的 child（**不删声明**） | 腿失联 | —— | ``close_software`` / :func:`close_service` |
| **切写权给手动** | ✗ **不动** | ✗ **不动**（dsh 与面板**待命、不关闭**） | ✓ | 软件进程内的闸门（**不经这里**） |
| **热更新**（改了软件代码） | ✓ 重启软件 | 腿自动重连 | ✗ | :meth:`Supervisor.restart` |

⇒ ``close_service`` **只管**"停这个 svc 的 child"；它**不碰**写权。
⭐ **切写权不动进程** ⇒ 这里没有任何"换腿/换操作者"的编排
（旧设计里的 ``switch_leg`` 已按三层架构改名为 :meth:`Supervisor.restart`）。

## ⚠ 生成的 overlay 三条不许丢（都来自旧实现 + ML 实测）

1. **``toolCallTimeoutMs``**：旧实现是 **600000**（10 分钟）。默认 60s 会让**长训练"假失败"**
   ——这是"看起来能用、实际在长任务上炸"的那类坑，最容易在重构里丢。
2. **``serverName`` 稳定**：它进工具名前缀 ``mcp__<serverName>__<tool>``，
   换了名字等于所有工具改名（dsh 侧历史对话/记忆里的名字全失效）。
3. **落在项目根的 ``runtime/`` 且不入库**（``runtime/`` 已 gitignore）：它是**生成物**，
   每次启动按**实际端口**重写。

## Known Limitations and Deferred Work

- **聚合端点与 supervisor 同进程**：本模块把 ``McpEndpoint`` 起在本进程里。
  ⇒ 本进程一死，整组子进程的聚合入口随之消失（但子进程本身不因此而死，见
  ``Supervisor`` 的收尾语义）。多进程聚合（聚合端点独立进程）没有需求驱动（n=1 不预建）。
- **``sync_backends`` 是显式调用**：不做"监听 supervisor 状态自动同步"。
  理由：状态变化点目前只有三处（``start`` / ``stop`` / ``restart``），
  在每处调一次即可；上监听会在没有需求时先长出一套事件面。
- **``open_service`` 只能起"声明里那份" child**：不支持运行期改声明
  （改声明要重启站点——声明是启动期输入，不是运行期状态）。
- 不做通知（见 ``aggregate`` 的 Known Limitations，总设计师已裁"本轮不做"）。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol

from .aggregate import META_CLOSE, META_OPEN, Aggregator
from .errors import MechaError
from .providers.mcp_client import Backend
from .site import Service, Site
from .software_control import SoftwareControl
from .supervise import ChildSpec, Supervisor

#: 旧实现用 10 分钟（默认 60s 会让长训练"假失败"）。⚠ **这条不许在重构里丢**。
DEFAULT_TOOL_CALL_TIMEOUT_MS = 600_000
#: 生成的 overlay 文件名（放项目根 ``runtime/`` 下，不入库）。
DSH_OVERLAY_FILENAME = "dsh-aggregate.patch.yml"


class BackendFactory(Protocol):
    """建一个后端句柄（生产用 ``Backend.connect``；判据注入假工厂）。"""

    def __call__(self, url: str, *, server_name: str) -> object: ...


def overlay_path(runtime_root: Path) -> Path:
    """该站点生成的 overlay 落点（**项目根 runtime/ 下、不入库**）。"""
    return Path(runtime_root) / DSH_OVERLAY_FILENAME


def overlay_text(*, mcp_port: int, server_name: str,
                 timeout_ms: int = DEFAULT_TOOL_CALL_TIMEOUT_MS,
                 entry_id: str | None = None,
                 header: str = "# 由 mecha 生成（每次启动按**实际端口**重写）——**不入库**。") -> str:
    """生成 dsh 第三层 overlay 的文本（指向**聚合端点**）。

    ⚠ ``- insert:`` 形态是**必须**的：裸 ``- id: …`` 会被 dsh 判
    ``patch: entry "…" not found`` 并**静默跳过**（旧实现实测过）。
    """
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
    """写 overlay（父目录按需创建）；返回写入路径。**覆盖写**（每次启动重写实际端口）。"""
    p = overlay_path(runtime_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(overlay_text(mcp_port=mcp_port, server_name=server_name,
                             timeout_ms=timeout_ms), encoding="utf-8")
    return p


def stable_server_name(site: Site) -> str:
    """由站点名算一个**稳定**的 ``serverName``（进工具名前缀 ⇒ 不许随运行期变）。

    ⚠ 它必须**只依赖声明**（站点名），不依赖端口/时间/pid —— 否则每次启动都改名，
    而 dsh 侧的历史对话里记的是旧名字。
    """
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", site.name.strip()).strip("-")
    if not cleaned:
        raise MechaError(
            f"站点名算不出可用的 serverName：{site.name!r}",
            kind="bad_server_name",
            hint="站点名至少要含一个字母/数字（serverName 进工具名前缀，必须稳定）",
        )
    return cleaned


def with_overlay_patch(child: ChildSpec, overlay: Path) -> ChildSpec:
    """把 overlay 塞进 dsh 的 ``--patch`` 列表（**本次实际使用的 spec** 的那一份）。

    ⚠ 用 ``dataclasses.replace`` 造**新** spec：``ChildSpec`` 是冻结的，
    而且"改声明"与"改本次 spawn"是两件事（后者才是我们要的）。
    """
    import dataclasses
    argv = list(child.argv)
    # dsh 的 patch 是**有序**列表（旧实现就是按顺序给多个 --patch）；追加到末尾。
    if "--patch" in argv:
        idx = len(argv) - 1 - argv[::-1].index("--patch")
        argv[idx + 1:idx + 1] = [str(overlay)]      # 紧跟它已有的那一个之后
    else:
        argv += ["--patch", str(overlay)]
    return dataclasses.replace(child, argv=tuple(argv))


class AggregateRuntime:
    """聚合端点的运行期接线：**进程控制面 + dsh overlay**。

    ``open_fn`` / ``close_fn`` 接的是 :class:`Supervisor` 的**真进程控制面**；
    ``state_fn`` 接的是 ``supervisor.state()``（唯一真值来源）。
    """

    def __init__(self, *, supervisor: Supervisor, services: Sequence[Service],
                 aggregator: Aggregator, backend_factory: BackendFactory,
                 timeout_ms: int = DEFAULT_TOOL_CALL_TIMEOUT_MS) -> None:
        self.super = supervisor
        self._services = {s.id: s for s in services}
        self.agg = aggregator
        self._factory = backend_factory
        self.timeout_ms = timeout_ms
        #: ⭐ **把三个回调接上** —— 本类就是"聚合端点 ↔ supervisor"的那根线。
        #: ⚠ 不做这一步，元工具 ``query_software`` 的 ``process`` 栏永远是 ``None``
        #: ⇒ "跑着的软件被报成没有进程事实"，**违反铁律 7**（元工具必须经
        #: ``supervisor.state()`` 取唯一真值，不能自己记账）。
        #: 这是**实测抓到的缺口**（判据② 「前置：跑着时 process 栏如实报」钉住）。
        aggregator.wire(state_fn=supervisor.state,
                        open_fn=self.open_service,
                        close_fn=self.close_service,
                        #: ⚠ **必须把声明的全部 id 交出去**（含还没挂上的）：
                        #: 元工具要能区分"**存在但没跑**"与"**压根没有这个软件**"。
                        #: 只看"挂过的"会把前者误报成后者。
                        known=tuple(self._services))

    # ---- 进程控制面（接元工具）----
    def open_service(self, svc_id: str) -> None:
        """起该 svc 的 child（**用声明里那份 ChildSpec**）。id 未知 ⇒ 响亮失败。"""
        svc = self._require(svc_id)
        self.super.start(svc.child())
        self.sync_backends()

    def close_service(self, svc_id: str) -> None:
        """停该 svc 的 child（**停进程，不删声明**）。id 未知 ⇒ 响亮失败。

        ⚠ 先 ``agg.detach`` 再停：这样**在停的过程中**来的调用会立刻拿到
        "已关闭 + 怎么重开"，而不是等超时。
        """
        self._require(svc_id)
        self._detach_if_attached(svc_id)
        cid = self._child_id(svc_id)
        if cid in self.super.state_ids():
            self.super.stop(cid)
        self.sync_backends()

    def _require(self, svc_id: str) -> Service:
        svc = self._services.get(svc_id)
        if svc is None:
            raise MechaError(
                f"没有这个软件：{svc_id!r}",
                kind="unknown_service",
                hint=f"已知软件：{sorted(self._services)}（见站点声明）",
            )
        return svc

    def _child_id(self, svc_id: str) -> str:
        return self._services[svc_id].child().id

    def _detach_if_attached(self, svc_id: str) -> None:
        try:
            self.agg.detach(svc_id)
        except MechaError:
            pass          # 本来就没挂过 ⇒ 不是错（关闭要求的是"结果状态正确"）

    # ---- 后端同步 ----
    def sync_backends(self) -> None:
        """按 ``supervisor.state()`` 把后端对齐到"谁在跑"。

        - 跑着且**有回读端口** ⇒ （若还没挂）attach；
        - 没跑 ⇒ detach（**保留工具名做墓碑**，见 ``Aggregator.registry``）。
        """
        rows = {r["id"]: r for r in self.super.state()["children"]}
        for svc_id, svc in self._services.items():
            row = rows.get(svc.child().id)
            running = bool(row and row.get("state") in ("ready", "running"))
            if running and self._url_for(svc, row) is not None:
                if svc_id not in self.agg.attached():
                    url = self._url_for(svc, row)
                    assert url is not None
                    backend = self._factory(url, server_name=svc_id)
                    self.agg.attach(svc_id, backend)      # type: ignore[arg-type]
            else:
                self._detach_if_attached(svc_id)

    def _url_for(self, svc: Service, row: Mapping[str, object]) -> str | None:
        """后端 URL：端口一律**回读**（``state()["port"]``），不猜、不用预选值。"""
        port = row.get("port")
        if not isinstance(port, int) or port <= 0:
            return None
        probe = svc.ready.probe
        if probe:
            # 探活串形如 http://127.0.0.1:{port}/health ⇒ 换成 MCP 端点
            base = probe.split("{port}")[0].rstrip("/")
            return f"{base}:{port}/mcp"
        return f"http://127.0.0.1:{port}/mcp"

    def probe_authority(self, spec: object) -> dict | None:
        """读**一个软件**的当前写权（给聚合面的 `read_write_authority` 用）。

        ⭐ **不缓存**（契约 §7.1.1 要点 1）：每次都真的走一次控制端点。
        loopback 很便宜，而缓存会引入"失效策略"这个**新的复杂度面**。

        @returns 三种形态：**成功**（带 mode 等）／那一个 **`unreadable`**／
            ``None``（**它不在这条路上** ⇒ 调用方**不报它**——铁律 8：不编造）。
        """
        svc_id = str(getattr(spec, "id", ""))
        if svc_id not in self.agg.attached():
            return None                      # 没挂上 = 没在跑 ⇒ **不报它**
        control = SoftwareControl.connect(svc_id, getattr(spec, "data_root"))
        avail, _why = control.available()
        if not avail:
            return None                      # 没起来 ⇒ 同样不报（那不是"读不到"）
        try:
            out = control.read_write_authority()
        except MechaError as exc:
            # ⚠ **只这一个报读不到**，不拖垮整个调用；⚠ 措辞与"确实没有"分开。
            #   `control_rejected` 的典型来源：**该软件还没实现这个读操作**
            #   （契约已定案、项目侧尚未落地）—— 那**不是**"没有写权这回事"。
            return {"unreadable": f"{exc.kind}：{exc.message}"}
        if not isinstance(out, dict) or not out.get("mode"):
            # 有回执但**没有 mode** ⇒ 不是真值（防"看着像事实、其实全是空"）
            return {"unreadable": "回执里没有 mode（对端可能还没实现这个读操作）"}
        # 软件侧回执形状就是 `authority_view` 那几个字段（+ `ok`）⇒ 原样透出
        row = {k: out.get(k) for k in ("ok", "mode", "ai_can_write", "human_holds")}
        return row

    # ---- dsh overlay ----
    def prepare_dsh(self, site: Site, mcp_port: int) -> Path:
        """生成指向聚合端点的 overlay（**每次启动按实际端口重写**）。"""
        return write_dsh_overlay(site_runtime(site), mcp_port=mcp_port,
                                server_name=stable_server_name(site),
                                timeout_ms=self.timeout_ms)


def site_runtime(site: Site) -> Path:
    """站点运行期根（与 ``launch._runtime_root`` 同一处；这里只取同一个约定）。"""
    return Path.cwd() / "_mecha_runtime"


def make_prepare_hook(runtime: AggregateRuntime, site: Site,
                      mcp_port_getter: Callable[[], int | None]) -> Callable:
    """造一个 ``prepare_hooks`` 回调：**spawn dsh 之前**把 overlay 塞进它的 ``--patch``。

    ⚠ 为什么用 prepare 钩子而不是在别处拼好：dsh 的 ``ChildSpec`` 是**启动期**给的，
    而 overlay 的端口要等**聚合端点真的起来**才知道 ⇒ 只能在"**spawn 之前那一刻**"接上。
    这正是步骤 1 留这个出口的原因（修正 1）。
    """
    def hook(spec: ChildSpec, child: object) -> ChildSpec | None:
        if spec.id != "dsh":
            return None                                   # 只管 dsh
        port = mcp_port_getter()
        if port is None:
            raise MechaError(
                "聚合端点还没起来，无法给 dsh 生成 overlay",
                kind="aggregate_port_unknown",
                hint="顺序必须是 svc* → aggregator → dsh（StartupPlan.ordered 已保证）",
            )
        overlay = runtime.prepare_dsh(site, port)
        return with_overlay_patch(spec, overlay)
    return hook
