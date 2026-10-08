"""``supervise`` / ``site`` / ``launch`` / ``status_page`` 的判据（**全部用 mock 子进程**）。

## 为什么全部用 mock

被测的是**进程管理**（spawn / 就绪 / 重启 / 杀树），不是任何真实软件。
用真软件会让判据依赖环境（且慢），用 mock 才能钉住**时序**与**边界**。
先例：EL 的 ``checks/launcher_selfcheck.py:103-128`` 就是拿假进程钉
"spawn → terminate → 等真退出 → respawn"那段时序的。

## ⚠ 红→绿怎么重放（第 3、7 条必须能红）

第 3 条（假绿）与第 7 条（杀树）是本次改造的**核心两条**，写成"直接通过"是假绿。
本判据支持**指向别处的一份实现**：

    MECHA_SUPERVISE_PATH=<另一份 supervise.py>  python checks/launch_selfcheck.py

⇒ 把缺陷人为放回去（补丁**只在临时副本上**、并**自证补丁真的落上了**，R16），
跑同一条判据 ⇒ 它**必须红**。两条红证的具体补丁见提交说明与 README。

## Known Limitations and Deferred Work

- **不测真实 dsh / 真实软件**：那些是接入仓自己的验收（本判据只管机制）。
- 第 1 条只断言**旗标在**（``creationflags`` 含两位），不真去数窗口——
  "没黑框"没法在无头 CI 上机械断言；能断言的是"用了不开窗的旗标"。
- 第 6 条的重启用**很小的 max_restarts**（不等 60s 窗口）⇒ 判据快且确定。
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILS: list[str] = []
SKIPS: list[str] = []


def _check(name: str, cond: bool, extra: object = "") -> bool:
    if cond:
        print(f"  ✔ {name}")
    else:
        print(f"  ✖ {name}" + (f"  <- {extra}" if extra != "" else ""))
        FAILS.append(name)
    return bool(cond)


def _load_supervise():
    """从 ``MECHA_SUPERVISE_PKG`` / ``MECHA_SUPERVISE_PATH``（若有）或本仓加载 ``supervise``。

    ⚠ 按**文件**加载时 ``supervise.py`` 里的**相对 import**（``from .errors import …``）
    会失败（没有父包）⇒ 红证请用 ``MECHA_SUPERVISE_PKG``：把打了补丁的那份放进一个
    **整包的临时副本**里、按包名 import（相对 import 与同包模块都对得上）。
    """
    pkg = os.environ.get("MECHA_SUPERVISE_PKG", "").strip()
    if pkg:
        sys.path.insert(0, pkg)
        # ⚠ **不要在这里写 `import importlib`**：函数内任何 `import X` 都会让 `X`
        # 变成**整个函数的局部名** ⇒ 走下面 `MECHA_SUPERVISE_PATH` 分支时
        # 那句赋值还没执行，于是 `importlib` 未绑定 ⇒ `UnboundLocalError`
        # （**实测踩到**：这条判据宣称支持两种覆盖机制，而按文件的那个**从来没工作过**，
        #  我是第一个真去用它做红证的人）。顶层已有 `import importlib.util`。
        for name in [m for m in list(sys.modules) if m == "mecha" or m.startswith("mecha.")]:
            del sys.modules[name]
        mod = importlib.import_module("mecha.supervise")
        print(f"  [探针] 用的是包副本 {pkg}（MECHA_SUPERVISE_PKG）")
        return mod
    override = os.environ.get("MECHA_SUPERVISE_PATH", "").strip()
    if override:
        path = Path(override)
        spec = importlib.util.spec_from_file_location("_probe_supervise", path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_probe_supervise"] = mod
        spec.loader.exec_module(mod)
        print(f"  [探针] 用的是 {path}（MECHA_SUPERVISE_PATH）")
        return mod
    from mecha import supervise
    return supervise


S = _load_supervise()

# --------------------------------------------------------------------------- mock

#: 一个可编程的 mock 子进程：按脚本干活（写端口文件、起孙进程、打印、退出码）。
MOCK = r'''
import os, sys, time, json, socket, subprocess
mode = sys.argv[1]
here = os.path.dirname(os.path.abspath(__file__))

def write_port(name, port):
    with open(os.path.join(here, name), "w", encoding="utf-8") as f:
        f.write(str(port))

if mode == "stale_port":
    # ⚠ 要点：**旧值与新值两个端口都真的有人在听、都回 200**
    #   ⇒ "文件存在且非空"成立、探活也成立 ⇒ 唯一能识别它不是就绪的东西只剩
    #   "**这个文件是 spawn 之前就有的**"（快照比较）。这正是 EL 那个假绿的形状。
    #   ⭐ 红证：把 `_port_file_changed` 退化成 `return True` ⇒ 它会**立刻**用**旧值**
    #   判定就绪 ⇒ 就绪证据里出现旧端口 ⇒ 判据红（旧值≠新值）。
    import http.server, threading
    old_port = int(sys.argv[2]); new_port = int(sys.argv[3])
    def serve(port):
        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "0"); self.end_headers()
        http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
    threading.Thread(target=serve, args=(new_port,), daemon=True).start()
    def flipper():
        time.sleep(1.0)
        write_port(sys.argv[4], new_port)      # ⭐ 文件换成**新值**（1s 后）
    threading.Thread(target=flipper, daemon=True).start()
    serve(old_port)                            # 主线程服务旧值那个端口
elif mode == "logline":
    print("HELLO-FROM-MOCK", flush=True)
    time.sleep(30)
elif mode == "flaky_http":
    port = int(sys.argv[2]); write_port(sys.argv[3], port)
    # 先 404 两次，再 200 ⇒ 就绪必须等到真 200
    import http.server, threading
    state = {"n": 0}
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            state["n"] += 1
            self.send_response(404 if state["n"] <= 2 else 200)
            self.send_header("Content-Length", "0"); self.end_headers()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.serve_forever()
elif mode == "exit3":
    print("about to fail", flush=True)
    time.sleep(0.2)
    sys.exit(3)
elif mode == "crash":
    print("crash", flush=True)
    sys.exit(9)
elif mode == "grandchild":
    # 派生一个**孙进程**（睡很久），自己写端口文件后也睡
    port = int(sys.argv[2]); write_port(sys.argv[3], port)
    gc = subprocess.Popen([sys.executable, os.path.abspath(__file__), "sleepy", str(port)],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(os.path.join(here, "grandchild.pid"), "w") as f:
        f.write(str(gc.pid))
    print("GRANDCHILD-SPAWNED", flush=True)
    time.sleep(60)
elif mode == "sleepy":
    port = int(sys.argv[2])
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port)); srv.listen(8)
    while True:
        try: srv.accept()
        except OSError: break
elif mode == "bind_port":
    port = int(sys.argv[2]); write_port(sys.argv[3], port)
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port)); srv.listen(8)
    while True:
        try: srv.accept()
        except OSError: break
elif mode == "env_probe":
    #: 把自己**实际拿到**的环境导出成 JSON，供判据核对（只读、不改）。
    import json
    keys = sys.argv[2].split(",")
    out = {k: os.environ[k] for k in keys if k in os.environ}
    open(sys.argv[3], "w", encoding="utf-8").write(json.dumps(out))
    while True:
        time.sleep(60)
'''


def _free_port() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Env:
    """一个临时工作目录 + 写好的 mock 脚本（每个用例一个，互不污染）。"""

    def __init__(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="mecha-launch-"))
        self.mock = self.dir / "mock_child.py"
        self.mock.write_text(MOCK, encoding="utf-8")
        self.logs = self.dir / "logs"
        self.sup = S.Supervisor(log_dir=self.logs, state_path=self.dir / "state.json",
                                max_restarts=2, max_restart_period_s=30.0)

    def spec(self, cid: str, *args: str, ready=None, restart="permanent",
             stop_timeout: float = 3.0, env=None) -> "S.ChildSpec":
        return S.ChildSpec(id=cid, argv=(sys.executable, str(self.mock), *args),
                           cwd=self.dir, ready=ready, restart=restart,
                           stop_timeout=stop_timeout, env=dict(env or {}))

    def close(self) -> None:
        try:
            self.sup.shutdown()
        except Exception:                                  # noqa: BLE001
            pass
        shutil.rmtree(self.dir, ignore_errors=True)


def _entry(env: Env, cid: str) -> dict:
    for row in env.sup.state()["children"]:
        if row["id"] == cid:
            return row
    raise AssertionError(f"state() 里没有 {cid}：{env.sup.state()}")


# --------------------------------------------------------------------------- 判据


def check_no_window_flag() -> None:
    flags = S._spawn_flags()
    if os.name == "nt":
        nowin = subprocess.CREATE_NO_WINDOW                       # type: ignore[attr-defined]
        newgrp = subprocess.CREATE_NEW_PROCESS_GROUP              # type: ignore[attr-defined]
        _check("① spawn 带 CREATE_NO_WINDOW（不开黑框；旧 launcher 用的是 CREATE_NEW_CONSOLE）",
               bool(flags & nowin), f"flags={flags}")
        _check("① spawn 带 CREATE_NEW_PROCESS_GROUP（两家旧 launcher 都没有它）",
               bool(flags & newgrp), f"flags={flags}")
    else:
        # POSIX 上等价物是 start_new_session（在 Popen 里给），旗标位本来就没有
        _check("① POSIX：不走 taskkill 路径，改用 killpg（见 ⑦）", True)


def check_log_redirect() -> None:
    env = Env()
    try:
        env.sup.start(env.spec("logline", "logline"))  # mock 自己会 flush
        time.sleep(0.6)
        row = _entry(env, "logline")
        text = Path(row["log"]).read_text(encoding="utf-8", errors="replace")
        _check("② stdout 被重定向进 runtime/logs/<id>.log（**绝不 DEVNULL**）",
               "HELLO-FROM-MOCK" in text, f"日志内容={text[:80]!r}")
    finally:
        env.close()


def check_stale_port_file() -> None:
    """⭐ 第 3 条：预置一个"**内容非空、且真的有人在听**"的端口文件 ⇒ 必须不判就绪。

    ## 为什么这条这么造（红证逼出来的）
    最朴素的造法（"旧端口没人听"）**不够**：那时探活会失败，判据"红"是因为探活、
    不是因为快照比较 ⇒ 把 ``_port_file_changed`` 退化成 ``return True`` 它**照样绿**
    （本判据第一版就是这样，红证没能红）。
    ⇒ 现在的造法让**旧端口也有人听**：于是"文件存在且非空"与"探活通过"**都成立**，
    唯一能识别它的只剩"**这个文件是 spawn 之前就有的**"——正是快照比较的职责。
    """
    env = Env()
    try:
        old_port, new_port = _free_port(), _free_port()
        (env.dir / ".mcp-port").write_text(str(old_port), encoding="utf-8")   # 旧值（且有人听）
        ready = S.ReadySpec(port_file=".mcp-port", probe="http://127.0.0.1:{port}/health",
                            timeout_s=8.0)
        env.sup.start(env.spec("stale", "stale_port", str(old_port), str(new_port),
                               ".mcp-port", ready=ready))
        row = _entry(env, "stale")
        _check("③ 端口文件是 spawn 前就有的（旧值、且旧端口真有人听）⇒ **不判就绪**",
               row["state"] == "ready" and row["port"] == new_port,
               f"state={row['state']} port={row['port']!r} ev={row['ready_evidence']!r}")
        _check("③ 回读的是**文件里那个新值**，不是预置的旧值（旧值≠就绪）",
               row["port"] == new_port and row["port"] != old_port,
               f"port={row['port']!r} 旧值={old_port} 新值={new_port}")
        _check("③ 就绪证据说明是**文件变了**（而不是「探活通过」就完事）",
               "端口文件" in row["ready_evidence"], f"ev={row['ready_evidence']!r}")
        _check("③ `is_ready()` 与证据自洽（两个条件都成立）", row["ready"] is True)
    finally:
        env.close()


def check_stale_port_unreachable() -> None:
    """⭐ 第 3 条的**强版**：旧端口**没人听**时，绝不许把它当就绪（EL 的原始形状）。"""
    env = Env()
    try:
        (env.dir / ".mcp-port").write_text(str(_free_port()), encoding="utf-8")
        ready = S.ReadySpec(port_file=".mcp-port", tcp=True, timeout_s=2.0)
        env.sup.start(env.spec("stale2", "sleepy", "1", ready=ready))
        row = _entry(env, "stale2")
        _check("③b 陈旧端口（非空但**没人听**）⇒ unhealthy、且不报那个端口",
               row["state"] == "unhealthy" and row["port"] is None,
               f"state={row['state']} port={row['port']!r}")
        _check("③b 就绪证据写明「还在旧值」", "旧值" in row["ready_evidence"],
               f"ev={row['ready_evidence']!r}")
    finally:
        env.close()


def check_http_404_then_200() -> None:
    env = Env()
    try:
        port = _free_port()
        ready = S.ReadySpec(port_file=".mcp-port", probe="http://127.0.0.1:{port}/health",
                            timeout_s=8.0)
        env.sup.start(env.spec("flaky", "flaky_http", str(port), ".mcp-port", ready=ready))
        row = _entry(env, "flaky")
        _check("④ 前两次 404 ⇒ 不判就绪；等到真 200 才 ready", row["state"] == "ready",
               f"state={row['state']} ev={row['ready_evidence']!r}")
        _check("④ 就绪证据里带**通过的那次状态码**（可复核）",
               "200" in row["ready_evidence"], f"ev={row['ready_evidence']!r}")
        _check("④ state=ready **且**证据非空（is_ready 的两个条件都成立）", row["ready"] is True)
    finally:
        env.close()


def check_exit_code_and_log_tail() -> None:
    env = Env()
    try:
        env.sup.start(env.spec("boom", "exit3", restart="temporary"))
        time.sleep(0.8)
        row = _entry(env, "boom")
        _check("⑤ 非零退出码被如实记录（exit=3）", row["exit_code"] == 3,
               f"exit_code={row['exit_code']!r}")
        tail = env.sup.child("boom").log_tail(5)
        _check("⑤ 日志末几行可读（给状态页的「看日志」用）",
               any("about to fail" in ln for ln in tail), f"tail={tail}")
    finally:
        env.close()


def check_restart_budget() -> None:
    env = Env()
    try:
        env.sup.start(env.spec("crashy", "crash"))          # max_restarts=2（Env 里设的）
        deadline = time.time() + 8
        row = _entry(env, "crashy")
        while time.time() < deadline and row["state"] != "unhealthy":
            time.sleep(0.2)
            row = _entry(env, "crashy")
        _check("⑥ 连崩 ⇒ 重起若干次后**停止重试**并标 unhealthy",
               row["state"] == "unhealthy", f"state={row['state']}")
        _check("⑥ 重启次数被记下来（不是静默放弃）", row["restarts"] >= 1,
               f"restarts={row['restarts']}")
        _check("⑥ 上限原因写进 last_error（人能看到「为什么不再重试」）",
               "重启超限" in str(row["last_error"]), f"last_error={row['last_error']!r}")
    finally:
        env.close()


def check_kill_tree() -> None:
    """⭐ 第 7 条：mock 派生孙进程 ⇒ stop() 之后孙进程也必须死。"""
    env = Env()
    try:
        port = _free_port()
        ready = S.ReadySpec(port_file=".mcp-port", tcp=True, timeout_s=6.0)
        env.sup.start(env.spec("tree", "grandchild", str(port), ".mcp-port", ready=ready))
        pid_file = env.dir / "grandchild.pid"
        for _ in range(30):
            if pid_file.exists():
                break
            time.sleep(0.1)
        if not pid_file.exists():
            _check("⑦ mock 起了孙进程（前置条件）", False, "孙进程 pid 文件没出现")
            return
        gc_pid = int(pid_file.read_text().strip())
        _check("⑦ 前置：孙进程活着", S.pid_alive(gc_pid), f"gc_pid={gc_pid}")
        env.sup.stop("tree")
        time.sleep(0.8)
        _check("⑦ stop() 后**孙进程也死了**（只杀直系会留下持端口的孤儿）",
               not S.pid_alive(gc_pid), f"gc_pid={gc_pid} 仍活着")
    finally:
        env.close()


def check_real_port_readback() -> None:
    env = Env()
    try:
        port = _free_port()
        ready = S.ReadySpec(port_file=".mcp-port", tcp=True, timeout_s=6.0)
        env.sup.start(env.spec("bind", "bind_port", str(port), ".mcp-port", ready=ready))
        row = _entry(env, "bind")
        _check("⑧ state() 里的端口 = **文件里实际写的那个**（回读，不是预选值）",
               row["port"] == port, f"port={row['port']!r} 期望 {port}")
        _check("⑧ 端口文件内容与 state() 一致（两处同源）",
               (env.dir / ".mcp-port").read_text().strip() == str(port))
    finally:
        env.close()


def check_child_env_is_utf8_and_empty_means_unset() -> None:
    """⭐ 判据 ⑨：**统一注入 UTF-8** + **空串 = 显式取消该变量**。

    ## 为什么必须有这条（两个接入仓各自实测到的真事故面）

    1. ⚠ **本模块自己制造了那个前提**：我们把子进程 stdout **收进管道**
       （铁律 1：不开窗但日志必留）⇒ 子进程的 stdio 编码在 Windows 上退回 **cp936**
       ⇒ 网络代码里一句 ``print("⚠ …")`` 抛 ``UnicodeEncodeError``，
       **让整个后台任务崩掉**（接入仓实测、本机可复现）。
       ⇒ 所以 supervisor 必须**统一注入** `PYTHONIOENCODING=utf-8`
       （这是所有子进程的共同需求；放进各项目声明只会把纪律复制一遍）。
    2. ⚠ **空串 = "显式取消"**：声明方要能表达"**别让这个变量被继承进来**"
       （某接入仓的 `EL_NO_AI_SERVER` 一旦被继承，**服务端根本不会起**），
       而不必依赖父进程恰好没设它。

    ⚠ **怎么故意退化还能让它绿**（所以三段都要断言）：
    只断言"`PYTHONIOENCODING` 是 utf-8"是不够的 —— 把"空串删除"那段去掉**照样绿**。
    所以必须**同时**断言"清空了的变量在子进程里**真的不存在**"。
    """
    import json

    env = Env()
    try:
        # ⚠ **本判据必须自己造出前提**，不能依赖"父进程恰好没设 PYTHONIOENCODING"：
        # supervisor 是 ``env = dict(os.environ)`` + ``setdefault`` ⇒ **父进程若已设了它，
        # "框架没注入"也会绿**（实测：我自己的 shell 设了它 ⇒ "故意去掉注入"的退化
        # **照样 PASS**）。而门禁 `run_all` 自己的环境里**就有**这个变量 ⇒
        # 写成"要求父进程没有"会把门禁弄红（**我踩过**）。
        #
        # ⇒ 正解：**在子进程侧**显式给空串 —— 空串 = "显式取消这个变量"，
        #   于是 `env` 里键**不存在** ⇒ `setdefault("PYTHONIOENCODING", "utf-8")` 必然生效。
        #   这条断言**与父环境无关**，因此无论怎么跑都测的是"框架注入"本身。
        probe = env.dir / "child_env.json"
        keys = "PYTHONIOENCODING,PYTHONUNBUFFERED,EL_NO_AI_SERVER,ME_TOO"
        env.sup.start(env.spec("probe", "env_probe", keys, str(probe),
                               env={"EL_NO_AI_SERVER": "", "ME_TOO": "",
                                    "PYTHONIOENCODING": ""}))
        for _ in range(60):
            if probe.is_file():
                break
            time.sleep(0.1)
        assert probe.is_file(), "mock 没写出环境快照（判据无法核对）"
        got = json.loads(probe.read_text(encoding="utf-8"))

        _check("⑨ ⭐ 统一注入了 PYTHONIOENCODING=utf-8（否则 stdout 被重定向 ⇒ cp936 崩）",
               got.get("PYTHONIOENCODING") == "utf-8", f"got={got!r}")
        _check("⑨ PYTHONUNBUFFERED 也在（输出不卡缓冲）",
               got.get("PYTHONUNBUFFERED") == "1", f"got={got!r}")
        _check("⑨ ⭐ 声明里给空串的变量**在子进程里真的不存在**（空串 = 显式取消）",
               "EL_NO_AI_SERVER" not in got and "ME_TOO" not in got, f"got={got!r}")

        # ⚠ 对偶：**显式给的值优先**（判据要能构造"不注入"的对照组，
        #   也防止实现被写成"无条件覆盖调用方给的编码"）。
        probe2 = env.dir / "child_env2.json"
        env.sup.start(env.spec("probe2", "env_probe", "PYTHONIOENCODING", str(probe2),
                               env={"PYTHONIOENCODING": "gbk"}))
        for _ in range(60):
            if probe2.is_file():
                break
            time.sleep(0.1)
        got2 = json.loads(probe2.read_text(encoding="utf-8")) if probe2.is_file() else {}
        _check("⑨ 对偶：**显式给的编码优先**（不许无条件覆盖调用方的声明）",
               got2.get("PYTHONIOENCODING") == "gbk", f"got={got2!r}")
    finally:
        env.close()


def check_lease_exclusive_and_restart() -> None:
    """⑨ 写租约互斥 + **重启**（原"换腿"判据，按三层架构改名换用途）。

    ## ⚠ 判据**没有**因为撤 `switch_leg` 而被删
    被撤掉的是**定位**（"换操作者"），而"**停 → 等真退出 → 起**"这段**时序**对
    **重启**仍然有用（重启也必须先停干净、等写租约释放）。⇒ 改名字与用途，机制与断言保留。

    ## 红证怎么造（老规矩）
    只断言"重启之后有新 pid"是不够的：把实现写成"**不停旧的、直接再起一个**"也能绿。
    ⇒ 必须断言**旧进程真的没了**（``pid_alive(旧pid)`` 为假、且旧 pid ≠ 新 pid）。
    """
    env = Env()
    try:
        root = env.dir / "data"
        root.mkdir()
        a = S.ChildSpec(id="app-a", argv=(sys.executable, str(env.mock), "logline"),
                        cwd=env.dir, data_root=root, stop_timeout=3.0)
        b = S.ChildSpec(id="app-b", argv=(sys.executable, str(env.mock), "logline"),
                        cwd=env.dir, data_root=root, stop_timeout=3.0)
        env.sup.start(a)
        row_a = next(r for r in env.sup.state()["children"] if r["id"] == "app-a")
        _check("⑨ 在跑的软件**声明持有写租约**（面板「写权」那一栏据此渲染）",
               row_a["holds_write_lease"] is True, f"row={row_a}")
        raised = False
        try:
            env.sup.start(b)
        except Exception as exc:                            # noqa: BLE001 - 期望就是拒
            raised = "lease_not_exclusive" in str(getattr(exc, "kind", "")) or "租约" in str(exc)
        _check("⑨ 同一数据根**第二个软件实例起不来**（互斥，在 spawn 之前就拒）",
               raised, "第二个 child 竟然起来了")
        if not raised:
            return

        # ---- 重启：停 → 等真退出 → 起（新 pid、新就绪快照）----
        old_pid = row_a["pid"]
        rec: dict = {}
        # ⚠ 退化版（"不停旧的、直接再起一个"）会在这里**抛** ⇒ 必须**接住并判红**：
        #   否则"抛出去"会让整个用例中止，表现为"0 条红"——红证探针实测就是这个形状
        #   （判据看似通过、其实根本没跑到断言）。⇒ **重启过程抛错本身就必须是红**。
        try:
            rec = env.sup.restart("app-a")
        except Exception as exc:                            # noqa: BLE001 - 期望实现跑通
            rec = {"raised": f"{type(exc).__name__}: {exc}"}
        _check("⑨ 重启：先停旧实例、确认真退出，再起新的（时序不能反）",
               rec.get("stopped") and rec.get("exited") and rec.get("started"),
               f"record={rec}")
        _check("⑨ ⭐ **旧进程真的没了**（`pid_alive(旧pid)` 为假）",
               old_pid is not None and not S.pid_alive(old_pid), f"old_pid={old_pid}")
        _check("⑨ ⭐ 新 pid 与旧 pid **不同**（不是「没停就再起一个」）",
               rec.get("new_pid") is not None and rec["new_pid"] != old_pid,
               f"old={old_pid} new={rec.get('new_pid')}")
        row_a2 = next(r for r in env.sup.state()["children"] if r["id"] == "app-a")
        _check("⑨ 重启后写权**仍归它**（新实例持租约，没有 lease_not_exclusive）",
               row_a2["holds_write_lease"] is True, f"row={row_a2}")
        _check("⑨ 重启记录进了 cleaned（状态页看得到）",
               any("重启" in x for x in env.sup.cleaned), f"cleaned={env.sup.cleaned}")
    finally:
        env.close()


def check_declaration_shape_three_layer() -> None:
    """⑬ 声明形状（三层架构 §1.2）：``has_gui`` / ``initial_mode`` 在，``manual`` **不在**。

    ## 红证怎么造
    把 `has_gui` / `initial_mode` 两个字段删掉 ⇒ 前两条红；
    把 `manual: ChildSpec | None` 加回来 ⇒ 第三条红（"它不该是 child"这条承诺被破坏）。

    ## 为什么"没有 manual 字段"也要断言
    三层架构的裁决是"**人那条腿不是 supervisor 的子进程**，而是软件进程内的窗口"。
    只要 `Service` 上还留着 `manual: ChildSpec`，**"换个名字又能把它当 child 起"这条路就是通的**
    —— 那正是被证伪的旧形态。⇒ 用"**字段不存在**"把那条路堵死（R13 的行为级写法）。
    """
    from mecha.authority import Mode
    from mecha.site import Service
    import dataclasses

    names = {f.name for f in dataclasses.fields(Service)}
    _check("⑬ 声明里有 `has_gui`（面板据此决定显不显示「切到手动」）", "has_gui" in names,
           f"fields={sorted(names)}")
    _check("⑬ 声明里有 `initial_mode`（开始时用什么写权，由启动者声明）",
           "initial_mode" in names, f"fields={sorted(names)}")
    _check("⑬ ⭐ 声明里**没有** `manual`（人那条腿不是 child ⇒ 那条旧路必须堵死）",
           "manual" not in names, f"fields={sorted(names)}")

    d = Path(tempfile.mkdtemp(prefix="decl-"))
    try:
        svc = Service(id="X", brief="b", argv=(sys.executable, "-c", "pass"), cwd=d,
                      ready=S.ReadySpec(tcp=True, timeout_s=1.0), data_root=d / "data")
        _check("⑬ `has_gui` 默认 False（没声明 GUI 的项目面板上不该出现那一档）",
               svc.has_gui is False, f"has_gui={svc.has_gui}")
        _check("⑬ `initial_mode` 默认 = LOCKED（与 authority.DEFAULT_MODE 同一处真值）",
               svc.initial_mode is Mode.LOCKED, f"initial_mode={svc.initial_mode}")
        loud = False
        try:
            Service(id="X", brief="b", argv=("x",), cwd=d, ready=S.ReadySpec(),
                    data_root=d / "data", manual=None)      # type: ignore[call-arg]
        except TypeError:
            loud = True
        _check("⑬ ⭐ 传 `manual=` **当场 TypeError**（迁移期要响，不许静默忽略）", loud)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def check_startup_plan_order_and_stale() -> None:
    """修正 1/2：计划里有**显式顺序**；残留能被 clean_stale 执行并记进状态。"""
    from mecha.site import DSHSpec, ReadySpec as SReady, Service, Site, Strategy
    env = Env()
    try:
        # 造一个"陈旧端口文件"（端口没人听）
        stale_pf = env.dir / ".site-port"
        stale_pf.write_text("59998", encoding="utf-8")
        svc = Service(
            id="Demo", brief="演示", argv=(sys.executable, str(env.mock), "logline"),
            cwd=env.dir, ready=SReady(port_file=".site-port", timeout_s=1.0),
            data_root=env.dir / "data")
        dsh = DSHSpec(command=(sys.executable, "-c", "import time;time.sleep(30)"), cwd=env.dir)
        site = Site(name="demo-site", services=(svc,), dsh=dsh, strategy=Strategy())
        from mecha import launch
        plan = launch.plan_startup(site, ["Demo"], {})
        _check("⑩ StartupPlan.ordered 是**显式顺序**：svc* → dsh（dsh 在最后）",
               plan.ordered[-1].id == "dsh" and plan.ordered[0].id == "Demo",
               f"ordered={[c.id for c in plan.ordered]}")
        _check("⑩ 陈旧端口文件被探到（端口没人听 ⇒ 判残留）",
               any(i.kind == "port_file" for i in plan.stale),
               f"stale={[i.target for i in plan.stale]}")
        done = env.sup.clean_stale(plan.stale)
        _check("⑩ clean_stale **真的执行了**并返回人话清单", len(done) >= 1, f"done={done}")
        _check("⑩ 清掉的东西**出现在 state()** 里（不许只写日志）",
               any("陈旧端口文件" in x for x in env.sup.state().get("cleaned", [])),
               f"cleaned={env.sup.state().get('cleaned')}")
        _check("⑩ 陈旧端口文件**真的没了**", not stale_pf.exists())
    finally:
        env.close()


def check_dry_run_has_no_side_effects() -> None:
    """⭐ 判据 ⑫：``--dry-run`` **不许有任何副作用**（尤其**不许造目录**）。

    ## 为什么必须有这条（接入仓实测报出来的真 bug）

    原实现先 ``acquire_singleton(runtime)`` 再判 ``--dry-run``，
    而 ``acquire_singleton`` 会 ``runtime.mkdir(...)``
    ⇒ ⭐ **一次 `--dry-run`（甚至只在判据里调一次 `load_sites()`）
      就会在 cwd 下造出 `_mecha_runtime/`** ⇒ 接入仓的工作区**无缘无故变脏**。

    ⚠ **本条是"边界"判据，不是"功能"判据** —— 它测的是"**不该发生的事有没有发生**"。
    这类判据最容易被写成假绿："跑完没报错"不等于"没副作用"。

    ⚠ **怎么故意退化还能让它绿**（所以下面要**两段都断言**）：
    ① 只断言"`_mecha_runtime` 不存在"—— 若实现改成把目录建到**别处**（比如项目根），
       这条照样绿 ⇒ 所以还要断言 ② **整个临时 cwd 里没有新增任何文件/目录**。
    """
    import contextlib
    import io
    import tempfile

    from mecha import launch as L

    td = Path(tempfile.mkdtemp(prefix="mecha-dry-"))
    site_dir = td / "site"
    site_dir.mkdir()
    (site_dir / "mecha_site.py").write_text(
        "from pathlib import Path\n"
        "from mecha.site import DSHSpec, ReadySpec, Service, Site\n"
        "ROOT = Path(__file__).resolve().parent\n"
        "SITE = Site(name='dry-probe', services=(\n"
        "    Service(id='D', brief='x', argv=('x',), cwd=ROOT,\n"
        "            ready=ReadySpec(port_file='.p'), data_root=ROOT),\n"
        "), dsh=DSHSpec(command=('dsh',), cwd=ROOT))\n",
        encoding="utf-8")
    try:
        cwd = os.getcwd()
        os.chdir(td)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = L.main(["--dry-run", "--root", str(site_dir), "--no-browser"])
        finally:
            os.chdir(cwd)
        _check("⑫ dry-run 返回 0（它只是打印计划）", rc == 0, f"rc={rc}")
        _check("⑫ ⭐ dry-run **不造** `_mecha_runtime/`（原实现会造，脏接入仓的工作区）",
               not (td / "_mecha_runtime").exists(),
               f"被造出来了：{(td / '_mecha_runtime')}")
        #: ② 对偶：**整个 cwd 里一个新东西都不许有**（防止"建到别处去"照样绿）
        left = sorted(p.name for p in td.iterdir() if p.name != "site")
        _check("⑫ ⭐ 且 **cwd 里没有任何新增**（对偶：防'改个地方建目录'照样绿）",
               left == [], f"多出来的：{left}")
    finally:
        shutil.rmtree(td, ignore_errors=True)


def check_declared_env_is_injected_with_values() -> None:
    """⭐ 判据 ⑭：`initial_mode` / `data_root` **真的注入**进软件进程的 env，
    且**取的是 `.value` 而不是 `str(枚举)`**。

    ## 为什么必须有这条（**三个接入仓各自踩了同一个坑**）

    ``Mode`` 是 ``str`` 枚举 ⇒ ``str(Mode.AI)`` 给的是 **``'Mode.AI'``**，
    而软件读 ``MECHA_INITIAL_MODE`` 时想要的是 **``'ai'``**。
    ⚠ 这个坑框架里踩过一次（``SvcState.DOWN`` 同款），
    三家接入时**又各自踩了一次**（实测：三个子代理都断在这上面）。

    ⚠ **怎么故意退化还能让它绿**（所以下面要断言具体值）：
    只断言"env 里有这个键"是不够的 —— 把实现改成 ``str(self.initial_mode)``
    **照样有这个键**，只是值是 ``'Mode.AI'``。

    ⚠ 还有一段**对偶**：项目**显式给**的同名变量**不许被覆盖**
    （与框架统一注入 ``PYTHONIOENCODING`` 同一纪律：``setdefault`` 语义）。
    """
    from mecha.authority import Mode
    from mecha.site import MECHA_MODE_ENV, MECHA_ROOT_ENV, Service
    from mecha.site import ReadySpec as _RS

    svc = Service(id="probe", brief="x", argv=("x",),
                  cwd=Path.cwd(), ready=_RS(port_file=".p"),
                  data_root=Path.cwd() / "data", initial_mode=Mode.AI)
    kid = svc.child()
    _check("⑭ ⭐ `MECHA_INITIAL_MODE` 注入的是 **`'ai'`**（`.value`），不是 `'Mode.AI'`",
           kid.env.get(MECHA_MODE_ENV) == "ai", f"got={kid.env.get(MECHA_MODE_ENV)!r}")
    _check("⑭ `MECHA_SOFTWARE_ROOT` 注入 = 声明的 `data_root`",
           kid.env.get(MECHA_ROOT_ENV) == str(svc.data_root),
           f"got={kid.env.get(MECHA_ROOT_ENV)!r}")

    #: 对偶：**项目显式给的优先**（不许被框架覆盖）
    svc2 = Service(id="probe2", brief="x", argv=("x",),
                   cwd=Path.cwd(), ready=_RS(port_file=".p"),
                   data_root=Path.cwd() / "data2", initial_mode=Mode.AI,
                   env={MECHA_MODE_ENV: "human"})
    kid2 = svc2.child()
    _check("⑭ 对偶：项目**显式给**的同名变量优先（不许被框架覆盖）",
           kid2.env.get(MECHA_MODE_ENV) == "human", f"got={kid2.env.get(MECHA_MODE_ENV)!r}")

    #: 每一档都要是"小写裸值"（防将来加新档时漏了 `.value`）
    for m in (Mode.LOCKED, Mode.HUMAN, Mode.AI, Mode.OPEN):
        s = Service(id="p3", brief="x", argv=("x",), cwd=Path.cwd(),
                    ready=_RS(port_file=".p"), data_root=Path.cwd() / "d3",
                    initial_mode=m)
        got = s.child().env.get(MECHA_MODE_ENV)
        _check(f"⑭ `{m.value}` 档注入的是 `{m.value!r}`（不是 `{str(m)!r}`）",
               got == m.value, f"got={got!r}")


def check_status_page_console() -> None:
    """⑪ **常驻控制台**（一页）+ 三个控件分工 + 四条布局要求。

    ## 红证怎么造
    - **只渲染"勾过的"** ⇒ "始终展示全部声明项目"那几条红（造法：把 services() 过滤成
      "intent 为真的"）；
    - **把写权栏渲染成能点的按钮** ⇒ "不许谎称可点"那条红（造法：给它加 onclick）；
    - **把折叠改成"缩小内边距"** ⇒ "折叠真的让出宽度"那条红（造法：只改 padding）。
    """
    from mecha.providers.status_page import MODE_PENDING_TEXT, render_page

    class Svc:
        def __init__(self, sid, brief, has_gui):
            self.id = sid
            self.brief = brief
            self.has_gui = has_gui

    class Ctl:
        """最小控制面（只为渲染）。⚠ 三个软件：两个有 GUI、一个没有。"""

        def __init__(self) -> None:
            self._started = False
            self._err = ""
            self._svcs = (Svc("ML", "传统机器学习 / 神经网络 / 自动调参", True),
                          Svc("EL", "仿真与参数扫描", True),
                          Svc("GP", "论文管理与推荐", False))
            self._intent = {"ML": True, "EL": True, "GP": False}
            self._rows: list[dict] = []

        def sites(self):
            class St:
                name = "demo"
            return (St(),)

        def services(self):
            return self._svcs

        def started(self) -> bool:
            return self._started

        def intent(self) -> dict:
            return dict(self._intent)

        def set_intent(self, sid, on): self._intent[sid] = on
        def set_service_enabled(self, sid, on): self._intent[sid] = on
        def last_status(self) -> dict:
            return {"children": [], "last_error": self._err}

        def state(self) -> dict:
            return {"children": list(self._rows), "started": self._started, "enabled": [],
                    "sites": ["demo"], "cleaned": [], "last_error": self._err,
                    "intent": dict(self._intent)}

        def start(self, enabled): return {"ok": True}
        def shutdown(self): return {"ok": True}

    c = Ctl()
    p1 = render_page(c)
    # ⭐ 判据 3：**始终**展示全部声明项目（启动前）
    _check("⑪ ⭐ 启动前就列出**全部声明项目**（未勾的 GP 也在）",
           all(s in p1 for s in ("ML", "EL", "GP")), "少了某个项目")
    _check("⑪ `has_gui=False` 的 GP 也在列表里（只是写权栏不同）",
           "GP" in p1 and "论文管理与推荐" in p1)
    # ⭐ 判据 4：写权栏本轮"待接"，且**不可点**
    _check("⑪ ⭐ 写权栏显示「待接」并注明依赖控制通道",
           MODE_PENDING_TEXT in p1, f"p1 里没有 {MODE_PENDING_TEXT!r}")
    _check("⑪ ⭐ 写权栏**不是**能点的按钮（不许谎称可点）",
           "onclick=\"mode" not in p1 and "写权：" in p1)
    _check("⑪ `has_gui=False` ⇒ 写权栏「—」（它没有手动这一档）", "—" in p1)
    # ⭐ 同页：不是两步向导
    _check("⑪ 是**常驻控制台**（一页）：标题就是控制台，没有「选择页」这一步",
           "控制台" in p1 and "选择要启用的项目" not in p1)
    # ⭐ 四条布局要求
    _check("⑪ 布局①：折叠切换**整列宽度**（grid-template-columns 260px ↔ 0）",
           "grid-template-columns:260px 1fr" in p1 and "#shell.collapsed{grid-template-columns:0 1fr}" in p1)
    _check("⑪ 布局②：列表在**自己的格子**里滚动（overflow-y:auto + min-height:0），页面不被撑长",
           "overflow-y:auto" in p1 and "min-height:0" in p1)
    _check("⑪ 布局③：**没有卡片框**（不给行套 border+radius 的卡片容器）",
           "border-radius:10px" not in p1 and "card" not in p1)
    _check("⑪ 布局④：**没有「详情」那种占一整行的标题**", "详情" not in p1)

    c._started = True
    c._rows = [{"id": "ML", "pid": 4321, "state": "ready", "port": 5111}]
    p2 = render_page(c)
    _check("⑪ 启动后**仍是同一页**（还是控制台，不是另一张状态页）",
           "控制台" in p2 and "运行状态" not in p2)
    _check("⑪ 启动后**仍然列出全部项目**（含没起的 GP）",
           all(s in p2 for s in ("ML", "EL", "GP")))
    _check("⑪ 存活那一栏读的是 `state()` 的真事实（打得出 pid）", "4321" in p2)
    _check("⑪ 「还没启动」与「启动失败」措辞**不同**",
           "还没启动" in p1 and "启动失败" not in p1)
    c._err = "child_spawn_failed：找不到可执行文件"
    p3 = render_page(c)
    _check("⑪ 有 last_error 时显示**具体原因**（不是「未知」）",
           "启动失败" in p3 and "child_spawn_failed" in p3)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("[launch_selfcheck] supervisor / site / launch / status_page")
    check_no_window_flag()
    check_log_redirect()
    check_stale_port_file()
    check_stale_port_unreachable()
    check_http_404_then_200()
    check_exit_code_and_log_tail()
    check_restart_budget()
    check_kill_tree()
    check_real_port_readback()
    check_child_env_is_utf8_and_empty_means_unset()
    check_lease_exclusive_and_restart()
    check_declaration_shape_three_layer()
    check_startup_plan_order_and_stale()
    check_dry_run_has_no_side_effects()
    check_declared_env_is_injected_with_values()
    check_status_page_console()
    if FAILS:
        print(f"launch_selfcheck: FAIL（{len(FAILS)} 条）：{FAILS}")
        return 1
    print("launch_selfcheck: PASS（④条铁律 + 修正 1/2/4 的机制都钉住了）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
