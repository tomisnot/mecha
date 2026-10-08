"""步骤 2b 的判据：聚合端点**接上** supervisor + 生成 dsh 第三层 overlay + **真端到端**。

## ⚠ 造法纪律（老规矩：先问"怎么故意退化还能让它绿"）

- **判据 1 的陷阱**：只断言"聚合端点的账里它没了" ⇒ 把 ``close_fn`` 接成
  "只 ``detach``、不碰 supervisor"照样绿。⇒ **必须**断言 ``supervisor.state()``
  里那个 child **真的停了**（``state=stopped`` 且 pid 为 None）。
- **判据 2 的陷阱**：只断言"元工具里有那个 id" ⇒ 把 ``state_fn`` 去掉也能绿。
  ⇒ 造法要求**制造分叉**：聚合端点挂着、supervisor 已经停了 ⇒ 必须报**停**。
- **判据 3 的陷阱**：只断言"文件里有 toolCallTimeoutMs 这个键" ⇒ 把它写成默认 60s 也绿。
  ⇒ 断言**值必须 > 60s 且等于声明的 600000**（默认值也能通过"键存在"这种弱断言）。
- **判据 5 的陷阱**：只断言"overlay 文件存在" ⇒ 那条链其实没接上也绿。
  ⇒ 端到端要**真起**：真 MCP 服务端 + 真连接 + 真 ``dsh`` child 的 argv 里**出现**那份 overlay。

## 为什么端到端可以真起而不慢

被测的 MCP 服务端本身就是框架既有的 ``McpEndpoint``（``registry`` + ``LocalToolHost``），
它**进程内**起 uvicorn、几秒内就绪；``dsh`` 用一个 mock（打印自己的 argv）。
⇒ 这条覆盖了"聚合端点 → 真后端 → 真连接 → dsh 拿到 overlay"整条链，
而**不需要**真的 dsh / ML / EL。

## Known Limitations and Deferred Work

- **不验真 dsh**：判据只验"dsh 的 ``ChildSpec`` 里出现了指向聚合端点的 overlay"，
  不验 dsh 真去连它（那需要真 dsh 实例；属接入仓的验收）。
- **不验通知**（本轮不做，见 ``aggregate`` 的 Known Limitations）。
- ``sync_backends`` 只按"state 里 ready/running 且**有回读端口**"挂后端：
  **没端口就是没挂**（不许猜端口——ML 那条"写预选值"的教训）。
"""

from __future__ import annotations

import importlib
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from mecha.aggregate import Aggregator  # noqa: E402
from mecha.aggregate_runtime import (DEFAULT_TOOL_CALL_TIMEOUT_MS,  # noqa: E402
                                    AggregateRuntime, make_prepare_hook,
                                    overlay_text, stable_server_name, with_overlay_patch)
from mecha.errors import MechaError  # noqa: E402
from mecha.launch import LaunchController  # noqa: E402
from mecha.site import DSHSpec, ReadySpec, Service, Site  # noqa: E402
from mecha.tools import ToolRegistry, define_tool  # noqa: E402


def _load_supervise():
    """从 ``MECHA_SUPERVISE_PKG``（若有）或本仓加载 ``supervise``。

    ## ⚠ 为什么这个口子必须有（实测踩到的假红证）

    红证的做法是"把缺陷人为放回去，判据**必须**红"。而**放回去**只能放**别处** ——
    仓内零改动 ⇒ 必须能把本判据指向一份**打了补丁的整包临时副本**。
    本文件原先**没有**这个口子（直接 ``from mecha.supervise import …``）⇒
    我按 `launch_selfcheck` 的成例做补丁时，**补丁根本没被加载**，
    于是"红证"跑的是**真实现** ⇒ 两次退化**都显示绿**（假红证，比没有还坏）。

    ⚠ 按**包**加载（``MECHA_SUPERVISE_PKG`` 指向含 ``mecha/`` 的目录），
    不用按文件加载 —— ``supervise.py`` 里有相对 import（``from .errors import …``），
    按文件加载会失败（同一坑 `launch_selfcheck` 已记）。
    """
    pkg = os.environ.get("MECHA_SUPERVISE_PKG", "").strip()
    if pkg:
        sys.path.insert(0, pkg)
        for name in [m for m in list(sys.modules) if m == "mecha" or m.startswith("mecha.")]:
            del sys.modules[name]
        mod = importlib.import_module("mecha.supervise")
        print(f"  [探针] 用的是包副本 {pkg}（MECHA_SUPERVISE_PKG）")
        return mod
    from mecha import supervise
    return supervise


