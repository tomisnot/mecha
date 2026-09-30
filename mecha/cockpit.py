"""cockpit：只读监控端点的框架实现（Monitor 这一层的传输）。

Monitor 是软件自己四件里与 Gate / History / Surface **并列的第一层**，但此前它只有
``monitor.read()`` 一个读面：能不能被人看见，取决于宿主是否记得再写一个 HTTP
端点。两个下游因此各写了一份**逐字节同形**的端点（同一个契约、两份维护），
本模块把那层传输收进框架——这是补完自己声明过的面，不是扩张。

## 只读是设计红线

路由表里**没有任何**写 / 切模式入口，一切 ``POST`` 恒 ``404``。读的是"谁改了什么"
的操作流水，不是域数据覆写面；控制权切换归**看门人的进程级交接**。因此端点
**无鉴权**、默认只绑回环——「AI 不能自授权」的护栏靠 ``Authority`` 的 side 判定
（AI 通道改不了 mode），不靠端点鉴权，也不靠 token。

## 契约（冻结，逐字段照两家现有实现）

``OPTIONS *`` → ``204`` + CORS（无 body）；``GET /status`` → ``{ok, mode}``；
``GET /activity`` → ``{ok, events, mode}``（事件 6 键，无 before/after/reason）；
``GET /history`` → ``{ok, events, mode}``（事件含 op / before / after / reason）；
``GET /config`` → ``{ok, has_schema, groups, orphans, n_keys, n_snapshot_keys}``；
未知路径 → ``404 {"ok": false, "error": "unknown endpoint"}``；
任意 ``POST`` → ``404 {"ok": false, "error": "read-only monitor endpoint"}``。
路径一律先取 ``urlsplit(self.path).path`` 再**精确匹配**（查询串交给 ``parse_qs``，
会做 URL 解码）；``since_seq`` 缺失 / 非纯数字 ⇒ ``0``，语义是 ``seq >= N``（闭区间）。
排序是契约：``events`` 升序、``orphans`` 按名升序、``groups`` 族与前缀字典序；
服务端**不截断、不倒序、不缓存**，``since_seq`` 是唯一裁剪。

⚠ **``before`` 必须 fold 全史、不能只 fold ``since_seq`` 窗口**：它是"同键上一条
事件的值"（首写 ``null``），不是快照旧值。fold 与过滤必须分离——写成"只 fold 窗口"
会让增量轮询的 ``before`` 从 ``null`` 起，面板展开行显示 ``∅ → 新值``，**静默错**
且只有人肉看面板才会发现。判据里有一条专测它。

## 数据来自哪里（seam）

本模块**不认** ``Software``、不 import ``assembly``：它只认 ``MonitorSource``——
``events()`` / ``snapshot()`` / ``mode()`` / ``schema_rows()`` / ``status_extra()``
五个成员（鸭子类型即可，不受 Protocol 约束）。``Software`` 提供前三个；
``schema_rows`` 与 ``status_extra`` 是**项目注入**的两项：

- ``schema_rows``：配置 schema 行（每行至少 ``name``，可选 ``family`` / ``unit`` /
  ``type`` / ``desc``）。**框架不定义 schema 概念**——EL 从引擎参数表来，ML 没有
  这层就如实空表，端点走"无 schema"分支（``has_schema=false`` + 快照全进
  ``orphans``）。往 ``Engine`` 加必实现会逼没有 schema 的项目编造行（"凭空造树把
  面板撑满"），所以只有注入这一个形态。
- ``status_extra``：``/status`` 的附加键（如 pid / 端口一类**宿主生命周期事实**——
  那个端点自己不知道）。默认空，于是默认响应与"只回 ``{ok, mode}``"的宿主逐字节一致。

## 端口

默认 ``port=0``（OS 分配）——**库内零端口字面量**是既有纪律：固定端口会让同机
两实例互抢、让面板连不上旧端口却只显示"离线"（假绿）。发现机制是最小的一份：
``port_file``（裸端口文本，清理规则见 ``portfile``）+ 可选的 ``runtime_writer``
钩子。**运行期描述符的 schema 不属本模块**：框架只负责"何时写"，文件名与字段表
归项目（项目名与根也归项目）。

## Known Limitations and Deferred Work

- **不做鉴权、不做 TLS、不做 POST**：只读 + 回环是本层的全部安全模型；要暴露到
  非回环就得由宿主在前面加一层真正的网关（本模块不提供"只读 token"这类中间态）。
- **不做访问日志与限流**：``log_message`` 静音；轮询频率归面板。启动/停止各回调
  一行 ``log`` 是刻意的（人排查"起没起来"要比"零输出"有用）。
- **不校验 schema 行的内容**：``unit`` / ``type`` / ``desc`` 只做"缺省空串"投影，
  不解释语义（领域分类归宿主）。行形状坏（非映射 / 缺非空 ``name``）**fail loud**，
  不外泄成 500 之外的怪响应。
- **附加路由的失败语义是"响亮失败"**：handler 抛异常 ⇒ ``500`` + 结构化错误，
  **不返回如实空表**。"读不到数据"与"确实没有数据"必须可区分——在传输层把两者
  抹平成空表，正是本仓反复在防的假绿。
- **``runtime_writer`` 只在 start / stop 各调一次**（``monitor_port=<端口>`` /
  ``monitor_port=None``）：不做周期性刷新，也不代管描述符的其它字段（``mode`` /
  ``dsh_port`` 归看门人，两方"合并写、互不抹掉"是宿主的实现责任）。
- **不持有端口文件名字面量**：``.mcp-port`` 一类名字是宿主与其消费方的发现约定，
  库内不写死（D-7 纪律）。调用方给路径。
"""

