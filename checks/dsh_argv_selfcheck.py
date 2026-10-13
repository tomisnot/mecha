"""启动参数回归：--patch 与值保持成对，overlay 放在最后。

默认运行合成声明的参数验证与已知错位案例。
python checks/dsh_argv_selfcheck.py --live 显式运行真实 dsh 启动场景；
需要 dsh 在 PATH、MECHA_SITE_ROOTS 指向真实项目声明。该场景在临时目录内
启动隔离实例并清理进程，不属于日常套件；它不验证浏览器弹窗或客户端打包。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mecha.aggregate_runtime import with_overlay_patch  # noqa: E402
from mecha.supervise import ChildSpec  # noqa: E402

FAILS: list[str] = []
#: ③ 的**等待上界**（有界轮询；见那一侧的注释：冷启动实测要 ~50s，第二次启动 <10s）。
BOOT_TIMEOUT_S = 120.0


def _check(name: str, cond: bool, extra: object = "") -> bool:
    tag = "✔" if cond else "✖"
    print(f"  {tag} {name}" + ("" if cond else f"   ← {extra}"))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def argv_defects(argv: list[str]) -> list[str]:
    """dsh argv 的**形状缺陷**清单（空 = 合法）。⚠ 独立成函数 ⇒ 红证要**真跑它**。

    规则（照 `apps/cli/src/args.ts` 的语义）：**启动器选项**（`--patch` / `--profile` …）
    在遇到**它不认识的那个 token** 为止；之后全归 app。⇒ 由此推出两条可判的：

    1. 每个 `--patch` 后面**必须**紧跟一个**值**（而那个值**不许**又是 `--patch`）；
    2. ⭐ `web`（profile 名）与 `--port`（app 选项）**之间**出现的、不以 `-` 开头的 token
       **只能是某个 `--patch` 的值** —— 否则它就是个**裸 token**（会被交给 app ⇒ commander
       报 `too many arguments`）。
    """
    bad: list[str] = []
    if "--patch" not in argv:
        return ["argv 里一个 --patch 都没有（overlay 没塞进去？）"]
    for i, tok in enumerate(argv):
        if tok != "--patch":
            continue
        nxt = argv[i + 1] if i + 1 < len(argv) else None
        if nxt is None or nxt == "--patch" or nxt.startswith("-"):
            bad.append(f"--patch 后面没有值（index={i}，next={nxt!r}）")
    # ② 裸 token：`web` 与 `--port` 之间的非选项 token 必须是某个 --patch 的值
    try:
        lo = argv.index("web")
    except ValueError:
        lo = 0
    hi = argv.index("--port") if "--port" in argv else len(argv)
    for i in range(lo + 1, hi):
        tok = argv[i]
        if tok.startswith("-"):
            continue
        if i > 0 and argv[i - 1] == "--patch":
            continue
        bad.append(f"裸 token（会被交给 web app ⇒ commander 报 too many arguments）：{tok!r}")
    # ③ overlay 必须是**最后**一份 patch（它要压过 profile 与项目层）
    vals = [argv[i + 1] for i, t in enumerate(argv[:-1]) if t == "--patch" and i + 1 < len(argv)]
    if not vals or "dsh-aggregate" not in vals[-1]:
        bad.append(f"overlay 不在最后一份 patch 上：{vals}")
    return bad


def _fixture() -> tuple[ChildSpec, Path]:
    """合成夹具：**不依赖三家声明**（判据要能在任何机器上跑）。

    argv 形状照真声明：`dsh web --patch <项目1> --patch <项目2> --port 3081`。
    """
    tmp = Path(tempfile.mkdtemp(prefix="dsh-argv-"))
    p1, p2 = tmp / "one.patch.yml", tmp / "two.patch.yml"
    for p in (p1, p2):
        p.write_text("[]\n", encoding="utf-8")       # 合法的最小 patch（空列表）
    overlay = tmp / "dsh-aggregate.patch.yml"
    overlay.write_text("- insert: []\n", encoding="utf-8")
    child = ChildSpec(id="dsh", argv=(sys.executable, "-c", "pass", "web",
                                      "--patch", str(p1), "--patch", str(p2),
                                      "--port", "3081"),
                      cwd=tmp, ready=None)
    return child, overlay


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("[dsh_argv] dsh 的 argv 形状：--patch 必须与它的值成对、overlay 必须最后")

    child, overlay = _fixture()
    fixed = list(with_overlay_patch(child, overlay).argv)
    print(f"[dsh_argv] 框架生成的 argv：{' '.join(fixed[3:])}")

    # ---- ① 形状 ----
    defects = argv_defects(fixed)
    _check("① 形状合法（每个 --patch 带值 / 无裸 token / overlay 最后）",
           not defects, defects)
    n_patch = fixed.count("--patch")
    _check("① 三份 patch（项目两份 + overlay）", n_patch == 3, f"实得 {n_patch}")
    _check("① overlay 真的在 argv 里", str(overlay) in fixed, fixed)

    # ---- ② 红证：**旧的错位 argv** 必须被判出来（真跑同一个函数）----
    old = list(child.argv)
    idx = len(old) - 1 - old[::-1].index("--patch")      # 最后一个 --patch **标志**
    old[idx + 1:idx + 1] = [str(overlay)]                # ⛔ 旧行为：插在标志与值之间
    opened = argv_defects(old)
    if not opened:
        _check("② 红证：旧的错位 argv 必须被判出来", False,
               f"喂了 {old[3:]} 而一条缺陷都没报 ⇒ 判据是哑的")
    else:
        _check("② 红证：旧的错位 argv 必须被判出来", True)
        print(f"      （判据报出的缺陷：{opened[0]}）")

    # ---- ③ 真机：按**真声明**合成的 argv 起一次真 dsh ----
    #
    # ⚠ 为什么真机那一档要**用三家真声明**（而不是上面那份合成夹具）：
    #   夹具的 `--patch` 是空内容，于是**没人**把 `webserver.port` 从"字面量 3080"
    #   改回 `!!js ctx.webStartup.port`（那是**项目隔离层**干的活）⇒ `--port 0` 不生效
    #   ⇒ 撞上正在跑的 dsh（本会话宿主就在 3080）⇒ `EADDRINUSE`（实测量到过这一坑）。
    #   ⇒ 真机档**必须**用真声明（它才带那层 patch）；缺仓库就 SKIP（不假绿）。
    if "--live" not in sys.argv:
        print(f"dsh_argv_selfcheck: {'FAIL' if FAILS else 'PASS'}（参数组合；真 dsh 用 --live 单独验）")
        return 1 if FAILS else 0
    roots_env = os.environ.get("MECHA_SITE_ROOTS", "").strip()
    if not roots_env:
        print("SKIP: 缺 MECHA_SITE_ROOTS ⇒ 跳过「真机」那一档（①② 已跑；"
              "严格模式 --require-external 会把它当 FAIL）")
        return 1 if FAILS else 0
    from mecha.aggregate_runtime import write_dsh_overlay
    from mecha.launch import load_sites, merge_dsh
    roots = [Path(p) for p in roots_env.split(os.pathsep) if p.strip()]
    real_child, _ = merge_dsh(load_sites(roots))
    ov = write_dsh_overlay(child.cwd, mcp_port=65535, server_name="probe")
    real = list(with_overlay_patch(real_child, ov).argv)
    real_defects = argv_defects(real)
    _check("③ 真声明的 argv 形状也合法", not real_defects, real_defects)

    dsh = shutil.which("dsh")
    if dsh is None:
        print("SKIP: PATH 里没有 dsh ⇒ 跳过「真机 argv 能起 dsh」那一档")
        return 1 if FAILS else 0
    log = Path(tempfile.gettempdir()) / "mecha-dsh-argv-boot.log"
    with open(log, "w", encoding="utf-8") as fh:
        proc = subprocess.Popen([dsh, *real[1:], "--port", "0", "--no-open"],
                                cwd=str(real_child.cwd), stdout=fh,
                                stderr=subprocess.STDOUT,
                                env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    try:
        #: ⚠ **有界轮询**，不是"固定睡 N 秒"（2026-10-09 改成这样，因为实测抓到自己这条判据的**假红**）：
        #: dsh 的**冷启动**要为每份 client bundle 现算 `rev` 哈希 ⇒ **实测 50 秒**才打出 URL 行
        #: （重建过 bundle 之后尤其如此），而第二次启动有缓存 ⇒ **10 秒内**就好。
        #: 「固定 15 秒」会因此假红 ⇒ 判据必须**等它就绪**，但同时**有上界**（超时即红，不无限等）。
        deadline = time.monotonic() + BOOT_TIMEOUT_S
        alive, text = True, ""
        while time.monotonic() < deadline:
            text = log.read_text(encoding="utf-8", errors="replace")
            alive = proc.poll() is None
            if not alive or "dsh web: http://" in text:
                break
            time.sleep(0.5)
        _check(f"③ 真 dsh 按这个 argv 起得来（{BOOT_TIMEOUT_S:.0f}s 上限内仍活着）", alive,
               f"exit={proc.poll()}；日志尾部={text[-300:]!r}")
        _check("③ 且它打出了 `dsh web: http://…` 那行（= 就绪，且它会自己开窗）",
               "dsh web: http://" in text, text[-300:])
        _check("③ 反面：日志里**没有** `too many arguments`（本次那个真因的指纹）",
               "too many arguments" not in text, text[-300:])
    finally:
        if proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, check=False)

    if FAILS:
        print(f"dsh_argv_selfcheck: FAIL（{len(FAILS)} 条）：{FAILS}")
        return 1
    print("dsh_argv_selfcheck: PASS（形状 + 红证 + **真机起得来**）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