_SV = _load_supervise()
ChildSpec, Supervisor = _SV.ChildSpec, _SV.Supervisor

FAILS: list[str] = []


def _check(name: str, cond: bool, extra: object = "") -> None:
    if cond:
        print(f"  ✔ {name}")
    else:
        print(f"  ✖ {name}" + (f"  <- {extra}" if extra != "" else ""))
        FAILS.append(name)


class FakeBackend:
    def __init__(self, tools: list[dict]) -> None:
        self._tools = tools
        self.calls: list[tuple[str, dict]] = []

    async def tools(self) -> list[dict]:
        return self._tools

    async def call(self, name: str, args: dict) -> dict:
        self.calls.append((name, args))
        return {"is_error": False, "text": f"ok:{name}"}

    async def close(self) -> None:
        return None


def _fake_factory(tools: list[dict] | None = None):
    made: list[FakeBackend] = []

    def factory(url: str, *, server_name: str) -> FakeBackend:
        b = FakeBackend(tools or [{"name": "echo_thing", "description": "d",
                                   "inputSchema": {}}])
        made.append(b)
        return b
    factory.made = made  # type: ignore[attr-defined]
    return factory


class Rig:
    """一个临时站点 + supervisor + 聚合运行期（全临时目录，收尾清干净）。"""

    def __init__(self, *, agg=None, factory=None) -> None:
        import shutil
        import tempfile
        self.dir = Path(tempfile.mkdtemp(prefix="mecha-2b-"))
        self.logs = self.dir / "logs"
        self.mock = self.dir / "mock_child.py"
        self.mock.write_text(
            "import sys, time\n"
            "print('MOCK-ARGV: ' + ' '.join(sys.argv[1:]), flush=True)\n"
            "time.sleep(30)\n", encoding="utf-8")
        self.super = Supervisor(log_dir=self.logs, state_path=self.dir / "state.json",
                                max_restarts=1, max_restart_period_s=30.0)
        self._shutil = shutil
        #: svc 的"就绪"用 TCP（mock 自己绑一个端口并写文件）
        self.port = _free_port()
        self.ready = ReadySpec(port_file=".svc-port", tcp=True, timeout_s=8.0)
        self.svc = Service(
            id="MOCK", brief="判据用的假软件",
            argv=(sys.executable, str(self.mock)),
            cwd=self.dir, ready=self.ready, data_root=self.dir / "data",
        )
        self.site = Site(name="2b-site", services=(self.svc,),
                         dsh=DSHSpec(command=(sys.executable, str(self.mock)), cwd=self.dir))
        self.factory = factory or _fake_factory()
        self.agg = agg or Aggregator()
        # 让 mock 绑端口：用 bind_port 模式（复用 launch_selfcheck 的 mock 思路，另写一份）
        (self.dir / "mock_child.py").write_text(_MOCK_SRC, encoding="utf-8")
        self.svc = Service(
            id="MOCK", brief="判据用的假软件",
            argv=(sys.executable, str(self.mock), "bind", str(self.port), ".svc-port"),
            cwd=self.dir, ready=self.ready, data_root=self.dir / "data",
        )
        self.site = Site(name="2b-site", services=(self.svc,),
                         dsh=DSHSpec(command=(sys.executable, str(self.mock), "dsh"), cwd=self.dir))
        self.rt = AggregateRuntime(supervisor=self.super, services=(self.svc,),
                                   aggregator=self.agg, backend_factory=self.factory)

    def close(self) -> None:
        try:
            self.super.shutdown()
        except Exception:                                   # noqa: BLE001
            pass
        self._shutil.rmtree(self.dir, ignore_errors=True)


