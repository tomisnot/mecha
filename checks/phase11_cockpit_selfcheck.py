"""Phase 11 判据：cockpit 只读监控端点 + portfile（L6 装配级 + L7 对偶）。

钉住冻结契约（ADR 两层传输上提），八组：

  A 投影正确性：真 ``assemble()`` 装配的 ``Software`` 上跑四条投影的字段与排序
  B ``before`` 全史 fold：**致死性**——增量窗口内无前值、全史有前值时必须仍给出
    前值（写成"只 fold 窗口"会静默错：面板展开行显示 ``∅ → 新值``）
  C HTTP 契约：真起端点（``port=0``），逐条对齐路由 / 状态码 / 头 / 字段集
  D 附加路由：成功 / **响亮失败**（500，不是空表）/ 重名拒 / 可逆
  E 生命周期：两实例并存互不覆盖（类属性共享全局的隐患）、端口文件两条清理语义、
    ``runtime_writer`` 的调用时机
  F ``portfile``：三条语义（读 / 写 / 清），含 ``expected`` 不匹配则不删
  G 概括投影：``Claim`` → ``{"value", "target"}``（否则对账信息在 wire 上丢失）
  H **POST 请求体 drain**（既有缺陷修复）：带 body 的 POST 若不读干净就关连接，
    Windows 内核回 RST ⇒ 客户端在读状态行前被打断（``ConnectionAbortedError`` /
    ``WinError 10053``）。本组钉三件：①**决定性**判别子——公告的 body 未发完时
    服务端不得提前回响应（不修的实现必然在这里红）；②压测式——带 body POST 多轮，
    零中断、每轮干净 404；③``_drain_request_body`` 的三分规则 + "不许误报"对偶
    （正常路径不多加 ``Connection`` 头、GET/OPTIONS 契约不变）

L7 纪律：每组都配"不许误报"的对偶断言（合规输入必须 0 命中）。

用法：python checks/phase11_cockpit_selfcheck.py
退出码：0 全绿；1 有失败。

## Known Limitations and Deferred Work

- 不起真浏览器、不跑真面板：面板兼容性由"响应逐字段断言"覆盖，"面板真渲染得出来"
  属活体验收（人看），机械不可判。
- **不做跨实现字节对拍**：两家现有实现将被薄壳替换，本判据钉的是**冻结契约**本身；
  新旧实现同进程双起逐字段 diff 属迁移期的一次性验收（规格 §8 的建议），不在本仓。
- 不测非回环绑定与鉴权缺失的**安全性**（只读 + 回环是设计前提，不是被测属性）；
  也不测"AI 不能自授权"——那属 ``authority`` 的判据面。
- H 组读了模块私有面 ``_Handler``（要直接用假对象测 ``_drain_request_body`` 的三分，
  并要绕过 ``urllib`` 直发"公告大 body 只发一半"的请求）。RST 是**概率性**的，
  所以 H 组不只靠压测：决定性判别子在任何一次运行里都能判红未修的实现。
- **残余面（未覆盖，已登记）**：``do_GET`` / ``do_OPTIONS`` 带 body、以及
  ``BaseHTTPRequestHandler`` 对未实现方法回的 501，同样"不 drain 就关"。
  POST 是唯一有真实消费者的路径（面板只发 GET、门禁只发 POST），故不为其加机制。
"""

from __future__ import annotations

import http.client
import json
import socket
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mecha.assembly import assemble  # noqa: E402
from mecha.authority import Mode  # noqa: E402
from mecha.cockpit import (  # noqa: E402
    CORE_ROUTES, MonitorEndpoint, MonitorSource, activity_records, config_tree,
    history_records, monitor_summary_payload, start_monitor_endpoint,
)
from mecha.cockpit import _Handler  # noqa: E402  (判据刻意读私有面，见 Limitations)
from mecha.errors import MechaError  # noqa: E402
from mecha.gate import accept_all  # noqa: E402
from mecha.monitor import Claim, MonitorView  # noqa: E402
from mecha.portfile import clear_port_file, read_port, write_port_file  # noqa: E402

#: 压测轮数。单次跑抓不到这个缺陷（本仓实测未修时 40 轮里命中 0–13 次，抖动大），
#: 所以既压测**又**配一条决定性判别子（见 check_post_body_drain）。
POST_ROUNDS = 40

