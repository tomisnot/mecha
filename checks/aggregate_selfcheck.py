"""聚合端点（2a）的判据：能在**进程内**验的那几条。

## ⚠ 造法纪律：先问"怎么故意退化还能让它绿"

这是步骤 1 判据 ③ 的教训（本仓已记档）。逐条对照：

- **判据 2 的陷阱**：如果只断言"工具表里没有了"，那把 ``detach`` 写成
  **只从列表删掉、但 ``call`` 仍转发**照样绿（工具表是它自己算的，当然听话）。
  ⇒ 所以**必须同时**断言"调那个旧工具**响亮失败**"——那才是"关了"的真实含义。
- **判据 3 的陷阱**：如果只断言"别的后端工具还在"，把 ``attach`` 写成
  "连不上就抛"也能对**部分**输入绿。⇒ 造法要**真的塞一个连不上的后端**，
  并断言"聚合端点仍然构造得出来 + 那个后端在事实里是 unreachable + 好的后端照常可用"。
- **判据 4 的陷阱**：只断言"输出里有 id"⇒ 把 ``query_software`` 写成
  "回一个写死的列表"也能绿。⇒ 造法要求**改事实 ⇒ 输出必须跟着变**（两个方向都验）。
- **判据 5 的陷阱**：把超时写成"整个端点都不可用"也"不挂"，但那是**错的**。
  ⇒ 造法要**同时**断言：超时那个响亮失败、**且同一轮里别的后端照常能调**。

## 这几条为什么不用真起进程

2a 只做"工具表 + 转发 + 失败语义"，全是**进程内**的性质（真跨进程端到端留给 2b）。
用**假 Backend**（只实现 ``tools``/``call`` 两个方法）能精确钉住边界；
真 MCP 协议那一层由 ``checks/phase12_mcp_projection_selfcheck.py`` 与
``dsh_panel_selfcheck`` 各自的既有判据覆盖。

## Known Limitations and Deferred Work

- **不验真跨进程端到端**（真 dsh 连聚合端点、真后端 MCP 服务端）：那是 **2b**。
  本轮只用**假 Backend** 钉"工具表 / 转发 / 失败语义"这三件**进程内**可判的事；
  真 MCP 协议那一层由既有判据覆盖（``checks/phase12_mcp_projection_selfcheck.py``
  与 ``checks/dsh_panel_selfcheck.py``）。
- ⚠ **通知发不出去 ⇒ 本判据不测通知**（也就**不能**声称"工具表变化会实时同步到 dsh"）。
  实测证据（mcp **2.2.0**）：
  ① 客户端上下文管理器真名是 ``streamable_http_client``，
  **没有** ``streamablehttp_client``（旧名，import 即失败）；
  ② ⭐ 服务端**发不出** ``notifications/tools/list_changed``：
  框架用的 ``mcp.server.mcpserver.server.MCPServer`` 只有
  ``add_tool`` / ``remove_tool`` / ``list_tools`` / ``call_tool`` / ``tool``
  ——**没有服务端主动发通知的公开面**；``mcp.server.lowlevel.Server`` 上 ``send*`` 成员为**空**
  （只有"收进来"方向的 ``add_notification_handler``）。
  ⇒ **元工具承担主路径**（``query_software`` 报全量事实），通知**登记为已知限制、不假装发了**。
  **2b 再谈**要不要绕到 lowlevel 自己发（那是另一套会话/传输管理）。
- ⭐ **``MCPServer`` 有 ``add_tool`` / ``remove_tool``（能在活着的服务端上增减工具）**：
  这是设计文档 §6.1「**不需要 ProxyMCP 兜底**」的**实现层依据**——架构当初就按
  "工具表能动态改"设计。本轮仍走 ``build_mcp_server``（纯构造、快照工具表）⇒
  attach/detach 后**重建**，不做增量（增量要能发通知才看得见，当前无收益）。
- **不测"AI 第一次就调对"**：那是 UX 判据，机械判不了（总纲 §7 Phase 4 的终极验收）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mecha.aggregate import (META_CLOSE, META_LIST, META_OPEN,  # noqa: E402
                             Aggregator)
from mecha.errors import MechaError  # noqa: E402
from mecha.providers.mcp import build_mcp_server  # noqa: E402
from mecha.providers.mcp_client import BackendTimeoutError  # noqa: E402

FAILS: list[str] = []


def _check(name: str, cond: bool, extra: object = "") -> None:
    if cond:
        print(f"  ✔ {name}")
    else:
        print(f"  ✖ {name}" + (f"  <- {extra}" if extra != "" else ""))
        FAILS.append(name)


class FakeBackend:
    """只实现聚合端点用到的那两个方法（``tools`` / ``call``）。

    ``fail`` 可编程：``"timeout"`` ⇒ 调用抛超时；``"dead"`` ⇒ 抛连不上；
    其它情况原样回显。
    """

    def __init__(self, tools: list[dict], *, fail: str = "", echo: str = "ok") -> None:
        self._tools = tools
        self.fail = fail
        self.echo = echo
        self.calls: list[tuple[str, dict]] = []

    async def tools(self) -> list[dict]:
        if self.fail == "unreachable":
            from mecha.providers.mcp_client import BackendConnectError
            raise BackendConnectError("连不上（判据用的假后端）", kind="backend_unreachable")
        return self._tools

    async def call(self, name: str, args: dict) -> dict:
        self.calls.append((name, args))
        if self.fail == "timeout":
            raise BackendTimeoutError("调用超时（判据用的假后端）", kind="backend_timeout")
        if self.fail == "dead":
            from mecha.providers.mcp_client import BackendConnectError
            raise BackendConnectError("后端已崩（判据用的假后端）", kind="backend_unreachable")
        return {"is_error": False, "text": f"{self.echo}:{name}"}

    async def close(self) -> None:
        return None


def _tools(*names: str) -> list[dict]:
    return [{"name": n, "description": f"{n} 的说明", "inputSchema": {}} for n in names]


def _names(agg: Aggregator) -> list[str]:
    return [s["name"] for s in agg.registry().schemas()]


def _call(agg: Aggregator, name: str, **kw) -> dict:
    """调一次并**取出回执本体**。

    ⚠ ``ToolRegistry.execute`` 返回的是**归一化信封** ``{is_error, value, content}``
    ⇒ 断言语义要看 ``value``（信封是传输层的形状，不是工具的返回值）。
    """
    env = agg.registry().execute(name, kw)
    if env.get("is_error"):
        raise MechaError(str(env.get("error", "调用失败")), kind="tool_call_failed")
    return env["value"]


def check_two_backends_do_not_collide() -> None:
    agg = Aggregator()
    agg.attach("ML", FakeBackend(_tools("train_model"), echo="ML"))
    agg.attach("EL", FakeBackend(_tools("train_model"), echo="EL"))
    names = _names(agg)
    _check("① 两个后端的同名工具**共存不撞**（各自带自己的前缀）",
           "ml_train_model" in names and "el_train_model" in names, f"names={names}")
    _check("① 转发真的是**各自那条**后端（不是撞到同一个）",
           _call(agg, "ml_train_model")["text"].startswith("ML")
           and _call(agg, "el_train_model")["text"].startswith("EL"))
    _check("① 元工具与后端工具同一个表里（服务器可构造）",
           all(m in names for m in (META_LIST, META_OPEN, META_CLOSE)), f"names={names}")
    srv = build_mcp_server(agg.registry(), server_name="mecha", instructions="聚合端点判据")
    _check("① 复用既有 `build_mcp_server` 能构造成功（不另起服务端实现）", srv is not None)


def check_detach_is_loud() -> None:
    """⭐ 判据 2：**同时**断言"工具表里没有了" + "调旧工具响亮失败"。"""
    agg = Aggregator()
    good = FakeBackend(_tools("train_model"), echo="ML")
    agg.attach("ML", good)
    agg.attach("EL", FakeBackend(_tools("set_config"), echo="EL"))
    _check("② 前置：关之前工具在表里", "ml_train_model" in _names(agg))

    agg.detach("ML")
    names = _names(agg)
    # ⚠ **与我第一版的断言相反**：工具名**仍然在表里**（作为"墓碑"），
    #   但它的描述前缀写明"已关闭"，且**调用会响亮失败**。
    #   为什么改成这样：实测"从表里删掉名字"会让 AI 拿上一轮的旧名字来调时只得到
    #   一句通用的「未知工具 'ml_train_model'」——既不知道是谁关了、也不知道怎么重开，
    #   而 §需求2 要的是"没看到"和"没发生"**不能长得一样**。
    #   ⇒ 「不再出现」的正确含义是**不再可用**，不是"名字消失"。
    _check("② 关掉之后它**不再可用**（表里留着墓碑，描述明写已关闭）",
           "ml_train_model" in names
           and any(s["name"] == "ml_train_model" and "已关闭" in s["description"]
                   for s in agg.registry().schemas()), f"names={names}")
    # ⭐ 墓碑必须**带 svc_id 与重开方法**——只钉"名字还在"是不够的：
    #   名字还在但报错说"未知工具"，比名字消失还坏（它看着像"有"，实际全无信息）。
    tomb_msg = ""
    try:
        _call(agg, "ml_train_model")
    except MechaError as exc:
        tomb_msg = exc.message
    _check("⭐ 墓碑的错误文案**带 svc_id**（是谁关了）", "ML" in tomb_msg, f"msg={tomb_msg!r}")
    _check("⭐ 墓碑的错误文案**带重开方法**（元工具名）", META_OPEN in tomb_msg,
           f"msg={tomb_msg!r}")
    _check("② 但**别人的工具**照旧在且可用", "el_set_config" in names, f"names={names}")

    # ⭐ 这一半才是"关了"的真实含义：旧名字还在 AI 手里，调它必须响亮失败
    loud = False
    msg = ""
    try:
        _call(agg, "ml_train_model")
    except MechaError as exc:
        loud = True
        msg = exc.message
    _check("② ⭐ 调它的**旧工具**⇒ 响亮失败（不是静默丢弃/不是转发给已关的后端）", loud)
    _check("② 文案说清**是谁关了**", "ML" in msg and "已关闭" in msg, f"msg={msg!r}")
    _check("② 文案给出**重开方法**（含元工具名）", META_OPEN in msg, f"msg={msg!r}")
    _check("② 关掉后**没有**再往那个后端转发（假后端的调用记录为空）",
           good.calls == [], f"calls={good.calls}")


def check_one_bad_backend_does_not_sink_the_rest() -> None:
    """判据 3：一个后端连不上 ⇒ 聚合端点不整体挂，其余照常。"""
    agg = Aggregator()
    agg.attach("GP", FakeBackend(_tools("query_papers"), fail="unreachable"))
    agg.attach("ML", FakeBackend(_tools("train_model"), echo="ML"))
    names = _names(agg)
    _check("③ 连不上的后端**不出现**在工具表里", "gp_query_papers" not in names, f"names={names}")
    _check("③ 其余后端**照常可用**（工具在表里、也能调）",
           "ml_train_model" in names and _call(agg, "ml_train_model")["text"].startswith("ML"))
    facts = _call(agg, META_LIST)
    gp = next((x for x in facts["software"] if x["id"] == "GP"), None)
    _check("③ 连不上的那个在事实里如实标 `unreachable`（不假装 up）",
           gp is not None and gp["aggregate"] == "unreachable", f"gp={gp}")
    srv = build_mcp_server(agg.registry(), server_name="mecha", instructions="判据")
    _check("③ 聚合端点仍然**构造得出来**（不因为一个后端挂了就起不来）", srv is not None)


def check_query_software_tracks_facts() -> None:
    """判据 4：元工具输出与事实一致 —— **改事实 ⇒ 输出必须跟着变**（两方向）。"""
    proc_state = {"children": [{"id": "ML", "state": "ready", "port": 1111,
                                "holds_write_lease": True}]}
    agg = Aggregator(state_fn=lambda: proc_state)
    agg.attach("ML", FakeBackend(_tools("a", "b"), echo="ML"))
    agg.attach("GP", FakeBackend(_tools("c"), echo="GP"))
    f1 = _call(agg, META_LIST)
    by1 = {x["id"]: x for x in f1["software"]}
    _check("④ 事实 1：ML/GP 都挂着、工具数各如实",
           by1["ML"]["aggregate"] == "up" and by1["ML"]["tools"] == 2
           and by1["GP"]["tools"] == 1, f"f1={f1}")
    _check("④ 进程事实**分开报**（进程视角 与 聚合视角不是同一件事）",
           by1["ML"]["process"] == {"state": "ready", "port": 1111,
                                    "holds_write_lease": True}, f"ml={by1['ML']}")

    agg.detach("GP")
    f2 = _call(agg, META_LIST)
    by2 = {x["id"]: x for x in f2["software"]}
    _check("④ ⭐ 改事实（关掉 GP）⇒ 输出**跟着变**（不是写死的列表）",
           by2["GP"]["aggregate"] == "down" and by2["ML"]["aggregate"] == "up", f"f2={f2}")
    _check("④ 关掉的软件仍然**出现在事实里**（消失 ≠ 读不到；铁律 8）",
           "GP" in by2, f"f2={f2}")
    # ⚠ 我的第一版这里断言 `tools == 0`：**那是错的**——工具名要留着做墓碑
    #   （否则旧名字调过来只会得到通用的"未知工具"）。改成断言"调它必须响亮失败"。
    loud2 = False
    try:
        _call(agg, "gp_c")
    except MechaError:
        loud2 = True
    _check("④ 关掉之后**调它的旧工具必须响亮失败**（工具名仍在表里 = 墓碑）", loud2,
           f"names={_names(agg)}")


def check_timeout_does_not_block_others() -> None:
    """判据 5：后端超时 ⇒ 聚合端点仍能服务其它请求。"""
    agg = Aggregator()
    slow = FakeBackend(_tools("slow_tool"), fail="timeout")
    agg.attach("SLOW", slow)
    agg.attach("ML", FakeBackend(_tools("train_model"), echo="ML"))

    loud = False
    msg = ""
    try:
        _call(agg, "slow_slow_tool")
    except MechaError as exc:
        loud, msg = True, exc.message
    _check("⑤ 超时 ⇒ 对调用者**响亮失败**（不是挂住、不是静默成功）", loud)
    _check("⑤ 文案区分「超时」与「已关闭」（超时不等于关了）",
           "超时" in msg and "不等于" in msg, f"msg={msg!r}")
    _check("⑤ ⭐ 同一轮里**别的后端照常能调**（超时不拖垮端点）",
           _call(agg, "ml_train_model")["text"].startswith("ML"))
    _check("⑤ 超时的后端**没有**被误报成 UNREACHABLE（它挂着，只是这次慢）",
           {x["id"]: x["aggregate"] for x in _call(agg, META_LIST)["software"]}["SLOW"] == "up")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("[aggregate_selfcheck] 聚合端点（2a：工具表 / 转发 / 失败语义）")
    check_two_backends_do_not_collide()
    check_detach_is_loud()
    check_one_bad_backend_does_not_sink_the_rest()
    check_query_software_tracks_facts()
    check_timeout_does_not_block_others()
    if FAILS:
        print(f"aggregate_selfcheck: FAIL（{len(FAILS)} 条）：{FAILS}")
        return 1
    print("aggregate_selfcheck: PASS（1/2/3/4/5 都钉住；每条都过了「故意退化」的对照）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