_MOCK_SRC = r'''
import os, socket, sys, time
mode = sys.argv[1] if len(sys.argv) > 1 else "bind"
if mode == "bind":
    port = int(sys.argv[2]); name = sys.argv[3]
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port)); s.listen(8)
    open(os.path.join(os.path.dirname(os.path.abspath(__file__)), name), "w").write(str(port))
    print("BOUND %d" % port, flush=True)
    while True:
        try: s.accept()
        except OSError: break
else:
    print("MOCK-ARGV: " + " ".join(sys.argv[1:]), flush=True)
    time.sleep(30)
'''


def _free_port() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _rows(sup: Supervisor) -> dict:
    return {r["id"]: r for r in sup.state()["children"]}


def _call(agg: Aggregator, name: str, **kw) -> dict:
    env = agg.registry().execute(name, kw)
    if env.get("is_error"):
        # ⚠ 错误信封的 `error` **可能是 dict**（``{message, kind, hint}``）⇒ 直接 ``str()`` 会把
        # 内层的人类文案埋进字典字面量里，调用方按文案断言就判不出来
        # （实测踩过：``loud=0`` 其实是错误信息被包成了 dict，**不是**"没响亮失败"）。
        err = env.get("error")
        msg = str(err.get("message") or err) if isinstance(err, dict) else str(err or "失败")
        raise MechaError(msg, kind="tool_call_failed")
    return env["value"]


# ---------------------------------------------------------------- 判据


def check_toggle_on_off_three_times() -> None:
    """⭐ 判据 7（**面板滑块**）：关→开→关→开→关→开，连做三次都必须成立。

    ## 为什么必须有这条（实测抓到的真缺口）

    用户要的"面板上每项一个开关滑块，**启动前启动后都能动**"，
    落到框架就是 **`close_service` → `open_service`** 这一个动作。
    而实测它**第三次就死**（原实现）：

    ```
    ① 开：OK  state=ready
    ② 关：OK  state=stopped
    ③ 再开：✖ duplicate_child:「child id 重复：'MOCK'」
    ```

    根因：``stop()`` 按 OTP 语义**保留 spec**（``terminate_child``），
    但 ``start()`` 对**已有 id 一律拒绝** —— OTP 里本该配对的
    ``restart_child/2`` **我们没有**。

    ⚠ **怎么故意退化还能让它绿**（所以下面三段都要断言）：
    只断言"没抛异常"是不够的 —— 把实现改成"关的时候顺手把 child 从表里删掉"
    （**丢掉声明**）也能让"再开"成功，但那**违反 OTP 语义**：
    ``state_ids()`` 会不再认得这个 id ⇒ 面板上那一项会**消失**。
    ⇒ 所以每轮都要断言"**声明还在**"且"**真的在跑**"。
    """
    rig = Rig()
    try:
        for i in range(1, 4):
            # ⚠ **异常必须转成判据失败，不许让它把整个判据崩掉**（实测教训）：
            # 退化实现下 `open_service` 抛 `duplicate_child` ⇒ 原写法直接崩在异常上，
            # 于是 `exit=1` 但**一条 ✖ 都没打印** —— 红证看起来"没红"，
            # 而实际是"**崩溃冒充了失败**"。红证读不出差别，就等于没有红证。
            try:
                rig.rt.open_service("MOCK")
            except MechaError as exc:
                _check(f"⑦ 第 {i} 轮：开 ⇒ 真的在跑（pid 有、state=ready）",
                       False, f"开失败了：kind={exc.kind}：{exc.message[:60]}")
                break
            row = _rows(rig.super)["MOCK"]
            _check(f"⑦ 第 {i} 轮：开 ⇒ 真的在跑（pid 有、state=ready）",
                   row["state"] == "ready" and bool(row["pid"]), f"row={row}")

            rig.rt.close_service("MOCK")
            row = _rows(rig.super)["MOCK"]
            _check(f"⑦ 第 {i} 轮：关 ⇒ 真的停了",
                   row["state"] == "stopped" and row["pid"] is None, f"row={row}")
            # ⚠ 关键对偶：关**不许删声明**（否则面板上那一项会消失）
            _check(f"⑦ 第 {i} 轮：关之后**声明仍在**（state_ids 还认得它，面板那项不消失）",
                   "MOCK" in rig.super.state_ids(), f"ids={rig.super.state_ids()}")
    finally:
        rig.close()


