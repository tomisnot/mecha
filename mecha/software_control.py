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
from dataclasses import dataclass
from pathlib import Path

from .errors import MechaError
from .portfile import read_port
from .providers.mcp_client import (Backend, BackendConnectError, BackendError,
                                   BackendProtocolError, BackendTimeoutError)

__all__ = ["CONTROL_PORT_FILE", "CONTROL_TIMEOUT_S", "SoftwareControl",
           "control_port_path"]

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
        return _check_domain_result(self.svc_id, op, result)

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
