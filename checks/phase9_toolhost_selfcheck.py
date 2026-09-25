"""Phase 9 判据：ToolHost 契约——工具**定义**与宿主**传输**分成两个 seam。

工具定义（``define_tool`` / ``ToolRegistry``）与"定义怎么交到宿主、调用怎么
回来"（ToolHost）是两件事：dsh/MCP 只是宿主传输的一个 Provider，local 是出厂
Provider，核心不 import dsh，也不手抄工具名单。

本判据只测机制，不测任何真宿主：

  A 投影：``declare`` 之后 ``schemas()`` 只出白名单字段（``name``/
    ``description``/``parameters``）；``execute``/``output``/``render`` 绝不
    泄漏（本项目**选择拒收**白名单外字段，见 ADR `ToolHost契约`）。
  B 合规：``call`` 成功返回 canonical value + content，且不误报。
  C 归一化：未知工具 / 缺参 / 回执缺必填键 / 工具体意外异常 / 宿主句柄抛错 /
    宿主回执畸形（非映射、缺 is_error、错误支形状坏、成功支缺键）——
    全部结构化 ``ToolFailure {message, info}``，**不抛穿**。
  D 反例：schema 带白名单外字段（``execute``）必须被 ``declare`` 拒；
    缺字段 / 坏形状 / 交过来的是 ``ToolDefinition`` 本体同样拒。
  E teardown 幂等（连调不炸、不改变调用能力）。
  F L7 对偶：经 ``assemble`` 的合规路径全绿——真注册表的工具能从装配出的宿主
    声明、投影、调用（机制建好必须装配接上）。

用法：python checks/phase9_toolhost_selfcheck.py
退出码：0 全绿；1 有失败。

## Known Limitations and Deferred Work

- 不测真 dsh/MCP ``ctx.tools`` 注册（本仓不 import dsh，dsh 侧 Provider 未实现；
  模块 Known Limitations 与 ADR 已绑触发）。这里验证形状与不变式在库内成立。
- 不测跨进程/跨会话的工具面重建：宿主生命周期归部署期，n=0 不建。
- 不测传输层权限（写权归 Gate；框架不做可见性分级——原 ``visibility.py`` 已删除）：
  ToolHost 只搬运，测它新增权限检查反而是要求它越界。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mecha.assembly import assemble  # noqa: E402
from mecha.errors import MechaError  # noqa: E402
from mecha.gate import accept_all  # noqa: E402
from mecha.surface import Engine  # noqa: E402
from mecha.toolhost import LOCAL_HOST, LocalToolHost, ToolHost  # noqa: E402
from mecha.tools import MODEL_VISIBLE_FIELDS, ToolRegistry, define_tool  # noqa: E402

#: 合规工具名用判据自己的中性值（不复制任何项目的领域命令名）。
BAD_DECLARATION = "bad_tool_declaration"


class ToyEngine(Engine):
    def schema(self):
        return {"run_output_required": ["ok"], "keys": []}

    def run(self, spec, *, context, **opts):
        return {"ok": True}

    def summary(self):
        return {"ok": True}

    def estimate(self, spec):
        return {"est_sec": 0.1}


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _fail(out: list[str], msg: str) -> None:
    print(f"  ✗ {msg}")
    out.append(msg)


def _good_tool(**over):
    kw = dict(
        name="do_thing",
        description="按给定参数跑一次计算，并把结果回执交给调用方。",
        parameters={"key": {"type": "string"}, "value": {"type": "number"}},
        output_schema={"type": "object", "required": ["ok", "applied"]},
        execute=lambda key, value: {"ok": True, "applied": key},
    )
    kw.update(over)
    return define_tool(**kw)


def _failure_kind(r) -> str:
    """取结构化失败的 kind；形状不符时返回空串（调用方据此报红）。"""
    if not isinstance(r, dict) or r.get("is_error") is not True:
        return ""
    err = r.get("error")
    if not isinstance(err, dict) or set(err) != {"message", "info"}:
        return ""
    info = err.get("info")
    if not isinstance(info, dict) or "kind" not in info:
        return ""
    return str(info["kind"])


def _reg(*tools) -> ToolRegistry:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    return reg


def _host_with_bad_result(result, *, label: str) -> LocalToolHost:
    """宿主句柄坏掉的替身：execute 直接返回畸形结果（不改库内代码）。"""

    class BadRegistry:
        def execute(self, name, args):
            return result

        def schemas(self):
            return []

    return LocalToolHost(BadRegistry(), name=label)


class _FakeHost:
    """最小合规宿主替身（证明 ToolHost 是 Protocol，不是 LocalToolHost 私产）。"""

    name = "fake"

    def __init__(self) -> None:
        self.declared: list = []
        self.torn = 0

    def declare(self, schemas) -> None:
        self.declared = list(schemas)

    def call(self, name, args=None):
        return {"is_error": False, "value": {"ok": True}, "content": []}

    def schemas(self):
        return [{"name": "x", "description": "d", "parameters": {}}]

    def teardown(self) -> None:
        self.torn += 1


def check_protocol_and_projection(out: list[str]) -> None:
    """A：宿主协议 + 白名单投影（execute/output/render 不泄漏）。"""
    if isinstance(LocalToolHost(_reg()), ToolHost) and isinstance(_FakeHost(), ToolHost):
        _ok("ToolHost 是 Protocol：LocalToolHost 与第三方宿主同为合规实现")
    else:
        _fail(out, "ToolHost 协议判定不符（LocalToolHost 或替身未被认作实现）")
    reg = _reg(_good_tool())
    host = LocalToolHost(reg)
    host.declare(host.schemas())
    schemas = host.schemas()
    if len(schemas) == 1 and set(schemas[0]) == set(MODEL_VISIBLE_FIELDS):
        _ok(f"schemas() 只出白名单字段 {sorted(MODEL_VISIBLE_FIELDS)}")
    else:
        _fail(out, f"投影字段不符：{schemas}")
    leaked = [f for f in ("execute", "output", "render")
              if any(f in s for s in schemas)]
    if not leaked:
        _ok("execute/output/render 未进模型可见投影（消 D-25）")
    else:
        _fail(out, f"投影泄漏了实现字段：{leaked}")
    # 名字清单不手抄：注册表是唯一来源，dispose 即消失
    dispose = reg.register(_good_tool(name="set_value"))
    if len(host.schemas()) == 2:
        dispose()
        if len(host.schemas()) == 1:
            _ok("投影随注册/dispose 实时变化（无手抄名单可漂）")
        else:
            _fail(out, "dispose 后宿主投影仍含旧项")
    else:
        _fail(out, "第二次注册未进宿主投影")
    # declare 幂等：重复声明不改变投影
    host.declare(host.schemas())
    host.declare(host.schemas())
    if len(host.schemas()) == 1:
        _ok("重复 declare 幂等（投影不增不减）")
    else:
        _fail(out, "重复 declare 改变了投影")


def check_good_call(out: list[str]) -> None:
    """B：合规调用成功 + canonical value + content（不误报）。"""
    reg = _reg(_good_tool())
    result = LocalToolHost(reg).call("do_thing", {"key": "alpha", "value": 1})
    if result.get("is_error") is False and result.get("value") == {"ok": True,
                                                                  "applied": "alpha"}:
        _ok("合规调用成功：canonical value 原样回（不误报）")
    else:
        _fail(out, f"合规调用回执不符：{result}")
    if result.get("content"):
        _ok("成功回执带渲染 content")
    else:
        _fail(out, f"成功回执缺 content：{result}")
    # 宿主替身（非 local）走同一契约
    fake = _FakeHost()
    fake.declare(fake.schemas())
    if _failure_kind(fake.call("anything")) == "":
        _ok("第三方宿主替身按同一契约返回成功（协议不是 LocalToolHost 私产）")
    else:
        _fail(out, "第三方宿主替身的成功回执未按契约形状")


def check_normalized_failures(out: list[str]) -> None:
    """C：全部失败归一化成结构化 ToolFailure，不抛穿。"""
    host = LocalToolHost(_reg(_good_tool()))
    cases: list[tuple[str, str, dict]] = [
        ("未知工具", "unknown_tool", host.call("no_such_tool", {})),
        ("缺必填参数", "missing_arguments", host.call("do_thing", {"key": "a"})),
    ]
    bad_receipt = LocalToolHost(_reg(_good_tool(
        execute=lambda key, value: {"ok": True})))          # 缺 applied
    cases.append(("回执缺必填键", "bad_tool_output",
                  bad_receipt.call("do_thing", {"key": "a", "value": 1})))
    boom = LocalToolHost(_reg(_good_tool(
        execute=lambda key, value: (_ for _ in ()).throw(RuntimeError("boom")))))
    cases.append(("工具体意外异常", "internal_error",
                  boom.call("do_thing", {"key": "a", "value": 1})))
    for label, want, result in cases:
        got = _failure_kind(result)
        if got == want:
            _ok(f"{label} → 结构化 ToolFailure（kind={want}），不抛穿")
        else:
            _fail(out, f"{label} 归一化不符：期望 kind={want}，实得 {got!r}（{result}）")
    # 教学性：失败必须带 message 与 info.hint 字段（L4 错误是模型的 UI）
    unknown = host.call("no_such_tool", {})
    info = unknown["error"]["info"]
    if unknown["error"]["message"] and set(info) == {"kind", "hint", "suggest"}:
        _ok("失败携带 message + info{kind,hint,suggest}（可教学形状）")
    else:
        _fail(out, f"失败形状不可教学：{unknown}")


def check_exception_never_escapes(out: list[str]) -> None:
    """C'：宿主句柄自身抛错 / 返回畸形结果 —— 都不许抛穿。"""

    class ExplodingRegistry:
        def execute(self, name, args):
            raise RuntimeError("host handle exploded")

        def schemas(self):
            return []

    try:
        result = LocalToolHost(ExplodingRegistry(), name=LOCAL_HOST).call("do_thing", {})
        kind = _failure_kind(result)
        if kind == "toolhost_error":
            _ok("宿主句柄抛错 → toolhost_error 结构化失败（异常不逃过边界）")
        else:
            _fail(out, f"宿主句柄抛错未归一化：kind={kind!r}（{result}）")
    except Exception as e:  # noqa: BLE001 - 抛穿即形状违规
        _fail(out, f"宿主句柄抛错竟抛穿：{type(e).__name__}: {e}")

    malformed = [
        ("非映射回执", "裸字符串"),
        ("缺 is_error", {"value": {}, "content": []}),
        ("is_error 非布尔", {"is_error": "yes", "value": {}, "content": []}),
        ("错误支形状坏", {"is_error": True, "error": {"message": "m"}}),
        ("成功支缺 value/content", {"is_error": False, "value": {}}),
    ]
    for label, probe in malformed:
        try:
            kind = _failure_kind(_host_with_bad_result(probe, label="probe").call(
                "do_thing", {}))
            if kind == "bad_host_result":
                _ok(f"宿主回执畸形（{label}）→ bad_host_result（不当成值透传）")
            else:
                _fail(out, f"畸形回执（{label}）未归一化：kind={kind!r}")
        except Exception as e:  # noqa: BLE001 - 抛穿即形状违规
            _fail(out, f"畸形回执（{label}）竟抛穿：{type(e).__name__}: {e}")