def check_close_reaches_supervisor() -> None:
    """⭐ 判据 1：`close_software` 真的把 supervisor 里那个 child 停了。"""
    rig = Rig()
    try:
        rig.rt.open_service("MOCK")
        rig.rt.sync_backends()
        row = _rows(rig.super)["MOCK"]
        _check("① 前置：open_service 之后 child 真的在跑（ready）",
               row["state"] == "ready" and row["pid"], f"row={row}")
        _check("① 前置：后端已挂上（有回读端口才有后端）", "MOCK" in rig.agg.attached(),
               f"attached={rig.agg.attached()}")

        rig.rt.close_service("MOCK")
        after = _rows(rig.super)["MOCK"]
        # ⭐ 这一半是关键：只 detach、不碰 supervisor 的退化实现会在这里被抓
        _check("① ⭐ close_software ⇒ **supervisor 里那个 child 真的停了**",
               after["state"] == "stopped" and after["pid"] is None, f"after={after}")
        # ⚠ **不能**断言 `"MOCK" not in attached()` —— `detach` 按**墓碑设计故意保留**后端
        # （工具名要留着做墓碑）⇒ 那样断言会与设计打架、永远是假。
        # 要判"聚合端点这边的视角一致"就读**状态**：它必须是 down。
        _check("① 且聚合端点视角也一致（后端状态 = down，墓碑保留）",
               rig.agg.states().get("MOCK") == "down", f"states={rig.agg.states()}")
        _check("① 关闭**不删声明**（child 仍在 state_ids 里，可再开）",
               "MOCK" in rig.super.state_ids())
    finally:
        rig.close()


def check_meta_tool_reports_supervisor_fact() -> None:
    """⭐ 判据 2：元工具报的"谁在跑"来自 supervisor（造分叉 ⇒ 必须报停）。"""
    rig = Rig()
    try:
        rig.rt.open_service("MOCK")
        rig.rt.sync_backends()
        f1 = _call(rig.agg, "query_software")
        m1 = next(x for x in f1["software"] if x["id"] == "MOCK")
        _check("② 前置：跑着时 `process` 栏如实报（不是 None）",
               m1["process"] and m1["process"]["state"] == "ready", f"m1={m1}")

        # ⭐ 制造分叉：**只**让 supervisor 停（不动聚合端点的账）
        rig.super.stop("MOCK")
        rig.rt.sync_backends()
        f2 = _call(rig.agg, "query_software")
        m2 = next(x for x in f2["software"] if x["id"] == "MOCK")
        _check("② ⭐ supervisor 停了 ⇒ 元工具**必须报停**（不信聚合端点自己的账）",
               m2["process"] and m2["process"]["state"] == "stopped", f"m2={m2}")
        _check("② 分叉了也不会把「已停」报成「在跑」",
               not (m2["process"] or {}).get("holds_write_lease"), f"m2={m2}")
    finally:
        rig.close()


def check_meta_tool_unknown_id_is_loud() -> None:
    """判据 6：对不存在的 id ⇒ 响亮失败。"""
    rig = Rig()
    try:
        loud = 0
        for tool in ("open_software", "close_software"):
            try:
                _call(rig.agg, tool, id="NOPE")
            except MechaError as exc:
                if "NOPE" in exc.message and "没有这个软件" in exc.message:
                    loud += 1
        _check("⑥ open/close 对不存在的 id 都**响亮失败**（不静默）", loud == 2, f"loud={loud}")
    finally:
        rig.close()


