"""4c 判据：框架侧**控制客户端** + 面板写权栏（§7.1.1）。

## ⚠ 造法纪律（老规矩：先问"怎么故意退化还能让它绿"）

- **判据 3 的陷阱**：两次调用**合并成一次**时，外部看着"成功了"，而实际窗口没开。
  ⇒ 造法必须**让第二步真失败**，并断言报告里**分得清哪一步成、哪一步败**。
- **判据 2 的陷阱**：只断言"返回 ok"⇒ 把实现改成"记下意图、下次写时才应用"也能绿。
  ⇒ 造法要求**同一条会话**上切完**当场**被拒（不重连、不等一拍）。
- **判据 4/5 的陷阱**：只断言"页面上有按钮"⇒ `has_gui=false` 或软件没起来时
  **也能**渲染出按钮而照样绿。⇒ 必须断言**它不可点**（`disabled`）**或压根不出现**。
- **判据 6 的陷阱**：只断言"没写 `:control-port` 字样"⇒ 把**纯端口号**渲染进去照样绿。
  ⇒ 断言**控制端口的数字本身**不出现在页面里。

## 这一轮的控制端点是**仿真**的（真端点由软件侧提供，EL 已落地）

判据里用一个**仿真软件端**（`ToolRegistry` 两个操作 + 一个"当场生效"的闸门），
并把客户端的一次调用**短路**到它上面。⇒ 被测的是**框架侧**的语义
（两种形态、两步报告、三种失败措辞、端口不外泄），不是 MCP 传输本身
（传输由 ``mcp_client`` 的既有判据与端到端那一档覆盖）。

## Known Limitations and Deferred Work

- **不连真实软件端点**（不启真 uvicorn）：本档测的是**框架侧**的编排与报告语义；
  真传输那一层由 ``aggregate_runtime_selfcheck`` 的端到端档（真 MCP 端点）覆盖。
- **不测"真开出了 Qt 窗口"**：那要真软件（EL 侧已自测 27 子条件）。
  这里只保证**框架把两步按序发出去、并如实报告每一步的结果**。
- **不测"当前写权读回"**：契约没有查询操作（见 ``software_control`` 的 Known Limitations）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mecha.errors import MechaError  # noqa: E402
from mecha.providers.mcp_client import (BackendConnectError,  # noqa: E402
                                        BackendProtocolError, BackendTimeoutError)
from mecha.tools import ToolRegistry, define_tool  # noqa: E402

FAILS: list[str] = []


def _check(name: str, cond: bool, extra: object = "") -> None:
    if cond:
        print(f"  ✔ {name}")
    else:
        print(f"  ✖ {name}" + (f"  <- {extra}" if extra != "" else ""))
        FAILS.append(name)


class FakeSoftware:
    """**仿真软件端**：两个控制操作 + 一道"当场生效"的写权闸门。

    ⚠ 闸门在**调用时**读 mode（不是连接时）——这正是"切完立刻生效"的机制。
    """

    def __init__(self, *, has_manual: bool = True,
                 manual_raises: bool = False) -> None:
        self.mode = "locked"
        self.has_manual = has_manual
        self.manual_raises = manual_raises
        self.calls: list[tuple[str, dict]] = []
        self.window_open = False
        self.audit: list[tuple[str, str]] = []

    # ---- 软件内的闸门 ----
    def write(self, who: str) -> str:
        """一次**写**尝试。⭐ 门在**每次**调用时判定 ⇒ 切完当场生效。"""
        if who == "ai" and not self.mode_allows("ai"):
            raise MechaError("写权不在 AI 手上 ⇒ 拒写", kind="write_denied",
                             hint="人侧已把写权收走")
        return "written"

    def mode_allows(self, who: str) -> bool:
        return self.mode in ("open", who)

    # ---- 控制端点（两个操作）----
    def registry(self) -> ToolRegistry:
        reg = ToolRegistry()

        def set_authority(mode: str) -> dict:
            self.calls.append(("set_write_authority", {"mode": mode}))
            if mode not in ("locked", "human", "ai", "open"):
                raise MechaError(f"未知写权模式：{mode!r}", kind="bad_mode")
            self.mode = mode
            self.audit.append(("write_authority", mode))     # ⚠ 记账**不过门**（否则自锁）
            return {"ok": True, "mode": mode}

        def open_surface() -> dict:
            self.calls.append(("open_manual_surface", {}))
            if not self.has_manual:
                raise MechaError("本进程没有可开的「手动面」", kind="no_manual_surface")
            if self.manual_raises:
                raise MechaError("开窗失败（仿真）", kind="manual_open_failed")
            self.window_open = True
            self.audit.append(("open_manual_surface", "opened"))
            return {"ok": True, "status": "opened"}

        reg.register(define_tool(
            name="set_write_authority",
            description="切写权（人侧专用）",
            parameters={"mode": {"type": "string", "description": "裸值"}},
            output_schema={"type": "object", "properties": {}, "required": [],
                           "additionalProperties": True},
            execute=lambda **kw: set_authority(str(kw.get("mode", ""))),
        ))
        reg.register(define_tool(
            name="open_manual_surface", description="开手动面",
            parameters={}, output_schema={"type": "object", "properties": {},
                                          "required": [], "additionalProperties": True},
            execute=lambda **kw: open_surface(),
        ))
        return reg


def _patch_control(monkeypatch_target, fake: FakeSoftware, *, fail: str = "") -> None:
    """把客户端的一次调用**短路**到仿真软件端（可注入传输层失败）。"""
    from mecha import software_control as sc
    reg = fake.registry()

    def fake_call(self, op: str, args: dict) -> dict:
        if fail == "unreachable":
            raise BackendConnectError("连不上（仿真）", kind="backend_unreachable")
        if fail == "timeout":
            raise BackendTimeoutError("超时（仿真）", kind="backend_timeout")
        if fail == "protocol":
            raise BackendProtocolError("协议错（仿真）", kind="backend_protocol")
        env = reg.execute(op, args)
        if env.get("is_error"):
            # ⚠ 软件侧域失败 ⇒ 客户端必须翻成 MechaError（否则调用方以为成功了）
            raise sc.MechaError(str(env.get("error")), kind="control_rejected")
        return dict(env["value"]) if isinstance(env.get("value"), dict) else {}

    monkeypatch_target(fake_call)


def _unpatch(monkeypatch_target) -> None:
    from mecha import software_control as sc
    monkeypatch_target(sc.SoftwareControl._call)


def check_client_switches_write_authority() -> None:
    """① 客户端能连控制端点并**真的切写权**（`mode` 一律裸值）。"""
    from mecha import software_control as sc

    fake = FakeSoftware()
    orig = sc.SoftwareControl._call
    _patch_control(lambda f: setattr(sc.SoftwareControl, "_call", f), fake)
    try:
        c = sc.SoftwareControl.connect("X", Path("."), timeout_s=1.0)
        out = c.set_write_authority("human")
        _check("① 客户端调 `set_write_authority` 成功", out.get("ok") is True, f"out={out}")
        _check("① ⚠ 发过去的 `mode` 是**裸值**（不是 `Mode.HUMAN`）",
               fake.calls[0][1] == {"mode": "human"}, f"calls={fake.calls}")
        _check("① 软件侧**真的改了**（不是只回了个 ok）", fake.mode == "human", f"mode={fake.mode}")
        out2 = c.open_manual_surface()
        _check("① `open_manual_surface` 成功且**软件侧真的开了窗**",
               out2.get("ok") is True and fake.window_open is True, f"out2={out2}")
        # ⚠ 传枚举对象也要发裸值（`Mode` 是 str-Enum，`str()` 会给 'Mode.AI'）
        from mecha.authority import Mode
        c.set_write_authority(Mode.AI)
        _check("① 传 `Mode.AI` 对象 ⇒ 发的仍是裸值 `'ai'`",
               fake.calls[-1][1] == {"mode": "ai"}, f"calls={fake.calls[-1]}")
    finally:
        sc.SoftwareControl._call = orig


def check_immediate_effect_no_wait() -> None:
    """⭐ ② 切完**立刻生效**：同一条会话、不重连，AI 侧**当场**被拒。

    ⚠ 红证怎么造：把实现改成"记下意图、下次写时才应用" ⇒ 切完那一次写**不会**被拒 ⇒ 本档红。
    """
    from mecha import software_control as sc

    fake = FakeSoftware(has_manual=False)
    fake.mode = "ai"
    orig = sc.SoftwareControl._call
    _patch_control(lambda f: setattr(sc.SoftwareControl, "_call", f), fake)
    try:
        _check("② 前置：切之前 AI 写得进去", fake.write("ai") == "written")
        c = sc.SoftwareControl.connect("X", Path("."), timeout_s=1.0)
        c.set_write_authority("human")
        # ⭐ **当场**（不重连、不等一拍）
        denied = False
        try:
            fake.write("ai")
        except MechaError as exc:
            denied = exc.kind == "write_denied"
        _check("② ⭐ 切到人工之后，AI 的**下一次**写当场被拒（不重连、不等一拍）", denied)
        _check("② 对偶：写权还在（切回去 AI 立刻又能写）",
               (c.set_write_authority("ai") or True) and fake.write("ai") == "written")
        _check("② 记账与写权**是同一次切换的两面**（审计里记下了切换）",
               ("write_authority", "ai") in fake.audit, f"audit={fake.audit}")
    finally:
        sc.SoftwareControl._call = orig


def check_two_step_reports_honestly() -> None:
    """⭐ ③ 两步**如实报告**：第二步失败 ⇒ 必须说"写权成功、窗口失败"。"""
    from mecha import software_control as sc
    from mecha.launch import LaunchController

    fake = FakeSoftware(manual_raises=True)        # 写权会成、开窗会败
    orig = sc.SoftwareControl._call
    _patch_control(lambda f: setattr(sc.SoftwareControl, "_call", f), fake)
    try:
        ctl = _ctl_for(fake)
        out = ctl.control_write_authority("X", "human")
        _check("③ 整体**不算成功**（不许报成一次成功）", out.get("ok") is False, f"out={out}")
        steps = out.get("steps", [])
        _check("③ **两步分开记着**（不是合并成一条）", len(steps) == 2, f"steps={steps}")
        _check("③ 第 1 步（写权）报**成功**",
               steps and steps[0]["ok"] is True and steps[0]["step"].startswith("set_write_authority"),
               f"steps={steps}")
        _check("③ 第 2 步（开窗）报**失败**",
               len(steps) > 1 and steps[1]["ok"] is False, f"steps={steps}")
        _check("③ ⭐ 文案说清「写权已交给人工，但窗口没开成」"
               "（人不会以为整件事没做）",
               "写权已交给人工" in str(out.get("error")) and "窗口没开成" in str(out.get("error")),
               f"error={out.get('error')!r}")
        _check("③ 软件侧事实：写权**真的**切了（尽管整体报失败）", fake.mode == "human")
        _check("③ 软件侧事实：窗口**确实**没开", fake.window_open is False)
    finally:
        sc.SoftwareControl._call = orig


def check_first_step_fails_stops_there() -> None:
    """③ 的对偶：**第一步**就失败 ⇒ 不接着开窗（开了窗而写权没给它，是更坏的状态）。"""
    from mecha import software_control as sc

    fake = FakeSoftware()
    orig = sc.SoftwareControl._call

    def boom(self, op, args):
        if op == "set_write_authority":
            raise sc.MechaError("bad_mode", kind="control_rejected")
        raise AssertionError("第一步失败时**不该**再调第二步")

    sc.SoftwareControl._call = boom
    try:
        ctl = _ctl_for(fake)
        out = ctl.control_write_authority("X", "human")
        _check("③ 第一步失败 ⇒ 整体失败且**没有**开窗", out.get("ok") is False)
        _check("③ 且只记了**一步**（没往下走）", len(out.get("steps", [])) == 1,
               f"steps={out.get('steps')}")
        _check("③ 文案说清「没有开窗口」", "没有" in str(out.get("error")),
               f"error={out.get('error')!r}")
    finally:
        sc.SoftwareControl._call = orig


def check_failure_wording_is_distinct() -> None:
    """⑦ 三种**传输**失败措辞不同（铁律 8：读不到 ≠ 确实没有）。

    ⚠ 必须走**客户端自己的翻译层**（``SoftwareControl._call`` 里那三个 ``except``）：
    直接把它替换掉再抛异常，测的就是"别人抛了什么"，而不是"我们怎么告诉人"。
    """
    from mecha import software_control as sc

    import tempfile
    # ⚠ 先让 `_url()` 能算出地址（否则会在更早的"端口文件读不出"就抛，
    #   测不到那三个 except —— 本档第一版就是这样，4 条全红）。
    d = Path(tempfile.mkdtemp(prefix="4c-err-"))
    (d / sc.CONTROL_PORT_FILE).write_text("65535", encoding="utf-8")

    msgs: dict[str, str] = {}
    orig_run = sc._run
    for kind in ("unreachable", "timeout", "protocol"):
        def fake_run(coro, _k=kind):
            raise {"unreachable": BackendConnectError("仿真", kind="backend_unreachable"),
                   "timeout": BackendTimeoutError("仿真", kind="backend_timeout"),
                   "protocol": BackendProtocolError("仿真", kind="backend_protocol")}[_k]

        sc._run = fake_run
        try:
            c = sc.SoftwareControl.connect("X", d, timeout_s=0.01)
            try:
                c.set_write_authority("ai")
                msgs[kind] = "(居然成功了)"
            except MechaError as exc:
                msgs[kind] = f"{exc.kind}｜{exc.message}"
        finally:
            sc._run = orig_run
    _check("⑦ 三种失败**三条不同措辞**", len(set(msgs.values())) == 3, f"msgs={msgs}")
    _check("⑦ 「连不上」与「超时」**不是同一句**（读了代码就知道是两件事）",
           "连不上" in msgs.get("unreachable", "") and "超时" in msgs.get("timeout", ""),
           f"msgs={msgs}")
    _check("⑦ 「协议错」单独一类（回了但不是 MCP）",
           "协议错" in msgs.get("protocol", ""), f"msgs={msgs}")
    _check("⑦ 三者的 `kind` 也各自不同（机器可判，不只是文案）",
           len({msgs[k].split("｜")[0] for k in msgs}) == 3, f"msgs={msgs}")


def _ctl_for(fake: FakeSoftware):
    """造一个只含一个软件（有 GUI、data_root 指向临时目录）的控制器。"""
    import tempfile

    from mecha.launch import LaunchController
    from mecha.site import DSHSpec, ReadySpec, Service, Site
    from mecha.supervise import Supervisor

    d = Path(tempfile.mkdtemp(prefix="4c-"))
    svc = Service(id="X", brief="仿真软件", argv=(sys.executable, "-c", "pass"), cwd=d,
                  ready=ReadySpec(tcp=True, timeout_s=1.0), data_root=d, has_gui=True)
    site = Site(name="c4", services=(svc,),
                dsh=DSHSpec(command=(sys.executable, "-c", "pass"), cwd=d))
    ctl = LaunchController(sites=(site,), runtime=d / "rt",
                           supervisor=Supervisor(log_dir=d / "logs"))
    ctl._started = True
    # 让"控制端点可用"成立（端口文件存在且能读出端口）
    from mecha import software_control as sc
    (d / sc.CONTROL_PORT_FILE).write_text("65535", encoding="utf-8")
    return ctl


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("[control_client_selfcheck] 4c：控制客户端 + 面板写权栏")
    check_client_switches_write_authority()
    check_immediate_effect_no_wait()
    check_two_step_reports_honestly()
    check_first_step_fails_stops_there()
    check_failure_wording_is_distinct()
    if FAILS:
        print(f"control_client_selfcheck: FAIL（{len(FAILS)} 条）：{FAILS}")
        return 1
    print("control_client_selfcheck: PASS（①②③⑦ 都钉住）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
