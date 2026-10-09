"""软件**控制端点**的客户端（人侧专用那条腿）。

## 为什么单独一个模块（而不是塞进 `aggregate_runtime.py`）

`aggregate_runtime` 管的是**编排**：把 supervisor 的进程控制面与聚合端点接起来，
它的读者是"启动流程"。本模块是**一个协议客户端**：连某软件的控制端点、调两个操作，
读者是"面板/人侧操作"。

两者共享的只有"这是个客户端"这一点；放一起会让**两条不同端点的生命周期**纠缠
（聚合端点常驻一条、控制端点**只在按钮被按下时才连**）。⇒ 分开放。

## 契约（`docs/v2/软件-交互-写权-三层架构.md` §7.1.1，两端按同一份实现）

| 项 | 约定 |
|---|---|
| 端口文件 | ``<svc_root>/.control-port``（与 ``.mcp-port`` **并列、同名族**） |
| 传输 | MCP over streamable-http ⇒ ⭐ **复用** :class:`Backend`，**不发明第二种协议** |
| 操作 | ``set_write_authority(mode)`` · ``open_manual_surface()`` |
| ``mode`` 入参 | **裸值** ``locked``/``human``/``ai``/``open``（⚠ ``.value``，不是 ``str(Mode.AI)``） |
| 谁连 | **只有框架/面板** ⇒ ⚠ **端口号绝不进页面/给 AI 的输出** |

## ⚠ 超时：**3 秒**（比 `Backend` 默认的 30s 短一个量级）

理由：**这是人在按按钮等着的操作**，不是长训练。
- 太长（30s）⇒ 人盯着一个"转圈"的按钮，不知道是卡了还是没点着；
- 太短（<1s）⇒ 软件自己可能正在做一件小事（记账/开窗）就被判超时，**误报**成"连不上"。
⇒ 3s 是"人能忍"与"软件够用"的折中；⚠ 它与 ``Backend`` 的**三种失败区分**配合使用：
超时是**独立一种**，不许与"连不上"混成一句话（铁律 8）。

## ⚠ `initial_mode` 与切写权是**两条不同的路**（别混）

- ``initial_mode``（启动声明，走 env）⇒ **spawn 软件时**由框架注入（见 ``Service.child()``）；
- 本模块 ⇒ **运行中**由人按下按钮触发（软件进程内的闸门当场改）。
两条路的**最终真值**都在软件进程内，但**入口不同、时机不同**。

## Known Limitations and Deferred Work

- **不做"读回当前写权"**：契约只定了两个操作，没有查询操作 ⇒ 面板**不显示**"当前是 AI 还是
  人工"（会显示一个**猜的**值比不显示更坏）。⇒ 显示的是**面板自己的意图**并注明。
  **重新考虑条件**：契约加一个 ``get_write_authority()`` 之后。
- **每次调用现连**（短连）⇒ 与 ``Backend`` 同一取舍（见它的 Known Limitations）。
- **不做重试**：控制操作**幂等性未约定**（``open_manual_surface`` 是幂等的，但
  ``set_write_authority`` 的语义是"改状态"）⇒ 失败如实上抛，由人决定要不要再按一次。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .errors import MechaError
from .portfile import read_port
from .providers.mcp_client import (Backend, BackendConnectError, BackendError,
                                   BackendProtocolError, BackendTimeoutError)

__all__ = ["CONTROL_INSTRUCTIONS", "CONTROL_PORT_FILE", "CONTROL_TIMEOUT_S",
           "SoftwareControl", "build_control_registry", "control_port_path",
           "start_control_endpoint"]

#: 控制端点的端口文件（与 ``.mcp-port`` **并列、同名族**）。
CONTROL_PORT_FILE = ".control-port"

#: 控制调用的超时（秒）。见模块 docstring 的三条理由。
CONTROL_TIMEOUT_S = 3.0


def control_port_path(data_root: Path) -> Path:
    """控制端口的落点：``<data_root>/.control-port``。

    ⚠ 与 ``.mcp-port`` **同源习惯**（都写在软件的数据根）：读法也共用
    :func:`mecha.portfile.read_port`（不抄第二套"怎么读端口文件"）。
    """
    return Path(data_root) / CONTROL_PORT_FILE


@dataclass
class SoftwareControl:
    """连某软件的**控制端点**（人侧专用）。⚠ 与聚合端点是**两条不同的腿**。"""

    svc_id: str
    root: Path
    timeout_s: float = CONTROL_TIMEOUT_S

    @classmethod
    def connect(cls, svc_id: str, data_root: Path,
                timeout_s: float = CONTROL_TIMEOUT_S) -> "SoftwareControl":
        """建句柄（**懒连**：不在构造期探活，见下）。

        ⚠ 为什么不在这里就探活：面板**每次渲染**都要问"这个按钮能不能点"，
        若那时真去连一次，就会给每个软件每次刷新加一次网络往返。
        ⇒ 可用性由**端口文件在不在**判（廉价、且这正是"软件起没起来"的代理指标），
        真正的失败在**调用时**响亮报出。
        """
        return cls(svc_id=svc_id, root=Path(data_root), timeout_s=timeout_s)

    # ---- 可用性（面板据此决定按钮能不能点）----
    def available(self) -> tuple[bool, str]:
        """能不能调？返回 ``(能否, 人话原因)``。

        ⚠ 这是**廉价判据**（读一个文件），不是探活：它回答的是"**软件起没起来**"。
        端口文件在 ≠ 端点一定活着（软件可能刚崩）——那种情况由**调用时**的
        ``BackendConnectError`` 如实报出（铁律 8：读不到 ≠ 确实没有）。
        """
        p = control_port_path(self.root)
        if not p.exists():
            return False, f"软件 {self.svc_id} 还没起来（没有控制端点）"
        if read_port(p) is None:
            return False, f"软件 {self.svc_id} 的控制端口文件读不出端口（可能正在起/正在停）"
        return True, ""

    def _url(self) -> str:
        p = control_port_path(self.root)
        port = read_port(p)
        if port is None:
            raise MechaError(
                f"软件 {self.svc_id} 的控制端点不可用（端口文件读不出端口）",
                kind="control_unavailable",
                hint="它可能还没起来或正在停；用面板的存活滑块看它是否在跑",
            )
        return f"http://127.0.0.1:{port}/mcp"

    # ---- 两个操作 ----
    def set_write_authority(self, mode: str) -> dict:
        """切写权（``mode`` 必须是**裸值**：``locked``/``human``/``ai``/``open``）。"""
        raw = str(getattr(mode, "value", mode)).strip().lower()
        return self._call("set_write_authority", {"mode": raw})

    def open_manual_surface(self) -> dict:
        """在**软件进程内**开手动面（复用已装配的那份桥）。"""
        return self._call("open_manual_surface", {})

    def read_write_authority(self) -> dict:
        """读**当前写权**（只读自省，契约里的第三个控制操作）。

        ⚠ **尚未落地这个读操作的软件**（本轮只有部分项目实现了它）会回一个
        "未知工具"的**域失败**（``control_rejected``）。那**不是**"确实没有写权这回事"，
        而是"**这个软件还没实现它**" ⇒ 调用方必须与"读不到"分开报（铁律 8）。
        本方法**不吞**它，如实上抛。
        """
        return self._call("read_write_authority", {})

    def _call(self, op: str, args: dict) -> dict:
        """调一次控制端点。⚠ 把三种**传输**失败翻译成三种不同措辞（铁律 8）。

        ⚠ 还有第四种：**软件侧的域失败**（``bad_mode`` / ``no_manual_surface``）——
        它回来时**不是**传输异常，而是回执里的 ``is_error=True``（``Backend._normalize_result``
        保留了这一位）。⇒ 必须在这里翻成 ``MechaError``，否则调用方会以为"成功了"。
        """
        try:
            backend = _run(Backend.connect(self._url(), server_name=f"{self.svc_id}:control",
                                          timeout_s=self.timeout_s))
            out = _run(backend.call(op, args))
        except BackendTimeoutError as exc:
            raise MechaError(
                f"软件 {self.svc_id} 的控制端点**超时**（>{self.timeout_s:.0f}s）：{exc.message}",
                kind="control_timeout",
                hint="软件可能正忙（在开窗/在记账）；过一会儿再按一次",
            ) from exc
        except BackendConnectError as exc:
            raise MechaError(
                f"**连不上**软件 {self.svc_id} 的控制端点：{exc.message}",
                kind="control_unreachable",
                hint="端口文件在但它没在听 ⇒ 软件可能刚崩或正在重启"
                     "（⚠ 这与'超时'不是一件事）",
            ) from exc
        except BackendProtocolError as exc:
            raise MechaError(
                f"软件 {self.svc_id} 的控制端点**协议错**（回了但不是 MCP）：{exc.message}",
                kind="control_protocol",
                hint="确认那个端口真的是控制端点（不是能力面/监控端点）",
            ) from exc
        except BackendError as exc:
            raise MechaError(
                f"软件 {self.svc_id} 的控制调用失败：{exc.message}",
                kind="control_failed",
                hint="看软件日志；失败原因分类见上层 kind",
            ) from exc
        result = dict(out) if isinstance(out, dict) else {"value": out}
        _check_domain_result(self.svc_id, op, result)     #: ① 信封的 ``is_error``
        payload = _unwrap_domain(result)
        #: ② ⭐ **回执里 ``ok=false`` 也是域失败**（实测：软件把异常翻成
        #: ``{"ok": false, "error": …}`` **而没设 ``isError``** ⇒ 只看信封会漏掉它，
        #: 调用方拿到一个"看着像成功"的字典 —— 那正是本工程最忌讳的静默坏事）。
        if isinstance(payload, dict) and payload.get("ok") is False:
            detail = payload.get("error") or payload.get("message") or payload
            raise MechaError(
                f"软件 {self.svc_id} 拒绝了 {op}：{str(detail)[:200]}",
                kind="control_rejected",
                hint="这是**软件侧**的判断（不是连不上）；按它给的 hint 处理",
            )
        return payload


def _unwrap_domain(result: dict) -> dict:
    """把回执**信封**解成**域结果**（⚠ 少了这一步，调用方读什么都恒 ``None``）。

    ## 为什么必须有（**实测抓到的真 bug**）

    `Backend.call()` 回的是**信封**：``{"is_error": bool, "text": "<JSON>", "structured": …}``
    —— 域结果（``{"ok": true, "mode": "ai", …}``）**藏在** ``structured`` 或 ``text`` 的 JSON 里。

    ⚠ 不解包会怎样（实测）：ML 真起 `--mcp` 后，``read_write_authority()`` 回的字典里
    **没有 ``mode``** ⇒ 调用方读 ``out["mode"]`` **恒 ``None``**
    ⇒ ⭐ **面板的"读真值"对真软件形同不存在**（它以为读到了，其实全是空）。

    ⚠ 为什么零件判据一直没抓到：那些判据**短路网络**、用**内存替身**，
    而替身的形状与真信封**不同**（R12「替身失真」的同款）。

    ⚠ 解包顺序：``structured``（结构化，最可信）⇒ ``text`` 的 JSON（回落）⇒ 原样回。
    """
    structured = result.get("structured")
    if isinstance(structured, dict):
        return structured
    txt = result.get("text")
    if isinstance(txt, str) and txt.lstrip().startswith("{"):
        try:
            parsed = json.loads(txt)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    return result

    def close(self) -> None:
        """短连实现 ⇒ 无常驻资源（留着是为了接口稳定）。"""
        return None


def _check_domain_result(svc_id: str, op: str, result: dict) -> dict:
    """软件**域回执**的公共翻译：``is_error`` ⇒ 抛（否则调用方以为成功了）。

    ⚠ 抽成模块级函数是**为了判据能复用同一份**：判据短路网络往返时若自己重写一遍
    "什么算失败"，测的就是判据的规则、而不是实现里的规则（本档第一版就漏了这一步，
    结果"域失败"被当成成功、字段全是 ``None``）。
    """
    if result.get("is_error"):
        raise MechaError(
            f"软件 {svc_id} 拒绝了 {op}：{result.get('text') or result}",
            kind="control_rejected",
            hint="这是**软件侧**的判断（不是连不上）；按它给的 hint 处理",
        )
    return result


def _run(coro: object) -> object:
    """跑一个协程（调用点都在**同步**的面板/判据里）。

    ⚠ 用**自己的事件循环**而不是 ``asyncio.run``：``asyncio.run`` 在"已经有一个在跑的
    循环"的线程里会抛（面板可能被嵌在异步宿主里）。与 ``aggregate._run`` 同一取舍。
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)      # type: ignore[arg-type]
    finally:
        loop.close()


