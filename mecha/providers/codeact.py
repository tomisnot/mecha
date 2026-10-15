"""CodeAct：自研保留 + 逐子调用记日志（总纲 §7 Phase 3 末行）。

**不追 PTC/code-runtime**（20 天三次破坏性改名，Python runtime 在 experimental/
——ADR 追-dsh-PTC-code-runtime）。保留自研，补上 v1 缺的"逐子调用记日志"。

形态：一个受限动作解释器。动作是**结构化 dict**（不是 eval 任意代码——
库不执行模型写的任意代码，那是宿主 code-runtime 的权力，我们只编排自家工具）。
每个子调用产出一条 ``SubCallLog``，落 History（可归因，Phase 1 洞 2 的配套：
  计算动作由项目自己的 dispatcher 提供，框架不预设任何通用 Engine 语义。

## Known Limitations and Deferred Work

- **动作集（与代码逐项对齐，2026-09-26 改正）**：**内置四个** ``run`` / ``summary`` /
  ``estimate`` / ``schema`` **＋ ``Surface`` 已注册的 ``query`` 项**（经 ``Surface.query``）。
  ⚠ **不触达**注册的 **command / tool** 面——旧文案写"Surface 已注册项"读起来像"什么都行"，
  实际只到 query 那一层。需要更强 CodeAct 的项目自带动作 Provider（推迟到 n=2）。
  ⚠ 若将来要让它触达 command/tool 面，**必须同时回答"过不过写权门"**（见下一条），
  不能只把 `_dispatch` 加一支了事。
- ⚠ **动作不写域状态，因此不在写权闸的管辖里**（这是刻意的，不是漏闸）：``run`` 走
  ``Surface.run``，而 ``Surface`` **不碰 Gate/History**（计算/演化面）。域状态的写发生在
  **宿主自己的 handler 里**（``gate.set`` ⇐ 那一步过门）。⇒ 把 ``Surface.run`` 塞进写权门
  会要求"只读计算也要先开闸"，那与既有的**读写分离**（只读命令在 LOCKED 下照样可调）冲突。
  ⚠ **但边界要留痕**：**若某项目的引擎 ``run`` 在 Gate 之外改自己的存储**（"域状态外置"），
  那么 CodeAct 这条路径**不受写权闸管辖**——正解是**让宿主的写经 Gate**，不是给 CodeAct
  加闸（`Surface.run` 是公开面，在 CodeAct 这一条路上设卡挡不住旁边那十扇门）。
- **账逐条即时写**（2026-09-26 起）：每个子调用**一有结果就 append 一条**（不再"跑完整段
  再一次性落盘"），且一段动作共用一个 ``call_id`` ⇒ **中途崩溃/异常时，已跑过的步都在史里**、
  这一整段仍可用 ``call_id`` 串起来。⚠ 代价：**史事件数变多**（一段 N 步 = N 条）。
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
                 actor_provider: Callable[[], str],
                 dispatcher: Callable[[str, Mapping[str, object], ExecutionContext], object] | None = None) -> None:
        self._surface = surface
        self._history = history
        self._actor = actor_provider
        self._dispatcher = dispatcher

    def run(self, actions: Sequence[Action], *, context: ExecutionContext | None = None,
            call_id: str = "") -> CodeActResult:
        ctx = context or ExecutionContext()
        result = CodeActResult()
        for i, a in enumerate(actions):
            if ctx.cancelled:
                self._log_step(i, a.op, False, "cancelled", call_id)
                result.steps.append(SubCallLog(i, a.op, False, "cancelled"))
                result.stopped_at = i
                break
            try:
                out = self._dispatch(a, ctx)
            except MechaError as e:
                detail = e.teaching_text()
                self._log_step(i, a.op, False, detail, call_id)
                result.steps.append(SubCallLog(i, a.op, False, detail))
                result.stopped_at = i
                break
            except BaseException as e:
                # ⚠ 与 A1 同一判据：**子调用已进入 ⇒ 失败也要留痕**，然后**原样放走**
                # （不把 bug 伪装成域失败；只是先记一条痕）。
                detail = f"{type(e).__name__}: {e}"
                self._log_step(i, a.op, False, detail, call_id)
                result.steps.append(SubCallLog(i, a.op, False, detail))
                result.stopped_at = i
                raise
            self._log_step(i, a.op, True, "", call_id)
            result.steps.append(SubCallLog(i, a.op, True))
            result.outputs.append(out)
        return result

    def _log_step(self, index: int, op: str, ok: bool, detail: str,
                  call_id: str) -> None:
        """**一步一条、即时落史**（不再"整段跑完才写"）：中途崩溃时已跑过的步仍在史里。

        一段动作共用一个 ``call_id`` ⇒ 逐条写也仍然"整段可识别"（不必新增批 id）。
        """
        self._history.append_history(
            f"codeact.{index}", "", {"op": op, "ok": ok, "detail": detail},
            None, self._actor(), "codeact step", call_id,
        )

    def _dispatch(self, a: Action, ctx: ExecutionContext) -> object:
        if self._dispatcher is not None:
            try:
                return self._dispatcher(a.op, a.args, ctx)
            except UnknownOp:
                pass
        # 其余走注册查询
        try:
            return self._surface.query(a.op, **dict(a.args))
        except UnknownOp:
            raise UnknownOp(
                f"动作 {a.op!r} 不在能力面",
                kind="unknown_action",
                hint="查询：注册项见 Surface.entries('query')；计算动作由项目自己的 dispatcher 提供",
            ) from None
