"""只读额度快照：问 codex app-server 要账号与 rate limit。

## 为什么需要它

vendor 的 Web UI **看不到额度和账号**——它的代理有一张 deny-by-default 的 RPC 白名单
（30 条），只放行 thread / turn / model 那些，`account/*` 一个都没放。这不是 bug，是它
刻意不碰"账号与登出"。所以额度只能在 mecha 这一侧读。

## 怎么读

app-server 只说 WebSocket，而 mecha 核心是零依赖 Python ⇒ 用 vendor 自带的 `ws`
（`webui/node_modules/ws`）跑一个几十行的 Node 探测脚本，拿 JSON 回来。
**不为这件事往 Python 环境里加依赖。**

⚠ **只读**：只调 `account/read` 与 `account/rateLimits/read`。不登录、不登出、不改配置。
⚠ 读不到时**如实说原因**（端口没有 / token 没有 / node 没有 / 超时 / 上游报错），
绝不显示上一轮的旧数字（铁律 8：读不到 ≠ 确实没有）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from ..portfile import read_port
from .config import paths_for

#: 探测脚本（与 vendor 的 `ws` 同目录树；升级 webui 时它不受影响）。
PROBE = Path(__file__).resolve().parent / "quota_probe.mjs"


def _fmt(ts: object) -> str:
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return ""


def snapshot(runtime_root: Path, *, timeout: float = 8.0) -> dict:
    """返回可直接给页面渲染的字典；失败时 ``{"ok": False, "why": ...}``。"""
    paths = paths_for(runtime_root)
    port = read_port(paths.app_server_port) or 0
    if not port:
        return {"ok": False, "why": "宿主还没起过（没有 app-server 端口）"}
    if not paths.ws_token.is_file():
        return {"ok": False, "why": "缺少 ws token（宿主没准备成功）"}
    node = shutil.which("node")
    if node is None:
        return {"ok": False, "why": "本机没有 node（探测脚本要它）"}
    if not PROBE.is_file():
        return {"ok": False, "why": f"探测脚本缺失：{PROBE}"}
    try:
        done = subprocess.run(
            [node, str(PROBE), "--url", f"ws://127.0.0.1:{port}",
             "--token-file", str(paths.ws_token)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "why": "读取超时（app-server 没回应）"}
    except OSError as exc:
        return {"ok": False, "why": f"起不了探测脚本：{exc}"}
    if done.returncode != 0:
        why = (done.stderr or done.stdout or "").strip().splitlines()
        return {"ok": False, "why": (why[-1] if why else "探测失败")[:200]}
    try:
        raw = json.loads(done.stdout or "{}")
    except ValueError:
        return {"ok": False, "why": "探测输出不是 JSON"}
    if not raw.get("ok"):
        return {"ok": False, "why": "app-server 拒绝了这个查询"}

    windows = []
    for item in raw.get("windows") or []:
        try:
            used = int(item.get("usedPercent") or 0)
        except (TypeError, ValueError):
            continue
        windows.append({
            "label": str(item.get("label") or ""),
            "usedPercent": used,
            "leftPercent": max(0, 100 - used),
            "resetsAt": _fmt(item.get("resetsAt")),
        })
    return {
        "ok": True,
        "email": str(raw.get("email") or ""),
        "plan": str(raw.get("plan") or ""),
        "windows": windows,
        "credits": str(raw.get("credits") or "0"),
        "resetCredits": int(raw.get("resetCredits") or 0),
        "resetExpires": _fmt(raw.get("resetExpires")),
    }


__all__ = ["PROBE", "snapshot"]