#: 压测 body 体积（够大才留得下未读数据 ⇒ 才可能触发 RST）。
POST_BODY_BYTES = 256 * 1024

#: 投影字段集（契约的一部分：多一个少一个都是漂移）。
ACTIVITY_KEYS = {"kind", "actor", "target", "value", "seq", "call_id"}
HISTORY_KEYS = {"seq", "kind", "actor", "target", "value", "before", "after",
                "reason", "call_id"}
ROW_KEYS = {"name", "short", "prefix", "value", "unit", "type", "desc", "set"}
ORPHAN_KEYS = {"name", "value", "set"}


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _fail(out: list[str], msg: str) -> None:
    print(f"  ✗ {msg}")
    out.append(msg)


class _Toy:
    """最小引擎（本判据不测领域，只测传输）。"""

    def schema(self):
        return {"run_output_required": ["ok"]}

    def run(self, spec, *, context, **opts):
        return {"ok": True}

    def summary(self):
        return {"ok": True}

    def estimate(self, spec):
        return {"est_sec": 1.0}


def _software(tmp: Path, *, mode: Mode = Mode.AI, summarizer=None):
    return assemble(root=tmp, data_dir_name=".cockpit-check", engine=_Toy(),
                    summarizer=summarizer or (lambda evs: {}),
                    validate=accept_all, mode=mode, project="cockpit-check")


def _write(sw, key: str, value, reason: str = "r") -> None:
    sw.gate.set(sw.channels["ai"], key, value, reason)


def _raises(fn, kind: str) -> bool:
    """断言某次调用以指定 kind 的 MechaError 失败（fail loud 的对偶）。"""
    try:
        fn()
    except MechaError as e:
        return e.kind == kind
    return False


def _request(url: str, method: str = "GET") -> tuple[int, dict, str]:
    """发一次请求，返回 (状态码, 头, 正文文本)。4xx 也照常返回（不当异常）。"""
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, dict(resp.headers), resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8")


# --------------------------------------------------------------------------
# A 投影正确性
# --------------------------------------------------------------------------

def check_projection(out: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="cockpit-a-") as td:
        with _software(Path(td)) as sw:
            _write(sw, "alpha", 1, "first")
            _write(sw, "beta.gamma", 2, "second")
            _write(sw, "alpha", 3, "third")
            source = MonitorSource.from_software(sw)

            # 无 schema 分支（可观测契约，不是降级）
            tree = config_tree(source)
            if (tree["has_schema"] is False and tree["groups"] == {}
                    and tree["n_keys"] == 0 and tree["n_snapshot_keys"] == 2):
                _ok("无 schema 分支：has_schema=False / groups={} / n_keys=0 / "
                    "n_snapshot_keys=快照键数")
            else:
                _fail(out, f"无 schema 分支形状错：{tree}")
            names = [o["name"] for o in tree["orphans"]]
            if names == ["alpha", "beta.gamma"]:
                _ok("orphans 覆盖快照全部键且按 name 升序")
            else:
                _fail(out, f"orphans 集合/排序错：{names}")
            alpha = [o for o in tree["orphans"] if o["name"] == "alpha"][0]
            if set(alpha) == ORPHAN_KEYS and alpha["value"] == 3 \
                    and alpha["set"]["seq"] == 3:
                _ok("orphans 行字段与'当前值 + 最后写入归因'口径正确")
            else:
                _fail(out, f"orphans 行错：{alpha}")

            # 有 schema：族 → 前缀二级树 + 行字段 + 现值 join
            rows = [{"name": "alpha", "family": "core", "unit": "u",
                     "type": "int", "desc": "d"},
                    {"name": "beta.gamma"},
                    {"name": "top"}]
            tree2 = config_tree(source, schema_rows=rows)
            if tree2["has_schema"] and tree2["n_keys"] == 3 \
                    and tree2["n_snapshot_keys"] == 2:
                _ok("有 schema 分支：n_keys=schema 行数、n_snapshot_keys=快照键数")
            else:
                _fail(out, f"有 schema 分支计数错：{tree2}")
            prefixes = [(f, [g["prefix"] for g in gs])
                        for f, gs in tree2["groups"].items()]
            if prefixes == [("core", [""]), ("other", ["", "beta"])]:
                _ok("groups 族与前缀均字典序、二级树形状正确")
            else:
                _fail(out, f"groups 形状错：{prefixes}")
            row = tree2["groups"]["core"][0]["rows"][0]
            if set(row) == ROW_KEYS and row["short"] == "alpha" \
                    and row["prefix"] == "" and row["value"] == 3 \
                    and row["unit"] == "u" and row["desc"] == "d" \
                    and row["set"]["actor"] == "ai":
                _ok("rows 行字段齐全，现值取自快照、set 取自全史")
            else:
                _fail(out, f"rows 行错：{row}")
            gamma = tree2["groups"]["other"][1]["rows"][0]
            if gamma["short"] == "gamma" and gamma["prefix"] == "beta":
                _ok("含 '.' 的键正确切分 prefix/short")
            else:
                _fail(out, f"prefix/short 切分错：{gamma}")
            if tree2["orphans"] == []:
                _ok("schema 已声明的键不重复进 orphans")
            else:
                _fail(out, f"orphans 与 schema 重叠：{tree2['orphans']}")

            # 事件字段集（多/少一个都是漂移）
            act = activity_records(source)
            hist = history_records(source)
            if len(act) == 3 and all(set(e) == ACTIVITY_KEYS for e in act):
                _ok("/activity 事件恰好 6 键、全量、升序")
            else:
                _fail(out, f"/activity 事件字段错：{act[:1]}")
            if len(hist) == 3 and all(set(e) == HISTORY_KEYS for e in hist) \
                    and all(e["value"] == e["after"] for e in hist):
                _ok("/history 事件恰好 9 键、value == after")
            else:
                _fail(out, f"/history 事件字段错：{hist[:1]}")

            # 不许误报（L7）：坏 schema 行必须 fail loud，不静默跳过
            if _raises(lambda: config_tree(source, schema_rows=[{"nope": 1}]),
                       "bad_schema_row") and \
                    _raises(lambda: config_tree(source, schema_rows=["x"]),
                            "bad_schema_row"):
                _ok("坏 schema 行（缺 name / 非映射）fail loud（不静默丢行）")
            else:
                _fail(out, "坏 schema 行未被拒（静默丢行 = 假绿）")


