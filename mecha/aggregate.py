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

- ⭐ **工具表热同步已接上**（2026-10-10，用户裁决的方案 B）——原先这里是"**发不出
  ``notifications/tools/list_changed``**"（那版结论只对了一半：SDK 的高层 ``MCPServer``
  确实没有服务端通知面，但 ``Connection.send_tool_list_changed()`` 是有的）。现在三件事都做了：
  ① **表真的会变**（``McpEndpoint.publish_tools`` 对账）② **声明能力**
  （``declare_tools_list_changed``）③ **服务端主动广播**（``notify_tools_changed``，
  给每个活连接发，**不在任何工具调用里顺手发**）。
  ⚠ 代价与守卫见 ``providers/mcp.py`` 的 Known Limitations 与判据
  ``checks/aggregate_tools_hot_sync_selfcheck.py``（SDK 一改形状就红）。
  ⚠ 仍是 **best-effort**：没有独立 SSE 通道的连接（2026-era 单请求信封/无状态）
  **天生收不到推送**；那一档我们**INFO 记一笔**，不假装发了。
- ⚠ **"要不要重新挂"必须读 :meth:`states`，不许读 :meth:`attached`**（真机实测抓到的真 bug）：
  ``attached`` 按墓碑设计**含已关闭的后端** ⇒ 拿它判就会"软件重启后工具永远回不到可用"
  （现象：进程 ready、工具全是墓碑）。⇒ ``AggregateRuntime.sync_backends`` 用 ``states()``；
  判据 ``checks/aggregate_tools_hot_sync_selfcheck.py`` ⑥ 钉住这条反向判断。
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
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum

from .errors import MechaError
from .tools import ToolRegistry, define_tool
from .providers.mcp_client import (Backend, BackendConnectError, BackendError,
                                   BackendTimeoutError)

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


def _as_full_schema(sch: Mapping[str, object]) -> dict:
    """把后端工具声明的 ``inputSchema`` 规整成 ``define_tool(parameters=…)`` 要的**整份 schema**。

    ⚠⚠ **这是修一个静默回归**（实测两端对照，2026-10-10）：
    旧代码只传 ``sch["properties"]``（**裸参数表**），而 ``parameters=`` 的约定是
    **整份 JSON schema**（见 ``ml_mecha/core_commands.py:32``）⇒ 发出时再去里面找
    ``.properties`` 就**找不到** ⇒ 聚合端点把工具声明成"**无参**"。
    （同一个 bug 还有第二个独立成因：``providers/mcp_client.py`` 读驼峰
    ``inputSchema``，而 mcp SDK 2.2.0 的字段是 snake_case ``input_schema`` ⇒ 取到 ``{}``。）

    ⚠ 这里**两份都收**：既有"整份 schema"（后端 SDK 客户端修好后就是这个），
    也有"裸参数表"（旧路径/元工具那种写法）——两种都要能落到合法 schema 上。
    **不要**反过来去猜后端用哪种；收到什么就按什么规整。
    """
    if not isinstance(sch, Mapping):
        return {"type": "object", "properties": {}, "required": [],
                "additionalProperties": True}
    #: 已经是整份 schema（有 properties / type）⇒ 原样用
    if "properties" in sch or "type" in sch:
        out = dict(sch)
    #: 裸参数表 ⇒ 补成合法 schema（不丢参数）
    else:
        out = {"type": "object", "properties": dict(sch)}
    out.setdefault("type", "object")
    out.setdefault("properties", {})
    return out


