"""判据 ⑳：**工具面只有一条腿** —— 三家项目的 dsh 半必须同形（空载体 + 一行 bootstrap），
且资产里的自愈 MCP 桥**不许回来**。

## 为什么单独立这一条（用户裁决 = 方案 A，2026-10-10）

现场：**只勾一家启动**，而 dsh 里那个 MCP 客户端却叫**别家**的名字（聚合端点借了第一个站点的名字，
已修），并且工具表里同时有**两条腿**：

| 腿 | 谁实现 | 工具名 | 有框架元工具吗 |
|---|---|---|---|
| 项目侧**自愈桥** | 资产的 `mcp-bridge.ts` + `host/mount-host-plugin.ts`（项目各接一次） | `mcp__<项目名>__*` | ⛔ **没有** |
| 框架**聚合端点** | `mecha/aggregate.py` + overlay | `mcp__mecha__<svc>_<tool>` | ✅ 有（`read_write_authority` / `query_software` / 开关软件） |

⚠ 三条实害：① `merge_dsh` 的 patches 是**全站点并集**（与"勾了谁"无关）⇒ 只开一家时
**别家的桥照样被加载**（对着一个没跑的软件重试）；② 工具名重复、AI 可能挑错；
③ ⭐ **能力不等**：写权/开关元工具只在聚合端点那条腿上。

⇒ 用户裁决：**删桥**（"不经 mecha 单独跑 dsh 就应该不出现工具"）。
⇒ 本判据把"三家同形 + 桥不许复活"钉成机器事实（R13：删机制要留反向判断）。

## 三条（第 3 条是红证）

| # | 断言 | 红了说明什么 |
|---|---|---|
| ① | ⭐ **三家项目**的 `dsh/src/index.ts` 都是**空载体**：`inject = []`、`apply()` 不收 ctx/config、源码（**剥注释后**）里没有 `mountHostPlugin` / `hostPluginInject` / `@mecha/dsh-panel/host` | 有人把桥接回去了（或新项目抄了老形状） |
| ② | 三家都**没有** `dsh/src/host/` 目录；资产里**没有** `mcp-bridge.ts` / `host/mount-host-plugin.ts` / `host/register-tools.ts` / `host/mcp-session-http.ts` / `host/config.ts` | 桥那套文件又长回来了 |
| ③ | ⭐ **红证**：把**老的**（带桥的）EL 入口文本喂给**同一个**判定函数 ⇒ 必须被判出来（R7/R16：不另抄谓词） | 判定函数被写哑（例如只查文件在不在） |

## ⚠ 外部依赖（缺即 **SKIP**，严格模式 `--require-external` 变 FAIL）

**三家项目的仓**（`MECHA_SITE_ROOTS` 或 `MECHA_ML_ROOT` / `MECHA_EL_ROOT` / `MECHA_GP_ROOT`）。
判据读的是**磁盘上的文件内容**（不经 git）——本工程有"gitignore 的文件逃出验收"的教训。

## Known Limitations and Deferred Work

- **不判"运行时真的只有一条腿"**：那要看 dsh 的 client 插件清单 + 真 MCP 连接（人工/真机验收）；
  本判据只判**源码与资产文件形状**（可离线、可红）。
- **不覆盖"新项目"**：名单是**声明驱动**的（读三家 root 的 `mecha_site.py` 找 `dsh/src`），
  但"某家某天换了目录布局"会让本判据**读不到** ⇒ ①要求每家的入口**必须存在**（读不到即红）。
- ⚠ **注释不算数**：三家的载体 docstring 里**故意**写着"桥已删除"这类字句（那是该留的指引）
  ⇒ 判定前**必须剥注释**，否则判据会被自己的说明弄红（EL/GP 侧各实测踩过一次）。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSET = ROOT / "mecha" / "dsh-panel"

FAILS: list[str] = []

#: ⛔ 桥那套文件（资产的）：**都不许存在**。
BRIDGE_FILES = (
    "mcp-bridge.ts",
    "host/mount-host-plugin.ts",
    "host/register-tools.ts",
    "host/mcp-session-http.ts",
    "host/config.ts",
    "host/index.ts",
    "host/mcp-sdk-shims.d.ts",
)
#: ⛔ 源码里不许出现的标识（剥注释后判定）。
BRIDGE_MARKERS = ("mountHostPlugin", "hostPluginInject", "@mecha/dsh-panel/host")


def _check(name: str, cond: bool, extra: object = "") -> bool:
    print(f"  {'✔' if cond else '✖'} {name}" + ("" if cond else f"   ← {extra}"))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def strip_comments(src: str) -> str:
    """剥掉 TS 注释（`//` 与 `/* */`）。

    ⚠ **必须剥**：载体 docstring 里**故意**写着"桥已删除/不许回来"（那是该留的指引），
    不剥就会把判据自己弄红（EL/GP 各实测踩到）。⚠ 别把 `http://` 的 `//` 当注释。
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(^|[^:])//.*$", r"\1", src, flags=re.M)