# --------------------------------------------------------------------------
# B before 全史 fold（致死性）
# --------------------------------------------------------------------------

def check_before_folds_full_history(out: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="cockpit-b-") as td:
        with _software(Path(td)) as sw:
            _write(sw, "k", 1)
            _write(sw, "k", 2)
            _write(sw, "m", 9)
            source = MonitorSource.from_software(sw)
            full = history_records(source, 0)
            window = history_records(source, 2)

            if window == [e for e in full if e["seq"] >= 2]:
                _ok("增量请求 == 全量请求按 seq 过滤（fold 与过滤分离）")
            else:
                _fail(out, f"增量与全量不一致：{window} vs {full}")
            second = [e for e in window if e["seq"] == 2][0]
            if second["before"] == 1:
                _ok("⭐ 增量窗口内无前值、全史有前值 ⇒ before 仍为 1（致死性："
                    "写成'只 fold 窗口'会得 None）")
            else:
                _fail(out, f"before 未 fold 全史：seq=2 的 before={second['before']!r}"
                           f"（应为 1）——面板会显示 ∅ → 新值（静默错）")
            first = [e for e in full if e["seq"] == 1][0]
            if first["before"] is None:
                _ok("对偶（不许误报）：首写的 before 是 None，不是域默认值")
            else:
                _fail(out, f"首写 before 非 None：{first['before']!r}")


# --------------------------------------------------------------------------
# C HTTP 契约
# --------------------------------------------------------------------------

