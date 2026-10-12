"""判据 ㉑：⭐ **工具表热同步**（服务端三件事）+ **经聚合端点真调一个工具**。

## 它补的是哪一类洞（都有实测现场）

2026-10-10 实测（整站起着、三家软件全 ready）：

| 现象 | 根因 |
|---|---|
| 端点只发**元工具**，软件的工具一个都没有 | ① 端点的表是 ``start()`` 那一刻的快照：``Aggregator`` 里明明有该软件的**全部**工具，**没人**对 ``MCPServer`` 调 ``add_tool/remove_tool`` |
| **连这 4 个元工具都调不动**（``AttributeError: '_LiveRegistry' object has no attribute 'execute'``） | ``launch`` 里那个活表门面只实现了 ``schemas()``/``get()``，而调用要 ``registry.execute`` |
| 声明的是 ``tools.listChanged = False`` | ② ``MCPServer(name=…, instructions=…)`` 没传 ``NotificationOptions``；不声明，客户端 SDK **静默不挂**通知处理器 |
| 发不出通知 | ③ 服务端没有"活连接"的通路（SDK 没有连接生命周期回调 ⇒ 包 ``Connection`` 的两个公开工厂） |
| 软件起来了也要**人手动动滑块**才 attach | 没有任何东西把"软件就绪"接到 ``sync_backends``（``ready`` 钩子接上即可） |

## 五条（前四条各带红证）

| # | 断言 | 红了说明什么 |
|---|---|---|
| ① | ⭐ **经聚合端点**（真 HTTP）**真调**一个工具并拿到结果 | ``_LiveRegistry`` 又缺方法了（门面只"列得出"） |
| ② | ``initialize`` 回执里 ``capabilities.tools.listChanged is True`` | 声明丢了 ⇒ 客户端会**静默忽略**我们的通知 |
| ③ | 改注册表 + ``publish_tools`` ⇒ **新会话** ``tools/list`` 立刻看到；**不调** publish 就看不到（⛔ 红证） | 表又冻在快照上了 |
| ④ | ⭐ **客户端在没发任何请求的情况下**收到 ``notifications/tools/list_changed``（⛔ 红证：关掉登记表 ⇒ 收不到） | 活连接登记/广播坏了 |
| ⑤ | 登记表**一连接一条**（同一连接多次请求仍 1 条）＋ 端点的名字集合 ≡ registry 的名字集合 | 登记泄漏；或"活表"与 registry 漂移 |

## ⚠ 外部依赖（缺即 **SKIP**，严格模式 `--require-external` 变 FAIL）

**`mcp` python SDK**（本仓的 ``mecha.providers.mcp`` 本来就要它）＋ 起一个本地 HTTP 端点。
不碰真 dsh、不碰真软件。

## Known Limitations and Deferred Work

- **不验 dsh 真收到**：那条要真起 dsh（本判据只到"通知进了这条 HTTP 连接的 SSE 流"，
  即客户端 SDK 那一侧**协议层**已收到）；真机验收另做（起整站 + 起一家软件）。
- ⚠ **包装 SDK 公开类方法**是**有意的代价**（见 `providers/mcp.py` 的 Known Limitations）：
  本判据的 ②④ 就是"SDK 一改形状就红"的那两条守卫。
- **不判"墓碑描述逐字"**：那由 `aggregate.py` 自己的判据/单测覆盖（这里只用"名字集合"对齐）。
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402

from mecha.providers.mcp import (McpEndpoint, build_mcp_server,  # noqa: E402
                                 declare_tools_list_changed)
from mecha.tools import ToolRegistry, define_tool  # noqa: E402

FAILS: list[str] = []
#: 等通知的上界（秒）：客户端已初始化、之后**一个请求都不发**，只等推送。
NOTIFY_WAIT_S = 6.0


def _check(name: str, cond: bool, extra: object = "") -> bool:
    print(f"  {'✔' if cond else '✖'} {name}" + ("" if cond else f"   ← {extra}"))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def _tool(name: str, desc: str = "x"):
    return define_tool(
        name=name, description=desc, parameters={},
        output_schema={"type": "object", "properties": {}, "required": [],
                       "additionalProperties": True},
        execute=lambda **kw: {"ok": True, "tool": name})


def _registry(*names: str) -> ToolRegistry:
    reg = ToolRegistry()
    for n in names:
        reg.register(_tool(n))
    return reg


def _tools_list_changed(tools_caps: object) -> object:
    """取 ``tools`` 能力里的那一格（⚠ SDK 的 Python 属性名是 **snake_case**
    ``list_changed``；线协议上是 camelCase ``listChanged`` ⇒ 两种都认）。"""
    for name in ("listChanged", "list_changed"):
        value = getattr(tools_caps, name, None)
        if value is not None:
            return value
    return None


def _declared_capability(server) -> object:
    """端点会声明的那份能力（与 SDK 在 initialize 时用的是**同一个**函数）。"""
    low = server._lowlevel_server                      # noqa: SLF001
    opts = low.create_initialization_options()
    return _tools_list_changed(opts.capabilities.tools)


def check_capability_declaration() -> None:
    """② 声明能力：装了 ⇒ True；不装 ⇒ False（**红证就在同一条判据里**）。"""
    reg = _registry("ping")
    plain = build_mcp_server(reg, server_name="plain", instructions="x")
    declared = build_mcp_server(reg, server_name="declared", instructions="x")
    declare_tools_list_changed(declared)
    _check("② 装了声明 ⇒ tools.listChanged is True", _declared_capability(declared) is True,
           _declared_capability(declared))
    _check("② ⛔ 红证：不装声明时它是 False（所以这条判据真的在测东西）",
           not _declared_capability(plain), _declared_capability(plain))


def _endpoint_e2e() -> None:
    """①③④⑤ 一条真 HTTP 链路跑完：真调工具、活表会变、通知真到、不泄漏。"""
    reg = _registry("a")
    endpoint = McpEndpoint(reg, server_name="hot-sync", instructions="判据用",
                           log=lambda m: print("      ·", m),
                           track_connections=True)
    port = endpoint.start()
    _check("① 端点起来了（回读端口）", isinstance(port, int) and port > 0, port)

    received: list[str] = []

    def _record(msg) -> None:
        """从**协议层**的一帧里取方法名（不依赖 SDK 的"通知回调"私有口）。"""
        try:
            root = getattr(msg, "message", msg)
            method = getattr(root, "method", None) or getattr(
                getattr(root, "root", None), "method", None)
            if method:
                received.append(str(method))
        except Exception:                                  # noqa: BLE001
            pass

    async def run_client() -> None:
        import anyio
        async with streamable_http_client(endpoint.url) as (read, write):
            send, recv = anyio.create_memory_object_stream(200)

            async def tee() -> None:
                """把 SDK 的读流**转发**给会话，顺手记下每一帧的方法名。"""
                try:
                    async for item in read:
                        _record(item)
                        await send.send(item)
                except Exception:                          # noqa: BLE001
                    pass
                finally:
                    await send.aclose()

            async with anyio.create_task_group() as tg:
                tg.start_soon(tee)
                async with ClientSession(recv, write) as session:
                    res = await session.initialize()
                    cap = _tools_list_changed(res.capabilities.tools)
                    _check("② 真客户端 initialize 看到 tools.listChanged is True", cap is True, cap)
                    names = sorted(t.name for t in (await session.list_tools()).tools)
                    _check("③ 初始工具表 = 注册表里的 ['a']", names == ["a"], names)

                    # --- ① 经端点真调一个工具 ---
                    called = await session.call_tool("a", {})
                    text = "".join(getattr(c, "text", "") or "" for c in (called.content or []))
                    _check("① ⭐ 经端点**真调**一个工具并拿到结果", '"ok"' in text and "a" in text,
                           text[:160])

                    # --- ③ 活表：改注册表 + publish ⇒ 新表 ---
                    reg.register(_tool("b"))
                    changed = endpoint.publish_tools(reg)
                    _check("③ publish_tools 报告「变了」", changed is True, changed)
                    names2 = sorted(t.name for t in (await session.list_tools()).tools)
                    _check("③ ⭐ 同一个客户端再列 ⇒ 看得到新工具 b", names2 == ["a", "b"], names2)

                    # --- ⑤ 不变量（⚠ 要在"故意造不一致"的红证**之前**断言）---
                    _check("⑤ 端点的名字集合 ≡ registry 的名字集合",
                           set(endpoint._declared) == {s["name"] for s in reg.schemas()},  # noqa: SLF001
                           sorted(endpoint._declared))                                     # noqa: SLF001
                    _check("⑤ 登记表**一连接一条**（多次请求不膨胀）",
                           endpoint.connection_count() == 1, endpoint.connection_count())

                    # --- ① 经端点真调**对账后新注册**的那个工具 ---
                    #: ⚠ 这条是补的：原先只调"建表时就注册"的那一个（`a`），于是
                    #: **对账路径自己的 bug** 漏掉了——实测抓到的真 bug：
                    #: 对账时把 `toolhost=None` 传了下去 ⇒ 新注册（含**墓碑**）的 handler
                    #: 一调就 `AttributeError: 'NoneType' object has no attribute 'call'`
                    #: ⇒ 被 SDK 顶成通用 `Error executing tool X`（墓碑该带的"怎么重开"全丢）。
                    called_b = await session.call_tool("b", {})
                    text_b = "".join(getattr(c, "text", "") or ""
                                     for c in (called_b.content or []))
                    _check("① ⭐ 对账**新注册**的工具也调得动（b）",
                           called_b.is_error is False and '"ok"' in text_b, text_b[:160])

                    # --- ④ 通知：客户端**一个请求都没发**，只等推送 ---
                    deadline = time.monotonic() + NOTIFY_WAIT_S
                    while time.monotonic() < deadline:
                        if "notifications/tools/list_changed" in received:
                            break
                        await asyncio.sleep(0.2)
                    _check("④ ⭐ 客户端在**没发任何请求**的情况下收到 tools/list_changed",
                           "notifications/tools/list_changed" in received, received[-5:])

                    # --- ③ 红证：只改注册表、**不 publish** ⇒ 表不动 ---
                    reg.register(_tool("c"))
                    names3 = sorted(t.name for t in (await session.list_tools()).tools)
                    _check("③ ⛔ 红证：不调 publish 就看不到 c（表不会自己变）",
                           names3 == ["a", "b"], names3)

                    # --- ④ 红证：关掉登记表 ⇒ 变更不再广播 ---
                    marker = len([m for m in received if m.endswith("list_changed")])
                    endpoint._tracker = None                                            # noqa: SLF001
                    endpoint.publish_tools(reg)
                    await asyncio.sleep(min(2.0, NOTIFY_WAIT_S / 3))
                    now = len([m for m in received if m.endswith("list_changed")])
                    _check("④ ⛔ 红证：没有登记表就不再广播（那才是它在起作用）",
                           now == marker, f"{marker} → {now}")

    try:
        asyncio.run(run_client())
    finally:
        endpoint.stop()


def check_reattach_predicate() -> None:
    """⑥ **重挂的判据必须是"状态"而不是"在不在表里"**（真机抓到的第二个真 bug）。

    现场：GP 停掉再起来、进程 ``ready``、可它的工具**全是墓碑**。
    根因：``AggregateRuntime.sync_backends`` 用 ``svc_id not in agg.attached()`` 判"要不要挂"，
    而 ``attached()`` 按墓碑设计**含已关闭的后端**（它自己的 docstring 就写着"想判它还活着
    吗不要用这个方法，用 ``states()``"）⇒ detach 过之后**永远不再 attach**。

    ⚠ 这里做**源码级反向断言**（离线的行为级判据在真机探针里）：本工程已有同型守卫
    （"不许出现 X"的扫描），代价是它只挡"改回老写法"，不挡"换个法子写错"。
    """
    src = (ROOT / "mecha" / "aggregate_runtime.py").read_text(encoding="utf-8")
    body = src.split("def sync_backends", 1)[-1].split("\n    def ", 1)[0]
    _check("⑥ 重挂判据用 states()（读到聚合端点这边的真实状态）", "states()" in body, body[:120])
    _check("⑥ ⛔ 红证：不许再用 attached() 判「要不要重挂」（它含墓碑）",
           "not in self.agg.attached()" not in body, "sync_backends 里又出现了 attached()")


def _failing_tool():
    """一个**域失败**的判据工具（抛 MechaError ⇒ 框架发 `is_error=True` 的回执）。"""
    from mecha.errors import MechaError

    def _boom(**kw):
        raise MechaError("判据用的域失败", kind="toy_rejected", hint="先读后写")

    return define_tool(name="boom", description="必失败的判据工具", parameters={},
                       output_schema={"type": "object", "required": ["ok"]},
                       execute=_boom)


def check_failure_reaches_client_as_failure() -> None:
    """① 经**真链路**：后端的域失败 ⇒ 客户端拿到 `is_error=True` + 可解析的 `kind`。

    ⚠ 由来（2026-10-12 实测抓到）：``mcp_client._normalize_result`` 只读驼峰 ``isError``，
    而 mcp 2.2 的字段**真名是 snake_case ``is_error``** ⇒ 该位**恒为假** ⇒ 后端的域失败
    在聚合面上**全被读成成功**（R11"观测量必须能变"的教科书形态；本仓为同一个坑栽第三次）。

    ⚠⚠ **为什么原先漏掉**：失败透传只用**假 Backend** 测过（替身直接回 dict，**绕过**
    ``_normalize_result`` 这一层）⇒ 真链路上那一句从没被执行到。
    ⇒ **判据不许用替身测真路**（本工程最贵的一课）。
    """
    from mecha.providers.mcp_client import Backend
    reg = ToolRegistry()
    reg.register(_failing_tool())
    endpoint = McpEndpoint(reg, server_name="failure-path", instructions="判据用")
    endpoint.start()
    try:
        backend = asyncio.run(Backend.connect(endpoint.url, server_name="failure-path"))
        res = asyncio.run(backend.call("boom", {}))
        _check("① ⭐ 域失败经真链路 ⇒ 客户端看到 `is_error=True`（不是成功）",
               res.get("is_error") is True, res)
        try:
            body = json.loads(str(res.get("text", "")))
        except ValueError:
            body = {}
        info = ((body.get("error") or {}).get("info") or {}) if isinstance(body, dict) else {}
        _check("① 正文可解析出 `info.kind`（AI 据此决定怎么改，而不是读散文）",
               info.get("kind") == "toy_rejected", body)
    finally:
        endpoint.stop()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    #: ⚠ 压掉 SDK 自己的 INFO 日志（它每次 HTTP 都打一行）——判据的输出要能一眼看完。
    import logging
    logging.getLogger("mcp").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    print("[hot_sync] 工具表热同步：声明能力 / 活表会变 / 真收到通知 / 经端点真调")
    try:
        check_capability_declaration()
        check_reattach_predicate()
    except Exception as exc:                               # noqa: BLE001
        _check("② 能力声明那一档跑起来了", False, f"{type(exc).__name__}: {exc}")
    try:
        _endpoint_e2e()
    except ImportError as exc:
        print(f"SKIP: 缺依赖 ⇒ {exc}")
        return 1 if FAILS else 0
    except Exception as exc:                               # noqa: BLE001
        import traceback
        traceback.print_exc()
        _check("① 端点端到端那一档跑起来了", False, f"{type(exc).__name__}: {exc}")
    try:
        check_failure_reaches_client_as_failure()
    except Exception as exc:                               # noqa: BLE001
        _check("① 失败透传那一档跑起来了", False, f"{type(exc).__name__}: {exc}")

    if FAILS:
        print(f"aggregate_tools_hot_sync_selfcheck: FAIL（{len(FAILS)} 条）：{FAILS}")
        return 1
    print("aggregate_tools_hot_sync_selfcheck: PASS"
          "（声明 + 活表 + 通知 + 真调 + 失败透传 + 三条红证）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