from __future__ import annotations

import http.server
import json
import os
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import parse_qs, urlsplit

from .errors import MechaError, ToolFailure
from .monitor import Claim
from .portfile import clear_port_file, write_port_file
from .roles import Disposer

__all__ = [
    "CORE_ROUTES",
    "SET_KIND",
    "MonitorSource",
    "MonitorEndpoint",
    "activity_records",
    "config_tree",
    "history_records",
    "monitor_summary_payload",
    "start_monitor_endpoint",
]

#: 史只有 set 语义（无删除事件）⇒ ``kind`` 恒此值（两家的实现都硬编码它）。
SET_KIND = "set"

#: 核心路由（精确匹配；附加路由不得与之重名）。
CORE_ROUTES = ("/status", "/activity", "/history", "/config")

#: 事件投影必须提供 `since_seq` 时的列名。
_SINCE_KEY = "since_seq"

#: 附加路由默认拒绝的路径特征（一切写入口都不存在）。
_JSON_CONTENT_TYPE = "application/json"


def _mode_token(value: object) -> str:
    """把 mode 投影成 wire 字符串：``Mode`` 取 ``.value``，裸字符串原样。

    ``Mode`` 是 ``str`` 枚举 ⇒ 直接 ``json.dumps`` 也能得到同样的字节，但显式取
    ``.value`` 让"口径是 value 不是 repr"成为代码事实（``str(Mode.AI)`` 在不同
    Python 版本下是 ``'Mode.AI'``，靠 ``json`` 的实现细节过线太脆）。
    """
    return str(getattr(value, "value", value))


def _since_seq(query: Mapping[str, list[str]]) -> int:
    """取 ``since_seq``：缺失 / 非纯数字 ⇒ ``0``（不报错、不猜）。"""
    raw = (query.get(_SINCE_KEY) or ["0"])[0]
    return int(raw) if raw.isdigit() else 0


def _row_name(row: object) -> str:
    """schema 行的键名（必需字段）；形状坏即 fail loud（教学错误）。"""
    if not isinstance(row, Mapping):
        raise MechaError(
            f"schema 行不是映射，收到 {type(row).__name__}",
            kind="bad_schema_row",
            hint="每行是含非空 name 的映射；可选键 family / unit / type / desc",
        )
    name = row.get("name")
    if not isinstance(name, str) or not name.strip():
        raise MechaError(
            f"schema 行缺非空 name：{row!r}",
            kind="bad_schema_row",
            hint="行必须有非空字符串 name（键全名），否则无法与快照 join",
        )
    return name