# =========================================================================== 服务端
#
# ⭐ **服务端也在这个模块**（与上面的客户端**同一份契约、同一个家**）。
#
# ## 为什么必须上提到框架（实测教训）
#
# 在它之前，**每一家各写一份 ≈134 行**（EL `mecha_v2/control.py` 134 行 ·
# GP `mecha_adapter/control.py` 133 行，**结构逐行几乎相同**：同一个 ``.control-port``
# 常量、同一个 ``build_control_registry``、同一个 ``start_control_endpoint``），
# 而 **ML 干脆没有** ⇒ 它切不了写权。
# ⇒ 用户裁决：*"把 GP 这套上升进框架……减少下游项目开发负担"*、
#   *"我们为的是建设 mecha 框架，让以后的项目不用在启动、生存域这块费心，架构直接给安排好了"*。
# ⇒ 于是：**机制在框架，项目只交 3 个回调**（见 :func:`build_control_registry`）。
#
# ## 项目只交这三样（其余全在框架）
#
# | 交什么 | 为什么是项目的 |
# |---|---|
# | ``switch(mode_value)`` | "怎么改本软件的写权"只有项目知道（它自己的闸门对象） |
# | ``audit(op, value, reason)`` | 落**本项目那本账**（§4.3 的纪律：同一道门、同一本账） |
# | ``open_surface()`` 或 ``None`` | "怎么开手动面"是本项目的事；没有 ⇒ 传 ``None``（调用时**响亮失败**） |
# 外加两个**值**：``how_to_open`` 文案、``server_name``（端点服务名）。