def _backend_params(t: Mapping[str, object]) -> dict:
    """后端工具 → ``define_tool(parameters=…)`` 要的**裸参数表**（``{参数名: 声明}``）。

    ⚠⚠ **为什么必须是"裸的"**（2026-10-10 真机追出来的第二次套娃）：
    ``ToolDefinition.parameters`` 的契约是**裸参数表**（见 ``tools.py`` 的字段声明与
    `ml_mecha` 里所有 ``define_tool(parameters={...})`` 的写法），而
    ``providers/mcp.py:tool_input_schema`` 是**照这个约定**从 ``schema["parameters"]``
    里逐个取参数名的。⇒ 若这里塞进的是**整份 schema**
    （``{"type": "object", "properties": {...}}``），wire 上就会变成
        ``properties = {"type": {...}, "properties": {...}}``
    —— 参数表**整体套了一层壳**，客户端看到的参数名是 ``type`` 与 ``properties``。
    实测（端点 58908）：``ml_prepare_dataset.inputSchema.properties`` =
    ``{"type": {"type": "string"}, "properties": {}}``。

    ⇒ 这里统一成"取 ``properties``"；拿不到就退回整份声明里像参数表的那一层。
    """
    raw = t.get("inputSchema") or {}
    if not isinstance(raw, Mapping):
        return {}
    props = raw.get("properties")
    if isinstance(props, Mapping):
        return dict(props)
    if "properties" in raw or "type" in raw:
        return {}                      # 是 schema 但没有参数：真的是无参工具
    return dict(raw)                   # 本来就给了裸参数表


#: ⭐ **聚合端点自己的名字**（进 dsh 的 ``mcp__<serverName>__<tool>`` 前缀）。
#:
#: ## 为什么是**框架的**名字，而不是"某个站点的名字"（2026-10-09 实测更正）
#:
#: 原实现取 ``sites[0].name``（**第一个站点**）⇒ 用户只勾了 **GP** 启动，
#: 而 dsh 里那个 MCP 客户端却叫 **`ML Toolbox`**（因为 `MECHA_SITE_ROOTS` 里 ML 排第一）
#: ⇒ 现场看起来就是"**只选了 GP，载入的却是 ML 的 mcp**"。
#: ⚠ 那个名字是**位置**的偶然产物（站点顺序），而这个端点是**框架的**聚合面：
#: 它同时代表**所有**站点 ⇒ 名字必须由**框架**给，且**与站点顺序无关**。
#: ⚠ 代价（一次性）：工具前缀从 `mcp__ML-Toolbox__*` 变成 `mcp__mecha__*` ——
#: 现在改**比发布后改**便宜（dsh 侧历史对话里记的是旧名字）。
AGGREGATE_SERVER_NAME = "mecha"


