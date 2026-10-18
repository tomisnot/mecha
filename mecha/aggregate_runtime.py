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
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol

from .aggregate import AGGREGATE_SERVER_NAME, META_CLOSE, META_OPEN, Aggregator
from .errors import MechaError
from .providers.mcp_client import Backend
from .site import DSH_CHILD_ID, Service, Site
from .software_control import SoftwareControl
from .supervise import ChildSpec, Supervisor

#: 旧实现用 10 分钟（默认 60s 会让长训练"假失败"）。⚠ **这条不许在重构里丢**。
DEFAULT_TOOL_CALL_TIMEOUT_MS = 600_000
#: 生成的 overlay 文件名（放项目根 ``runtime/`` 下，不入库）。
DSH_OVERLAY_FILENAME = "dsh-aggregate.patch.yml"
#: ``unreachable`` 后端的有界重试（次数 / 间隔秒）——见 ``_retry_unreachable``。
UNREACHABLE_RETRIES = 5
RETRY_DELAY_S = 6.0


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


#: ⛔ **删掉的机制（R13）：`stable_server_name(site)`** —— 它由**站点名**算聚合端点的
#: ``serverName``。实测抓到的真 bug：用户**只勾了 GP**，dsh 里那个 MCP 客户端却叫
#: **`ML Toolbox`**（`MECHA_SITE_ROOTS` 里 ML 排第一）⇒ 现场就是
#: "**只选了 GP，载入的却是 ML 的 mcp**"。
#: ⭐ 那个端点是**框架的**聚合面（同时代表**所有**站点）⇒ 名字由框架给、**与站点顺序无关**
#: （唯一家：:data:`mecha.aggregate.AGGREGATE_SERVER_NAME`）。
#: ⚠ **反向判断**（不许它复活）：`checks/aggregate_runtime_selfcheck.py` 里的
#: 「**名字与站点顺序无关**」那条 —— 把 `prepare_dsh` 改回按站点名 ⇒ 当场红。