def check_http_contract(out: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="cockpit-c-") as td:
        with _software(Path(td)) as sw:
            _write(sw, "alpha", 1, "why")
            source = MonitorSource.from_software(sw)
            ep = start_monitor_endpoint(source, port=0)
            try:
                base = ep.url
                before_n = len(sw.history.events())

                code, headers, text = _request(base + "/status", "OPTIONS")
                if (code == 204 and text == ""
                        and headers.get("Access-Control-Allow-Origin") == "*"
                        and headers.get("Access-Control-Allow-Methods") == "GET, OPTIONS"
                        and headers.get("Access-Control-Allow-Headers") == "Content-Type"):
                    _ok("OPTIONS → 204 + 三个 CORS 头 + 无 body")
                else:
                    _fail(out, f"OPTIONS 错：{code} {headers} {text!r}")

                code, headers, text = _request(base + "/status")
                body = json.loads(text)
                if (code == 200 and body == {"ok": True, "mode": "ai"}
                        and headers.get("Content-Type") == "application/json"
                        and headers.get("Access-Control-Allow-Origin") == "*"):
                    _ok("/status → {ok, mode} 且默认不带附加键（与只回两键的宿主一致）")
                else:
                    _fail(out, f"/status 错：{code} {body} {headers.get('Content-Type')}")

                code, _, text = _request(base + "/activity")
                body = json.loads(text)
                if (code == 200 and set(body) == {"ok", "events", "mode"}
                        and body["mode"] == "ai"
                        and all(set(e) == ACTIVITY_KEYS for e in body["events"])):
                    _ok("/activity → {ok, events, mode}，事件 6 键")
                else:
                    _fail(out, f"/activity 错：{code} {body}")

                code, _, text = _request(base + "/history")
                body = json.loads(text)
                if (code == 200 and set(body) == {"ok", "events", "mode"}
                        and body["mode"] == "ai"
                        and all(set(e) == HISTORY_KEYS for e in body["events"])):
                    _ok("/history → {ok, events, mode}（mode 在场：面板写权徽标读它）")
                else:
                    _fail(out, f"/history 错：{code} {body}")

                code, _, text = _request(base + "/config")
                body = json.loads(text)
                if code == 200 and set(body) == {"ok", "has_schema", "groups",
                                                 "orphans", "n_keys",
                                                 "n_snapshot_keys"}:
                    _ok("/config → 六键（含恒回的 n_snapshot_keys）")
                else:
                    _fail(out, f"/config 错：{code} {body}")

                # 取纯路径：查询串不改变匹配结果
                code, _, text = _request(base + "/config?t=123")
                if code == 200 and json.loads(text)["ok"] is True:
                    _ok("带查询串的 /config 仍 200（先取纯路径再精确匹配）")
                else:
                    _fail(out, f"带查询串的 /config 错：{code} {text!r}")

                # 精确匹配（不许前缀命中）
                code, _, text = _request(base + "/historyfoo")
                if code == 404 and json.loads(text) == {"ok": False,
                                                        "error": "unknown endpoint"}:
                    _ok("/historyfoo → 404（精确匹配；前缀命中是缺陷不是特性）")
                else:
                    _fail(out, f"/historyfoo 错：{code} {text!r}")

                code, headers, text = _request(base + "/nope")
                if (code == 404
                        and json.loads(text) == {"ok": False,
                                                 "error": "unknown endpoint"}
                        and headers.get("Access-Control-Allow-Origin") == "*"):
                    _ok("未知路径 → 404 逐字 body + CORS（所有响应都带 CORS）")
                else:
                    _fail(out, f"未知路径错：{code} {text!r}")

                code, _, text = _request(base + "/status", "POST")
                if code == 404 and json.loads(text) == {
                        "ok": False, "error": "read-only monitor endpoint"}:
                    _ok("POST → 404 逐字 body（只读红线）")
                else:
                    _fail(out, f"POST 错：{code} {text!r}")
                if len(sw.history.events()) == before_n:
                    _ok("POST 不产生任何写 / 史事件（对偶：只读不是口号）")
                else:
                    _fail(out, "POST 改了史——只读红线破了")

                if all(p in CORE_ROUTES for p in ("/status", "/activity",
                                                  "/history", "/config")):
                    _ok("核心路由表与冻结契约一致")
            finally:
                ep.stop()


# --------------------------------------------------------------------------
# D 附加路由
# --------------------------------------------------------------------------