class Aggregator:
    """把多个后端合成一个 MCP 服务端（工具表可随 attach/detach 变）。

    ``state_fn``（可选）：拿"当前进程事实"的注入点（通常是 ``supervisor.state()``）——
    元工具 ``query_software`` 用它报**进程级**事实（谁在跑），而聚合端点自己只管
    "谁挂着、有几个工具"。**两者不是同一件事**，所以分开报（否则会把"挂上了"
    误报成"进程活着"）。
    """

    def __init__(self, *, server_name: str = AGGREGATE_SERVER_NAME,
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
             authority_probe: Callable[[object], dict | None] | None = None,
             resync_fn: Callable[[], object] | None = None) -> None:
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
        #: ⭐ **重挂回调**（由 ``AggregateRuntime`` 注入 ``sync_backends``）：
        #: 后端**会话级失败**（连不上/断了）时调一次，让它按**当前端口**重新 attach
        #: （attach 会造新的 ``Backend`` ⇒ 新会话 + 新 ``initialize``）。
        #: ⚠ 这正是"**要整体重建会话，而不是只重解析端口**"的落点：重建发生在 attach 层。
        self._resync_fn = resync_fn

    # ---- 挂 / 摘 ----
    def attach(self, svc_id: str, backend: Backend) -> None:
        """挂一个后端。⚠ **连不上不抛**：标 ``unreachable``，其余后端照常。

        ⚠⭐ **重挂失败时不许清掉旧工具表**（2026-10-10 探针抓到的真缺陷）：
        原先失败一律 `entry.tools = []` ⇒ 若这是**重挂**（软件重启/换端口期间的 resync），
        工具名会**凭空消失**，AI 手里的名字从"**墓碑**（带着'谁关了 + 怎么重开'）"
        退化成通用的"**未知工具**"——正是铁律 8 要防的"没看到 ≠ 没发生"。
        ⇒ 只在**从来没有过**工具表时才是空；有过就留着（当墓碑用）。
        """
        old = self._backends.get(svc_id)
        entry = _Attached(svc_id=svc_id, backend=backend)
        try:
            entry.tools = _run(backend.tools())
            entry.state = SvcState.UP
        except BackendError as exc:
            entry.tools = list(old.tools) if old is not None else []
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
                    #: ⭐ `unreachable`（大概在重启）与 `DOWN`（确实关了）**分开叙述**。
                    reg.register(self._tombstone(
                        svc_id, raw, t,
                        unreachable=entry.state is SvcState.UNREACHABLE))
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

    def _tombstone(self, svc_id: str, raw: str, t: Mapping[str, object], *,
                   unreachable: bool = False):
        """掉线后端留下的**响亮墓碑**：名字还在，调它就抛（带"谁关了"+"怎么重开"）。

        ⚠ **为什么不留"空位"而是留"墓碑"**：AI 手里可能还拿着上一轮工具表里的旧名字。
        若从这里把名字删掉，它调过来只会得到一句通用的「未知工具」——
        既不知道是谁关了、也不知道怎么重开，而 §需求 2 要的正是
        "**没看到**"与"**没发生**"**不能长得一样**（铁律 8 的同一条纪律）。
        """
        params = _backend_params(t)
        #: ⚠ 描述也分两档：`unreachable` 时**不许**写"已关闭"（那是在重启）——
        #: AI 读的就是这行描述（工具表里它最显眼）。
        reason = "连不上（可能在重启）" if unreachable else "已关闭"
        return define_tool(
            name=_compose(svc_id, raw),
            description=f"（{svc_id} {reason}）{t.get('description') or raw}",
            parameters=params,
            output_schema={"type": "object", "properties": {},
                           "required": [], "additionalProperties": True},
            execute=lambda **kw: _raise_backend_down(svc_id, raw, unreachable=unreachable),
        )

    def _backend_tool(self, svc_id: str, raw: str, t: Mapping[str, object]):
        """把一个后端工具**投影**成聚合面的工具。

        ⚠ **后端的域失败要抬成聚合面的失败**（``is_error=True``），不许当成功正文塞进
        ``value``——理由见 :func:`_raise_backend_failure`（P0-3 的判据）。
        """
        name = _compose(svc_id, raw)
        params = _backend_params(t)

        def _exec(**kw):
            result = self._call_backend(svc_id, raw, dict(kw))
            if result.get("is_error"):
                _raise_backend_failure(svc_id, raw, result)
            return result

        return define_tool(
            name=name,
            description=str(t.get("description") or f"{svc_id} 的 {raw}"),
            parameters=params,
            output_schema={"type": "object", "properties": {},
                           "required": [], "additionalProperties": True},
            execute=_exec,
        )

    def _call_backend(self, svc_id: str, raw: str, args: dict) -> Mapping[str, object]:
        """转发一次调用。⚠ **掉线时响亮失败**（带"谁关了" + "怎么重开"）。

        ## ⭐⭐ 三类失败**必须分开处置**（2026-10-10 真机复现后改准）

        | 失败 | 之前（错） | 现在 |
        |---|---|---|
        | **会话级断开**（`BackendConnectError`：连不上 / 调到一半断了 / 端口换了） | 一律当"**软件已关闭**" ⇒ 标 DOWN + 让 AI 去 `open_software` **重开**（而它其实正在被 supervisor 自动重启）| 标 **`unreachable`** + **`resync`（按当前端口重挂 = 新会话 + 新 initialize）** + **重试一次**；还不行就如实说"这一刻连不上，多半在重启" |
        | 调用超时（`BackendTimeoutError`） | 单独一档 ✅ | 不变（超时 ≠ 关闭） |
        | 真协议错（`BackendProtocolError`） | 与断开混在一起 | 保持"标 DOWN + 说清"（那是真有问题，不是重启） |

        ⚠ **为什么不能一律标 DOWN**：supervisor 的重启策略会让软件自己回来（可能**换个端口**），
        标 DOWN 等于把"**正在重启**"报成"**已关闭**"——AI 会去调 `open_software` 重开一个
        正在重启的东西，而**真相是它马上就会自己好**。
        """
        entry = self._backends.get(svc_id)
        if entry is None or entry.state is not SvcState.UP:
            _raise_backend_down(
                svc_id, raw, entry.why if entry is not None else "",
                unreachable=(entry is not None and entry.state is SvcState.UNREACHABLE))
        try:
            return _run(entry.backend.call(raw, args))
        except BackendTimeoutError as exc:
            raise MechaError(
                f"软件 {svc_id} 的 {raw} 调用超时：{exc.message}。"
                f"它可能卡住了——{META_LIST}() 看状态，必要时 {META_CLOSE}('{svc_id}') 后重开。",
                kind="backend_timeout",
                hint="超时**不等于**软件关了；先看它的日志再决定要不要重开",
            ) from exc
        except BackendConnectError as exc:
            return self._retry_after_resync(svc_id, raw, args, entry, exc)
        except BackendError as exc:
            # ⚠ 真协议错/其它后端错：后端"用着用着坏了" ⇒ 标 DOWN（下次立刻响亮失败，不再等超时）
            entry.state = SvcState.DOWN
            entry.why = exc.message
            raise MechaError(
                f"软件 {svc_id} 已关闭（{exc.message}）⇒ 不能调用 {raw}。"
                f"用 {META_OPEN}('{svc_id}') 重开它。",
                kind="backend_down",
                hint=f"{META_LIST}() 看当前哪些软件在跑",
            ) from exc

    def _retry_after_resync(self, svc_id: str, raw: str, args: dict,
                            entry: "_Attached", exc: BackendError) -> Mapping[str, object]:
        """会话级断开 ⇒ **重挂（新会话）+ 重试一次**；还不行就如实报"这一刻连不上"。

        ⚠ 重试**只一次**：重挂要真的重建后端连接（可能还含一次 `list_tools`），
        无限重试会把一次工具调用变成"卡住"（那是比失败更坏的东西）。
        """
        first_why = exc.message
        entry.state = SvcState.UNREACHABLE
        entry.why = first_why
        resynced = False
        if self._resync_fn is not None:
            try:
                self._resync_fn()
                resynced = True
            except Exception as resync_exc:                   # noqa: BLE001 - 重挂失败要说清
                first_why = f"{first_why}（重挂也失败：{type(resync_exc).__name__}: {resync_exc}）"
        fresh = self._backends.get(svc_id)
        if resynced and fresh is not None and fresh.state is SvcState.UP:
            try:
                return _run(fresh.backend.call(raw, args))     # ⭐ 真的自愈：新会话 + 新 initialize
            except BackendError as retry_exc:
                fresh.state = SvcState.UNREACHABLE
                fresh.why = retry_exc.message
                first_why = f"{first_why}；重挂后重试仍失败：{retry_exc.message}"
        raise MechaError(
            f"软件 {svc_id} 这一刻**连不上**（{first_why}）⇒ 不能调用 {raw}。"
            f"⚠ 它多半**正在重启**（supervisor 会把它拉起来）⇒ **过一会儿再调一次**；"
            f"真要确认它现在在不在：{META_LIST}()（别急着 {META_OPEN}——那会跟重启抢）。",
            kind="backend_unreachable",
            hint="每次调用都会重建会话（含 initialize）；端口变了聚合层会按当前端口重挂",
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


def _raise_backend_down(svc_id: str, raw: str, why: str = "", *,
                        unreachable: bool = False) -> "NoReturn":  # type: ignore[name-defined]
    """统一的"这个后端现在调不动"错误（**一处措辞**：墓碑与转发都用它）。

    ⭐⭐ **必须分两档说**（2026-10-10 探针抓到的第三处话术错）：
    - ``DOWN``（**确实被关了**）⇒ "已关闭 ⇒ 用 ``open_software`` 重开它"（可操作）；
    - ``UNREACHABLE``（**连不上**：多半正在被 supervisor 重启、可能**换了端口**）
      ⇒ ⚠ **不许**说"已关闭/去重开" —— 那会让 AI 去跟自动重启**抢**，
      而真相是"**过一会儿它自己就好**"（每次调用都会重建会话；ready 钩子会按新端口重挂）。
    """
    if unreachable:
        raise MechaError(
            f"软件 {svc_id} 这一刻**连不上**（{why or '连接失败'}）⇒ 不能调用 {raw}。"
            f"⚠ 它多半**正在重启**（supervisor 会把它拉起来）⇒ **过一会儿再调一次**；"
            f"要确认它现在在不在：{META_LIST}()（别急着 {META_OPEN}——那会跟重启抢）。",
            kind="backend_unreachable",
            hint="每次调用都会重建会话（含 initialize）；端口变了聚合层会按当前端口重挂",
        )
    raise MechaError(
        f"软件 {svc_id} 已关闭（{why or '已被关闭'}）⇒ 不能调用 {raw}。"
        f"用 {META_OPEN}('{svc_id}') 重开它。",
        kind="backend_down",
        hint=f"调 {META_LIST}() 看当前哪些软件在跑；或用 {META_OPEN}('{svc_id}') 重开",
    )


def _raise_backend_failure(svc_id: str, raw: str,
                           result: Mapping[str, object]) -> "NoReturn":  # type: ignore[name-defined]
    """后端的**域失败** ⇒ 在聚合面**响亮失败**（把教学信息原样抬到顶层）。

    ⭐ 为什么必须抬上来（不许当"成功的正文"）：

    1. **AI 调用体验 P0-3 的判据就是**"失败回执 ``isError=True`` 且正文可解析出
       ``info.kind``"。后端（``providers/mcp._error_result``）已经按这条发了；聚合面若把
       它塞进 ``value`` 里当成功返回，**AI 的宿主看到的是"成功"** ⇒ 那正是 P0-3 记的
       最贵的一类缺陷（"差点把参数崩了当成实验结论"）。
    2. 往返一次就够：``ToolRegistry.execute`` 会把这个 ``MechaError`` 归一化回
       ``{is_error: True, error: {message, info}}``——**形状与后端自己发的那个一致**，
       所以 AI 在两跳上看到的是同一套词汇。

    后端的正文是 ``{"ok": false, "error": {message, info: {kind, hint, suggest}}}``
    （见 ``providers/mcp._error_result``）。解析不出来也**照样失败**（用原文当消息），
    ⚠ **绝不静默当成功**——"看不懂的失败"仍然是失败。
    """
    text = result.get("text")
    payload: Mapping[str, object] = {}
    if isinstance(text, str) and text.lstrip().startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, Mapping):
                payload = parsed
        except ValueError:
            payload = {}
    error = payload.get("error")
    error = error if isinstance(error, Mapping) else {}
    info = error.get("info")
    info = info if isinstance(info, Mapping) else {}
    message = str(error.get("message") or (text if isinstance(text, str) else "")
                  or "后端失败（没有给正文）")
    raise MechaError(
        f"软件 {svc_id} 的 {raw} 失败：{message}",
        kind=str(info.get("kind") or "backend_tool_error"),
        hint=str(info.get("hint") or ""),
        suggest=str(info.get("suggest") or ""),
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
        #: ⭐ **收尾要干净**（2026-10-10）：MCP 客户端内部跑 anyio 任务组，失败/中断时
        #: 可能留下**待处理的 task**。直接 `close()` 会让它们变成
        #: "Task was destroyed but it is pending" / 未处理 ExceptionGroup 噪声
        #: —— 现场看就是"聚合层炸了、还不自愈"。⇒ 关之前先取消残余任务、放掉 asyncgen。
        try:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:                         # noqa: BLE001 - 收尾尽力，别再抛新的
            pass
        loop.close()