def with_overlay_patch(child: ChildSpec, overlay: Path) -> ChildSpec:
    """把 overlay 塞进 dsh 的 ``--patch`` 列表（**本次实际使用的 spec** 的那一份）。

    ⚠ 用 ``dataclasses.replace`` 造**新** spec：``ChildSpec`` 是冻结的，
    而且"改声明"与"改本次 spawn"是两件事（后者才是我们要的）。

    ## ⛔ 这里曾经有一个**让 dsh 永远起不来**的错位（2026-10-09 实测抓到）

    旧实现是 ``argv[idx + 1:idx + 1] = [overlay]``，而 ``idx`` 是最后一个 ``--patch``
    **标志**的下标 ⇒ 插进去的是**标志与它的值之间**：

    ```
    期望：  --patch F  --patch OVERLAY  --port 3081
    实际：  --patch OVERLAY  F  --port 3081        ← F 变成了**裸 token**
    ```

    ⚠ 而 dsh 的 CLI 规则是"**启动器选项在遇到它不认识的那个 token 为止；之后全归 app**"
    （`apps/cli/src/args.ts` 的 `passThroughOptions` + `enablePositionalOptions`）
    ⇒ 那个裸路径被交给 **web app** ⇒ commander 当场报

    ```
    error: too many arguments. Expected 0 arguments but got 1: <框架自己的 patch 路径>
    ```

    ⇒ ⭐ **dsh 以 exit=1 反复退出（`restarts=4`）、面板上那一行永远是 stopped、
    "dsh 窗口不弹出"** —— 而日志里那句错的形状**看起来像 dsh 不认 `--patch`**
    （⚠ 实测反了：`dsh web --patch X` **是合法的**，坏的是**值被顶掉**）。

    ⇒ 修法：在**那个 ``--patch`` 的值之后**插入**成对的一份**（``["--patch", overlay]``）
    —— ⚠ **不只是路径**：``--patch`` 是"标志 + 值"的成对形状，只插路径会把**前一个值**
    或**这个路径自己**顶成一个**裸 token**（两种错位都实测过，报错措辞一模一样）。
    """
    import dataclasses
    argv = list(child.argv)
    # dsh 的 patch 是**有序**列表（旧实现就是按顺序给多个 --patch）；追加到末尾。
    if "--patch" in argv:
        idx = len(argv) - 1 - argv[::-1].index("--patch")   # 最后一个 --patch **标志**
        #: ⚠ 插到它的**值之后**（`idx + 1` 是那个值），且**必须带自己的 `--patch`**。
        at = idx + 2 if idx + 1 < len(argv) else idx + 1
        argv[at:at] = ["--patch", str(overlay)]
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
                 endpoint: object | None = None,
                 overlay_refresh: Callable[[], object] | None = None,
                 timeout_ms: int = DEFAULT_TOOL_CALL_TIMEOUT_MS) -> None:
        self.super = supervisor
        self._services = {s.id: s for s in services}
        self.agg = aggregator
        self._factory = backend_factory
        self.timeout_ms = timeout_ms
        #: 重写 dsh overlay 的小钩子。mecha 注入的 host 插件监视此文件，
        #: 变化后通过公开 Loader API 重建 mcp-client；不依赖 DSH 核心 HMR。
        self._overlay_refresh = overlay_refresh
        #: ⭐ **活工具表那一侧**（``McpEndpoint``）：``sync_backends`` 每次对齐完就让它
        #: "对账 + 广播"（见 :meth:`publish_tools`）。⚠ 不注入它就退回"表不会变"的旧行为
        #: （判据 ``aggregate_tools_hot_sync_selfcheck`` 钉住注入 + 通知真到）。
        self._endpoint = endpoint
        #: 上一次同步工具表的失败（**不吞**：那是"AI 看不到工具"那一类故障）。
        self.last_publish_error: str | None = None
        #: `unreachable` 后端的有界重试计数（见 :meth:`_retry_unreachable`）。
        self._retry_count = 0
        #: 后端进程已经 ready、但 MCP 端点尚未接受连接时的待连接集合。
        #: 这类失败发生在 backend_factory 之前，Aggregator 还没有 entry，
        #: 因而不能只扫描 states() 里的 ``unreachable``。
        self._pending_connect: set[str] = set()
        #: 最近一次同步失败，供启动控制台显示，不作为进程状态的替代。
        self.last_sync_error: str | None = None
        #: ⭐ **把三个回调接上** —— 本类就是"聚合端点 ↔ supervisor"的那根线。
        #: ⚠ 不做这一步，元工具 ``query_software`` 的 ``process`` 栏永远是 ``None``
        #: ⇒ "跑着的软件被报成没有进程事实"，**违反铁律 7**（元工具必须经
        #: ``supervisor.state()`` 取唯一真值，不能自己记账）。
        #: 这是**实测抓到的缺口**（判据② 「前置：跑着时 process 栏如实报」钉住）。
        aggregator.wire(state_fn=supervisor.state,
                        open_fn=self.open_service,
                        close_fn=self.close_service,
                        #: ⭐ **会话级失败的自愈口**（2026-10-10）：后端连不上/调到一半断了
                        #: （多半是它在被 supervisor 重启、可能**换了端口**）时，
                        #: 聚合层调它 ⇒ 按**当前端口**重新 attach（新 ``Backend`` ⇒
                        #: 新会话 + 新 ``initialize``），然后重试一次。
                        #: ⚠ 这正是"**要整体重建会话，而不是只重解析端口**"的落点。
                        resync_fn=self.sync_backends,
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
        #: ⚠⚠ **不能用 `agg.attached()` 判"要不要重新挂"**（2026-10-10 真机抓到的真 bug）：
        #: 它按墓碑设计**含已关闭的后端**（`aggregate.attached()` 的 docstring 自己就写着
        #: "想判它活着吗**不要**用这个方法，用 `states()`"）⇒ 一旦 detach 过，
        #: `svc_id not in attached()` **永远为假** ⇒ **软件重启后工具永远回不到可用**
        #: （真机现象：进程已 ready、它的工具**全是墓碑**）。
        #: ⇒ 判据是"**聚合端点这边的状态不是 up** 就（重新）挂一次"。
        states = self.agg.states()
        for svc_id, svc in self._services.items():
            row = rows.get(svc.child().id)
            running = bool(row and row.get("state") in ("ready", "running"))
            if running and self._url_for(svc, row) is not None:
                if states.get(svc_id) != "up":
                    url = self._url_for(svc, row)
                    assert url is not None
                    try:
                        backend = self._factory(url, server_name=svc_id)
                        self.agg.attach(svc_id, backend)      # type: ignore[arg-type]
                        self._pending_connect.discard(svc_id)
                        if self.agg.states().get(svc_id) == "unreachable":
                            self.last_sync_error = (
                                f"{svc_id}: MCP backend initialized but tool list is unreachable")
                    except Exception as exc:                 # noqa: BLE001
                        #: ready 只证明端口可探活，不保证 MCP 已完成初始化。
                        #: 保留待连接项，稍后重试；不要让一个后端阻断其它软件的对账。
                        self._pending_connect.add(svc_id)
                        self.last_sync_error = (
                            f"{svc_id}: {type(exc).__name__}: {exc}")
                        # 这里可能发生在尚未完成 stdout UTF-8 重配置的启动阶段，
                        # 所以日志用 ASCII，避免 cp936 再制造一个“同步线程崩了”的假故障。
                        print(f"[aggregate] WARN backend pending: {self.last_sync_error}")
            else:
                self._pending_connect.discard(svc_id)
                self._detach_if_attached(svc_id)
        if (not self._pending_connect
                and not any(v == "unreachable" for v in self.agg.states().values())):
            self.last_sync_error = None
        #: ⭐ **对账 + 广播**（2026-10-10）：attach/detach 改的是 ``Aggregator`` 的
        #: registry，而**端点发出去的**是另一张表（``MCPServer`` 里那张）——
        #: ⚠ 不接这一步，AI 的工具表就永远停在"端点启动那一刻"（实测：registry 里
        #: 全部工具，端点只发那几个元工具）。顺序**先改表、后通知**（客户端收到通知会立刻
        #: ``tools/list``，那时必须已经能看到新表）。
        self.publish_tools()
        self._retry_unreachable()

    def _retry_unreachable(self) -> None:
        """⭐ 有界重试：后端标了 ``unreachable`` 就隔几秒再同步一次。

        ⚠ 为什么需要（真机实测抓到的形态）：软件**刚 ready** 那一刻它的 MCP 端点可能还没
        吃下我们这个客户端（``Backend.connect`` 失败 ⇒ 后端标 ``unreachable``，工具表里
        它是墓碑）；而 attach 只在"**有事件**"（就绪/退出/滑块）时才跑 ⇒ 没有重试的话
        那一整轮里 AI 就一直看到墓碑（现象："软件明明在跑，AI 却调不动它的工具"）。
        ⚠ 有界：最多 :data:`UNREACHABLE_RETRIES` 次、每次隔 :data:`RETRY_DELAY_S` 秒；
        全部 up 时计数归零（下次事件重新开始）。
        """
        states = self.agg.states()
        broken = [s for s in self._services
                  if states.get(s) == "unreachable" or s in self._pending_connect]
        if not broken:
            self._retry_count = 0
            return
        if self._retry_count >= UNREACHABLE_RETRIES:
            print(f"[aggregate] WARN backend still unreachable after "
                  f"{self._retry_count} retries: {sorted(broken)}; retry on next lifecycle event")
            return
        self._retry_count += 1
        timer = threading.Timer(RETRY_DELAY_S, self._retry_tick, args=(self._retry_count,))
        timer.daemon = True
        timer.start()

    def _retry_tick(self, count: int) -> None:
        """到点重同步一次（⚠ 只在计数没被"全部 up"重置时跑）。"""
        if count != self._retry_count:
            return
        try:
            self.sync_backends()
        except Exception as exc:                      # noqa: BLE001 - 后台重试不许炸线程
            print(f"[aggregate] WARN resync failed: {type(exc).__name__}: {exc}")

    def publish_tools(self) -> None:
        """把活工具表对齐到 ``Aggregator`` 的当前 registry，并在变化时广播。

        ⚠ 失败**不吞**（记进 ``last_publish_error`` 并打到日志）：工具表没对齐 =
        "AI 看不到软件工具"那一类故障，静默会让人以为一切正常。
        """
        endpoint = self._endpoint
        if endpoint is None:
            return
        try:
            changed = endpoint.publish_tools(self.agg.registry())
            self.last_publish_error = None
            if changed:
                if self._overlay_refresh is not None:
                    try:
                        self._overlay_refresh()
                    except Exception as exc:                 # noqa: BLE001
                        self.last_publish_error = (
                            f"overlay refresh: {type(exc).__name__}: {exc}")
                        print(f"[aggregate] WARN dsh overlay refresh failed: "
                              f"{self.last_publish_error}")
                #: ⚠ 走 supervisor 那张"给人看的清单"（状态页读得到），别自己另开一本账。
                self.super.cleaned.append(
                    f"工具表已更新（后端：{sorted(self.agg.attached())}）")
        except Exception as exc:                      # noqa: BLE001 - 记下来，别中断启停
            self.last_publish_error = f"{type(exc).__name__}: {exc}"
            print(f"[aggregate] ⚠ 工具表同步失败：{self.last_publish_error}")

    def _url_for(self, svc: Service, row: Mapping[str, object]) -> str | None:
        """后端 URL：端口一律**回读**（``state()["port"]``），不猜、不用预选值。"""
        port = row.get("port")
        if not isinstance(port, int) or port <= 0:
            return None
        probe = svc.ready.probe
        if probe:
            # 探活串形如 http://127.0.0.1:{port}/health ⇒ 换成 MCP 端点
            #
            # ⚠⭐ **冒号可能在 `{port}` 之前，也可能不在**（实测抓到的真 bug）：
            # 三家的探活串都是 ``http://127.0.0.1:{port}/mcp`` ⇒ **自带冒号**；
            # 而这里若再补一个冒号 ⇒ 拼出 ``http://127.0.0.1::62499/mcp``
            # ⇒ ⚠ **后端永远 attach 不上**（现象："启动失败：backend_protocol 协议错"），
            # 而**三家全中** —— AI 那条腿从来没接上过真项目。
            # ⚠ 判据没抓到它：夹具的探活串**没有那个冒号**（又是"替身失真"）。
            base = probe.split("{port}")[0].rstrip("/")
            if base.endswith(":"):
                base = base[:-1]
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
        #: ⭐ 白名单**必须带 `shared`**（2026-10-10）：AI 面据此能一眼看出"这家是共用档"
        #: （`Mode.OPEN` 下 `ai_can_write` 与 `human_holds` **同时为真**，单看任何一个
        #: 都会误读）。⚠ 实测踩到：这里漏了它 ⇒ **AI 面那份自省静默少一格**，
        #: 而启动控制台那份（另一处白名单）有它 ⇒ **两处口径分叉**（同一个事实两种形状）。
        row = {k: out.get(k) for k in ("ok", "mode", "ai_can_write", "human_holds", "shared")}
        #: ⭐ **每个软件带自己那句"怎么切"**：`how_to_open` 是**项目知识**（哪个页面/哪个开关），
        #: 而聚合面顶层那一句只取到**其中一个**项目的原话（`sorted()` 里的第一个非空）。
        #: ⇒ 逐条带上它，AI 才不会拿甲家的入口去指乙家。
        row["how_to_open"] = str(getattr(spec, "how_to_open", "") or "")
        return row

    # ---- dsh overlay ----
    def prepare_dsh(self, site: Site, mcp_port: int) -> Path:
        """生成指向聚合端点的 overlay（**每次启动按实际端口重写**）。

        ⚠ 名字用**框架自己的**（:data:`~mecha.aggregate.AGGREGATE_SERVER_NAME`），
        **不是**站点名 —— 后者是"位置"的偶然产物（实测：只开 GP 却叫 `ML Toolbox`）。
        ⚠ `site` 参数保留：overlay 的落点仍按**站点运行期根**算（`site_runtime`）。
        """
        return write_dsh_overlay(site_runtime(site), mcp_port=mcp_port,
                                server_name=AGGREGATE_SERVER_NAME,
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
        if spec.id != DSH_CHILD_ID:
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