def check_overlay_keeps_timeout() -> None:
    """⭐ 判据 3/4：overlay 含 `toolCallTimeoutMs` 且**不是默认 60s**；落 runtime/；serverName 稳定。"""
    rig = Rig()
    try:
        txt = overlay_text(mcp_port=12345, server_name="2b-site")
        parsed = yaml.safe_load(txt)
        cfg = parsed[0]["insert"][0]["config"]
        _check("③ overlay 里**有** `toolCallTimeoutMs`", "toolCallTimeoutMs" in cfg, f"cfg={cfg}")
        _check(f"③ ⭐ 它是 **{DEFAULT_TOOL_CALL_TIMEOUT_MS}**（10 分钟），**不是默认 60s**"
               "（默认会让长训练「假失败」）",
               cfg.get("toolCallTimeoutMs") == DEFAULT_TOOL_CALL_TIMEOUT_MS
               and cfg.get("toolCallTimeoutMs") != 60_000, f"cfg={cfg}")
        _check("③ `failOnStartupError: true`（连不上要响，不留空工具表）",
               cfg.get("failOnStartupError") is True)
        _check("④ `- insert:` 形态（裸 id 会被 dsh 静默跳过）",
               isinstance(parsed, list) and "insert" in parsed[0])

        p = rig.rt.prepare_dsh(rig.site, 12345)
        _check("④ overlay 落在项目根 `runtime/`（生成物、不入库）",
               p.parent.name == "_mecha_runtime" and p.name.endswith(".patch.yml"), f"p={p}")
        n1 = stable_server_name(rig.site)
        n2 = stable_server_name(rig.site)
        _check("④ `serverName` **稳定**（只依赖声明，不依赖端口/时间/pid）", n1 == n2, f"{n1}")
    finally:
        rig.close()


def check_prepare_hook_injects_overlay_into_dsh() -> None:
    """判据 5（前半）：``prepare_hooks`` 把 overlay 塞进 dsh 的 ``--patch``。"""
    rig = Rig()
    try:
        port_box = {"port": 65001}
        hook = make_prepare_hook(rig.rt, rig.site, lambda: port_box["port"])
        dsh = ChildSpec(id="dsh", argv=("dsh", "web", "--port", "3081"), cwd=rig.dir)
        out = hook(dsh, None)
        _check("⑤ prepare 钩子**改的是 dsh 的 spec**（别的 child 不动）",
               out is not None and out.id == "dsh")
        _check("⑤ overlay 进了 `--patch`", "--patch" in out.argv, f"argv={out.argv}")
        _check("⑤ `--patch` 的值就是生成的 overlay 路径",
               str(rig.rt.prepare_dsh(rig.site, port_box["port"])) in out.argv, f"argv={out.argv}")
        _check("⑤ 原来的 argv 没被吃掉（--port 3081 还在）", "3081" in out.argv, f"argv={out.argv}")
        # 另一个 child 不该被改
        other = ChildSpec(id="MOCK", argv=("x",), cwd=rig.dir)
        _check("⑤ 非 dsh 的 child **原样返回 None**（钩子只认 dsh）", hook(other, None) is None)
        # 聚合端点还没起来 ⇒ 响亮失败（不是生成一份端口错的 overlay）
        loud = False
        try:
            make_prepare_hook(rig.rt, rig.site, lambda: None)(dsh, None)
        except MechaError as exc:
            loud = "聚合端点还没起来" in exc.message
        _check("⑤ 聚合端点没起来时**响亮失败**（不生成端口错的 overlay）", loud)
    finally:
        rig.close()


