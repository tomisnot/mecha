"""CodeAct：自研保留 + 逐子调用记日志（总纲 §7 Phase 3 末行）。

**不追 PTC/code-runtime**（20 天三次破坏性改名，Python runtime 在 experimental/
——ADR 追-dsh-PTC-code-runtime）。保留自研，补上 v1 缺的"逐子调用记日志"。

形态：一个受限动作解释器。动作是**结构化 dict**（不是 eval 任意代码——
库不执行模型写的任意代码，那是宿主 code-runtime 的权力，我们只编排自家工具）。
每个子调用产出一条 ``SubCallLog``，落 History（可归因，Phase 1 洞 2 的配套：
物理演化只经 run，这里 run 动作就是 Surface.run）。

## Known Limitations and Deferred Work

- 动作集 = Surface 已注册项 + 内置 run/summary/estimate；不解释表达式、
  不 import。需要更强 CodeAct 的项目自带动作 Provider（推迟到 n=2）。
- 日志先收集后落盘（run 结束一次性 append），不逐条同步写——中途崩溃丢
  整段；需要逐条持久时换 LogSink 直写，届时写 ADR。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from ..errors import MechaError, UnknownOp
from ..history import History
from ..surface import ExecutionContext, EntryKind, Surface


@dataclass(frozen=True)
class Action:
    """一步动作：调用一个已注册能力。"""

    op: str
    args: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class SubCallLog:
    """逐子调用记录（Phase 3 补的债）。"""

    index: int
    op: str
    ok: bool
    detail: str = ""


@dataclass
class CodeActResult:
    steps: list[SubCallLog] = field(default_factory=list)
    outputs: list[object] = field(default_factory=list)
    stopped_at: int = -1  # 首个失败步；-1 = 全成


class CodeAct:
    """动作序列编排器。run(actions) 逐子调用记日志并落 History。"""

    def __init__(self, surface: Surface, history: History,
                 actor_provider: Callable[[], str]) -> None:
        self._surface = surface
        self._history = history
        self._actor = actor_provider
        self._known = {"run", "summary", "estimate", "schema"}

    def run(self, actions: Sequence[Action], *, context: ExecutionContext | None = None,
            call_id: str = "") -> CodeActResult:
        ctx = context or ExecutionContext()
        result = CodeActResult()
        for i, a in enumerate(actions):
            if ctx.cancelled:
                result.steps.append(SubCallLog(i, a.op, False, "cancelled"))
                result.stopped_at = i
                break
            try:
                out = self._dispatch(a, ctx)
                result.steps.append(SubCallLog(i, a.op, True))
                result.outputs.append(out)
            except MechaError as e:
                result.steps.append(SubCallLog(i, a.op, False, e.teaching_text()))
                result.stopped_at = i
                break
        # 逐子调用记日志 → 落 History（可归因；actor 来自通道）
        for s in result.steps:
            self._history.append(
                f"codeact.{s.index}", {"op": s.op, "ok": s.ok, "detail": s.detail},
                self._actor(), "codeact step", call_id,
            )
        return result

    def _dispatch(self, a: Action, ctx: ExecutionContext) -> object:
        if a.op == "run":
            spec = a.args.get("spec")
            if not isinstance(spec, Mapping):
                raise MechaError("run 动作需要 args['spec'] 映射",
                                 kind="bad_action", hint="Action('run', {'spec': {...}})")
            return self._surface.run(spec, context=ctx)
        if a.op == "summary":
            return self._surface.summary()
        if a.op == "estimate":
            return self._surface.estimate(dict(a.args.get("spec", {})))
        if a.op == "schema":
            return self._surface.schema()
        # 其余走注册查询
        try:
            return self._surface.query(a.op, **dict(a.args))
        except UnknownOp:
            raise UnknownOp(
                f"动作 {a.op!r} 不在能力面",
                kind="unknown_action",
                hint=f"内置：{sorted(self._known)}；查询：注册项见 Surface.entries('query')",
            ) from None
