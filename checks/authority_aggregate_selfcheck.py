"""4d 判据：把 `read_write_authority` 挂到 **AI 面（聚合端点）**，并让**面板读真值**。

## ⚠ 造法纪律（老规矩：先问"怎么故意退化还能让它绿"）

- **判据 1 的陷阱**：只断言"报里有那几个 id"⇒ **把没起来的也报一条**照样绿
  ⇒ 必须断言"**没起来的不在报里**"（正反两面都断言）。
- **判据 2 的陷阱**：只断言"有一个 unreadable"⇒ 让**整个调用抛错**也能在别处绿
  ⇒ 必须断言"**其余软件照常有值**"。
- **判据 4 的陷阱**（协调者点名的例子）：只断言"两次返回值一样"⇒ **缓存也绿**
  ⇒ 必须断言"**底层真的被调了两次**"（数计数，不看返回值）。
- **判据 5 的陷阱**：只断言"页面上有 mode 字样"⇒ **显示面板自己的意图**照样绿
  ⇒ 必须先读一次、**再用别的方式改写权**、再读 ⇒ 断言**显示变了**。

## 这里测的是**框架侧**（探针 + 聚合成形 + 面板取值）

软件侧那个 `read_write_authority` 由各项目实现；本档把"控制调用"短路掉
（只短路最后一次网络往返，**不短路** `available()` 等逻辑），
这样测的仍是**框架**怎么判可用、怎么汇总、怎么措辞。

## Known Limitations and Deferred Work

- **不启真软件**：真控制端点由项目侧提供并自测。本档保证框架侧的汇总/措辞/无缓存语义。
- **不测"软件侧返回的 mode 是否为真"**：那是软件侧的责任（框架透传）。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mecha.aggregate import Aggregator  # noqa: E402
from mecha.aggregate_runtime import AggregateRuntime  # noqa: E402
from mecha.errors import MechaError  # noqa: E402
from mecha.launch import LaunchController  # noqa: E402
from mecha.site import DSHSpec, ReadySpec, Service, Site  # noqa: E402
from mecha.supervise import Supervisor  # noqa: E402

FAILS: list[str] = []


def _check(name: str, cond: bool, extra: object = "") -> None:
    if cond:
        print(f"  ✔ {name}")
    else:
        print(f"  ✖ {name}" + (f"  <- {extra}" if extra != "" else ""))
        FAILS.append(name)


class Rig:
    """两个软件（都"在跑"）+ 一个没起来的；控制调用可编程。"""

    def __init__(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="4d-"))
        self.data = self.dir / "data"
        self.data.mkdir(parents=True, exist_ok=True)
        # 两个软件都让"控制端点可用"成立：写端口文件（读得出端口）
        for sid in ("AAA", "BBB", "CCC"):
            (self.data / sid).mkdir(exist_ok=True)
            (self.data / sid / ".control-port").write_text("65535", encoding="utf-8")
        self.svcs = {
            sid: Service(id=sid, brief=f"{sid} 简述",
                         argv=(sys.executable, "-c", "pass"), cwd=self.dir,
                         ready=ReadySpec(tcp=True, timeout_s=1.0),
                         data_root=self.data / sid, has_gui=(sid != "CCC"),
                         how_to_open=(f"{sid}：打开它自己的设置页切到人工" if sid == "AAA" else ""))
            for sid in ("AAA", "BBB", "CCC")}
        site = Site(name="d4", services=tuple(self.svcs.values()),
                    dsh=DSHSpec(command=(sys.executable, "-c", "pass"), cwd=self.dir))
        self.site = site
        self.super = Supervisor(log_dir=self.dir / "logs")
        self.agg = Aggregator()
        self.rt = AggregateRuntime(supervisor=self.super, services=tuple(self.svcs.values()),
                                   aggregator=self.agg, backend_factory=lambda *a, **k: None)
        #: 只把"真的在跑 / 挂上"的三个里的两个算进去（CCC 故意**没挂**=没起来）
        self.agg.wire(state_fn=self.super.state,
                      open_fn=self.rt.open_service, close_fn=self.rt.close_service,
                      known=[s.id for s in self.svcs.values()],
                      specs=tuple(self.svcs.values()),
                      authority_probe=self.rt.probe_authority)
        for sid in ("AAA", "BBB"):
            self.agg.attach(sid, _NullBackend())

    def close(self) -> None:
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)


class _NullBackend:
    async def tools(self) -> list[dict]:
        return [{"name": "do_thing", "description": "d", "inputSchema": {}}]

    async def call(self, name: str, args: dict) -> dict:
        return {"is_error": False, "text": "ok"}

    async def close(self) -> None:
        return None


def _stub_calls(script: dict):
    """短路**最后一次网络往返**：`script[svc_id]` 决定行为。

    - ``"ok"`` ⇒ 回一份写权；
    - ``"unreadable"`` ⇒ 回一个**域失败**（= 该软件还没实现这个读操作）；
    - 其它/缺省 ⇒ 抛连不上。
    返回一个**调用计数**列表（判据 4 数它）。
    """
    from mecha import software_control as sc

    calls: list[str] = []
    orig = sc.SoftwareControl._call

    def fake(self, op, args):
        calls.append(self.svc_id)
        if op != "read_write_authority":
            return {"ok": True}
        how = script.get(self.svc_id, "unreachable")
        if how == "ok":
            return {"is_error": False, "ok": True, "mode": "ai",
                    "ai_can_write": True, "human_holds": False}
        if how == "unreadable":
            # ⚠ 走**实现自己那份**域回执翻译（不在这里重写"什么算失败"）
            return sc._check_domain_result(self.svc_id, op,
                                           {"is_error": True,
                                            "text": "未知工具 read_write_authority"})
        raise sc.BackendConnectError("连不上（仿真）", kind="backend_unreachable")

    sc.SoftwareControl._call = fake
    return calls, orig


def check_reports_only_running() -> None:
    """⭐ ① 报**全部在跑的**软件，且**只报在跑的**。"""
    rig = Rig()
    calls, orig = _stub_calls({"AAA": "ok", "BBB": "ok"})
    try:
        out = rig.agg.authority_view_of_all()
        ids = [s["id"] for s in out["services"]]
        _check("① 在跑的两个都在报里", set(ids) == {"AAA", "BBB"}, f"ids={ids}")
        _check("① ⭐ **没起来的 CCC 不在报里**（不编造）", "CCC" not in ids, f"ids={ids}")
        _check("① `ok` 为真（整体成功）", out.get("ok") is True)
        a = next(s for s in out["services"] if s["id"] == "AAA")
        _check("① 每个条目带写权字段", a.get("mode") == "ai" and a.get("ai_can_write") is True,
               f"a={a}")
    finally:
        from mecha import software_control as sc
        sc.SoftwareControl._call = orig
        rig.close()


def check_one_unreadable_does_not_sink_the_rest() -> None:
    """⭐ ②③ 某一个读不到 ⇒ 只那一个 `unreadable`，其余照常；措辞与"确实没有"分开。"""
    rig = Rig()
    calls, orig = _stub_calls({"AAA": "ok", "BBB": "unreadable"})
    try:
        # ⚠ 退化版（"一个读不到就整体抛错"）会在这里**抛** ⇒ 必须**接住并判红**：
        #   否则"抛出去"会让整个用例中止、表现为"0 条红"（判据看似通过）。
        #   ⇒ **整体抛错本身就必须是红**。
        try:
            out = rig.agg.authority_view_of_all()
        except Exception as exc:                            # noqa: BLE001 - 期望实现不抛
            out = {"ok": False, "services": [], "raised": f"{type(exc).__name__}: {exc}"}
        _check("② ⭐ 整体**仍然成功**（一个读不到不拖垮整调用）",
               out.get("ok") is True and "raised" not in out, f"out={out}")
        by = {s["id"]: s for s in out.get("services", [])}
        _check("② ⭐ 读不到的那一个报 `unreadable`", "unreadable" in by.get("BBB", {}),
               f"bbb={by.get('BBB')}")
        _check("② ⭐ 其余**照常有值**", by.get("AAA", {}).get("mode") == "ai", f"aaa={by['AAA']}")
        _check("② 没起来的 CCC **仍然不在报里**（与「读不到」不是同一件事）",
               "CCC" not in by, f"by={list(by)}")
        # ⭐ 判据 3：措辞与"确实没有"分开
        _check("③ `unreadable` 带**具体原因**（不是一句「不可用」）",
               "control_rejected" in str(by.get("BBB", {}).get("unreadable", "")), f"bbb={by['BBB']}")
        _check("③ 那条原因**不是**「没有这个软件」（读不到 ≠ 确实没有）",
               "没有这个软件" not in str(by.get("BBB", {}).get("unreadable", "")), f"bbb={by['BBB']}")
    finally:
        from mecha import software_control as sc
        sc.SoftwareControl._call = orig
        rig.close()


def check_no_cache() -> None:
    """⭐ ④ **无缓存**：连调两次，底层**真的被调两次**（数调用，不看返回值）。"""
    rig = Rig()
    calls, orig = _stub_calls({"AAA": "ok", "BBB": "ok"})
    try:
        rig.agg.authority_view_of_all()
        n1 = len(calls)
        rig.agg.authority_view_of_all()
        n2 = len(calls)
        _check("④ 第一次真的问了两个软件", n1 == 2, f"calls={calls}")
        _check("④ ⭐ 第二次**也真的问了**（总调用 = 4，不是 2）", n2 == 4, f"calls={calls}")
    finally:
        from mecha import software_control as sc
        sc.SoftwareControl._call = orig
        rig.close()


def check_how_to_open_from_declaration() -> None:
    """⭐ ⑥ `how_to_open` **来自声明**（框架里没有项目名/写死的句子）。"""
    rig = Rig()
    calls, orig = _stub_calls({"AAA": "ok", "BBB": "ok"})
    try:
        out = rig.agg.authority_view_of_all()
        _check("⑥ `how_to_open` 是**声明里那句原话**",
               out.get("how_to_open") == "AAA：打开它自己的设置页切到人工",
               f"got={out.get('how_to_open')!r}")
        _check("⑥ 框架**没有**改写它（不是框架的缺省句）",
               "启动面板上把写权切到人工" not in str(out.get("how_to_open")),
               f"got={out.get('how_to_open')!r}")
    finally:
        from mecha import software_control as sc
        sc.SoftwareControl._call = orig
        rig.close()


def check_ai_surface_has_no_write_ops() -> None:
    """⑦ AI 面上**仍然没有** `set_write_authority` / `open_manual_surface`。"""
    rig = Rig()
    calls, orig = _stub_calls({"AAA": "ok", "BBB": "ok"})
    try:
        names = [s["name"] for s in rig.agg.registry().schemas()]
        _check("⑦ AI 面上**有** `read_write_authority`", "read_write_authority" in names,
               f"names={names}")
        _check("⑦ ⭐ AI 面上**没有** `set_write_authority`",
               "set_write_authority" not in names, f"names={names}")
        _check("⑦ ⭐ AI 面上**没有** `open_manual_surface`",
               "open_manual_surface" not in names, f"names={names}")
    finally:
        from mecha import software_control as sc
        sc.SoftwareControl._call = orig
        rig.close()


def check_panel_shows_read_value_not_intent() -> None:
    """⭐ ⑤ 面板显示的是**读回来的值**：改写权之后再读 ⇒ **显示变了**。

    ## 为什么这条能抓住"显示自己意图"的退化
    若面板显示的是自己记的意图，那么"用**别的方式**改写权"之后显示**不会变**
    （面板不知道）。⇒ 本档刻意**绕过面板**去改（直接改仿真软件侧的真值）。
    """
    rig = Rig()
    state = {"mode": "ai"}

    from mecha import software_control as sc
    orig = sc.SoftwareControl._call

    def fake(self, op, args):
        if op != "read_write_authority":
            return {"ok": True}
        return {"is_error": False, "ok": True, "mode": state["mode"],
                "ai_can_write": state["mode"] in ("ai", "open"),
                "human_holds": state["mode"] in ("human", "open")}

    sc.SoftwareControl._call = fake
    try:
        ctl = LaunchController(sites=(rig.site,), runtime=rig.dir / "rt",
                               supervisor=rig.super)
        ctl._started = True
        s1 = ctl.write_authority_state("AAA")
        _check("⑤ 前置：面板读到了真值（AI 持有）",
               (s1.get("read") or {}).get("mode") == "ai", f"s1={s1}")
        state["mode"] = "human"          # ⭐ **绕过面板**改真值
        s2 = ctl.write_authority_state("AAA")
        _check("⑤ ⭐ 真值一变 ⇒ 面板**跟着变**（显示的不是自己记的意图）",
               (s2.get("read") or {}).get("mode") == "human", f"s2={s2}")
        _check("⑤ 且两个布尔也跟着真值走",
               (s2.get("read") or {}).get("human_holds") is True
               and (s2.get("read") or {}).get("ai_can_write") is False, f"s2={s2}")
    finally:
        sc.SoftwareControl._call = orig
        rig.close()


def check_panel_says_unreadable_when_read_fails() -> None:
    """⑤ 的对偶：**读不到时如实说"读不到"**（不显示旧值、不显示 0）。"""
    rig = Rig()
    from mecha import software_control as sc
    orig = sc.SoftwareControl._call

    def fake(self, op, args):
        if op != "read_write_authority":
            return {"ok": True}
        # ⚠ 走**实现自己那份**域回执翻译（不在这里重写"什么算失败"）
        return sc._check_domain_result(self.svc_id, op,
                                       {"is_error": True,
                                        "text": "未知工具 read_write_authority"})

    sc.SoftwareControl._call = fake
    try:
        ctl = LaunchController(sites=(rig.site,), runtime=rig.dir / "rt",
                               supervisor=rig.super)
        ctl._started = True
        s = ctl.write_authority_state("AAA")
        _check("⑤ 读失败 ⇒ `read` 为 None（**不编造**）", s.get("read") is None, f"s={s}")
        _check("⑤ 且给出「读不到」的理由", bool(s.get("read_error")), f"s={s}")
        from mecha.providers.status_page import render_page

        class Ctl:
            def sites(self):
                class St:
                    name = "d4"
                return (St(),)

            def services(self):
                return (rig.svcs["AAA"],)

            def started(self):
                return True

            def intent(self):
                return {}

            def state(self):
                return {"children": [{"id": "AAA", "pid": 1, "state": "ready"}],
                        "sites": ["d4"], "started": True, "enabled": [], "cleaned": [],
                        "last_error": "", "intent": {}}

            def write_authority_state(self, sid):
                return s

        page = render_page(Ctl())
        _check("⑤ ⭐ 页面明写「读不到」（不是显示旧值/0）", "读不到" in page, "页面里没写读不到")
        _check("⑤ 页面**不给可点的写权按钮**（读不到时按下去也是猜）",
               "mode('AAA'" not in page, "读不到却给了按钮")
    finally:
        sc.SoftwareControl._call = orig
        rig.close()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("[authority_aggregate_selfcheck] 4d：AI 面聚合写权 + 面板读真值")
    check_reports_only_running()
    check_one_unreadable_does_not_sink_the_rest()
    check_no_cache()
    check_how_to_open_from_declaration()
    check_ai_surface_has_no_write_ops()
    check_panel_shows_read_value_not_intent()
    check_panel_says_unreadable_when_read_fails()
    if FAILS:
        print(f"authority_aggregate_selfcheck: FAIL（{len(FAILS)} 条）：{FAILS}")
        return 1
    print("authority_aggregate_selfcheck: PASS（1—7 都钉住）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