def check_declaration_rejects_drift(out: list[str]) -> None:
    """D：反例——白名单外字段必须被 declare 拒（本项目选择拒收，非静默过滤）。"""
    host = LocalToolHost(_reg(_good_tool()))
    good = host.schemas()
    if len(good) == 1:
        _ok("基准确认：合规投影 1 项（反例以此为底）")
    else:
        _fail(out, f"基准投影不符：{good}")
        return
    drifted = dict(good[0])
    drifted["execute"] = lambda **kw: {"ok": True, "applied": "x"}
    bads: list[tuple[str, object]] = [
        ("schema 混入 execute", [drifted]),
        ("schema 混入 output", [{**good[0], "output": {"schema": {}}}]),
        ("schema 缺 name", [{k: v for k, v in good[0].items() if k != "name"}]),
        ("schema 缺 parameters",
         [{k: v for k, v in good[0].items() if k != "parameters"}]),
        ("parameters 不是映射", [{**good[0], "parameters": "type: object"}]),
        ("description 为空串", [{**good[0], "description": "   "}]),
        ("交过来的是 ToolDefinition 本体", [_good_tool()]),
        ("整体不是列表", good[0]),
        ("整体是字符串", "do_thing"),
    ]
    for label, bad in bads:
        try:
            host.declare(bad)
            _fail(out, f"坏声明（{label}）竟被接受")
        except MechaError as e:
            if e.kind == BAD_DECLARATION:
                _ok(f"坏声明（{label}）fail loud 且 kind 正确")
            else:
                _fail(out, f"坏声明（{label}）kind 不符：{e.kind}")
        except Exception as e:  # noqa: BLE001 - 非 MechaError 即形状违规
            _fail(out, f"坏声明（{label}）抛了非教学错误：{type(e).__name__}: {e}")
    # 不许误报：合规投影仍被接受（拒收不是疯守卫）
    try:
        host.declare(good)
        _ok("合规投影仍被 declare 接受（不误报）")
    except MechaError as e:
        _fail(out, f"合规投影被拒：{e}")


