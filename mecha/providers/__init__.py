"""Provider 层（总纲 §5.1/§5.2⑤，Phase 3）：dsh 的 churn 全关在这里。

本包三个 seam：Log / Jobs / Approval。每个 = Definition（我们稳定）
+ local Provider（**出厂默认**，§5.3 nothing ships enabled 用在自己身上）
+ dsh Provider（显式 opt-in；构造时注入宿主对象，本包不 import dsh）。
（``artifacts`` 的 Definition 与 local 实现住**核心** ``mecha/artifacts.py``，
不在本包；``codeact``/``skills`` 是编排名录类的扩展，也在本包。）

## Known Limitations and Deferred Work

- dsh Provider 以"注入宿主句柄"的形态存在（duck-typing），没有真 dsh 进程可测；
  其接线判据在 Phase 5 的 EL 消费配置里补。
- 三家（History/Artifacts/Approval）各自要不要第三个 provider 推迟到 n=2
  （ADR 边界写机制不写分类）。
- **Spill 已于 2026-09-25 收口轮删除**：它零消费者、且**连发起方都没有**
  （框架内没有一处会问阈值再投递；两下游零代码）——"该不该截断落盘"是调用方的
  策略，不是 seam 的职责。其"不透明定位符"承诺由 ``artifacts`` 承接。
"""

from __future__ import annotations

from .log_local import JsonlLogSink
from .jobs import Job, JobRegistry, JobState
from .approval import ApprovalDecision, ApprovalProvider, LocalApproval
from .skills import SkillCatalog, SkillEntry
from .codeact import Action, CodeAct, CodeActResult, SubCallLog

__all__ = [
    "JsonlLogSink",
    "Job", "JobRegistry", "JobState",
    "ApprovalDecision", "ApprovalProvider", "LocalApproval",
    "SkillCatalog", "SkillEntry",
    "Action", "CodeAct", "CodeActResult", "SubCallLog",
]
