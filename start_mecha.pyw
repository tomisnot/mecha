# -*- coding: utf-8 -*-
"""⭐ **双击这个文件 = 起整个机甲生态**（**无控制台**）。

## 为什么是 `.pyw` 而不是 `.bat`（实测）

`.bat` 双击**必然开一个控制台窗口**（哪怕里面只写 `start`）—— 与需求"**0 黑框**"冲突。
`.pyw` 由 `pythonw.exe` 跑 ⇒ **不开控制台**。本机实测：
```
assoc .pyw            ⇒ .pyw=Python.NoConFile
ftype Python.NoConFile ⇒ "C:\\WINDOWS\\pyw.exe" "%L" %*
```
⚠ 若某台机器没有这个关联 ⇒ 右键 → 打开方式 → 选 `pythonw.exe`；或建一个指向
`pythonw.exe <本文件>` 的快捷方式。

## 它做四件事（都为着"双击就能用，而且坏了说得清"）

| # | 做什么 | 为什么 |
|---|---|---|
| **1** | ⭐ 把自己的输出**重定向到文件**（`_mecha_runtime/launcher.log`） | ⚠ **没有控制台 ⇒ 不留日志就等于"坏了什么都看不到"**。（铁律 1：日志必须留，**绝不 `DEVNULL`**） |
| **2** | 定**站点搜索根**：环境变量 `MECHA_SITE_ROOTS` 优先；否则读旁边的 `mecha_roots.txt` | ⚠ **一个根找不到三家** —— 本机上 GP 与另两仓**不在同一棵树里** |
| **3** | 调 `mecha.launch.main()` | 它会起各家软件 + **一个 dsh** + **那个常驻控制台**，并**自动开浏览器** |
| **4** | ⭐ **起不来就弹一个原生消息框**，告诉你"看哪个日志" | ⚠ 没有控制台时，**静默失败是最坏的一种**：人双击了、什么都没发生 |

## ⚠ 它**不**做的事（有意）

- **不**自己起进程、**不**自己探活、**不**自己记日志（那些都是 launcher/supervisor 的事）
  ⇒ ⭐ 本文件**只负责"没有控制台时的那点胶水"**（重定向 + 找根 + 报错可见）。
  ⚠ 加进来的每一行都必须是"**只有双击场景才需要**"的；能归 launcher 的一律归 launcher。

## `mecha_roots.txt` 的格式

```
# 一行一个仓路径（`#` 起注释；两端空白与引号会被去掉）
<盘>:\\<某父目录>\\Mechine learning
<盘>:\\<某父目录>\\energy-level
<盘>:\\...\\Get Paper
```
⚠ 它**是本机的东西** ⇒ 已 gitignore，仓里只放 `mecha_roots.txt.example`。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

#: ⚠ 入口自己的目录：用来在"被拷进某个项目仓"时**也能找到框架**。
ENTRY_DIR = Path(__file__).resolve().parent

#: ⚠ **用 cwd 而不是入口目录**：双击时 Explorer 把 cwd 设成文件所在目录，
#: 而 `mecha.launch` 的运行期目录就是 `<cwd>/_mecha_runtime` —— 两者一致最不意外。
RUNTIME = Path.cwd() / "_mecha_runtime"
LOG = RUNTIME / "launcher.log"

#: 根清单的文件名（放 cwd 或入口旁边都认）。
ROOTS_FILE = "mecha_roots.txt"


def _roots_file_text() -> str:
    """读根清单 ⇒ 返回 ``os.pathsep`` 连接的串（找不到 ⇒ 空串 = 交给 launcher 的缺省）。

    ⚠⚠ **必须用 `utf-8-sig` 读**（实测踩过）：Windows 上记事本、PowerShell 的
    ``Out-File -Encoding UTF8`` 都会写 **BOM**，而 ``'\\ufeff'.strip()`` **不会**去掉它
    （它不是 Python 意义上的空白）⇒ 那一行会变成"**一个不存在的根**" ⇒ 启动直接失败，
    而报错是"站点搜索根不存在：<一个看不见的字符>" —— **人根本看不懂**。
    ⇒ ``utf-8-sig`` 让 BOM 在读入时就消失。
    """
    for cand in (Path.cwd() / ROOTS_FILE, ENTRY_DIR / ROOTS_FILE):
        if not cand.is_file():
            continue
        out: list[str] = []
        for line in cand.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            line = line.split("#", 1)[0].strip().strip('"').strip("'")
            if line:
                out.append(line)
        if out:
            print(f"[start] 从 {cand} 读到 {len(out)} 个站点根", flush=True)
            return os.pathsep.join(out)
        print(f"[start] ⚠ {cand} 里没有有效路径（只有注释/空行）", flush=True)
    return ""


def _tell_user(title: str, msg: str) -> None:
    """⭐ **没有控制台时，必须让"坏了"看得见**（原生消息框；失败也不许再抛）。

    ⚠ 为什么不能只写日志：人双击了、屏幕毫无反应，他会以为"这程序就是坏的"，
    而真因躺在某个他不认识的日志里 ⇒ **等于没报错**。
    """
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, msg, title, 0x10)   # MB_ICONERROR
    except Exception:                                              # noqa: BLE001
        pass


def _find_capable_python() -> list[str] | None:
    """⭐ 找一个**装了依赖的**解释器（返回可执行 argv 前缀）；找不到 ⇒ ``None``。

    ## 为什么必须有这一步（**实测的根因**，别再删）

    双击 `.pyw` 时，拉起它的是 **`pyw.exe`**（Windows 的 Python 启动器），
    而它按**它自己的默认**选版本 —— 实测这台机器：``py -0`` 的默认是 **3.14**（没装 `mcp`），
    而装了依赖的是 **3.10**（PATH 上 `pythonw` 的第一个也是它）
    ⇒ ⚠ 用户双击就吃 **`ModuleNotFoundError: No module named 'mcp'`**。

    ⚠ **不硬编码任何本机路径**：本仓是**公开**的，写死 `<盘>:\\python310` 就是泄漏机器信息。
    ⭐ 做法：**问启动器自己**（`pyw -3.10` / `py -3.10` …）＋ PATH（`pythonw`）＋ 环境变量
    ``MECHA_PYTHON``（给用户一个显式覆盖口），逐个探 `import mcp`，用**第一个成功的**。
    """
    probes: list[list[str]] = []
    env = os.environ.get("MECHA_PYTHON", "").strip()
    if env:
        probes.append([env])
    for launcher in ("pyw", "py"):                       # ⚠ pyw 优先：它是**无控制台**那个
        exe = shutil.which(launcher)
        if exe:
            for v in ("-3.10", "-3.12", "-3.11", "-3.13", "-3"):
                probes.append([exe, v])
    w = shutil.which("pythonw")
    if w:
        probes.append([w])
    for probe in probes:
        try:
            r = subprocess.run([*probe, "-c", "import mcp"],
                               capture_output=True, timeout=25)
        except Exception:                                # noqa: BLE001 - 探不通就下一个
            continue
        if r.returncode == 0:
            return probe
    return None


def main(argv: list[str] | None = None) -> int:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    #: ⭐ 第 1 件事：输出落文件（**在调任何东西之前**，否则最早的报错会丢）。
    fh = open(LOG, "a", encoding="utf-8", errors="replace", buffering=1)   # noqa: SIM115
    sys.stdout = fh
    sys.stderr = fh
    print(f"\n===== {Path(__file__).name} 起 =====", flush=True)
    print(f"[start] cwd={Path.cwd()}", flush=True)
    print(f"[start] 日志={LOG}", flush=True)

    #: 自己的目录放到最前（被拷进项目仓时也能 import 到框架）。
    if str(ENTRY_DIR) not in sys.path:
        sys.path.insert(0, str(ENTRY_DIR))

    try:
        #: ⭐ **先解决"用哪个解释器"**（实测根因：`pyw.exe` 按自己的默认选了没装依赖的那个）。
        #: ⚠ 用 `MECHA_ENTRY_REEXEC` 防自锁（换过一次就不再换）。
        if os.environ.get("MECHA_ENTRY_REEXEC") != "1":
            try:
                import mcp                                    # noqa: F401
            except ModuleNotFoundError:
                found = _find_capable_python()
                if found is None:
                    raise RuntimeError(
                        f"当前 Python（{sys.executable}）缺 `mcp`，且**没找到**装了它的解释器。\n"
                        f"　· 给它装上：\"{sys.executable}\" -m pip install \"mecha[mcp]\"\n"
                        f"　· 或用环境变量 MECHA_PYTHON 指定一个装好的解释器路径")
                print(f"[start] ⚠ 当前解释器缺依赖 ⇒ 换用：{' '.join(found)}", flush=True)
                fh.flush()
                return subprocess.call(
                    [*found, str(Path(__file__).resolve()), *(argv if argv is not None
                                                              else sys.argv[1:])],
                    env={**os.environ, "MECHA_ENTRY_REEXEC": "1"})

        if not os.environ.get("MECHA_SITE_ROOTS", "").strip():
            roots = _roots_file_text()
            if roots:
                os.environ["MECHA_SITE_ROOTS"] = roots
            else:
                #: ⭐ **没根清单就要当场说清"怎么办"**。实测：用户双击后拿到的是
                #: `没找到任何站点声明（找过 [...\mecha] 下的 mecha_site.py）`
                #: ⇒ ⚠ **那句话没错，但没告诉他缺的是哪个文件、去哪儿补**（等于没说）。
                #: ⚠ 这里**主动失败、不回落 CWD**：双击时 cwd 就是入口所在目录，
                #: 而**框架仓自己没有 `mecha_site.py`** ⇒ 回落必然以一句更难懂的话失败。
                example = ENTRY_DIR / (ROOTS_FILE + ".example")
                raise RuntimeError(
                    f"没找到站点根清单：{Path.cwd() / ROOTS_FILE}\n"
                    f"　· 把 {example} 复制成 {ROOTS_FILE}，**一行写一个项目仓的完整路径**；\n"
                    f"　· 或用环境变量 MECHA_SITE_ROOTS（分号分隔多个仓）；\n"
                    f"　· 或命令行：python -m mecha --all --root <仓>（可重复给）。")
        else:
            print("[start] 用环境变量 MECHA_SITE_ROOTS（优先于根清单文件）", flush=True)

        from mecha.launch import main as launch_main
        return int(launch_main(argv if argv is not None else sys.argv[1:]))
    except SystemExit:
        raise
    except BaseException:                                  # noqa: BLE001 - 最后一道网
        tb = traceback.format_exc()
        print(tb, flush=True)
        _tell_user("机甲启动失败",
                   f"启动失败，原因已写进日志：\n\n{LOG}\n\n"
                   f"最后一行：\n{tb.strip().splitlines()[-1] if tb.strip() else '（无）'}")
        return 3


if __name__ == "__main__":
    sys.exit(main())