def activity_records(source, since_seq: int = 0) -> list[dict]:
    """轻量增量事件流：6 键（``kind`` / ``actor`` / ``target`` / ``value`` /
    ``seq`` / ``call_id``），按史序升序，只含 ``seq >= since_seq``。

    与 ``history_records`` 的区别：不带 ``before`` / ``after`` / ``reason``
    ——面板的"最近动作"列表只需知道谁改了什么、改成什么。
    """
    return [{"kind": SET_KIND, "actor": e.actor, "target": e.target,
             "value": e.after, "seq": e.seq, "call_id": e.call_id}
            for e in source.events() if e.seq >= since_seq]


def history_records(source, since_seq: int = 0) -> list[dict]:
    """飞行记录仪事件：按史序升序，只含 ``seq >= since_seq``。

    ``before`` 由**全史 fold 重建**（同键上一条事件的值，首写 ``None``）。
    ⚠ fold 覆盖全史、过滤只作用于 emit：两者写在一个循环里但**顺序不可换**
    （先算 before / 更新 last，再判 seq）——见模块 docstring 的静默错警告。
    """
    last: dict = {}
    out: list[dict] = []
    for event in source.events():
        before = last.get(event.target)
        last[event.target] = event.after
        if event.seq >= since_seq:
            out.append({"seq": event.seq, "kind": SET_KIND, "actor": event.actor,
                        "op": event.op,
                        "target": event.target, "after": event.after,
                        "before": before, "reason": event.reason,
                        "call_id": event.call_id, "ts": event.ts})
    return out


def config_tree(source, *, schema_rows=None) -> dict:
    """配置态结构树 ``{has_schema, groups, orphans, n_keys, n_snapshot_keys}``。

    族 → 前缀二级树：族取行的 ``family``（缺省 ``"other"``），前缀取键名第一个
    ``.`` 之前的部分（键名不含 ``.`` ⇒ 前缀 ``""``，面板渲染为 ``(top)``）。
    行内的 ``value`` 取自**当前快照**、``set`` 取自**全史最后一次写**的归因。

    ``schema_rows`` 给了就用它，否则用 ``source.schema_rows()``；两者都空 ⇒
    走"无 schema"分支（``has_schema=false``、``groups={}``、快照全部进
    ``orphans``）——那条分支是**可观测契约**（面板有专门文案），不是降级。

    ``n_snapshot_keys`` 恒回：快照键数（``n_keys`` 是 **schema 行数**，两者之差
    正是人最该看见的信号——"schema 说这么多键，实际只设了这么几个"）。

    ⚠ **审计键（``command.*``）不属于配置面**（2026-09-26 起）：命令审计走
    ``Gate.record``（**只记史、不改快照**）⇒ 它**不在快照里**，因此**不会**出现在
    ``orphans``（此前它借 ``gate.set`` 落史，于是混进快照、在这里被当"契约外的键"）。
    ⇒ 这也是 ``n_snapshot_keys`` 的一个隐含语义：**它只数域键**，不含审计。
    """
    snapshot = dict(source.snapshot())
    last: dict = {}
    for event in source.events():
        last[event.target] = {"reason": event.reason, "actor": event.actor,
                           "seq": event.seq}
    rows = list(schema_rows if schema_rows is not None else source.schema_rows())
    groups: dict = {}
    names: list[str] = []
    for row in rows:
        name = _row_name(row)
        names.append(name)
        family = str(row.get("family") or "other")
        head, _, rest = name.partition(".")
        prefix = head if rest else ""
        node = {"name": name, "short": rest if rest else name, "prefix": prefix,
                "value": snapshot.get(name), "unit": row.get("unit") or "",
                "type": row.get("type") or "", "desc": row.get("desc") or "",
                "set": last.get(name)}
        groups.setdefault(family, {}).setdefault(prefix, []).append(node)
    out_groups = {family: [{"prefix": p, "rows": byp}
                           for p, byp in sorted(sub.items())]
                  for family, sub in sorted(groups.items())}
    known = set(names)
    orphans = sorted(({"name": k, "value": v, "set": last.get(k)}
                      for k, v in snapshot.items() if k not in known),
                     key=lambda row: row["name"])
    return {"has_schema": bool(rows), "groups": out_groups, "orphans": orphans,
            "n_keys": len(rows), "n_snapshot_keys": len(snapshot)}