def check_end_to_end_real_mcp_backend() -> None:
    """⭐ 判据 5（后半，**真端到端**）：真 MCP 服务端 → 真连接 → dsh 拿到 overlay → 关掉 → 墓碑响。"""
    from mecha.providers.mcp import McpEndpoint

    reg = ToolRegistry()
    reg.register(define_tool(
        name="echo_text", description="回显一段文本",
        parameters={"text": {"type": "string", "description": "要回显的"}},
        output_schema={"type": "object", "properties": {}, "required": [],
                       "additionalProperties": True},
        execute=lambda **kw: {"echo": kw.get("text", "")},
    ))
    ep = McpEndpoint(reg, server_name="mock-spine", instructions="2b 端到端判据用的假脊椎")
    port = ep.start()                       # ⚠ 真实端口（回读）
    try:
        from mecha.providers.mcp_client import Backend

        async def real_factory(url: str, *, server_name: str):
            return await Backend.connect(url, server_name=server_name, timeout_s=5.0)

        agg = Aggregator()
        svc = Service(id="MOCK", brief="假脊椎",
                      argv=(sys.executable, "-c", "import time;time.sleep(30)"),
                      cwd=Path.cwd(), ready=ReadySpec(tcp=True, timeout_s=1.0),
                      data_root=Path.cwd() / "_2b_e2e_data")
        site = Site(name="2b-e2e", services=(svc,),
                    dsh=DSHSpec(command=(sys.executable, "-c", "import time;time.sleep(30)"),
                                cwd=Path.cwd()))
        # ⚠ 这里**不走 supervisor**（那个 child 是假的）；直接验"真后端能被聚合端点挂上并调通"
        backend = _run_await(real_factory(f"http://127.0.0.1:{port}/mcp", server_name="MOCK"))
        agg.attach("MOCK", backend)          # type: ignore[arg-type]
        names = [s["name"] for s in agg.registry().schemas()]
        _check("⑤ 真后端挂上后工具出现在表里（带 svc 前缀）",
               "mock_echo_text" in names, f"names={names}")
        out = _call(agg, "mock_echo_text", text="你好")
        # ⚠ `_call` 返回的是**工具回执信封**（`{is_error, text}`），不是后端的原始 dict
        # ⇒ 必须先解 `text` 再判（这里曾经直接 `out.get("echo")` ⇒ 明明调用成功却判红）。
        payload = out.get("text") or ""
        if isinstance(payload, str) and payload:
            import json as _json
            try:
                payload = _json.loads(payload)
            except ValueError:
                payload = {}
        _check("⑤ 真调用走通了（经真 MCP 协议）",
               isinstance(payload, dict) and payload.get("echo") == "你好", f"out={out}")

        # 关掉 ⇒ 墓碑响（工具名还在、调它必须抛）
        agg.detach("MOCK")
        loud = False
        msg = ""
        try:
            _call(agg, "mock_echo_text", text="x")
        except MechaError as exc:
            loud, msg = True, exc.message
        _check("⑤ 关掉之后调它的旧工具 ⇒ **响亮失败**（墓碑）", loud, f"msg={msg!r}")
        _check("⑤ 墓碑文案带 svc_id 与重开方法",
               "MOCK" in msg and "open_software" in msg, f"msg={msg!r}")

        # dsh 的 spec 里出现指向**聚合端点**的 overlay（第三层）
        overlay = Path.cwd() / "_mecha_runtime" / "dsh-aggregate.patch.yml"
        dsh_spec = ChildSpec(id="dsh", argv=("dsh", "web"), cwd=Path.cwd())
        hook = make_prepare_hook(
            type("R", (), {"prepare_dsh": lambda self, s, p: _write_tmp_overlay(p, site)})(),
            site, lambda: port)
        patched = hook(dsh_spec, None)
        _check("⑤ dsh 的 `--patch` 指向**聚合端点**那层 overlay",
               "--patch" in patched.argv and patched.argv[-1].endswith(".patch.yml"),
               f"argv={patched.argv}")
        _check("⑤ 那份 overlay 里的端口 = **聚合端点回读端口**",
               str(port) in Path(patched.argv[-1]).read_text(encoding="utf-8"),
               f"file={Path(patched.argv[-1]).read_text(encoding='utf-8')!r}")
        overlay.unlink(missing_ok=True)
    finally:
        ep.stop()


def _write_tmp_overlay(port: int, site: Site) -> Path:
    """端到端用：把 overlay 写进临时目录（不污染仓）。"""
    import tempfile
    p = Path(tempfile.mkdtemp(prefix="2b-overlay-")) / "dsh-aggregate.patch.yml"
    p.write_text(overlay_text(mcp_port=port, server_name=stable_server_name(site)),
                 encoding="utf-8")
    return p


def _run_await(coro):
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


META_NAMES = ("query_software", "open_software", "close_software")