def check_extra_routes(out: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="cockpit-d-") as td:
        with _software(Path(td)) as sw:
            source = MonitorSource.from_software(sw)
            ep = start_monitor_endpoint(
                source, port=0,
                extra_routes={"/summary": lambda q: {"summary": {"n": 1},
                                                     "disputed": False,
                                                     "dispute_reason": ""}})
            try:
                code, _, text = _request(ep.url + "/summary")
                body = json.loads(text)
                if code == 200 and body["ok"] is True and body["summary"] == {"n": 1}:
                    _ok("构造参数 extra_routes 注册的附加路由 → {ok:true, **返回体}")
                else:
                    _fail(out, f"附加路由错：{code} {body}")

                # 响亮失败：抛异常 ⇒ 500 + 结构化错误，**不是**如实空表
                def _boom(query):
                    raise RuntimeError("宿主炸了")

                ep.add_route("/boom", _boom)
                code, _, text = _request(ep.url + "/boom")
                body = json.loads(text)
                if (code == 500 and body["ok"] is False
                        and set(body["error"]) == {"message", "info"}
                        and body["error"]["info"]["kind"] == "monitor_internal_error"):
                    _ok("⭐ 附加路由抛异常 → 500 + 结构化错误（不返回空表："
                        "'读不到'与'确实没有'必须可区分）")
                else:
                    _fail(out, f"附加路由失败语义错：{code} {body}")

                ep.add_route("/bad", lambda q: "not-a-mapping")
                code, _, text = _request(ep.url + "/bad")
                if code == 500 and json.loads(text)["error"]["info"]["kind"] \
                        == "bad_route_payload":
                    _ok("附加路由返回非映射 → 500 bad_route_payload")
                else:
                    _fail(out, f"非映射返回未拒：{code} {text!r}")

                # 重名 / 坏路径 fail loud（不静默覆盖）
                if _raises(lambda: ep.add_route("/status", lambda q: {}),
                           "duplicate_route") and \
                        _raises(lambda: ep.add_route("/summary", lambda q: {}),
                                "duplicate_route") and \
                        _raises(lambda: ep.add_route("summary", lambda q: {}),
                                "bad_route"):
                    _ok("核心路由不可覆盖 / 同名不重复注册 / 坏路径 fail loud")
                else:
                    _fail(out, "附加路由重名或坏路径未被拒")

                # 可逆
                dispose = ep.add_route("/gone", lambda q: {"x": 1})
                if _request(ep.url + "/gone")[0] == 200:
                    _ok("新注册路由立即生效")
                else:
                    _fail(out, "新注册路由未生效")
                dispose()
                code, _, text = _request(ep.url + "/gone")
                if code == 404:
                    _ok("disposer 撤销后 → 404（注册是可逆副作用）")
                else:
                    _fail(out, f"disposer 未撤销：{code} {text!r}")
            finally:
                ep.stop()


# --------------------------------------------------------------------------
# E 生命周期与两实例隔离
# --------------------------------------------------------------------------