def monitor_summary_payload(view) -> dict:
    """把 ``MonitorView`` 投影成 wire 形状
    ``{summary, raw, summary_error, disputed, dispute_reason}``。

    ``summary`` 里的 ``Claim`` 必须转成 ``{"value", "target"}``：直接丢进
    ``json.dumps`` 会被 ``default=str`` 变成 ``"Claim(value=…)"`` 字符串，
    "它复述的是哪个原始键"就此丢失，对账无从谈起。标量元信息（计数之类）原样。

    ``disputed`` / ``dispute_reason`` 原样透出：概括与原始不一致时**原始赢**、
    概括显示存疑（两层信任的纪律）。

    ⚠ **`raw`（原始层）也上 wire**（2026-09-26 加）：那条纪律是"**两层并列**、原始赢"，
    而此前投影**只有概括层** ⇒ 概括层故障时整个读数都不在。补上原始层后，"故障"表现为
    `summary={}` + `disputed=True` + `summary_error` 明文，而**原始层照常可读**。
    ⚠ **不是新的披露面**：`/history` 早已把**全史事件（含 value）**放上 wire，
    而 `raw` 是那份史的 **fold**（每键只留最后一次）⇒ 信息量更小。
    """
    summary: dict = {}
    for key, value in dict(view.summary).items():
        if isinstance(value, Claim):
            summary[key] = {"value": value.value, "target": value.target}
        else:
            summary[key] = value
    return {"summary": summary, "raw": dict(view.raw_snapshot),
            "summary_error": str(getattr(view, "summary_error", "") or ""),
            "disputed": bool(view.disputed),
            "dispute_reason": str(view.dispute_reason or "")}


@runtime_checkable
class MonitorSource(Protocol):
    """cockpit 的数据 seam：五个成员，鸭子类型（不要求继承本类）。

    这是**参考形状**，不是准入检查：端点只按成员名取数，任何对象只要有这五个
    方法就能用。``from_software`` 是给 ``Software`` 形态的一行适配。
    """

    def events(self) -> Sequence[object]:
        """全史事件（升序），每项需有 ``seq`` / ``key`` / ``value`` / ``actor`` /
        ``reason`` / ``call_id``。"""

    def snapshot(self) -> Mapping[str, object]:
        """当前权威快照（只读投影）。"""

    def mode(self) -> object:
        """当前写权模式（``Mode`` 或裸字符串，投影时取 ``.value``）。"""

    def schema_rows(self) -> Sequence[Mapping[str, object]]:
        """配置 schema 行；没有这层的项目返回空序列（走"无 schema"分支）。"""

    def status_extra(self) -> Mapping[str, object]:
        """``/status`` 的附加键（宿主生命周期事实）；默认返回空映射。"""

    @classmethod
    def from_software(cls, software, *, schema_rows=None,
                      status_extra=None) -> "MonitorSource":
        """从 ``Software`` 形态适配：前三项现取，后两项由项目给（可省）。

        只按**成员名**识别（``history`` / ``gate`` / ``authority``），不 import
        ``assembly``——传输层不该依赖装配点，判据也不必为此起整台软件。
        """
        return _SoftwareSource(
            software.history.events,
            lambda: software.gate.snapshot,
            lambda: software.authority.mode,
            schema_rows if schema_rows is not None else (lambda: ()),
            status_extra if status_extra is not None else (lambda: {}),
        )