def carrier_defects(src: str) -> list[str]:
    """一个项目 host 半**不是空载体**的缺陷清单（空 = 合格）。

    ⚠ 独立成函数 ⇒ 红证要**真跑它**（R7/R16：不另抄一份谓词）。
    """
    bad: list[str] = []
    code = strip_comments(src)
    for marker in BRIDGE_MARKERS:
        if marker in code:
            bad.append(f"源码里出现桥的标识：{marker}")
    if not re.search(r"export\s+const\s+inject\s*:\s*string\[\]\s*=\s*\[\s*\]", code):
        bad.append("`inject` 不是空数组（载体不该依赖任何服务）")
    if not re.search(r"export\s+async\s+function\s+apply\s*\(\s*\)", code):
        bad.append("`apply` 还在收参数（没有桥要装了）")
    return bad


def _roots() -> dict[str, Path]:
    """三家的仓根（按 env 找；缺哪家就少哪家）。"""
    out: dict[str, Path] = {}
    env_sites = os.environ.get("MECHA_SITE_ROOTS", "").strip()
    if env_sites:
        for raw in env_sites.split(os.pathsep):
            p = Path(raw.strip())
            if p.is_dir():
                out[p.name] = p
    for key, var in (("ML", "MECHA_ML_ROOT"), ("EL", "MECHA_EL_ROOT"), ("GP", "MECHA_GP_ROOT")):
        raw = os.environ.get(var, "").strip()
        if raw and Path(raw).is_dir():
            out[Path(raw).name] = Path(raw)
        _ = key
    return out


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("[one_leg] 工具面只有一条腿：三家 dsh 半同形（空载体）且桥不许回来")

    # ---- ② 资产里没有桥那套文件 ----
    present = [f for f in BRIDGE_FILES if (ASSET / f).exists()]
    _check("② 资产里没有桥那套文件（mcp-bridge / host 骨架 / 工具注册 / SDK 会话 / 端点解析）",
           not present, present)
    _check("② 资产的面板那一半**还在**（别把该留的也删了）",
           (ASSET / "panel" / "auto.ts").is_file()
           and (ASSET / "host" / "monitor-url.ts").is_file()
           and (ASSET / "host-plugin" / "mecha-panel-host.mjs").is_file())

    # ---- ③ 红证：**老的**（带桥的）入口文本必须被判出来 ----
    #: ⚠ 夹具里**不许出现项目名**（框架不含领域值 ⇒ `vocabulary_selfcheck` 会红）。
    old_el = (
        "import { hostPluginInject, mountHostPlugin } from '@mecha/dsh-panel/host/mount-host-plugin.ts'\n"
        "export const inject = [...hostPluginInject()]\n"
        "export async function apply(ctx: Context, config: Config = {}): Promise<void> {\n"
        "  await mountHostPlugin(ctx, { serverName: 'svc', resolveUrl: () => url })\n"
        "}\n"
    )
    opened = carrier_defects(old_el)
    if opened:
        _check("③ 红证：带桥的老入口必须被判成「非载体」", True)
        print(f"      （判定函数报出的缺陷：{opened[0]}）")
    else:
        _check("③ 红证：带桥的老入口必须被判成「非载体」", False,
               "喂了带 mountHostPlugin 的老文本而一条缺陷都没报 ⇒ 判定函数是哑的")
    #: 对偶：**新形状**必须判合格（否则"全判红"也能骗过上面那条红证）
    new_ok = (
        "export const name = 'x'\n"
        "export const inject: string[] = []\n"
        "export async function apply(): Promise<void> {}\n"
    )
    _check("③ 对偶：新形状（空载体）必须判合格", not carrier_defects(new_ok),
           carrier_defects(new_ok))

    # ---- ① 三家同形 ----
    roots = _roots()
    if len(roots) < 3:
        print(f"SKIP: 只找到 {sorted(roots)} 家仓（要三家）⇒ 跳过「三家同形」那一档"
              "（②③ 已跑；严格模式 --require-external 会把它当 FAIL）")
        return 1 if FAILS else 0
    for label, root in sorted(roots.items()):
        entry = root / "dsh" / "src" / "index.ts"
        if not entry.is_file():
            _check(f"① {label}：host 入口存在（{entry}）", False, "读不到文件即红（不许静默跳过）")
            continue
        defects = carrier_defects(entry.read_text(encoding="utf-8"))
        _check(f"① {label}：host 半是**空载体**", not defects, defects)
        host_dir = root / "dsh" / "src" / "host"
        _check(f"① {label}：没有 `src/host/`（桥的家）", not host_dir.exists(), str(host_dir))

    if FAILS:
        print(f"panel_one_leg_selfcheck: FAIL（{len(FAILS)} 条）：{FAILS}")
        return 1
    print("panel_one_leg_selfcheck: PASS（三家同形 + 桥不许回来 + 红证已落）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