def check_lifecycle(out: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="cockpit-e1-") as td1, \
            tempfile.TemporaryDirectory(prefix="cockpit-e2-") as td2:
        with _software(Path(td1), mode=Mode.AI) as sw1, \
                _software(Path(td2), mode=Mode.HUMAN) as sw2:
            ep1 = start_monitor_endpoint(MonitorSource.from_software(sw1), port=0)
            ep2 = start_monitor_endpoint(MonitorSource.from_software(sw2), port=0)
            try:
                m1 = json.loads(_request(ep1.url + "/status")[2])["mode"]
                m2 = json.loads(_request(ep2.url + "/status")[2])["mode"]
                if m1 == "ai" and m2 == "human" and ep1.port != ep2.port:
                    _ok("⭐ 同进程两端点互不覆盖（各自 mode 正确）——"
                        "类属性共享全局会在这里红")
                else:
                    _fail(out, f"两实例互相覆盖：{m1} / {m2}")
            finally:
                ep1.stop()
                ep2.stop()

            # 端口文件：启动前清陈旧、起完写实际端口、收尾删
            pf = Path(td1) / ".cockpit-check-port"
            pf.write_text("1", encoding="utf-8")   # 陈旧文件（模拟死进程残留）
            seen: list = []
            ep3 = MonitorEndpoint(MonitorSource.from_software(sw1), port=0,
                                  port_file=pf,
                                  runtime_writer=lambda **kw: seen.append(kw))
            port = ep3.start()
            if read_port(pf) == port:
                _ok("start 清陈旧端口文件后写入**实际**端口（port=0 回读）")
            else:
                _fail(out, f"端口文件内容错：{read_port(pf)} != {port}")
            if seen and seen[0] == {"monitor_port": port}:
                _ok("runtime_writer 在 bind 成功后以 monitor_port=实际端口 调用一次")
            else:
                _fail(out, f"runtime_writer 调用错：{seen}")
            ep3.stop()
            ep3.stop()                                  # 幂等
            if read_port(pf) is None and seen[-1] == {"monitor_port": None}:
                _ok("stop 清端口文件 + 通知描述符（monitor_port=None），且幂等")
            else:
                _fail(out, f"stop 收尾错：{read_port(pf)} {seen}")
            if ep3.port is None and ep3.url == "":
                _ok("stop 后 port=None / url=''（未启动即空串）")
            else:
                _fail(out, f"stop 后事实面未复位：{ep3.port} {ep3.url!r}")
            if _raises(lambda: ep3.stop(), "monitor_already_started") is False:
                _ok("对偶（不许误报）：幂等 stop 不抛错")
            else:
                _fail(out, "幂等 stop 抛了错")

            ep4 = MonitorEndpoint(MonitorSource.from_software(sw1), port=0)
            ep4.start()
            if _raises(lambda: ep4.start(), "monitor_already_started"):
                _ok("重复 start fail loud（不静默起第二个 server）")
            else:
                _fail(out, "重复 start 未被拒")
            ep4.stop()

            # 源形状守卫（对偶：合法源不许误报）
            if _raises(lambda: MonitorEndpoint(object()), "bad_monitor_source"):
                _ok("非源对象 fail loud 并指路 from_software（不静默空史/空快照）")
            else:
                _fail(out, "坏 source 未被拒（会做出'永远健康但什么都没有'的面板）")
            try:
                MonitorEndpoint(MonitorSource.from_software(sw1),
                                schema_rows=lambda: ())
            except MechaError as e:
                if e.kind == "bad_monitor_source":
                    _ok("source 已是 MonitorSource 形状时再注入 schema_rows fail loud")
                else:
                    _fail(out, f"形状冲突 kind 错：{e.kind}")
            else:
                _fail(out, "形状冲突未被拒")


# --------------------------------------------------------------------------
# F portfile 三条语义
# --------------------------------------------------------------------------

def check_portfile(out: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="cockpit-f-") as td:
        path = Path(td) / ".port"
        if read_port(path) is None and read_port(None) is None:
            _ok("读：不存在 / 路径为 None ⇒ None（不猜）")
        else:
            _fail(out, "读不存在文件未返回 None")
        write_port_file(path, 45678)
        if read_port(path) == 45678:
            _ok("写 / 读往返一致（裸端口文本）")
        else:
            _fail(out, f"写读不一致：{read_port(path)}")
        path.write_text("not-a-port", encoding="utf-8")
        if read_port(path) is None:
            _ok("读：非数字内容 ⇒ None（损坏的发现文件当'不知道'）")
        else:
            _fail(out, "非数字内容未被当成 None")
        write_port_file(path, 45678)
        clear_port_file(path, 11111)                 # 不是自己写的端口
        if path.exists():
            _ok("清：expected 不匹配则**不删**（不误删接管者的新端口）")
        else:
            _fail(out, "expected 不匹配却删了（会误删新主人的发现文件）")
        clear_port_file(path, 45678)
        if not path.exists():
            _ok("清：expected 匹配则删（收尾语义）")
        else:
            _fail(out, "expected 匹配未删")
        write_port_file(path, 1)
        clear_port_file(path)                        # 无条件
        if not path.exists():
            _ok("清：expected=None 无条件删（启动前清陈旧文件的语义）")
        else:
            _fail(out, "无条件清未删")
        write_port_file(None, 1)
        clear_port_file(None)
        _ok("对偶（不许误报）：路径为 None 的写/清是安全空操作")


# --------------------------------------------------------------------------
# G 概括投影
# --------------------------------------------------------------------------

