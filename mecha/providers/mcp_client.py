"""跨进程 MCP 客户端：连一个**后端 MCP 服务端**（各自的软件脊椎），拿工具、调工具。

## 为什么是"真客户端"而不是进程内直调

⚠ ``providers/mcp.py:62`` 记着这条纪律的原话：**绝不假设 ``call_tool`` 可在进程内直调**
（源码 grep 过、协议层却有；两家下游用的还不是同一个 MCP 客户端）。
⇒ 聚合端点连后端时**必须走真协议**（streamable-http）：`initialize` → `list_tools` → `call_tool`。
这也正是"各自一条脊椎"的意思：后端是**独立进程里的真服务端**，聚合端点只是它的一个客户端。

## 超时与可读失败（⚠ 三件必须分开说清）

1. **连不上**（端点没人听 / 握手失败）⇒ ``BackendConnectError``；
2. **调用超时**（连上了但对端不回）⇒ ``BackendTimeoutError``；
3. **协议错**（回了但不是 MCP）⇒ ``BackendProtocolError``。

⇒ 三种**分别有类型、消息也分别写**。全都写"失败"会让人无法判断该去查端口、查死锁、还是查版本。
⚠ 超时是**必须**的：聚合端点是一个长跑服务，一次挂住的调用不能把它的服务线程占死
（判据 5 就钉这条）。

## 已知的 SDK 形状（实测 mcp 2.2.0，不是猜的）

- 客户端上下文管理器真名是 ``streamable_http_client``
  （**没有** ``streamablehttp_client``——那是旧名，2.2.0 里 import 会失败）；
- ``ClientSession.call_tool(name, arguments, read_timeout_seconds=…)`` 支持**逐次超时**；
- ``ClientSession.list_tools()`` 返回 ``ListToolsResult``，工具在 ``.tools`` 上。

## Known Limitations and Deferred Work

- **每次调用一个会话**：``call()`` 是**短连**（连→调→关），不做长连接复用。
  理由：后端可能随时被 supervisor 重启/换腿，长连接的失效判定与重连会立刻变成
  第二套桥（``mcp-bridge.ts`` 已经有一份自愈桥，但那是**宿主侧**的；这里再长一份
  是并行实现）。真需要性能时再评估（n=1 不预建）。
- 不实现 ``notifications/tools/list_changed`` 的**接收**：本模块只做请求/响应面。
- 不做重试：失败**如实上抛**（重试策略属调用方——聚合端点的语义是"如实告诉 AI 后端死了"）。
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from ..errors import MechaError

#: 单次调用的默认超时（秒）。可被 ``connect(..., timeout_s=…)`` 覆盖。
DEFAULT_TIMEOUT_S = 30.0


class BackendError(MechaError):
    """后端相关失败的基类（三种子类见模块 docstring）。"""


class BackendConnectError(BackendError):
    """连不上（没人听 / 握手失败）。"""


class BackendTimeoutError(BackendError):
    """连上了，但调用在超时内没有回。"""


class BackendProtocolError(BackendError):
    """回了，但不是 MCP（协议层错）。"""


@dataclass
class Backend:
    """一个后端 MCP 服务端（跨进程，绝不进程内直调）。

    ⚠ **每次 :meth:`call` 现连**（短连，见模块 Known Limitations）：后端会随 supervisor
    重启/换腿而换实例，长连接的失效判定会变成第二套自愈桥。
    """

    url: str
    server_name: str
    timeout_s: float = DEFAULT_TIMEOUT_S
    #: 最近一次 ``list_tools`` 的结果缓存（只给"没连也能报个大概"用，**不当作真值**）。
    _tools: list[dict] = field(default_factory=list, repr=False)

    @classmethod
    async def connect(cls, url: str, *, server_name: str,
                      timeout_s: float = DEFAULT_TIMEOUT_S) -> "Backend":
        """建一个后端句柄并**立刻验一次连通**（拉一次工具表）。

        ⚠ 为什么要立刻拉：``attach`` 时就得知道"这个后端到底有没有工具"——
        否则聚合端点会先宣告成功、等 AI 调的时候才发现连不上（那是静默坏事的形状）。
        """
        me = cls(url=url, server_name=server_name, timeout_s=timeout_s)
        me._tools = await me._fetch_tools()
        return me

    # ---- 内部：一次短连 ----
    async def _with_session(self, fn: Any) -> Any:
        """开一个短会话跑 ``fn(session)``，把 SDK 异常**翻译成三种可读失败**。"""
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as exc:                      # pragma: no cover - 依赖缺失
            raise BackendConnectError(
                f"MCP 客户端不可用（{exc}）",
                kind="mcp_client_missing",
                hint="装可选依赖：pip install 'mecha[mcp]'",
            ) from exc

        try:
            async with AsyncExitStack() as stack:
                streams = await stack.enter_async_context(
                    streamable_http_client(self.url))
                read, write = streams[0], streams[1]
                session = await stack.enter_async_context(
                    ClientSession(read, write, read_timeout_seconds=self.timeout_s))
                await session.initialize()
                try:
                    return await fn(session)
                except asyncio.TimeoutError as exc:
                    raise BackendTimeoutError(
                        f"调用后端 {self.server_name}（{self.url}）超时"
                        f"（>{self.timeout_s:.0f}s）",
                        kind="backend_timeout",
                        hint="后端可能卡住了；看它的日志（supervisor 的 runtime/logs/<id>.log）",
                    ) from exc
        except (BackendError,):
            raise
        except asyncio.TimeoutError as exc:
            raise BackendTimeoutError(
                f"连后端 {self.server_name}（{self.url}）超时（>{self.timeout_s:.0f}s）",
                kind="backend_timeout",
                hint="端口在听但握手不回；看后端日志",
            ) from exc
        except Exception as exc:                        # noqa: BLE001 - 翻译成可读失败
            name = type(exc).__name__
            msg = str(exc) or name
            low = msg.lower()
            if "timeout" in low or "timed out" in low:
                raise BackendTimeoutError(
                    f"连后端 {self.server_name}（{self.url}）超时",
                    kind="backend_timeout",
                    hint="后端可能正在启动/卡住",
                ) from exc
            if any(k in low for k in ("connect", "refused", "unreachable", "resolve",
                                      "getaddrinfo", "disconnect", "closed")):
                raise BackendConnectError(
                    f"连不上后端 {self.server_name}（{self.url}）：{msg}",
                    kind="backend_unreachable",
                    hint="端口文件里的端口没人听 ⇒ 那个软件可能已经关了"
                         "（用 query_software 看当前哪些在跑）",
                ) from exc
            raise BackendProtocolError(
                f"后端 {self.server_name}（{self.url}）协议错：{name}: {msg}",
                kind="backend_protocol",
                hint="确认那个地址真的是 MCP 端点（不是监控端点/网页）",
            ) from exc

    async def _fetch_tools(self) -> list[dict]:
        async def go(session: Any) -> list[dict]:
            res = await session.list_tools()
            out: list[dict] = []
            for t in getattr(res, "tools", []) or []:
                out.append({
                    "name": str(getattr(t, "name", "")),
                    "description": str(getattr(t, "description", "") or ""),
                    "inputSchema": getattr(t, "inputSchema", None) or {},
                })
            return out
        return await self._with_session(go)

    # ---- 公开面 ----
    async def tools(self) -> list[dict]:
        """后端当前的工具表（**每次现拉**——工具表会随后端重连而变化）。"""
        self._tools = await self._fetch_tools()
        return self._tools

    async def call(self, name: str, args: dict) -> dict:
        """调后端的一个工具。返回**归一化**后的回执（不把 SDK 形状漏给上层）。"""
        async def go(session: Any) -> dict:
            res = await session.call_tool(name, args or {},
                                          read_timeout_seconds=self.timeout_s)
            return _normalize_result(res)
        return await self._with_session(go)

    async def close(self) -> None:
        """本实现是短连 ⇒ 没有常驻资源要放。留着是为了接口稳定（调用方不必判类型）。"""
        return None


def _normalize_result(res: Any) -> dict:
    """把 ``CallToolResult`` 归一成普通 dict（**不把 SDK 类型漏出去**）。

    ⚠ 保留 ``is_error``：它是"域失败"的信号，聚合端点要**原样透传**——
    吞掉它会让 AI 以为调用成功了（静默坏事）。
    """
    content = getattr(res, "content", None)
    out: dict[str, Any] = {"is_error": bool(getattr(res, "isError", False))}
    texts: list[str] = []
    if isinstance(content, list):
        for block in content:
            t = getattr(block, "text", None)
            if isinstance(t, str):
                texts.append(t)
    if texts:
        out["text"] = "\n".join(texts)
    # 结构化结果（有些后端会给）也带上，模型能直接读
    structured = getattr(res, "structuredContent", None)
    if structured is not None:
        out["structured"] = structured
    if "text" not in out and "structured" not in out:
        out["text"] = ""
    return out