def check_teardown_idempotent(out: list[str]) -> None:
    """E：teardown 幂等，且不改变调用能力（可逆释放；无外部注册可撤）。"""
    reg = _reg(_good_tool())
    host = LocalToolHost(reg)
    host.declare(host.schemas())
    host.teardown()
    host.teardown()
    host.teardown()
    result = host.call("do_thing", {"key": "a", "value": 1})
    if _failure_kind(result) == "" and host.schemas():
        _ok("teardown 连调幂等：不抛错、投影与调用能力不受损")
    else:
        _fail(out, f"teardown 后语义改变：schemas={host.schemas()} result={result}")
    fake = _FakeHost()
    fake.teardown()
    fake.teardown()
    if fake.torn == 2:
        _ok("协议要求 teardown 幂等：第三方宿主替身连调不炸（契约不偏袒 local）")
    else:
        _fail(out, f"替身 teardown 调用次数不符：{fake.torn}")


def check_assembled_path(out: list[str]) -> None:
    """F：L7 对偶——经装配点的合规路径全绿（机制建好必须装配接上）。"""
    td = tempfile.mkdtemp(prefix="phase9-sw-")
    with assemble(root=Path(td), data_dir_name="p9data", engine=ToyEngine(),
                  summarizer=lambda evs: {}, validate=accept_all,
                  project="toyhost") as sw:
        host = sw.toolhost
        if isinstance(host, ToolHost) and host.registry is sw.tools:
            _ok("assemble 装配出的 Software.toolhost 托管的就是 Software.tools")
        else:
            _fail(out, "装配点产出的 toolhost 未接上同一份注册表")
            return
        sw.tools.register(_good_tool())
        host.declare(host.schemas())
        schemas = host.schemas()
        if len(schemas) == 1 and set(schemas[0]) == set(MODEL_VISIBLE_FIELDS):
            _ok("装配路径：注册 → 声明 → 投影贯通（真注册表非替身）")
        else:
            _fail(out, f"装配路径投影不符：{schemas}")
        result = host.call("do_thing", {"key": "beta", "value": 2})
        if result.get("is_error") is False and result["value"]["applied"] == "beta":
            _ok("装配路径：宿主调用走通（回执 canonical）")
        else:
            _fail(out, f"装配路径调用不符：{result}")
        if _failure_kind(host.call("no_such_tool", {})) == "unknown_tool":
            _ok("装配路径：未知工具同样结构化失败（守卫不载重）")
        else:
            _fail(out, "装配路径未知工具未归一化")
        before = len(host.schemas())
        sw.tools.register(_good_tool(name="set_value"))
        if len(host.schemas()) == before + 1:
            _ok("装配路径：投影随注册实时增长（无手抄名单）")
        else:
            _fail(out, "装配路径投影未随注册变化")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out: list[str] = []
    print("Phase 9 工具宿主契约判据：")
    check_protocol_and_projection(out)
    check_good_call(out)
    check_normalized_failures(out)
    check_exception_never_escapes(out)
    check_declaration_rejects_drift(out)
    check_teardown_idempotent(out)
    check_assembled_path(out)
    if out:
        print(f"phase9_toolhost_selfcheck: FAIL（{len(out)} 条）")
        return 1
    print("phase9_toolhost_selfcheck: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