def check_summary_projection(out: list[str]) -> None:
    view = MonitorView(
        summary={"复述": Claim(value=200.0, target="alpha"),
                 "计数": 3, "人话": Claim(value=1, target="missing")},
        raw_snapshot={"alpha": 200.0}, disputed=True, dispute_reason="不一致")
    payload = monitor_summary_payload(view)
    if (payload["summary"]["复述"] == {"value": 200.0, "target": "alpha"}
            and payload["summary"]["计数"] == 3
            and payload["disputed"] is True
            and payload["dispute_reason"] == "不一致"):
        _ok("Claim → {value, target}（标量原样；disputed / reason 透出）")
    else:
        _fail(out, f"概括投影错：{payload}")
    if json.dumps(payload, ensure_ascii=False, default=str).find("Claim(") == -1:
        _ok("对偶（不许误报）：投影后可 JSON 化且不含 'Claim(' 字面量"
            "（否则对账信息在 wire 上丢失）")
    else:
        _fail(out, "投影后仍出现 Claim( 字面量——对账信息会丢")
    if monitor_summary_payload(MonitorView(summary={}, raw_snapshot={},
                                          disputed=False)) == {
            "summary": {}, "disputed": False, "dispute_reason": ""}:
        _ok("空概括的边界形状正确（不误报）")
    else:
        _fail(out, "空概括形状错")


# --------------------------------------------------------------------------
# H POST 请求体 drain（既有缺陷修复）
# --------------------------------------------------------------------------

def _drain_rule(out: list[str]) -> None:
    """``_drain_request_body`` 的三分（纯逻辑，不依赖 RST 的时序运气）。"""

    class _Fake:
        close_connection = False
        MAX_DRAIN_BYTES = _Handler.MAX_DRAIN_BYTES

        def __init__(self, raw, payload=b""):
            self.headers = {} if raw is None else {"Content-Length": raw}
            self._buf = payload
            self.rfile = self

        def read(self, n):
            chunk, self._buf = self._buf[:n], self._buf[n:]
            return chunk

    def _call(raw, payload=b""):
        fake = _Fake(raw, payload)
        reusable = _Handler._drain_request_body(fake)
        return reusable, fake.close_connection

    if (_call(None) == (False, True)
            and _call("abc") == (False, True)
            and _call(str((1 << 20) + 1)) == (False, True)):
        _ok("drain 三分：Content-Length 缺失 / 非法 / 超上界 ⇒ 不硬读，要求关连接")
    else:
        _fail(out, "drain 三分（缺失 / 非法 / 超界）不符规则")
    if _call("0") == (True, False):
        _ok("drain：Content-Length 0 ⇒ 没有 body 要读，连接可复用")
    else:
        _fail(out, "drain 对 0 长度处理错")
    if _call("8", b"12345678") == (True, False):
        _ok("drain：合法长度 ⇒ 读净丢弃且**不**要求关连接（正常路径契约不变）")
    else:
        _fail(out, "drain 合法路径错")
    if _call("8", b"123") == (False, True):
        _ok("对偶（不许误报）：body 没发全 ⇒ 判为读不干净并要求关连接")
    else:
        _fail(out, "body 没发全却被当成读干净了")


