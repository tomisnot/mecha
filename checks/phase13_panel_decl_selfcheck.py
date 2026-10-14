"""监控声明的行为契约：字段校验、配置投影、框架插件与端口发现参数。

纯合成站点；客户端呈现和 host 取址分别由资产测试覆盖。
不扫描项目源码，也不要求测试夹具跟随所有真实项目的声明变化。
"""

from __future__ import annotations

import json
import shutil
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
    panel_tab_id,
    services_with_monitor,
)
from mecha.errors import MechaError  # noqa: E402
from mecha.launch import merge_dsh  # noqa: E402
from mecha.site import (  # noqa: E402
    DSHSpec, ExtraColumn, ExtraPage, MonitorSpec, ReadySpec, Service, Site,
)

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
    bad += payload_defects(payload)
    return bad


def payload_defects(payload: dict) -> list[str]:
    """那份参数的**禁项**（它给浏览器 ⇒ 不许有端口号/地址/本机绝对路径）。

    检查序列化载荷中的端口与路径泄漏。
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




def port_files_defects(env_value: str | None, payload: dict) -> list[str]:
    """`MECHA_PANEL_PORT_FILES` 的**形状与禁项**。

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













def check() -> tuple[int, list[str]]:
    lines: list[str] = []
    problems: list[str] = []
    problems += _check_shapes()
    problems += _check_payload()
    problems += _check_dsh_contributions()
    problems += _check_port_files_env()
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
    lines.append("phase13_panel_decl_selfcheck: PASS（声明校验、投影、插件与端口参数）")
    return 0, lines
    lines.append("phase13_panel_decl_selfcheck: PASS（声明形状 + 那份参数 + 框架自己的 patch "
                 "+ 路由常量两处相等 + ⭐ 端口文件路径（绝对/不泄漏）"
                 "声明契约验证失败）")
    return 0, lines


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if shutil.which("dsh") is None:
        print("SKIP phase13_panel_decl_selfcheck: 未找到 dsh 可执行文件（外部依赖）")
        return 0
    code, lines = check()
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