class _SoftwareSource:
    """``MonitorSource`` 的 ``Software`` 适配实现（五项全是薄转调）。"""

    def __init__(self, events, snapshot, mode, schema_rows, status_extra) -> None:
        self._events = events
        self._snapshot = snapshot
        self._mode = mode
        self._schema_rows = schema_rows
        self._status_extra = status_extra

    def events(self):
        return self._events()

    def snapshot(self):
        return self._snapshot()

    def mode(self):
        return self._mode()

    def schema_rows(self):
        return self._schema_rows()

    def status_extra(self):
        return self._status_extra()


#: 鸭子类型判定用的成员名（与 Protocol 一一对应）。
_SOURCE_MEMBERS = ("events", "snapshot", "mode", "schema_rows", "status_extra")

#: ``Software`` 形态的识别特征（只认这三个公开成员）。
_SOFTWARE_MEMBERS = ("history", "gate", "authority")


def _coerce_source(source, schema_rows, status_extra):
    """把首参归一化成 source：五成员对象直接用；``Software`` 形态自动适配。

    两种形态都认，是因为调用方真正想写的是"把端点接到这台软件上"，逼它先记一个
    ``from_software`` 只是仪式；而**第三种对象**（既没有五成员、也不是 Software
    形态）必须**响亮报错**——静默降级成空史/空快照会做出一个"永远健康但什么都
    没有"的面板，那是最坏的一类假绿。
    """
    has_members = all(callable(getattr(source, m, None)) for m in _SOURCE_MEMBERS)
    if has_members:
        if schema_rows is not None or status_extra is not None:
            raise MechaError(
                "source 已经是 MonitorSource 形状，schema_rows / status_extra "
                "不能再单独注入",
                kind="bad_monitor_source",
                hint="这两项属于 source 自己；要换就换一个 source",
                suggest="MonitorSource.from_software(sw, schema_rows=…, status_extra=…)",
            )
        return source
    looks_like_software = all(hasattr(source, m) for m in _SOFTWARE_MEMBERS)
    if looks_like_software:
        return MonitorSource.from_software(source, schema_rows=schema_rows,
                                          status_extra=status_extra)
    missing = [m for m in _SOURCE_MEMBERS
               if not callable(getattr(source, m, None))]
    raise MechaError(
        f"不是可用的监控数据源（{type(source).__name__}）：缺 {missing}",
        kind="bad_monitor_source",
        hint="要么给一个含这五个成员的对象，要么给 Software 形态"
             "（有 history / gate / authority）",
        suggest="MonitorSource.from_software(sw, schema_rows=…, status_extra=…)",
    )


def _route_error(exc: BaseException) -> dict:
    """任何宿主侧取数 / 路由失败 → 结构化错误体（形状与跨边界 ToolFailure 同源）。

    **不吞、不降级成空表**：把"读不到"变成"没有数据"是静默错，本仓反复在防。
    错误体用 ``MechaError.to_failure()`` 的 ``{message, info:{kind,…}}`` 形状——
    与工具面、命令面同一份，模型/人读 ``kind`` 决定怎么改。
    """
    if isinstance(exc, MechaError):
        error = exc.to_failure()
    else:
        error = ToolFailure(
            f"监控端点内部错误：{type(exc).__name__}: {exc}",
            kind="monitor_internal_error",
            hint="宿主侧取数或附加路由抛错；回执已结构化，栈见宿主日志",
        ).to_failure()
    return {"ok": False, "error": error}