#: 控制端点的端点说明（⚠ **不写地址、不写端口**）。项目可覆盖。
CONTROL_INSTRUCTIONS = (
    "本端点供**人侧**（框架 / 面板 / 人手腿）控制这份软件：读当前写权、切写权、按需开手动面。"
    "它不是给模型用的能力面——能力面在另一条端点上，两者共用同一套门与同一本账。"
)


def build_control_registry(authority: object, *,
                           switch: "Callable[[str], None]",
                           audit: "Callable[[str, str, str], None] | None" = None,
                           open_surface: "Callable[[], str] | None" = None,
                           how_to_open: str = "") -> object:
    """建**控制面**的工具表（三个操作）。⭐ 项目只交回调，机制在这里。

    ## 三个操作

    | 操作 | 语义 |
    |---|---|
    | ``read_write_authority()`` | ⭐ **只读自省**，用框架的**唯一实现** :func:`authority.register_authority_read`（靠 ``gate(side)`` 纯查询探侧 ⇒ 报的就是闸门真会怎么判） |
    | ``set_write_authority(mode)`` | 切写权（**裸值**）；未知值 ⇒ ``bad_mode`` **响亮失败**；切完**立刻生效** |
    | ``open_manual_surface()`` | 开人那条腿的面；⚠ 没注入 ``open_surface`` ⇒ ``no_manual_surface`` **响亮失败**（不静默成功） |

    ## ⚠⚠ `audit` 为什么**必须走 `Gate.record`、不许走 `Gate.set`**（防自锁）

    ⭐ **切写权本身就是它要改的那个东西**：若这个操作过一次普通的写权门，
    那么切到 ``ai`` 之后，**人侧就再也记不了账、也切不回来了** ⇒ **死锁**。
    ⇒ 项目给的 ``audit`` 应当是"**只记史、不过写权门**"的那条路
    （EL: ``bridge.record_audit``；GP: ``Gate.record``）。
    ⚠ **仍然是同一本账**（§4.3 的纪律照旧成立：查得到"谁在什么时候把写权切走了"）。

    ## ⚠ 纯查询不许有副作用

    ``read_write_authority`` 只探侧、**不落账**（判据钉着"调前调后 mode 不变、账不增长"）。
    """
    from .authority import Mode as _Mode
    from .authority import register_authority_read
    from .tools import ToolRegistry, define_tool

    reg = ToolRegistry()
    register_authority_read(reg, authority, how_to_open=how_to_open)

    def _set_write_authority(mode):
        raw = str(mode).strip().lower()
        try:
            #: ⚠ 一律 `.value`：`Mode` 是 **str-Enum**（`str(Mode.AI)` 给 'Mode.AI' 不是 'ai'）。
            m = _Mode(raw)
        except ValueError:
            raise MechaError(
                f"未知写权模式：{mode!r}", kind="bad_mode",
                hint="裸值只认 " + "/".join(x.value for x in _Mode)
                     + "（⚠ 不是 `str(Mode.AI)` 那种 'Mode.AI' 形状）")
        switch(m.value)
        if audit is not None:
            audit("control.write_authority", m.value, "人侧切写权（控制端点）")
        return {"ok": True, "mode": m.value}

    def _open_manual_surface():
        if open_surface is None:
            raise MechaError(
                "本进程没有可开的「手动面」（项目未注入 open_surface）",
                kind="no_manual_surface",
                hint="只有带人面（GUI 窗口 / Web 工作台）的软件才有这条腿；"
                     "没有就是没有，不假装开了一个")
        status = str(open_surface())
        if audit is not None:
            audit("control.open_manual_surface", status, "人侧按需开手动面（控制端点）")
        return {"ok": True, "status": status}

    reg.register(define_tool(
        name="set_write_authority",
        description=("切换这份软件的写权归属（人侧专用）。切完**立刻生效**："
                     "下一次写权判定就用新值（不允许'等一拍'）。"),
        parameters={"mode": {"type": "string",
                             "description": "裸值：locked / human / ai / open"}},
        output_schema={"type": "object", "required": ["ok", "mode"]},
        execute=lambda mode: _set_write_authority(mode),
    ))
    reg.register(define_tool(
        name="open_manual_surface",
        description=("请求打开「手动面」（人那条腿）。已经开着 ⇒ 幂等（不重复开、不报错）；"
                     "本项目没有人面 ⇒ **响亮失败**。"),
        parameters={},
        output_schema={"type": "object", "required": ["ok", "status"]},
        execute=lambda: _open_manual_surface(),
    ))
    return reg


def start_control_endpoint(registry: object, *, port_file: object, server_name: str,
                           instructions: str | None = None,
                           host: str = "127.0.0.1", log: object = None):
    """起控制端点（框架 :class:`~mecha.providers.mcp.McpEndpoint`；**端口回读**）。

    返回 ``(endpoint, actual_port)``。⚠ 端口号**不要**打印/回显给 AI（§7.1.1）：
    它只写进 ``port_file`` 给框架读。

    ⚠ ``providers.mcp`` 是 **extra**（核心不 import 它）⇒ 这里**函数内**才 import。
    """
    from .providers.mcp import McpEndpoint
    ep = McpEndpoint(registry, server_name=server_name,
                     instructions=instructions or CONTROL_INSTRUCTIONS,
                     host=host, port=0, port_file=str(port_file), log=log)
    return ep, ep.start()
