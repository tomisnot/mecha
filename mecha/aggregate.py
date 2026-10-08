"""聚合端点：把 N 个后端（各自的软件脊椎）合成**一个** MCP 服务端，给 dsh 挂。

## 它解决什么

dsh 只挂**一个** MCP 端点（见设计文档 §7.2）。而每个软件各有一条脊椎（自己的 MCP 服务端）
⇒ 需要一个"把 N 个合成 1 个"的层，且**工具表要能跟着后端的生死变**。

## 工具名（⚠ 与设计文档草稿有出入，我按**框架自己的守卫**落了）

- 后端工具 ⇒ ``<svc_id>_<tool>``（**小写、单下划线**）；
- 元工具 ⇒ ``query_software`` / ``open_software`` / ``close_software``。

⚠ 为什么不是草稿里的 ``<svc_id>__<tool>`` 与 ``mecha_list_software``：本仓的
``tools.check_tool_name`` **机械拒绝**那两种写法——
``mecha_`` 是**库自身包名**（明令禁止写进模型可见文本）、``list_`` 是**明令禁止的枚举式动词**
（官方改名惯例 ``list_*`` ⇒ ``query_*``），而 ``name__tool`` 的双下划线**不匹配**命名律
（``^[a-z][a-z0-9]*(_[a-z0-9]+)*$``，``ML__x`` 既大写又连续下划线）。
⇒ 为了让名字能落地，我**没有**放宽守卫（那是"改判据让它变绿"），而是把名字改成合规的。
⚠ 这是**文档与代码不符**：请以本模块为准回改设计文档，或告诉我改回你的名字（那我需要你批准放宽守卫）。

## 掉线的语义（**响亮失败**，绝不静默）

后端被 ``detach``（或它自己死了）之后：
- 它的工具**不再出现在工具表**里；
- 但 AI 可能还拿着**旧名字**（上一轮工具表里的）⇒ 那时 ``call`` **必须响亮失败**，
  文案里带**是谁关了** + **怎么重开**（``open_software``）。
⇒ 只从列表里删掉、却仍然把调用转发过去 = **假绿**（判据 2 就是照"怎么故意退化还能绿"设计的）。

## 一个后端挂了不拖垮整站

``attach`` 时后端连不上 ⇒ 那个后端标 ``unreachable``，**其余照常**（``build_server`` 不抛）。
判据 3 钉这条：把"连不上"做成整体失败是很容易的偷懒，而后果是**一个软件没起来 ⇒ 全部不可用**。

## Known Limitations and Deferred Work

- ⚠ **发不出 ``notifications/tools/list_changed``**（**实测证据**，mcp 2.2.0）：
  - 本框架用的是 ``mcp.server.mcpserver.server.MCPServer``，
    它**只有** ``add_tool`` / ``remove_tool`` / ``list_tools`` / ``call_tool`` / ``tool``
    ——**没有任何服务端主动发通知的公开面**；
  - ``mcp.server.lowlevel.Server`` 上 ``send*`` / ``changed*`` 成员为**空**
    （只有 ``add_notification_handler`` 那种"**收进来**"的方向）。
  ⇒ **不是"名字没找对"**，是这版 SDK 没给这个面。本轮的**主路径是元工具** ``query_software``
  （AI 自己看当前有哪些软件）；**通知如实登记为"想发但发不出"，不假装发了**。
  **2b 再谈**要不要绕到 lowlevel 自己发（那是另一套会话/传输管理，不在本轮）。
  **重新考虑条件**：dsh 确实监听该通知（``mcp-client/src/connection.ts:263-268`` 的
  ``listChanged.tools.onChanged → refreshTools()``）⇒ 一旦这版 SDK 给出服务端通知面，
  就该接上。
- ⭐ **``MCPServer`` 有 ``add_tool`` / ``remove_tool``（能在活着的服务端上增减工具）**：
  这是设计文档 §6.1「**不需要 ProxyMCP 那类兜底工具**」的**实现层依据**——
  架构当初就是按"**工具表能动态改**"设计的。⚠ 但本轮 ``build_server`` 走的是
  ``providers/mcp.build_mcp_server``（**纯构造、快照当前工具表**）⇒ attach/detach 后**重建**；
  不增量（增量要维护两套状态，且**要能发通知才看得见**，当前无收益）。
- **不做 ProxyMCP 那类兜底工具**（设计文档 §6.1 已否决）：dsh 支持工具表热同步，
  兜底会让"表过期"变成一个**可容忍**的状态，反而掩盖问题。
- ``call`` 是**短连**（每次连→调→关），见 ``mcp_client`` 的 Known Limitations。
- **``_compose`` 只做小写化**（``<svc_id.lower()>_<tool>``）：所以**两个只差大小写的 id
  会撞名**（``ML`` 与 ``ml``）。⚠ **主防线在声明期**（``site.Site.validate`` 的
  大小写不敏感查重）——那里报错更早、信息更全；这里不再重复一遍。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum

from .errors import MechaError
from .tools import ToolRegistry, define_tool
from .providers.mcp_client import Backend, BackendError, BackendTimeoutError

#: 元工具名（**合规名**，见模块 docstring）。
META_LIST = "query_software"
META_OPEN = "open_software"
META_CLOSE = "close_software"
META_TOOLS = (META_LIST, META_OPEN, META_CLOSE, "read_write_authority")


class SvcState(str, Enum):
    """后端在聚合端点眼里的状态（与 supervisor 的进程状态**不是同一件事**）。"""

    UP = "up"                 # 已 attach 且最近一次工具表拉取成功
    DOWN = "down"             # 已被 detach（或它自己连不上）
    UNREACHABLE = "unreachable"   # attach 时就没连上


@dataclass
class _Attached:
    svc_id: str
    backend: Backend
    tools: list[dict] = field(default_factory=list)
    state: SvcState = SvcState.UP
    why: str = ""


def _compose(svc_id: str, tool: str) -> str:
    """``<svc_id>_<tool>``（**小写**；命名律只收小写 + 单下划线）。"""
    return f"{svc_id.lower()}_{tool}"


class Aggregator:
    """把多个后端合成一个 MCP 服务端（工具表可随 attach/detach 变）。

    ``state_fn``（可选）：拿"当前进程事实"的注入点（通常是 ``supervisor.state()``）——
    元工具 ``query_software`` 用它报**进程级**事实（谁在跑），而聚合端点自己只管
    "谁挂着、有几个工具"。**两者不是同一件事**，所以分开报（否则会把"挂上了"
    误报成"进程活着"）。
    """

    def __init__(self, *, server_name: str = "mecha",
                 state_fn: Callable[[], Mapping[str, object]] | None = None,
                 open_fn: Callable[[str], object] | None = None,
                 close_fn: Callable[[str], object] | None = None) -> None:
        self.server_name = server_name
        self._state_fn = state_fn
        self._open_fn = open_fn
        self._close_fn = close_fn
        self._backends: dict[str, _Attached] = {}
        #: "**已知软件**"（站点声明里全部 svc id，含**还没挂上**的）。
        #: ⚠ 与 ``_backends``（挂过的）**不是一回事**：元工具要能区分
        #: "存在但没跑"与"压根没有这个软件"。构造时为空，由 :meth:`wire` 或
        #: ``attach`` 填。
        self._known: set[str] = set()
        #: 站点声明（``Service`` 对象）：写权聚合要从中取 ``how_to_open``。
        #: ⚠ 只读它，**不解释**它（框架不许把项目文案改写/写死）。
        self._specs: dict[str, object] = {}
        #: ⭐ 写权探针（由 ``AggregateRuntime`` 注入）：``spec -> dict | None``。
        #: 返回 ``None`` ⇒ **这个软件不在这条路上**（没起来/没挂）⇒ 不报它（铁律 8）。
        self._authority_probe: Callable[[object], dict | None] | None = None
        self._lock = None       # 惰性建（避免 import 期建锁）

    def wire(self, *, state_fn: Callable[[], Mapping[str, object]],
             open_fn: Callable[[str], object],
             close_fn: Callable[[str], object],
             known: Sequence[str] = (),
             specs: Sequence[object] = (),
             authority_probe: Callable[[object], dict | None] | None = None) -> None:
        """把回调与"已知软件清单"**接上**（可在构造后接）。

        ⚠ **为什么需要这个方法**（实测抓到的两个缺口）：

        1. ``AggregateRuntime`` 拿到的是**已经造好的** ``Aggregator`` 实例，
           而这三个回调原先**只有构造参数**一条路 ⇒ runtime 没法接，
           元工具里 ``query_software`` 的 ``process`` 栏就永远是 ``None``
           —— 现象是"**跑着的软件被报成没有进程事实**"，**违反铁律 7**
           （元工具必须从 ``supervisor.state()`` 取唯一真值，不能自己记账）。
        2. ``known``（**站点声明里全部** svc id，含**还没挂上**的）也必须接：
           元工具要能回答"**这个软件存在但没跑**"与"**压根没有这个软件**"的**区别**，
           只看 ``_backends`` 会把前者误报成后者。
           ⚠ 少了它，``open_software`` 会抛 ``AttributeError``（被包成 internal_error）
           —— 这**恰好就是"不响亮"的反例**：用户看到的是内部错误，不是"没有这个软件"。

        ⚠ 接线**不许改变已挂后端的语义**：这里只换回调/清单，不碰 ``_backends``。
        """
        self._state_fn = state_fn
        self._open_fn = open_fn
        self._close_fn = close_fn
        self._known = set(known)
        # ⭐ 站点声明与写权探针（契约 §7.1.1：聚合那个 `read_write_authority` 要用）
        self._specs = {str(getattr(s, "id", "")): s for s in specs}
        self._authority_probe = authority_probe

    # ---- 挂 / 摘 ----
    def attach(self, svc_id: str, backend: Backend) -> None:
        """挂一个后端。⚠ **连不上不抛**：标 ``unreachable``，其余后端照常。"""
        entry = _Attached(svc_id=svc_id, backend=backend)
        try:
            entry.tools = _run(backend.tools())
            entry.state = SvcState.UP
        except BackendError as exc:
            entry.tools = []
            entry.state = SvcState.UNREACHABLE
            entry.why = exc.message
        self._backends[svc_id] = entry

    def detach(self, svc_id: str) -> None:
        """摘一个后端（或标记它已关）。**id 未知 ⇒ 响亮失败**（不静默成功）。

        ⚠ **保留工具表**（不 ``clear``）：工具名要留着做**墓碑**（见 :meth:`registry`），
        否则 AI 拿旧名字来调只会看到通用的"未知工具"，而不是"它关了、这么重开"。
        真正不转发由 ``state is not UP`` 保证（:meth:`_call_backend`）。
        """
        entry = self._backends.get(svc_id)
        if entry is None:
            raise MechaError(
                f"聚合端点没有挂过这个后端：{svc_id!r}",
                kind="unknown_backend",
                hint=f"已挂：{sorted(self._backends)}",
            )
        entry.state = SvcState.DOWN
        entry.why = entry.why or "已被关闭"

    def attached(self) -> tuple[str, ...]:
        """挂过的后端 id（⚠ **含已关闭的**：墓碑要留在工具表里，见 :meth:`registry`）。

        ⇒ 想判"它还活着吗"**不要**用这个方法，用 :meth:`states`。
        """
        return tuple(sorted(self._backends))

    def states(self) -> dict[str, str]:
        """``svc_id → 状态``（``up`` / ``down`` / ``unreachable``）。

        ⚠ **为什么单独给一个读口**（实测抓到的判据矛盾）：
        ``detach`` 按墓碑设计**故意保留**后端 ⇒ 判"关掉了没有"时若去看
        ``attached()`` 会**永远为假**（判据与墓碑设计打架）。
        要判"聚合端点这边的视角"就得读**状态**，不是读"在不在表里"。
        """
        return {k: str(getattr(v.state, "value", v.state))
                for k, v in sorted(self._backends.items())}

    # ---- 工具面 ----
    def registry(self) -> ToolRegistry:
        """按**当前**后端集合构造工具表（每次现建 ⇒ attach/detach 立刻反映）。

        ⚠ **掉线的后端要留"响亮的墓碑"**：它的工具不再**可用**，但名字要在表里——
        否则 AI 拿着上一轮的旧名字来调，只会得到一句**通用的"未知工具"**
        （实测：``{'message': "未知工具 'ml_train_model'"}``），既不知道是谁关了、
        也不知道怎么重开。⇒ 掉线时把工具名**保住但换成会抛错的实现**。
        这正是判据 ② 逼出来的：只从表里删掉，读到的是"没有这个工具"，而不是"它关了"。
        """
        reg = ToolRegistry()
        for svc_id in sorted(self._backends):
            entry = self._backends[svc_id]
            for t in entry.tools:
                raw = str(t.get("name") or "")
                if not raw:
                    continue
                if entry.state is SvcState.UP:
                    reg.register(self._backend_tool(svc_id, raw, t))
                else:
                    reg.register(self._tombstone(svc_id, raw, t))
        for meta in self._meta_tools():
            reg.register(meta)
        # ⭐ 写权自省（**只读**）挂**聚合面**：契约定案"**一个聚合工具**，不是每个软件一个"
        #   （AI 经过聚合端点 ⇒ 聚合这个才是它实际会用的那一个）。
        # ⚠ 为什么不用 ``authority.register_authority_read``：那个函数把
        #   ``authority_view(Authority)``（**单份**写权）包成一个工具；聚合这里是
        #   "**多个软件各一份**、一次全报"，形状不同（``{ok, services[], how_to_open}``）。
        #   ⇒ 两者共用的是**同一份探侧实现**（``authority_view``，在探针里调），
        #   而不是同一个注册函数。
        reg.register(self._authority_tool())
        return reg

    def _tombstone(self, svc_id: str, raw: str, t: Mapping[str, object]):
        """掉线后端留下的**响亮墓碑**：名字还在，调它就抛（带"谁关了"+"怎么重开"）。

        ⚠ **为什么不留"空位"而是留"墓碑"**：AI 手里可能还拿着上一轮工具表里的旧名字。
        若从这里把名字删掉，它调过来只会得到一句通用的「未知工具」——
        既不知道是谁关了、也不知道怎么重开，而 §需求 2 要的正是
        "**没看到**"与"**没发生**"**不能长得一样**（铁律 8 的同一条纪律）。
        """
        props = t.get("inputSchema") or {}
        props = dict(props) if isinstance(props, Mapping) else {}
        params = props.get("properties")
        params = dict(params) if isinstance(params, Mapping) else {}
        return define_tool(
            name=_compose(svc_id, raw),
            description=f"（{svc_id} 已关闭）{t.get('description') or raw}",
            parameters=params,
            output_schema={"type": "object", "properties": {},
                           "required": [], "additionalProperties": True},
            execute=lambda **kw: _raise_backend_down(svc_id, raw),
        )

    def _backend_tool(self, svc_id: str, raw: str, t: Mapping[str, object]):
        name = _compose(svc_id, raw)
        props = t.get("inputSchema") or {}
        props = dict(props) if isinstance(props, Mapping) else {}
        params = props.get("properties")
        params = dict(params) if isinstance(params, Mapping) else {}
        return define_tool(
            name=name,
            description=str(t.get("description") or f"{svc_id} 的 {raw}"),
            parameters=params,
            output_schema={"type": "object", "properties": {},
                           "required": [], "additionalProperties": True},
            execute=lambda **kw: self._call_backend(svc_id, raw, dict(kw)),
        )

    def _call_backend(self, svc_id: str, raw: str, args: dict) -> Mapping[str, object]:
        """转发一次调用。⚠ **掉线时响亮失败**（带"谁关了" + "怎么重开"）。"""
        entry = self._backends.get(svc_id)
        if entry is None or entry.state is not SvcState.UP:
            _raise_backend_down(svc_id, raw, entry.why if entry is not None else "")
        try:
            return _run(entry.backend.call(raw, args))
        except BackendTimeoutError as exc:
            raise MechaError(
                f"软件 {svc_id} 的 {raw} 调用超时：{exc.message}。"
                f"它可能卡住了——{META_LIST}() 看状态，必要时 {META_CLOSE}('{svc_id}') 后重开。",
                kind="backend_timeout",
                hint="超时**不等于**软件关了；先看它的日志再决定要不要重开",
            ) from exc
        except BackendError as exc:
            # ⚠ 后端"用着用着死了"：把它标成 DOWN（下次调用立刻响亮失败，不用再等超时）
            entry.state = SvcState.DOWN
            entry.why = exc.message
            raise MechaError(
                f"软件 {svc_id} 已关闭（{exc.message}）⇒ 不能调用 {raw}。"
                f"用 {META_OPEN}('{svc_id}') 重开它。",
                kind="backend_down",
                hint=f"{META_LIST}() 看当前哪些软件在跑",
            ) from exc

    # ---- 元工具 ----
    def authority_view_of_all(self) -> dict:
        """⭐ **一次报全部在跑的软件**的写权（AI 面那个聚合工具的实现）。

        ## 四条要点（契约 §7.1.1「AI 面怎么拿到它」定案）

        1. ⭐ **不缓存**：每次调用**真的**去问每个在跑的软件。缓存会引入"失效策略"
           这个**新的复杂度面**，换来的只是省一次本机往返（loopback 很便宜）
           ⇒ 判据 ④ 断言"连调两次 ⇒ 底层真的被调了两次"。
        2. ``how_to_open`` 由**项目声明**给（``Service.how_to_open``）——
           框架**不写死**任何项目名/路径。
        3. ⚠ **只报在跑的软件**：没起来的**不编造**（"读不到" ≠ "确实没有"，铁律 8）。
        4. ⚠ **某一个读不到不许让整个调用失败**：那一个报 ``unreadable`` + 具体原因，
           其余照常（判据 ②③）。
        """
        services: list[dict] = []
        how_to_open = ""
        for sid in sorted(self._backends):
            spec = self._specs.get(sid)
            if spec is None:
                continue
            if spec.how_to_open and not how_to_open:      # type: ignore[attr-defined]
                how_to_open = spec.how_to_open            # 声明里的原话，框架不改写
            probe = self._authority_probe
            if probe is None:
                services.append({"id": sid, "unreadable": "本进程没有接控制客户端"})
                continue
            one = probe(spec)
            if one is None:
                continue        # ⚠ 不在这条路上（没起来/没挂）⇒ **不报它**
            row = {"id": sid}
            row.update(one)
            services.append(row)
        return {"ok": True, "services": services, "how_to_open": how_to_open}

    def _authority_tool(self):
        """聚合面的 ``read_write_authority()``（**只读**，一次报全部在跑的软件）。"""
        return define_tool(
            name="read_write_authority",
            description=("读**当前写权**：现在哪些软件在跑、各自归谁写、以及怎么切到人工。"
                         "⚠ 只读自省（不改任何东西）——写被拒之后先调它，别盲撞。"),
            parameters={},
            output_schema={"type": "object",
                           "properties": {"ok": {"type": "boolean"},
                                          "services": {"type": "array"},
                                          "how_to_open": {"type": "string"}},
                           "required": ["ok", "services"],
                           "additionalProperties": False},
            execute=lambda **_kw: self.authority_view_of_all(),
        )

    def _meta_tools(self) -> Sequence[object]:
        def software_facts() -> dict:
            """**事实**：谁挂着（聚合端点视角） + 谁在跑（进程视角，若注入了 state_fn）。"""
            proc: dict[str, object] = {}
            if self._state_fn is not None:
                for row in self._state_fn().get("children", []) or []:   # type: ignore[union-attr]
                    proc[str(row.get("id"))] = {
                        "state": row.get("state"), "port": row.get("port"),
                        "holds_write_lease": row.get("holds_write_lease"),
                    }
            out = []
            for svc_id in sorted(self._backends):
                e = self._backends[svc_id]
                out.append({
                    "id": svc_id,
                    "aggregate": e.state.value,
                    "tools": len(e.tools),
                    "why": e.why,
                    "process": proc.get(svc_id),
                })
            return {"software": out}

        def cmd(which: str, label: str, fn: Callable[[str], object] | None):
            def run(svc_id: str) -> dict:
                # ⚠ **先判"有没有这个软件"、再判"有没有接线"**：反过来的话，
                #   对一个**压根不存在**的 id 会回"没接线"——那是**答非所问**
                #   （判据 ⑥ 逼出来的：不存在就必须说"不存在"）。
                known = set(self._backends) | set(self._known)
                if svc_id not in known:
                    raise MechaError(
                        f"没有这个软件：{svc_id!r}",
                        kind="unknown_service",
                        hint=f"已知软件：{sorted(known)}"
                             f"（聚合端点只认挂过的；没挂过的要看站点声明）",
                    )
                if fn is None:
                    raise MechaError(
                        f"{label} 没有接线（本进程没给它 open/close 回调）",
                        kind="meta_not_wired",
                        hint="进程生死归 supervisor：把它接到 supervisor.start/stop 那一层",
                    )
                fn(svc_id)
                return {"ok": True, "id": svc_id, "action": which}
            return run

        return (
            define_tool(
                name=META_LIST,
                description="列出当前有哪些软件、各自挂上了几个工具、以及进程是否在跑。",
                parameters={}, output_schema={"type": "object", "properties": {},
                                              "required": [], "additionalProperties": True},
                execute=lambda **kw: software_facts(),
            ),
            define_tool(
                name=META_OPEN,
                description="把某个软件重新打开（重开它那条脊椎，工具表随之恢复）。",
                parameters={"id": {"type": "string", "description": "软件 id，如 GP"}},
                output_schema={"type": "object", "properties": {}, "required": [],
                               "additionalProperties": True},
                execute=lambda **kw: cmd("open", META_OPEN, self._open_fn)(str(kw.get("id", ""))),
            ),
            define_tool(
                name=META_CLOSE,
                description="关掉某个软件（它的工具随即从工具表消失）。",
                parameters={"id": {"type": "string", "description": "软件 id，如 GP"}},
                output_schema={"type": "object", "properties": {}, "required": [],
                               "additionalProperties": True},
                execute=lambda **kw: cmd("close", META_CLOSE, self._close_fn)(str(kw.get("id", ""))),
            ),
        )


def _raise_backend_down(svc_id: str, raw: str, why: str = "") -> "NoReturn":  # type: ignore[name-defined]
    """统一的"该后端已关闭"错误（**一处措辞**：墓碑与转发都用它）。"""
    raise MechaError(
        f"软件 {svc_id} 已关闭（{why or '已被关闭'}）⇒ 不能调用 {raw}。"
        f"用 {META_OPEN}('{svc_id}') 重开它。",
        kind="backend_down",
        hint=f"调 {META_LIST}() 看当前哪些软件在跑；或用 {META_OPEN}('{svc_id}') 重开",
    )


def _run(coro: object) -> object:
    """跑一个协程（这里的调用点都在**同步**的 execute 里）。

    ⚠ 用**自己的事件循环**而不是 ``asyncio.run``：``asyncio.run`` 在"已经有一个在跑的
    循环"的线程里会抛（聚合端点可能被嵌在异步宿主里）。``new_event_loop`` + 显式关闭
    对两种情况都成立。
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)      # type: ignore[arg-type]
    finally:
        loop.close()