class _Handler(http.server.BaseHTTPRequestHandler):
    """只读路由的处理器基类。

    ``endpoint`` 由 :meth:`MonitorEndpoint.start` 绑到**按实例派生**的子类上
    （不是类属性共享全局）：同一进程起两个端点时，类属性会互相覆盖——那是实测
    过的隐患，不是理论风险。
    """

    endpoint = None

    def log_message(self, *args) -> None:            # noqa: N802 - 基类约定
        """静音：访问日志不给控制台刷屏（面板每拍都打会淹掉真输出）。"""

    def _json(self, code: int, obj: object, *, close: bool = False) -> None:
        """统一响应：JSON + CORS + 精确 Content-Length（所有状态码都带 CORS）。

        ``close=True`` 时额外发 ``Connection: close``——**只在请求体读不干净时**
        用（见 :meth:`_drain_request_body`）；正常路径不加这个头，契约一字不变。
        """
        data = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", _JSON_CONTENT_TYPE)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        if close:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self) -> None:                    # noqa: N802 - 基类约定
        """预检放行：面板（dsh 页在别的端口）轮询本端点是跨源请求。

        只读 + 绑回环 ⇒ 允许任意源不构成风险面；无 body、无 Content-Length。
        """
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:                        # noqa: N802 - 基类约定
        """纯路径精确匹配；查询串交给 ``parse_qs``（做 URL 解码）。"""
        parts = urlsplit(self.path)
        endpoint = type(self).endpoint
        try:
            payload, code = endpoint._dispatch(parts.path, parse_qs(parts.query))
        except Exception as exc:                     # noqa: BLE001 - 传输边界
            payload, code = _route_error(exc), 500
        self._json(code, payload)

    #: drain 请求体的上界：超过就不读完，改为要求关连接（不为 drain 无限期地读）。
    MAX_DRAIN_BYTES = 1 << 20

    def _drain_request_body(self) -> bool:
        """把请求体读干净再回响应；返回"连接是否还能复用"。

        为什么必须：请求体没读干净就关连接时，Windows 内核回 RST，客户端在读**状态
        行之前**就被打断（``ConnectionAbortedError`` / ``WinError 10053``）。这是
        既有缺陷（迁移前的老实现同病，EL 用 ``git show`` 取旧实现做过决定性实验）；
        代价不是用户可见故障——真实面板只发 GET——而是**两个项目的门禁偶发假红**
        （本仓实测未修时 40 轮里命中 0–13 次）。

        三分（照 HTTP 语义，不猜）：

        - 合法 ``Content-Length`` 且在界内 ⇒ 读净丢弃，连接可复用（不加任何头）；
        - ``Content-Length`` 缺失 / 非法 / 超上界 ⇒ **不硬读**，要求关连接
          （调用方据此发 ``Connection: close``）——最接近"不读就关"的诚实处置。

        已知残余：声明了 ``Content-Length`` 却永远不发 body 的客户端会让这个请求
        线程阻塞在读取上（``ThreadingHTTPServer`` 一连接一线程，不牵连其它连接）；
        不为它加读超时——那是新机制，今天没有消费者。
        """
        raw = self.headers.get("Content-Length")
        text = "" if raw is None else str(raw).strip()
        if not text.isdigit():
            self.close_connection = True
            return False
        remaining = int(text)
        if remaining <= 0:
            return True
        if remaining > self.MAX_DRAIN_BYTES:
            self.close_connection = True
            return False
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 65536))
            if not chunk:
                break
            remaining -= len(chunk)
        if remaining:
            self.close_connection = True
            return False
        return True

    def do_POST(self) -> None:                       # noqa: N802 - 基类约定
        """只读红线：一切写 / 切模式 POST 恒 404（控制权归看门人的进程交接）。

        ⚠ 回响应前**必须先把请求体读干净**（:meth:`_drain_request_body`）。读不干净
        时只多加 ``Connection: close``——**状态码与 body 一字不变**。
        """
        drained = self._drain_request_body()
        self._json(404, {"ok": False, "error": "read-only monitor endpoint"},
                   close=not drained)