def check_post_body_drain(out: list[str]) -> None:
    """H 组：带 body 的 POST 必须被读干净再回 404（否则 Windows 内核回 RST）。"""
    _drain_rule(out)
    with tempfile.TemporaryDirectory(prefix="cockpit-h-") as td:
        with _software(Path(td)) as sw:
            ep = start_monitor_endpoint(MonitorSource.from_software(sw), port=0)
            try:
                # ① 决定性判别子：公告的 body 未发完时不得提前回响应
                sock = socket.create_connection(("127.0.0.1", ep.port), timeout=10)
                early: bool | None = None
                blob = b""
                try:
                    sock.sendall(b"POST /status HTTP/1.1\r\nHost: check\r\n"
                                 b"Content-Length: 1000\r\n\r\n" + b"x" * 10)
                    sock.settimeout(1.5)
                    try:
                        sock.recv(200)
                        early = True
                    except socket.timeout:
                        early = False
                    except OSError:
                        early = True          # 连接被打断 = 服务端提前收尾
                    sock.settimeout(5.0)
                    try:
                        sock.sendall(b"x" * 990)
                        while b"\r\n\r\n" not in blob:
                            chunk = sock.recv(4096)
                            if not chunk:
                                break
                            blob += chunk
                    except OSError:
                        pass                      # 未修实现的典型表现：RST
                finally:
                    sock.close()
                if early is False:
                    _ok("⭐ 决定性：公告的 body 未发完时服务端**不提前回响应**"
                        "（不修的实现会立刻回 404 ⇒ 这里必然红，与 RST 时序运气无关）")
                else:
                    _fail(out, "服务端在请求体发完之前就回了响应——没读 body 就关连接"
                               "（既有缺陷：Windows 内核回 RST）")
                if b"404" in blob.split(b"\r\n", 1)[0]:
                    _ok("补完 body 后拿到干净的 404（状态码未变）")
                else:
                    _fail(out, f"补完 body 后未拿到 404：{blob[:80]!r}")

                # ② 压测：带 body 的 POST 不得出现中断
                body = b"x" * POST_BODY_BYTES
                bad: list[str] = []
                for _ in range(POST_ROUNDS):
                    conn = http.client.HTTPConnection("127.0.0.1", ep.port,
                                                      timeout=10)
                    try:
                        conn.request("POST", "/status", body=body,
                                     headers={"Content-Type": "application/json"})
                        resp = conn.getresponse()
                        data = resp.read()
                        if (resp.status != 404
                                or b"read-only monitor endpoint" not in data):
                            bad.append(f"bad-response {resp.status} {data[:40]!r}")
                    except Exception as exc:              # noqa: BLE001
                        bad.append(f"{type(exc).__name__}: {exc}")
                    finally:
                        try:
                            conn.close()
                        except Exception:                 # noqa: BLE001
                            pass
                if not bad:
                    _ok(f"⭐ 压测 {POST_ROUNDS} 轮带 body POST"
                        f"（{POST_BODY_BYTES // 1024} KiB）：零 ConnectionAbortedError / "
                        f"零 WinError 10053，且每轮都是干净 404")
                else:
                    _fail(out, f"压测 {POST_ROUNDS} 轮命中 {len(bad)} 次"
                               f"（首例：{bad[0]}）——不 drain 就关连接 ⇒ Windows RST")

                # ③ 规范分支：缺 Content-Length ⇒ 如实要求关连接（不硬读）
                sock2 = socket.create_connection(("127.0.0.1", ep.port), timeout=10)
                raw = b""
                try:
                    sock2.sendall(b"POST /status HTTP/1.1\r\nHost: check\r\n\r\n")
                    while b"\r\n\r\n" not in raw:
                        chunk = sock2.recv(4096)
                        if not chunk:
                            break
                        raw += chunk
                finally:
                    sock2.close()
                head = raw.split(b"\r\n\r\n", 1)[0] if raw else b""
                if b"404" in head.split(b"\r\n", 1)[0] and b"Connection: close" in head:
                    _ok("缺 Content-Length 的 POST ⇒ 404 + Connection: close"
                        "（不硬读，如实要求关连接）")
                else:
                    _fail(out, f"缺 Content-Length 的 POST 未按规范处置：{head[:120]!r}")

                # ④ 不许误报：正常路径契约一字不变
                code, headers, text = _request(ep.url + "/status", "POST")
                if (code == 404 and json.loads(text) == {
                        "ok": False, "error": "read-only monitor endpoint"}):
                    _ok("对偶：POST（带 Content-Length: 0）仍 404 逐字 body")
                else:
                    _fail(out, f"POST 契约变了：{code} {text!r}")
                if headers.get("Connection") is None:
                    _ok("对偶：读得干净的路径**不新增** Connection 头（wire 一字不变）")
                else:
                    _fail(out, f"正常路径多出 Connection 头："
                               f"{headers.get('Connection')!r}")
                code, headers, _ = _request(ep.url + "/status")
                if code == 200 and headers.get("Connection") is None:
                    _ok("对偶：GET 完全不受影响（无新增 Connection 头）")
                else:
                    _fail(out, f"GET 契约变了：{code} "
                               f"Connection={headers.get('Connection')!r}")
                code, headers, _ = _request(ep.url + "/status", "OPTIONS")
                if code == 204 and headers.get("Connection") is None:
                    _ok("对偶：OPTIONS 完全不受影响")
                else:
                    _fail(out, f"OPTIONS 契约变了：{code}")
            finally:
                ep.stop()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out: list[str] = []
    print("Phase 11 判据（cockpit 只读监控端点 + portfile）：")
    check_projection(out)
    check_before_folds_full_history(out)
    check_http_contract(out)
    check_extra_routes(out)
    check_lifecycle(out)
    check_portfile(out)
    check_summary_projection(out)
    check_post_body_drain(out)
    if out:
        print(f"phase11_cockpit_selfcheck: FAIL（{len(out)} 条）")
        return 1
    print("phase11_cockpit_selfcheck: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