def check_controller_survival_toggle() -> None:
    """⑧ 面板的存活滑块（``LaunchController.set_service_enabled``）——**真起真停**。

    ## 红证怎么造
    只断言"面板的账里它变了"⇒ 把实现退化成"**只改 ``_enabled``、不碰 supervisor**"
    照样绿。⇒ 必须断言 ``supervisor.state()`` 里那个 child **真的停了 / 真的起了**。

    ⚠ 它复用的是 ``AggregateRuntime.open_service/close_service``（判据 ⑦ 已三轮钉过），
    这里只验**面板入口真的走到了那条正规路径**（不是复制一份实现）。
    """
    rig = Rig()
    try:
        rig.rt.open_service("MOCK")
        rig.rt.sync_backends()
        ctl = LaunchController(sites=(rig.site,), runtime=rig.dir / "rt",
                               supervisor=rig.super)
        ctl.agg_runtime = rig.rt
        ctl._started = True                      # 面板只有"整站启动之后"才真开关

        out = ctl.set_service_enabled("MOCK", False)
        row = _rows(rig.super)["MOCK"]
        _check("⑧ ⭐ 面板关 ⇒ supervisor 里那个 child **真的停了**",
               out.get("applied") == "live" and row["state"] == "stopped" and row["pid"] is None,
               f"out={out} row={row}")
        _check("⑧ 关之后**声明仍在**（面板那项不消失）", "MOCK" in rig.super.state_ids())
        _check("⑧ 关之后端点侧**跟着摘了**（AI 手里不留工具）",
               rig.agg.states().get("MOCK") == "down", f"states={rig.agg.states()}")

        out2 = ctl.set_service_enabled("MOCK", True)
        row2 = _rows(rig.super)["MOCK"]
        _check("⑧ ⭐ 面板开 ⇒ 它**真的起来了**（pid 有、ready）",
               out2.get("applied") == "live" and row2["state"] == "ready" and row2["pid"],
               f"out={out2} row={row2}")
        _check("⑧ 开之后端点侧**跟着挂上**（sync_backends 真的调了）",
               "MOCK" in rig.agg.attached(), f"attached={rig.agg.attached()}")

        loud = False
        try:
            ctl.set_service_enabled("NOPE", False)
        except MechaError:
            loud = True
        _check("⑧ 对不存在的 id ⇒ **响亮失败**（不静默）", loud)

        # 未启动整站时：只记意图、**不 spawn**（面板在启动前也要能表达意图）
        ctl2 = LaunchController(sites=(rig.site,), runtime=rig.dir / "rt2",
                                supervisor=rig.super)
        ctl2.set_service_enabled("MOCK", False)
        _check("⑧ 整站未启动时 ⇒ `applied=intent`（不 spawn），意图被记下",
               ctl2.intent()["MOCK"] is False, f"intent={ctl2.intent()}")
    finally:
        rig.close()


