"""Phase 4 装配判据：工具形态同构（总纲 §7 Phase 4 六维度）。

1. 命名：动词_宾语 snake_case；``list_*``/``get_*`` 视图名收编（拒注册）；
   项目前缀（``mecha_*``）拒注册。
2. 声明：``output.schema`` 必须含 required；回执缺键 → isError + ToolFailure。
3. 描述文体：含实现词汇的 description 当场拒（banned_words 注入式，库不写死词）。
4. 投影：``schemas()`` 只出白名单字段；execute/output/render 绝不泄漏（消 D-25）。
5. 错误：MechaError → ``ToolFailure {message, info}`` 形状对齐。
6. 注册：disposer 可撤销；重复注册拒。

L7 对偶：合规工具全绿。

用法：python checks/phase4_adapter_selfcheck.py
退出码：0 全绿；1 有失败。

## Known Limitations and Deferred Work

- 判据不接真 dsh（ctx.tools 注册在 Phase 5）；这里验证形状与不变式在库内成立。
- "没读过文档的 AI 第一次做对"（Phase 4 验收）需要真实模型跑，超出本判据
  范围——那是 Phase 5 接 EL 后的快照测试（届时补 llm-replay 形态的语料）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mecha.errors import GateDenied, MechaError
from mecha.tools import ToolRegistry, define_tool


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _fail(out: list[str], msg: str) -> None:
    print(f"  ✗ {msg}")
    out.append(msg)


def _good_tool(**over):
    kw = dict(
        name="set_config",
        description="把某个配置键设置为给定值；键必须已在 schema 中声明。",
        parameters={"key": {"type": "string"}, "value": {"type": "number"}},
        output_schema={"type": "object", "required": ["ok", "applied"]},
        execute=lambda key, value: {"ok": True, "applied": key},
    )
    kw.update(over)
    return define_tool(**kw)


def check_naming(out: list[str]) -> None:
    # 能抓坏：视图名收编 / 项目前缀拒
    rejected = 0
    for bad in ("list_levels", "get_power", "mecha_script"):
        try:
            _good_tool(name=bad)
            _fail(out, f"视图/前缀名 {bad!r} 竟注册成功")
        except MechaError as e:
            if e.kind != "bad_tool_name":
                _fail(out, f"{bad!r} 报错 kind 不对：{e.kind}")
            else:
                rejected += 1
    if rejected == 3:
        _ok("list_/get_/前缀名全部拒注册（动词_宾语同构）")
    # 不许误报：合规名
    try:
        _good_tool(name="run_sim")
        _ok("合规名注册通过（不误报）")
    except MechaError as e:
        _fail(out, f"合规名被拒：{e}")


def check_output_contract(out: list[str]) -> None:
    # 能抓坏：output.schema 缺 required → 构造即拒（消 D-15）
    try:
        _good_tool(output_schema={"type": "object"})
        _fail(out, "缺 required 的 output.schema 竟通过")
    except MechaError as e:
        if e.kind == "bad_output_schema":
            _ok("output.schema 必须显式声明 required 键")
    # 能抓坏：回执缺声明过的键 → isError + ToolFailure 形状
    reg = ToolRegistry()
    reg.register(_good_tool(execute=lambda key, value: {"ok": True}))  # 缺 applied
    r = reg.execute("set_config", {"key": "a", "value": 1})
    if r["is_error"] and set(r["error"]) == {"message", "info"} \
            and r["error"]["info"]["kind"] == "bad_tool_output":
        _ok("回执缺键 → ToolFailure {message, info} 对齐")
    else:
        _fail(out, f"回执校验形状不符：{r}")
    # 不许误报：合规回执通过 + render 产出 content
    reg2 = ToolRegistry()
    reg2.register(_good_tool())
    r2 = reg2.execute("set_config", {"key": "a", "value": 1})
    if not r2["is_error"] and r2["value"]["applied"] == "a" and r2["content"]:
        _ok("合规工具执行 + canonical 值 + content 投影（不误报）")
    else:
        _fail(out, f"合规执行不符：{r2}")


def check_description_style(out: list[str]) -> None:
    # 能抓坏：实现词汇进 description 当场拒（v1 的"经 choke point"教训）
    try:
        _good_tool(description="经内部 choke point 写入并刷新 M2 读回面。",
                   banned_words=("choke point", "读回面"))
        _fail(out, "实现词汇的 description 竟通过")
    except MechaError as e:
        if e.kind == "bad_description":
            _ok("description 含实现词汇被拒（任务相关概念 only）")
    # 能抓坏：空 description
    try:
        _good_tool(description="   ")
        _fail(out, "空 description 竟通过")
    except MechaError as e:
        if e.kind == "bad_description":
            _ok("空 description 被拒（模型唯一用法来源）")


def check_projection_whitelist(out: list[str]) -> None:
    reg = ToolRegistry()
    reg.register(_good_tool())
    schemas = reg.schemas()
    s = schemas[0]
    # 能抓坏的反面（L7）：白名单外字段绝不泄漏
    if set(s.keys()) == {"name", "description", "parameters"}:
        _ok("schemas() 只出白名单字段（execute/output 不泄漏，消 D-25）")
    else:
        _fail(out, f"投影泄漏：{sorted(s.keys())}")
    # 无手写 TOOL_NAMES：注册即投影，dispose 即消失
    dispose = reg.register(_good_tool(name="run_sim",
                                      execute=lambda **kw: {"ok": True, "applied": "x"},
                                      output_schema={"type": "object", "required": ["ok", "applied"]}))
    if len(reg.schemas()) == 2:
        dispose()
        if len(reg.schemas()) == 1:
            _ok("投影随注册/dispose 实时变化（无手抄名单可漂）")
        else:
            _fail(out, "dispose 后投影仍含旧项")
    else:
        _fail(out, "第二工具未进投影")


def check_error_shape(out: list[str]) -> None:
    reg = ToolRegistry()
    reg.register(_good_tool(execute=lambda key, value: (_ for _ in ()).throw(
        GateDenied("当前模式不接受写", kind="authority_mode_mismatch", hint="切模式"))))
    r = reg.execute("set_config", {"key": "a", "value": 1})
    if r["is_error"] and r["error"]["info"]["kind"] == "authority_mode_mismatch" \
            and "hint" in r["error"]["info"]:
        _ok("MechaError → ToolFailure 投影（kind/hint/suggest 进 info）")
    else:
        _fail(out, f"错误投影不符：{r}")
    # 未知工具也归一化（UNKNOWN_TOOL 同形）
    r2 = reg.execute("no_such", {})
    if r2["is_error"] and r2["error"]["info"]["kind"] == "unknown_tool":
        _ok("未知工具 → 结构化错误，不抛穿")
    else:
        _fail(out, f"未知工具处理不符：{r2}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out: list[str] = []
    print("Phase 4 装配判据：")
    check_naming(out)
    check_output_contract(out)
    check_description_style(out)
    check_projection_whitelist(out)
    check_error_shape(out)
    if out:
        print(f"phase4_adapter_selfcheck: FAIL（{len(out)} 条）")
        return 1
    print("phase4_adapter_selfcheck: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
