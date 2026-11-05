"""判据 ㉕：控制台上的 **AI 宿主二选一**（dsh / codex）真的换得动。

## 为什么要有它

启动界面上原来只有一行 dsh：它既不能选宿主（宿主种类由 `--ai-host` 在命令行定死），
也不参与启动计划（`build_children` 无条件把宿主 child 追加在最后）⇒ 那一行在启动阶段
是个摆设，只用于启动后手动重启。现在它变成**两类之一**（项目可多选 / 宿主单选），
而且**启动前与运行中都能切**。

换宿主是**真的动东西**：停旧宿主进程、换适配器、重挂工具表刷新口、写新宿主的接入
配置、必要时把新宿主进程拉起来。所以这里逐条钉住，而不是只断言"字段变了"：

| # | 断言 | 为什么不能省 |
|---|---|---|
| ① | `host_choices()` 只有 dsh / codex 两项 | 控制台不该把 `none` 变成一个按钮 |
| ② | 启动前切 codex：**不动任何进程** | 还没启动，切的是"这一轮用哪个" |
| ③ | 切 codex 后**启动计划里没有 dsh** | 这才是"选 codex 就不起 dsh"的实质 |
| ④ | 切 codex 真的写出隔离 `CODEX_HOME/config.toml` 且指向当前聚合端点 | 外部宿主的全部工作就是这份配置 |
| ⑤ | 运行中 dsh → codex：**先停 dsh**，再切适配器 | 两个宿主同时连着会各说各话 |
| ⑥ | 运行中 codex → dsh：**把 dsh 起回来** | 否则界面上写着 dsh，实际没有进程 |
| ⑦ | 刷新口随之换掉（dsh 有 / codex 两个都是 None） | 不换会继续重写 dsh 的 overlay |
| ⑧ | spawn 钩子**每个宿主只装一次** | supervisor 只能追加钩子；重复装会让 `--patch` 叠两份 |
| ⑨ | 停不掉旧宿主 ⇒ **不换**且如实报 | 否则会出现"两个宿主都以为自己在位" |
| ⑩ | `none` 走控制台 ⇒ 拒绝并说明 | 那个档位不是按钮 |
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mecha.errors import MechaError  # noqa: E402
from mecha.host_adapter import HostWiring  # noqa: E402
from mecha.launch import LaunchController, build_children  # noqa: E402
from mecha.site import DSHSpec, ReadySpec, Service, Site  # noqa: E402

FAILS: list[str] = []


def _check(name: str, cond: bool, extra: object = "") -> None:
    print(f"  {'✓' if cond else '✗'} {name}" + (f" —— {extra}" if not cond and extra else ""))
    if not cond:
        FAILS.append(name)


class FakeSupervisor:
    """只记"谁被起/停"，不真 spawn；钩子表按真实现的语义（只能追加）。"""

    def __init__(self, log_dir: Path) -> None:
        self.log_dir = log_dir
        self.cleaned: list[str] = []
        self.started: list[str] = []
        self.stopped: list[str] = []
        self.prepare_hooks: list[object] = []
        self.alive: set[str] = set()
        self.fail_stop = False

    def state_ids(self) -> set[str]:
        return set(self.alive)

    def start(self, child) -> None:
        self.started.append(str(child.id))
        self.alive.add(str(child.id))

    def stop(self, child_id: str) -> None:
        if self.fail_stop:
            raise MechaError("停不掉", kind="stop_failed", hint="判据故意注入")
        self.stopped.append(str(child_id))
        self.alive.discard(str(child_id))

    def add_prepare_hook(self, hook) -> None:
        self.prepare_hooks.append(hook)

    def add_ready_hook(self, hook) -> None:
        pass

    def add_gone_hook(self, hook) -> None:
        pass

    def state(self) -> dict:
        return {"children": [{"id": cid, "pid": 4242, "state": "running"}
                             for cid in sorted(self.alive)]}

    def shutdown(self) -> None:
        self.alive.clear()


class FakeRuntime:
    """只记 bind_host 收到的两个刷新口。"""

    def __init__(self) -> None:
        self.binds: list[tuple[bool, bool]] = []

    def bind_host(self, *, overlay_refresh, client_refresh) -> None:
        self.binds.append((overlay_refresh is not None, client_refresh is not None))


def _site(tmp: Path) -> Site:
    artifact = tmp / "panel.mjs"
    artifact.write_text("// 判据替身\n", encoding="utf-8")
    svc = Service(id="AAA", brief="判据用软件", argv=(sys.executable, "-c", "pass"),
                  cwd=tmp, ready=ReadySpec(port_file=".mcp-port"),
                  data_root=tmp / "data", has_gui=False)
    dsh = DSHSpec(command=(sys.executable, "-c", "pass"), cwd=tmp,
                  port_preference=3081, plugin_artifacts=(artifact,))
    return Site(name="S", services=(svc,), dsh=dsh)


def _controller(tmp: Path, kind: str = "dsh"
                ) -> tuple[LaunchController, FakeSupervisor, FakeRuntime]:
    site = _site(tmp)
    sup = FakeSupervisor(tmp / "logs")
    runtime = tmp
    ctl = LaunchController(sites=(site,), runtime=runtime, supervisor=sup,
                           host_kind=kind)
    agg = FakeRuntime()
    ctl.agg_runtime = agg
    wiring = HostWiring(supervisor=sup, aggregator=None, runtime=object(),
                        endpoint=None, mcp_port=3089)
    #: ⚠ 照 `main()` 的真实顺序：**先**由接线装当前宿主的 spawn 钩子，**再**把它
    #: 交给控制器（`attach_wiring` 只把"已装过"记下来，不重复装）。
    ctl.host_adapter.install(ctl.host_plan(aggregate_port=3089,
                                          aggregate_url="http://127.0.0.1:3089/mcp"),
                             wiring)
    ctl.attach_wiring(wiring)
    ctl.prepare_host(aggregate_port=3089,
                     aggregate_url="http://127.0.0.1:3089/mcp")
    return ctl, sup, agg


def check_pre_start_switch(tmp: Path) -> None:
    ctl, sup, agg = _controller(tmp)
    choices = ctl.host_choices()
    _check("① 控制台只给 dsh / codex 两个选项",
           [c["kind"] for c in choices] == ["dsh", "codex"], str(choices))
    _check("① 默认就是 dsh", ctl.host_kind.value == "dsh")

    out = ctl.set_host_kind("codex")
    _check("② 启动前切 codex：不动任何进程",
           out["ok"] and not sup.started and not sup.stopped, f"{out}")
    _check("② 适配器与自省同步指向 codex",
           ctl.host_kind.value == "codex" and ctl.host_info()["kind"] == "codex"
           and ctl.host_info()["managed"] is False)

    ids = [c.id for c in build_children(ctl.sites, ["AAA"], ctl.host_kind)]
    _check("③ 切 codex 后启动计划里没有 dsh", "dsh" not in ids, str(ids))

    config = tmp / "codex-home" / "config.toml"
    text = config.read_text(encoding="utf-8") if config.is_file() else ""
    _check("④ 写出了隔离 CODEX_HOME 配置，且指向当前聚合端点",
           config.is_file() and 'url = "http://127.0.0.1:3089/mcp"' in text,
           f"exists={config.is_file()}")
    _check("⑦ 切到 codex 后两个刷新口都清空",
           agg.binds[-1] == (False, False), str(agg.binds))

    same = ctl.set_host_kind("codex")
    _check("② 重复选同一个宿主：不改、不报错",
           same["ok"] and same.get("changed") is False and same["steps"] == [])


def check_live_switch(tmp: Path) -> None:
    ctl, sup, agg = _controller(tmp)
    ctl._started = True
    sup.alive.add("dsh")                      # 模拟 dsh 已在跑
    hooks_before = len(sup.prepare_hooks)
    _check("⑧ 前置：dsh 的 spawn 钩子已在接线时装好", hooks_before == 1, str(hooks_before))

    out = ctl.set_host_kind("codex")
    _check("⑤ 运行中 dsh → codex：先停 dsh",
           out["ok"] and sup.stopped == ["dsh"], f"{sup.stopped}")
    _check("⑦ 刷新口换成外部宿主（两个都 None）",
           agg.binds[-1] == (False, False), str(agg.binds))

    out2 = ctl.set_host_kind("dsh")
    _check("⑥ 运行中 codex → dsh：把 dsh 起回来",
           out2["ok"] and sup.started.count("dsh") >= 1, str(sup.started))
    _check("⑦ 刷新口换回 dsh（两个都有）",
           agg.binds[-1] == (True, True), str(agg.binds))

    ctl.set_host_kind("codex")
    ctl.set_host_kind("dsh")
    _check("⑧ 来回切换：钩子不重复装（只追加的接口，装两次会叠两份 --patch）",
           len(sup.prepare_hooks) == hooks_before, str(len(sup.prepare_hooks)))


def check_switch_from_codex(tmp: Path) -> None:
    """初值是 codex 的情形：钩子在**切到 dsh 那一下**才装，且只装一次。"""
    ctl, sup, _agg = _controller(tmp, kind="codex")
    _check("⑧ 初值 codex：没有 dsh 的 spawn 钩子", sup.prepare_hooks == [], "有残留")
    ctl.set_host_kind("dsh")
    _check("⑧ 切到 dsh：装且只装一个钩子", len(sup.prepare_hooks) == 1,
           str(len(sup.prepare_hooks)))
    ctl.set_host_kind("codex")
    ctl.set_host_kind("dsh")
    _check("⑧ 再切回来：仍是一个钩子", len(sup.prepare_hooks) == 1,
           str(len(sup.prepare_hooks)))


def check_refusals(tmp: Path) -> None:
    ctl, sup, _agg = _controller(tmp)
    ctl._started = True
    sup.alive.add("dsh")
    sup.fail_stop = True
    out = ctl.set_host_kind("codex")
    _check("⑨ 停不掉旧宿主 ⇒ 不换，并如实报",
           out["ok"] is False and ctl.host_kind.value == "dsh"
           and any(s["ok"] is False for s in out["steps"]), str(out))

    sup.fail_stop = False
    none = ctl.set_host_kind("none")
    _check("⑩ none 走控制台被拒，并说明它是命令行档位",
           none["ok"] is False and ctl.host_kind.value == "dsh"
           and "命令行" in str(none.get("error", "")), str(none))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("宿主二选一判据：")
    with tempfile.TemporaryDirectory(prefix="mecha-host-switch-") as td:
        root = Path(td)
        for name, fn in (("pre", check_pre_start_switch), ("live", check_live_switch),
                         ("fromcodex", check_switch_from_codex),
                         ("refuse", check_refusals)):
            sub = root / name
            sub.mkdir()
            fn(sub)
    if FAILS:
        print(f"host_switch_selfcheck: FAIL（{len(FAILS)} 条）")
        return 1
    print("host_switch_selfcheck: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