def check_launch_wiring_at_the_launch_layer() -> None:
    """⭐ ``launch`` 那一层的接线：聚合端点起来 ⇒ **dsh child 的 ``--patch``** 指向它。

    ## 为什么这条要单独在 launch 层再钉一次
    "``AggregateRuntime`` 能生成 overlay" 与 "``launch`` 真的把它挂到 dsh 上"
    是**两件事**。只在 runtime 层断言的话，**钩子根本没挂**（或挂到别的 child 上）
    照样绿——文件仍会被别处生成。⇒ 必须断言 **dsh 那个 child 的 ``--patch`` 值**。
    """
    import shutil
    import tempfile

    from mecha.launch import build_aggregate_wiring

    d = Path(tempfile.mkdtemp(prefix="launch-wire-"))
    site = Site(name="wire-site",
                services=(Service(id="MOCK", brief="x",
                                  argv=(sys.executable, "-c", "pass"), cwd=d,
                                  ready=ReadySpec(tcp=True, timeout_s=1.0),
                                  data_root=d / "data"),),
                dsh=DSHSpec(command=(sys.executable, "-c", "pass"), cwd=d))
    sup = Supervisor(log_dir=d / "logs")
    ep, _rt, reg = build_aggregate_wiring(site=site, supervisor=sup)
    try:
        port = ep.port
        _check("⑦ 聚合端点起来了并**回读**到端口", isinstance(port, int) and port > 0,
               f"port={port!r}")
        _check("⑦ 门面是**活**的（初始含元工具；不是构造期快照）",
               set(META_NAMES) <= {s["name"] for s in reg.schemas()},
               f"names={[s['name'] for s in reg.schemas()]}")
        # ⭐ 走 **Supervisor 的真钩子路径**（不是直接调 runtime.prepare_dsh）
        dsh = ChildSpec(id="dsh", argv=("dsh", "web", "--port", "3081"), cwd=d)
        patched = sup._run_prepare(dsh)
        _check("⑦ ⭐ dsh 那个 child 的 argv 里出现了 `--patch`", "--patch" in patched.argv,
               f"argv={patched.argv}")
        ov = Path(patched.argv[patched.argv.index("--patch") + 1])
        _check("⑦ ⭐ 那份 overlay 里的端口 = 聚合端点**回读**端口",
               str(port) in ov.read_text(encoding="utf-8"), f"file={ov}")
        _check("⑦ 且带 `toolCallTimeoutMs: 600000`（长训练不假失败）",
               "toolCallTimeoutMs: 600000" in ov.read_text(encoding="utf-8"))
        _check("⑦ 别的 child 不被改（钩子只认 dsh）",
               sup._run_prepare(ChildSpec(id="MOCK", argv=("x",), cwd=d)).argv == ("x",))
    finally:
        ep.stop()
        sup.shutdown()
        shutil.rmtree(d, ignore_errors=True)


def _run_guarded(label: str, fn) -> None:
    """跑一个判据函数；**它抛的任何异常都转成一条判据失败**。

    ## ⚠ 为什么必须有这道护栏（实测踩到，且它伪装有"红证没红"）

    退化实现下 `open_service` 抛 `MechaError(duplicate_child)`。
    原写法让那个异常**一路冒到 `main()` 之外** ⇒ 进程退出码 1，
    但 **`✖` 一条都没打印、`FAILS` 是空的** ⇒ 从输出上看像"判据没测出问题"。

    ⭐ 这是**最坏的一种假绿**：**崩溃冒充了失败**。
    红证要读的是"`✖` 有没有出现"，而崩溃时它**一条都没有** ⇒ 红证失效。
    ⇒ 判据运行器必须把"抛异常"也当成"这条判据红了"。
    """
    try:
        fn()
    except Exception as exc:                               # noqa: BLE001 - 判据护栏
        _check(f"【护栏】{label} 抛异常（视为该判据失败，别让它冒充'没测出问题'）",
               False, f"{type(exc).__name__}: {str(exc)[:120]}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("[aggregate_runtime_selfcheck] 2b：接上 supervisor + dsh overlay + 端到端")
    _run_guarded("1 close 到达 supervisor", check_close_reaches_supervisor)
    _run_guarded("7 开关滑块连做三轮", check_toggle_on_off_three_times)
    _run_guarded("8 面板存活滑块（LaunchController 入口）", check_controller_survival_toggle)
    _run_guarded("2 元工具报 supervisor 事实", check_meta_tool_reports_supervisor_fact)
    _run_guarded("6 未知 id 响亮失败", check_meta_tool_unknown_id_is_loud)
    _run_guarded("3 overlay 保住超时", check_overlay_keeps_timeout)
    _run_guarded("5 prepare 钩子把 overlay 塞进 dsh", check_prepare_hook_injects_overlay_into_dsh)
    _run_guarded("端到端真 MCP 后端", check_end_to_end_real_mcp_backend)
    _run_guarded("launch 层接线", check_launch_wiring_at_the_launch_layer)
    if FAILS:
        print(f"aggregate_runtime_selfcheck: FAIL（{len(FAILS)} 条）：{FAILS}")
        return 1
    print("aggregate_runtime_selfcheck: PASS（1/2/3/4/5/6 + ⑦开关滑块 + 端到端都钉住）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