class MonitorEndpoint:
    """只读监控端点的生命周期与投影（起线程 / 干净停 / 写发现文件）。

    ``ThreadingHTTPServer`` 的构造即 bind+listen ⇒ **同步 bind**，起完就已经在
    listen，没有"起来了但还没 listen"的竞态，不需要探活。

    附加路由用 :meth:`add_route`（可逆，热加删立即生效）或构造参数
    ``extra_routes``；两者共用同一道校验（重名即拒）。
    """

    def __init__(self, source, *, host: str = "127.0.0.1", port: int = 0,
                 port_file: str | Path | None = None,
                 runtime_writer: Callable[..., Mapping[str, object]] | None = None,
                 schema_rows=None, status_extra=None,
                 extra_routes: Mapping[str, Callable] | None = None,
                 log: Callable[[str], None] | None = None) -> None:
        self._source = _coerce_source(source, schema_rows, status_extra)
        self._host = str(host)
        self._want_port = int(port)
        self._port_file = Path(port_file) if port_file is not None else None
        self._runtime_writer = runtime_writer
        self._log = log or (lambda _m: None)
        self._routes: dict[str, Callable] = {}
        self._server: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._port: int | None = None
        for path, handler in dict(extra_routes or {}).items():
            self.add_route(path, handler)

    # ---- 事实面 ----

    @property
    def source(self):
        """被投影的数据源（只读用途）。"""
        return self._source

    @property
    def port(self) -> int | None:
        return self._port

    @property
    def url(self) -> str:
        """``""`` 或 ``http://host:port``（未启动即空串，调用方不必自己拼）。"""
        return "" if self._port is None else "http://%s:%d" % (self._host, self._port)

    # ---- 附加路由 ----

    def add_route(self, path: str, handler: Callable) -> Disposer:
        """注册一条**只读**附加路由；返回撤销该路由的 disposer。

        ``handler(query: Mapping[str, list[str]]) -> Mapping``，返回体由本模块统一
        包成 ``{"ok": true, **返回体}`` 并走同一套序列化与响应头（项目不自己写
        ``ok``）。核心路由与已注册路径**不可覆盖**（重名 fail loud——静默覆盖会让
        两条同名路由悄悄争一个位置）。注册 / 撤销在运行中立即生效。

        失败语义是**响亮失败**：handler 抛异常 ⇒ ``500`` + 结构化错误体，
        不返回空表（"读不到"与"确实没有"必须可区分）。
        """
        if not isinstance(path, str) or not path.startswith("/") or len(path) < 2:
            raise MechaError(
                f"附加路由路径 {path!r} 不合法",
                kind="bad_route",
                hint="必须是以 '/' 开头的绝对路径（如 '/summary'）",
            )
        if path in CORE_ROUTES:
            raise MechaError(
                f"附加路由 {path!r} 与核心路由重名",
                kind="duplicate_route",
                hint=f"核心路由不可覆盖：{list(CORE_ROUTES)}",
            )
        if path in self._routes:
            raise MechaError(
                f"附加路由 {path!r} 已注册",
                kind="duplicate_route",
                hint="同名先 dispose 再 register",
            )
        if not callable(handler):
            raise MechaError(
                f"附加路由 {path!r} 的处理函数不可调用（{type(handler).__name__}）",
                kind="bad_route",
                hint="handler 接收 query（parse_qs 的映射）并返回可 JSON 化的映射",
            )
        self._routes[path] = handler

        def dispose() -> None:
            self._routes.pop(path, None)

        return dispose

    def routes(self) -> list[str]:
        """已注册的附加路由（排序）。"""
        return sorted(self._routes)

    # ---- 分派与投影（处理器的取数口） ----

    def _dispatch(self, path: str, query: Mapping[str, list[str]]
                  ) -> tuple[dict, int]:
        """纯路径 → (响应体, 状态码)。抛出的异常由处理器归一化成 500。"""
        if path == "/status":
            return self._status(), 200
        if path == "/activity":
            return {"ok": True,
                    "events": activity_records(self._source, _since_seq(query)),
                    "mode": _mode_token(self._source.mode())}, 200
        if path == "/history":
            return {"ok": True,
                    "events": history_records(self._source, _since_seq(query)),
                    "mode": _mode_token(self._source.mode())}, 200
        if path == "/config":
            return {"ok": True, **config_tree(self._source)}, 200
        handler = self._routes.get(path)
        if handler is not None:
            payload = handler(dict(query))
            if not isinstance(payload, Mapping):
                raise MechaError(
                    f"附加路由 {path!r} 返回 {type(payload).__name__}，不是映射",
                    kind="bad_route_payload",
                    hint="handler 必须返回映射；框架会包成 {ok: true, **返回体}",
                )
            return {"ok": True, **dict(payload)}, 200
        return {"ok": False, "error": "unknown endpoint"}, 404

    def _status(self) -> dict:
        """``{ok, mode}`` + ``status_extra`` 的附加键（宿主生命周期事实）。"""
        payload: dict = {"ok": True, "mode": _mode_token(self._source.mode())}
        extra = self._source.status_extra()
        if extra:
            if not isinstance(extra, Mapping):
                raise MechaError(
                    f"status_extra 返回 {type(extra).__name__}，不是映射",
                    kind="bad_status_extra",
                    hint="status_extra() 必须返回映射（可空）",
                )
            payload.update(dict(extra))
        return payload

    # ---- 生命周期 ----

    def start(self) -> int:
        """起端点并返回**实际**端口（``port=0`` 时由 OS 分配后回读）。

        顺序：清陈旧端口文件 → bind → 写端口文件 → 调 ``runtime_writer``。
        发现文件先清后写是**规则**而非习惯（Windows 强杀不跑 ``finally``，
        见 ``portfile`` 的安全论证）；``runtime_writer`` 在 bind 成功之后才调，
        不让描述符里出现一个从没 listen 过的端口。
        """
        if self._server is not None:
            raise MechaError(
                "监控端点已启动",
                kind="monitor_already_started",
                hint="一个 MonitorEndpoint 只 start 一次；要重起先 stop()",
            )
        clear_port_file(self._port_file)
        handler = type("_BoundMonitorHandler", (_Handler,), {"endpoint": self})
        server = http.server.ThreadingHTTPServer((self._host, self._want_port),
                                                 handler)
        self._port = int(server.server_address[1])
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True,
                                        name="mecha-cockpit")
        self._thread.start()
        write_port_file(self._port_file, self._port)
        if self._runtime_writer is not None:
            self._runtime_writer(monitor_port=self._port)
        self._log("[mecha-cockpit] %s（只读：%s）"
                  % (self.url, " ".join(CORE_ROUTES)))
        return self._port

    def stop(self, timeout: float = 10.0) -> None:
        """干净退出（幂等）：shutdown → server_close → join → 清发现文件。

        端口文件按"内容仍是自己写的端口"才删——交接窗口里下一台权威可能已经写了
        新端口，无条件删会把它的发现文件抹掉。
        """
        server, self._server = self._server, None
        if server is not None:
            try:
                server.shutdown()
            except Exception:                        # noqa: BLE001 - 已在收尾
                pass
            try:
                server.server_close()
            except Exception:                        # noqa: BLE001 - 同上
                pass
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(max(0.1, float(timeout)))
        clear_port_file(self._port_file, self._port)
        if self._runtime_writer is not None:
            self._runtime_writer(monitor_port=None)
        self._port = None

    def __enter__(self) -> "MonitorEndpoint":
        self.start()
        return self

    def __exit__(self, *exc) -> bool:
        self.stop()
        return False


def start_monitor_endpoint(source, *, host: str = "127.0.0.1", port: int = 0,
                           port_file: str | Path | None = None,
                           runtime_writer=None, schema_rows=None,
                           status_extra=None, extra_routes=None,
                           log=None) -> MonitorEndpoint:
    """便利函数：起一个**已 start** 的 :class:`MonitorEndpoint`。

    返回端点是刻意的（不是裸端口 int）：面板/判据需要 ``.port`` 与 ``.url``，
    而生命周期（``stop``）也必须有人握着——只回 int 会让调用方无处关停。
    """
    endpoint = MonitorEndpoint(source, host=host, port=port, port_file=port_file,
                               runtime_writer=runtime_writer,
                               schema_rows=schema_rows,
                               status_extra=status_extra,
                               extra_routes=extra_routes, log=log)
    endpoint.start()
    return endpoint
