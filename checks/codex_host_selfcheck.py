"""判据 ㉗：codex 是**托管宿主**（与 dsh 同构）——真起进程、真就绪、真能停。

## 为什么要它

2026-11-05 之前 codex 是"外部 MCP 模式"：`managed=False`、`child()` 返回 `None`，
滑块只能表达"这一轮用谁"，永远不会有 pid。现在改成托管：

    codex app-server --listen ws://127.0.0.1:<port> --ws-auth capability-token --ws-token-file …

这条判据钉住"托管"这件事的每一环，而且**全是真的**（真进程、真 HTTP、真端口）：

| # | 断言 | 缺了会怎样 |
|---|---|---|
| ① | `prepare_start` 备好端口 / ws token / 登录态 | 起来也是 "Not logged in"，或用错端口 |
| ② | supervisor 起 app-server ⇒ 状态 `ready` | "起来了"但没有就绪判据（假绿） |
| ③ | `/readyz` 真回 200 | 端口在听、服务没准备好 |
| ④ | 宿主 ready 后**自动**起界面代理 | 界面要手动开 |
| ⑤ | 界面首页 200、`/proxy/healthz` ok | 代理起了但没连上上游 |
| ⑥ | 停宿主 ⇒ 界面**一起停** | 关掉宿主留一个空壳界面（生存域分裂） |

⚠ 本机没有 `codex` / `node` 时 SKIP（这是外部依赖，不是框架失败）。
⚠ 会真起 codex 进程并占用两个端口，所以放在 `codex` 套件里，不进核心。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mecha.codex_adapter.config import paths_for  # noqa: E402
from mecha.codex_adapter.host import CodexHostAdapter  # noqa: E402
from mecha.host_adapter import AIHostKind, HostWiring, make_host_plan  # noqa: E402
from mecha.portfile import read_port  # noqa: E402
from mecha.supervise import Supervisor  # noqa: E402

FAILS: list[str] = []


def _check(name: str, cond: bool, extra: object = "") -> None:
    print(f"  {'✓' if cond else '✗'} {name}" + (f" —— {extra}" if not cond and extra else ""))
    if not cond:
        FAILS.append(name)


def _wait(pred, timeout: float, step: float = 0.3) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return False


def _http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            return int(resp.status) == 200
    except Exception:                                    # noqa: BLE001 - 判据只看通不通
        return False


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not (shutil.which("codex.cmd") or shutil.which("codex")):
        print("SKIP: 本机没有 codex 可执行文件（托管宿主验收需要它）")
        return 0
    if not shutil.which("node"):
        print("SKIP: 本机没有 node（界面代理需要它）")
        return 0

    print("Codex 托管宿主判据：")
    with tempfile.TemporaryDirectory(prefix="mecha-codex-host-") as td:
        runtime = Path(td)
        adapter = CodexHostAdapter()
        plan = make_host_plan(AIHostKind.CODEX, (), runtime,
                              aggregate_url="http://127.0.0.1:1/mcp")
        paths = paths_for(runtime)
        facts = adapter.prepare_start(plan)
        _check("① 启动前备好端口 / token / 登录态",
               facts["app_server_port"] > 0 and facts["ui_port"] > 0
               and paths.ws_token.is_file()
               and len(paths.ws_token.read_text(encoding="utf-8").strip()) >= 32,
               str(facts))
        _check("① 登录态按预期处理（copied / exists / missing 都不是崩）",
               facts["auth"] in ("copied", "exists", "missing"), str(facts["auth"]))
        adapter.prepare(plan)                             # 写 MCP 配置（app-server 一起来就读它）

        sup = Supervisor(log_dir=runtime / "logs", state_path=runtime / "state.json")
        adapter.install(plan, HostWiring(supervisor=sup, aggregator=None,
                                         runtime=None, mcp_port=0))
        app_port = read_port(paths.app_server_port) or 0
        ui_port = read_port(paths.ui_port) or 0
        try:
            sup.start(adapter.child(plan))                # ready hook 里会起界面
            state = {c["id"]: c.get("state") for c in sup.state()["children"]}
            _check("② supervisor 起来后 codex 状态是 ready", state.get("codex") == "ready",
                   str(state))
            _check("③ app-server 的 /readyz 真回 200",
                   _http_ok(f"http://127.0.0.1:{app_port}/readyz"))
            ok_ui = _wait(lambda: {c["id"]: c.get("state")
                                   for c in sup.state()["children"]}.get("codex-ui") == "ready",
                          timeout=30.0)
            state = {c["id"]: c.get("state") for c in sup.state()["children"]}
            _check("④ 宿主 ready 后界面**自动**起来了", ok_ui, str(state))
            _check("⑤ 界面首页 200、代理 /proxy/healthz ok",
                   _http_ok(f"http://127.0.0.1:{ui_port}/")
                   and _http_ok(f"http://127.0.0.1:{ui_port}/proxy/healthz"))
            sup.stop("codex")
            time.sleep(1.0)
            state = {c["id"]: c.get("state") for c in sup.state()["children"]}
            _check("⑥ 停宿主 ⇒ 界面一起停（生存域不分家）",
                   state.get("codex") in (None, "stopped")
                   and state.get("codex-ui") in (None, "stopped"), str(state))
        finally:
            sup.shutdown()

    if FAILS:
        print(f"codex_host_selfcheck: FAIL（{len(FAILS)} 条）")
        return 1
    print("codex_host_selfcheck: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
