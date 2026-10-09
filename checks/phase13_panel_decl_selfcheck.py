"""判据 ⑱：**监控声明 → dsh 参数**（阶段②-1）——声明形状 / 那份参数 / 框架自己的 patch。

三层架构 §7.3.6（字段形状）+ §7.3.3（命名定案）+ §7.3.8（施工补记）。这一条守的是
**"新项目接入只写声明"那条承诺的地基**：声明能被翻成一份**不含端口号**的参数，
且**由框架自己的 patch** 把框架插件带进那**一个** dsh。

## 逐条守什么

| # | 断言 | 为什么 |
|---|---|---|
| 1 | `MonitorSpec` 的形状与缺省 | `title` 缺省 ⇒ `Service.id`（§7.3.6）；必填只有 `route_path`/`port_file` |
| 2 | 声明写歪 ⇒ **声明期响亮失败** | 写歪的症状是"面板空的"（最坏那类假绿）⇒ 不许飘到运行期 |
| 3 | 那份参数**只含声明了 monitor 的服务**，命名 = `mecha:<id>` / `mecha-panel:<id>` | §7.3.3 定案（dsh 契约拒绝重复 id / 同 tier 同 kind） |
| 4 | ⚠ 那份参数里**没有端口号、没有本机绝对路径** | 它会给**浏览器**；端口一律由路由**现取** |
| 4b | ⭐ **第三个变量**（`MECHA_PANEL_PORT_FILES`）形状对：`{svc_id: **绝对**路径}`、服务集合与那份参数一致、且**不出现在给浏览器的那份参数里** | ⭐ 它让**框架的 host 插件替项目取端口**（"新项目连 host 半都不用写"那条承诺的**另一半**）；⚠ 相对路径会按 dsh 的 cwd 解析（那是**别人**的项目根） |
| 5 | ⭐ 框架的 patch + 插件产物 + 两个变量**恒进那一个 dsh**（含"项目不声明 dsh"那一支） | 否则"新项目还得自己挂框架插件" = **没预付** |
| 6 | 路由常量**两处相等**（Python 权威 ↔ 资产 TS 镜像） | 不一致 = 浏览器 fetch 404 = 一个 tab 都没有 |
| 7 | ⭐ 夹具 `panel/real-panel-services.json` **与三家的真声明对拍** | ⚠ 这正是"夹具失真"那条红线的机器守卫（本仓刚栽过一次：夹具与真形状不一致 ⇒ 判据全绿而真项目一炸） |

⚠ **#7 需要三家仓在位**（`MECHA_SITE_ROOTS`，或 `MECHA_ML_ROOT`/`MECHA_EL_ROOT`/`MECHA_GP_ROOT`）。
缺 ⇒ **SKIP**（严格模式下 `run_all --require-external` 把它变成 FAIL）——
**不假装对拍过**。⚠ 本文件**不写任何本机绝对路径**（`personal_paths_selfcheck` 的纪律）。

## Known Limitations and Deferred Work

- **不验真 dsh**：那份 patch 是否真被 dsh 认、插件是否真加载，要真机（`--dump-config` + 真页面）。
  ⚠ §7.3.5 已如实登记同一条限制。本判据只验**框架生成的那份东西**对不对。
- ⭐ **项目的 host 半那条监控地址路由已上提**（2026-10-09）：本判据验的是**框架注入的那份参数**
  （含 `MECHA_PANEL_PORT_FILES` 的绝对/不泄漏）；"框架的 host 插件真按它注册了 N 条路由、
  且现取端口"由资产的 `mecha-panel-host.test.ts`（含**与资产实现的等价判据**）钉住。
- **红证三条**（端口守卫 / 夹具对拍 / **端口文件路径**）：形状与 patch 那几条靠**直接断言**，
  不再各造一份变体（本判据的重点是"夹具与真形状一致"，那条最贵）。
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mecha.dsh_panel import (  # noqa: E402
    PANEL_PORT_FILES_ENV,
    PANEL_ROUTE_ENV,
    PANEL_SERVICES_ENV,
    PANEL_SERVICES_ROUTE,
    framework_patch_path,
    framework_plugin_artifact,
    panel_kind,
    panel_payload,
    panel_port_files,
    panel_services,
    panel_tab_id,
    services_with_monitor,
)
from mecha.errors import MechaError  # noqa: E402
from mecha.launch import flat_services, load_sites, merge_dsh  # noqa: E402
from mecha.site import (  # noqa: E402
    DSHSpec, ExtraColumn, ExtraPage, MonitorSpec, ReadySpec, Service, Site,
)

FIXTURE = ROOT / "mecha" / "dsh-panel" / "panel" / "real-panel-services.json"
SERVICES_TS = ROOT / "mecha" / "dsh-panel" / "panel" / "services.ts"
AUTO_TEST = ROOT / "mecha" / "dsh-panel" / "panel" / "auto.test.ts"
#: 三家的服务 id（对拍时要求**恰好**是这三个 —— 少一家或多一家都该被人看见）。
REAL_IDS = ("ML", "EL", "GP")


def _svc(svc_id: str = "X", **kw) -> Service:
    base: dict = {
        "id": svc_id, "brief": "b", "argv": ("true",), "cwd": Path("."),
        "ready": ReadySpec(port_file=".p"), "data_root": Path("."),
    }
    base.update(kw)
    return Service(**base)


def _expect_error(what: str, fn) -> str | None:
    """跑 `fn`，**要求它响亮失败**；返回 None 表示过了，否则返回一句话。"""
    try:
        fn()
    except MechaError as exc:
        return None if exc.kind else f"{what}：抛了但没有 kind"
    except Exception as exc:                            # noqa: BLE001 - 判据要能看见任何异常
        return f"{what}：抛的不是 MechaError（{type(exc).__name__}: {exc}）"
    return f"{what}：**应当响亮失败，但它过了**"


def _check_shapes() -> list[str]:
    bad: list[str] = []
    # 1) title 缺省 ⇒ Service.id（§7.3.6）
    # ⚠ 用例里的服务名/id 一律**中性**（词表守卫扫 checks/：库里不许出现项目领域值）
    s = _svc("AAA", monitor=MonitorSpec(route_path="/a/monitor-url", port_file=".a-monitor-port"))
    if s.monitor is None or s.monitor.title != "AAA":
        bad.append(f"title 缺省应当补成 Service.id，实际 {s.monitor!r}")
    # 显式标题不被改写
    s2 = _svc("BBB", monitor=MonitorSpec(route_path="/b/monitor-url", port_file=".p", title="显式标题"))
    if s2.monitor is None or s2.monitor.title != "显式标题":
        bad.append("显式 title 被改写了")
    # 没有 monitor ⇒ None（"没监控面"在类型上可表达）
    if _svc("CCC").monitor is not None:
        bad.append("没声明 monitor 的服务不该有 MonitorSpec")

    # 2) 写歪 ⇒ 声明期响亮失败
    for what, kwargs in (
        ("route_path 空", {"route_path": "  ", "port_file": ".p"}),
        ("route_path 不是路径", {"route_path": "http://127.0.0.1:1/x", "port_file": ".p"}),
        ("port_file 空", {"route_path": "/x", "port_file": ""}),
        ("port_file 写成了地址", {"route_path": "/x", "port_file": "http://127.0.0.1:1/p"}),
    ):
        msg = _expect_error(f"MonitorSpec({what})",
                            lambda kw=kwargs: _svc("A", monitor=MonitorSpec(**kw)).validate())
        if msg:
            bad.append(msg)
    # 附加页：没有列 / 路径不是路径 / id 重复
    msg = _expect_error("附加页没有列", lambda: _svc("A", monitor=MonitorSpec(
        route_path="/x", port_file=".p", extra_pages=(ExtraPage(id="a", label="l", path="/a"),),
    )).validate())
    if msg:
        bad.append(msg)
    msg = _expect_error("附加页 id 重复", lambda: _svc("A", monitor=MonitorSpec(
        route_path="/x", port_file=".p",
        extra_pages=(ExtraPage(id="a", label="l", path="/a", columns=(ExtraColumn("c", "f"),)),
                     ExtraPage(id="a", label="l2", path="/b", columns=(ExtraColumn("c", "f"),))),
    )).validate())
    if msg:
        bad.append(msg)
    return bad


def _check_payload() -> list[str]:
    bad: list[str] = []
    a = _svc("AAA", monitor=MonitorSpec(route_path="/a/monitor-url", port_file=".a-port"))
    b = _svc("BBB")                                     # 没有监控面 ⇒ 不进参数
    c = _svc("CCC", monitor=MonitorSpec(route_path="/c/monitor-url", port_file=".c-port",
                                        extra_pages=(ExtraPage(id="p", label="L", path="/p",
                                                               columns=(ExtraColumn("列", "f"),)),)))
    payload = panel_payload((a, b, c))
    ids = [s["id"] for s in payload["services"]]
    if ids != ["AAA", "CCC"]:
        bad.append(f"那份参数应当只含声明了 monitor 的服务，实际 {ids}")
    if [s["kind"] for s in payload["services"]] != ["mecha:AAA", "mecha:CCC"]:
        bad.append("kind 命名不是 §7.3.3 定案（mecha:<svc_id>）")
    if [s["tabId"] for s in payload["services"]] != ["mecha-panel:AAA", "mecha-panel:CCC"]:
        bad.append("tabId 命名不是 §7.3.3 定案（mecha-panel:<svc_id>）")
    if panel_kind("X") != "mecha:X" or panel_tab_id("X") != "mecha-panel:X":
        bad.append("panel_kind / panel_tab_id 与 §7.3.3 不一致")
    if payload["routePath"] != PANEL_SERVICES_ROUTE:
        bad.append("那份参数里的 routePath 与 PANEL_SERVICES_ROUTE 不一致")
    if services_with_monitor((a, b, c)) != (a, c):
        bad.append("services_with_monitor 的顺序/内容不对（顺序必须是声明顺序）")
    # 附加页 wire：camelCase + 只有数据（没有函数）
    page = payload["services"][1]["extraPages"][0]
    if sorted(page) != ["columns", "emptyText", "id", "label", "note", "path", "title",
                        "unreadableText"]:
        bad.append(f"附加页 wire 的键不对：{sorted(page)}")
    if page["columns"] != [{"label": "列", "field": "f"}]:
        bad.append(f"列 wire 不对：{page['columns']}")
    if not isinstance(page["title"], str) or page["title"] != "L":
        bad.append("附加页 title 缺省应当补成 label")
    # ⭐ **那份参数本身的禁项**（端口号/地址/绝对路径）——**单独一个函数**：
    #   红证要**真跑它**（喂一份下毒的载荷），不是另抄一遍谓词（那会变成"关于守卫的假结论"）。
    bad += payload_defects(payload)
    return bad


def payload_defects(payload: dict) -> list[str]:
    """那份参数的**禁项**（它给浏览器 ⇒ 不许有端口号/地址/本机绝对路径）。

    ⚠ 独立成函数是**为了红证能真跑它**（`_red_proofs` 喂一份下毒的载荷，断言它当场报红）。
    """
    bad: list[str] = []
    text = json.dumps(payload, ensure_ascii=False)
    if "://" in text or "http" in text:
        bad.append(f"那份参数里出现了地址：{text[:200]}")
    if re.search(r":\d{2,5}\b", text):
        # `/runs?limit=50` 那种（`=` 后面的数）不算；这里抓的是 `:<2~5 位数字>`
        bad.append(f"那份参数里出现了像端口的东西：{text[:200]}")
    if re.search(r"[A-Za-z]:[\\/]", text):
        bad.append(f"那份参数里出现了本机绝对路径：{text[:200]}")
    for forbidden in ("root", "cwd", "data_root"):
        if forbidden in text:
            bad.append(f"那份参数里出现了 {forbidden}（绝对路径/根不该给浏览器）")
    return bad


def fixture_defects(real: dict, fixture: dict) -> list[str]:
    """夹具 ↔ 真声明的**差异清单**（空 = 一致）。⚠ 独立成函数：红证要真跑它。"""
    bad: list[str] = []
    for sid in sorted({s["id"] for s in real["services"]} | {s["id"] for s in fixture["services"]}):
        got = next((s for s in real["services"] if s["id"] == sid), None)
        want = next((s for s in fixture["services"] if s["id"] == sid), None)
        if got != want:
            bad.append(f"夹具与真声明不一致（{sid}）：真={got!r} 夹具={want!r}")
    if real.get("routePath") != fixture.get("routePath"):
        bad.append("夹具的 routePath 与框架的不一致")
    if real.get("services") is not None and not real["services"] and fixture.get("services"):
        bad.append("真声明里一个监控面都没有，而夹具里有 ⇒ 夹具是编的")
    return bad


def port_files_defects(env_value: str | None, payload: dict) -> list[str]:
    """`MECHA_PANEL_PORT_FILES` 的**形状与禁项**（⚠ 独立成函数 ⇒ 红证要真跑它）。

    三条（各对着一条纪律）：

    1. 形状必须是 `{svc_id: **绝对**路径}` —— ⚠ 相对路径会在 dsh 进程里按**别人的** cwd
       解析（dsh 的 cwd 只有一个，§1.1.1 的已知限制）⇒ 症状是"那个 tab 永远 503"；
    2. 服务集合 = **那份浏览器参数里的服务集合**（两者由同一个注入点生成，缺一个就是接线错）；
    3. ⭐ 这些**绝对路径不许出现在给浏览器的那份参数里**
       （那份参数给浏览器；绝对路径只给框架的 host 插件 —— 两个变量、两种读者）。
    """
    bad: list[str] = []
    if not env_value:
        return [f"{PANEL_PORT_FILES_ENV} 没注入（框架的 host 插件就没法替项目取端口）"]
    try:
        parsed = json.loads(env_value)
    except json.JSONDecodeError as exc:
        return [f"{PANEL_PORT_FILES_ENV} 不是 JSON：{exc}"]
    if not isinstance(parsed, dict):
        return [f"{PANEL_PORT_FILES_ENV} 的形状该是 {{svc_id: 绝对路径}}，实得 {type(parsed).__name__}"]
    want_ids = {s["id"] for s in payload.get("services", [])}
    if set(parsed) != want_ids:
        bad.append(f"{PANEL_PORT_FILES_ENV} 的服务集合与那份参数不一致："
                   f"{sorted(parsed)} vs {sorted(want_ids)}")
    for sid, path in parsed.items():
        if not isinstance(path, str) or not path.strip():
            bad.append(f"{sid} 的端口文件路径为空")
            continue
        if not Path(path).is_absolute():
            bad.append(f"{sid} 的端口文件路径**不是绝对路径**：{path!r}"
                       "（dsh 只有一个 cwd ⇒ 相对路径会按别人的 cwd 解析）")
    browser_text = json.dumps(payload, ensure_ascii=False)
    for sid, path in parsed.items():
        if not isinstance(path, str) or not path:
            continue
        #: ⚠ **两种形态都要比**：Windows 路径里的 `\` 在 JSON 里是转义的
        #: ⇒ 只比原串的话，这条守卫在 Windows 上**恒绿**（假守卫）。
        for form in {path, json.dumps(path, ensure_ascii=False)[1:-1]}:
            if form in browser_text:
                bad.append(f"绝对路径漏进了**给浏览器**的那份参数（{sid}）：它只该走 "
                           f"{PANEL_PORT_FILES_ENV}")
                break
    return bad


def _check_port_files_env() -> list[str]:
    """⭐ 第三个变量的**存在性 + 形状 + 不泄漏**（两个 dsh 分支都要有）。"""
    bad: list[str] = []
    svc = _svc("AAA", monitor=MonitorSpec(route_path="/a/monitor-url", port_file=".a-port"),
               cwd=Path("/probe-root"), data_root=Path("/probe-root"))
    payload = panel_payload((svc,))
    # 解析规则住在 `Service.monitor_port_file()`（一处翻译）：相对 `data_root` ⇒ 拼成**绝对**
    expect = svc.monitor_port_file()
    files = panel_port_files((svc,))
    if expect is None or not expect.is_absolute():
        bad.append(f"Service.monitor_port_file() 出口该是绝对路径，实得 {expect!r}")
    elif files != {"AAA": str(expect)}:
        bad.append(f"panel_port_files 的解析不对：{files}（应当 = monitor_port_file()）")
    bad += port_files_defects(json.dumps(files, ensure_ascii=False), payload)

    # 两个 dsh 分支（声明了 / 完全不管）都必须带上它
    for label, site in (
        ("项目声明了 dsh", Site(name="S", services=(svc,),
                              dsh=DSHSpec(command=("dsh",), cwd=Path(".")))),
        ("项目不声明 dsh", Site(name="T", services=(svc,))),
    ):
        _, merged = merge_dsh((site,))
        got = merged.env.get(PANEL_PORT_FILES_ENV)
        if not got:
            bad.append(f"{label} 时没注入 {PANEL_PORT_FILES_ENV}")
            continue
        bad += [f"{label}：{d}" for d in port_files_defects(got, json.loads(
            merged.env.get(PANEL_SERVICES_ENV, "{}") or "{}"))]

    # 站点抢注这个变量（不同值）⇒ **响亮失败**（与另两个变量同口径）
    msg = _expect_error("站点抢注框架的端口文件变量", lambda: merge_dsh((Site(
        name="U", services=(svc,),
        dsh=DSHSpec(command=("dsh",), cwd=Path("."), env={PANEL_PORT_FILES_ENV: "{}"}),
    ),)))
    if msg:
        bad.append(msg)
    return bad


def _check_dsh_contributions() -> list[str]:
    bad: list[str] = []
    svc = _svc("AAA", monitor=MonitorSpec(route_path="/a/monitor-url", port_file=".a-port"))
    patch = framework_patch_path()
    artifact = framework_plugin_artifact()
    if not patch.is_file():
        bad.append(f"框架的 patch 不存在：{patch}")
    if not artifact.is_file():
        bad.append(f"框架的插件产物不存在：{artifact}")

    # ① 项目声明了 dsh
    site = Site(name="S", services=(svc,), dsh=DSHSpec(command=("dsh",), cwd=Path(".")))
    child, merged = merge_dsh((site,))
    if patch not in merged.patches:
        bad.append("合并后的 dsh 没带上**框架自己的 patch**（那就等于让新项目自己挂框架插件）")
    if artifact not in merged.plugin_artifacts:
        bad.append("合并后的 dsh 没把框架插件列进 plugin_artifacts（少它就等 dsh 起来报错）")
    if merged.env.get(PANEL_ROUTE_ENV) != PANEL_SERVICES_ROUTE:
        bad.append(f"{PANEL_ROUTE_ENV} 没进 dsh 的环境")
    parsed = json.loads(merged.env.get(PANEL_SERVICES_ENV, "null") or "null")
    if not isinstance(parsed, dict) or [s["id"] for s in parsed.get("services", [])] != ["AAA"]:
        bad.append(f"{PANEL_SERVICES_ENV} 不是那份参数：{merged.env.get(PANEL_SERVICES_ENV)!r}")
    if "--patch" not in child.argv or str(patch) not in child.argv:
        bad.append(f"框架 patch 没进 dsh 的 argv：{child.argv}")
    if str(artifact) not in child.argv and artifact.name not in json.dumps(child.argv):
        pass                                            # plugin_artifacts 不进 argv，只做存在性校验

    # ② ⭐ 项目**不声明** dsh 的那一支也要带（"项目可以完全不管 dsh"）
    bare_site = Site(name="T", services=(svc,))
    _, merged2 = merge_dsh((bare_site,))
    if patch not in merged2.patches or artifact not in merged2.plugin_artifacts:
        bad.append("项目不声明 dsh 时**没带上**框架的 patch/插件（那一支也要带）")
    if merged2.env.get(PANEL_ROUTE_ENV) != PANEL_SERVICES_ROUTE:
        bad.append("项目不声明 dsh 时没注入面板变量")

    # ③ 站点若自己给那两个变量（不同值）⇒ **响亮失败**（面板参数只有一个家）
    msg = _expect_error("站点抢注框架的面板变量", lambda: merge_dsh((Site(
        name="U", services=(svc,),
        dsh=DSHSpec(command=("dsh",), cwd=Path("."), env={PANEL_ROUTE_ENV: "/mine"}),
    ),)))
    if msg:
        bad.append(msg)
    return bad


def _check_route_mirror() -> list[str]:
    bad: list[str] = []
    if not SERVICES_TS.is_file():
        return [f"资产的 services.ts 不在：{SERVICES_TS}"]
    src = SERVICES_TS.read_text(encoding="utf-8")
    m = re.search(r"export const PANEL_SERVICES_ROUTE\s*=\s*'([^']+)'", src)
    if m is None:
        return ["services.ts 里读不到 PANEL_SERVICES_ROUTE（判据读不到 ⇒ 视为红，不静默放过）"]
    mirror = m.group(1)
    if mirror != PANEL_SERVICES_ROUTE:
        bad.append(f"路由常量两处不一致：Python={PANEL_SERVICES_ROUTE!r} vs 资产 TS={mirror!r}"
                   "（不一致 = 浏览器 fetch 404 = 一个 tab 都没有）")
    if not AUTO_TEST.is_file() or "real-panel-services.json" not in AUTO_TEST.read_text(encoding="utf-8"):
        bad.append("资产单测没有读那份夹具（夹具成了孤儿 ⇒ 与真声明对拍也就无从谈起）")
    return bad


def _roots() -> tuple[list[Path] | None, str]:
    """三家的站点搜索根（**只用环境变量**；本文件不许写本机路径）。"""
    env = os.environ.get("MECHA_SITE_ROOTS", "").strip()
    if env:
        return [Path(p) for p in env.split(os.pathsep) if p.strip()], "MECHA_SITE_ROOTS"
    singles = {name: os.environ.get(f"MECHA_{name}_ROOT", "").strip()
               for name in ("ML", "EL", "GP")}
    if all(singles.values()):
        return [Path(v) for v in singles.values()], "MECHA_ML_ROOT/MECHA_EL_ROOT/MECHA_GP_ROOT"
    return None, ""


def _check_fixture_against_real() -> tuple[list[str], str]:
    """⭐ 夹具 ↔ **三家真声明**对拍。返回 (问题列表, 跳过原因或空串)。"""
    roots, how = _roots()
    if roots is None:
        return [], ("缺三家仓 ⇒ 跳过「夹具 ↔ 真声明」对拍"
                    "（设 MECHA_SITE_ROOTS，或 MECHA_ML_ROOT/MECHA_EL_ROOT/MECHA_GP_ROOT 启用）")
    try:
        sites = load_sites(roots)
    except MechaError as exc:
        return [f"按 {how} 加载三家声明失败：{exc.kind}：{exc.message}"], ""
    svcs = flat_services(sites)
    ids = tuple(s.id for s in svcs if s.monitor is not None)
    bad: list[str] = []
    if ids != REAL_IDS:
        bad.append(f"三家声明里带监控面的服务应当是 {REAL_IDS}，实际 {ids}"
                   "（新增/删掉一家 ⇒ 本判据与夹具都要跟着改）")
    real = panel_payload(svcs)
    bad += _fixture_defects_vs(real)
    return bad, ""


def _fixture_defects_vs(real: dict) -> list[str]:
    """读夹具并与 `real` 对拍。⚠ **夹具本身坏掉也要报成"一条不过"**，不许抛穿：
    它正是这条判据要守的东西 —— 一次 `SyntaxError`/`JSONDecodeError` 冒到顶，
    看到的人只会以为"框架坏了"，而真因是夹具被改坏（或带了 BOM）。"""
    raw = FIXTURE.read_bytes()
    bad: list[str] = []
    if raw[:3] == b"\xef\xbb\xbf":
        # ⚠ 本工程在 BOM 上栽过两次（入口读根清单、判据 ast.parse）；JSON 带 BOM 还会让
        #   浏览器侧的 `JSON.parse` 直接炸 ⇒ 这里是**缺陷**，不是"读的时候宽容一点"。
        bad.append(f"夹具带了 BOM（{FIXTURE.name}）：JSON 带 BOM 会让 `JSON.parse` 抛")
    try:
        fixture = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return bad + [f"夹具读不出（{FIXTURE.name}）：{type(exc).__name__}: {exc}"]
    if not isinstance(fixture, dict):
        return bad + [f"夹具的顶层不是对象：{type(fixture).__name__}"]
    return bad + fixture_defects(real, fixture)


# ------------------------------------------------------------------ 红证（扰动必须落上）

def _red_proofs() -> list[str]:
    """两条红证：**扰动真造出来**，且**真的判据**在扰动上变红。

    ⚠ 本仓纪律（R7/R16）：红证**不许**另抄一遍谓词 —— 那得到的是"关于守卫的假结论"。
    ⇒ 这里喂的是**真判据函数**（:func:`payload_defects` / :func:`fixture_defects`）。
    ⚠ 另一条同样重要：**扰动没落上也要报红**（否则"判据抓不住"这个结论本身是假的）。
    """
    bad: list[str] = []
    # 红证 ①：把那份参数**下毒**（塞一个端口地址）⇒ `payload_defects` 必须当场报出来。
    payload = panel_payload((_svc("AAA", monitor=MonitorSpec(
        route_path="/a/monitor-url", port_file=".a-port")),))
    clean = payload_defects(payload)
    if clean:
        bad.append(f"红证①的前提不成立：干净的载荷本该 0 缺陷，实际 {clean}")
    poisoned = json.loads(json.dumps(payload))
    poisoned["services"][0]["routePath"] = "http://127.0.0.1:62499/mcp"
    defects = payload_defects(poisoned)
    if not defects:
        bad.append("红证①没红：往那份参数里塞了 `http://127.0.0.1:62499/mcp`，"
                   "而 payload_defects 一个缺陷都没报（⇒ 端口守卫是哑的）")
    elif not any("地址" in d for d in defects):
        bad.append(f"红证①红了但不是那条：{defects}")

    # 红证 ②：把夹具**改一个字段** ⇒ `fixture_defects` 必须报出差异（并点名是哪个服务）。
    roots, _how = _roots()
    if roots is not None:
        try:
            real = panel_payload(flat_services(load_sites(roots)))
        except MechaError:
            real = None
        if real is not None:
            if fixture_defects(real, real):
                bad.append("红证②的前提不成立：拿**同一份**当真/当夹具，本该 0 差异")
            tampered = json.loads(json.dumps(real))
            if tampered["services"]:
                sid = tampered["services"][0]["id"]
                tampered["services"][0]["routePath"] = "/not/what/the/declaration/says"
                diffs = fixture_defects(real, tampered)
                if not diffs:
                    bad.append("红证②没红：夹具被改了一个字段，而对拍一条差异都没报"
                               "（⇒ 夹具失真抓不住）")
                elif not any(sid in d for d in diffs):
                    bad.append(f"红证②红了但没点名是哪个服务（{sid}）：{diffs}")
    # 红证 ③：把 `MECHA_PANEL_PORT_FILES` **下毒**两种形态 ⇒ `port_files_defects` 各自必须报。
    #   ① 相对路径（dsh 只有一个 cwd ⇒ 会按别人的 cwd 解析）
    #   ② 把那条绝对路径**塞进给浏览器的那份参数**（两个变量串了）
    payload3 = panel_payload((_svc("AAA", monitor=MonitorSpec(
        route_path="/a/monitor-url", port_file=".a-port"), cwd=Path("/probe-root")),))
    abs_path = str(panel_port_files((_svc("AAA", monitor=MonitorSpec(
        route_path="/a/monitor-url", port_file=".a-port"),
        cwd=Path("/probe-root")),))["AAA"])
    clean3 = port_files_defects(json.dumps({"AAA": abs_path}), payload3)
    if clean3:
        bad.append(f"红证③的前提不成立：干净的那份本该 0 缺陷，实际 {clean3}")
    rel = port_files_defects('{"AAA": ".a-port"}', payload3)
    if not any("绝对路径" in d for d in rel):
        bad.append("红证③没红：喂了**相对路径**，而 port_files_defects 没报出来"
                   "（⇒ 那条守卫是哑的）")
    poisoned_payload = json.loads(json.dumps(payload3))
    poisoned_payload["services"][0]["portFile"] = abs_path
    leaked = port_files_defects(json.dumps({"AAA": abs_path}), poisoned_payload)
    if not any("漏进" in d for d in leaked):
        bad.append("红证③没红：把绝对路径塞进了给浏览器的那份参数，而它没报出来"
                   "（⇒ 泄漏守卫是哑的）")
    return bad


def check() -> tuple[int, list[str]]:
    lines: list[str] = []
    problems: list[str] = []
    problems += _check_shapes()
    problems += _check_payload()
    problems += _check_dsh_contributions()
    problems += _check_port_files_env()
    problems += _check_route_mirror()
    problems += _red_proofs()
    fixture_bad, skip_why = _check_fixture_against_real()
    problems += fixture_bad
    lines.append(f"[phase13] 框架 patch：{framework_patch_path()}")
    lines.append(f"[phase13] 框架插件产物：{framework_plugin_artifact()}")
    lines.append(f"[phase13] 那份参数的路由：{PANEL_SERVICES_ROUTE}"
                 f"（env {PANEL_ROUTE_ENV} / {PANEL_SERVICES_ENV} / {PANEL_PORT_FILES_ENV}）")
    if problems:
        lines.append("[phase13] ✖ 不过的条目：")
        lines.extend(f"    - {p}" for p in problems)
        lines.append(f"phase13_panel_decl_selfcheck: FAIL —— {len(problems)} 条不过"
                     "（监控声明 → dsh 参数 那一层）")
        return 1, lines
    if skip_why:
        lines.append(f"[phase13] {skip_why}")
        lines.append(f"SKIP: {skip_why}")
        return 0, lines
    lines.append("phase13_panel_decl_selfcheck: PASS（声明形状 + 那份参数 + 框架自己的 patch "
                 "+ 路由常量两处相等 + ⭐ 端口文件路径（绝对/不泄漏）"
                 "+ **夹具与三家真声明对拍**；三条红证已落）")
    return 0, lines


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    code, lines = check()
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
