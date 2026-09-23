"""Provider 层（总纲 §5.1/§5.2⑤，Phase 3）：dsh 的 churn 全关在这里。

四个 seam：Log / Jobs / Spill / Approval。每个 = Definition（我们稳定）
+ local Provider（**出厂默认**，§5.3 nothing ships enabled 用在自己身上）
+ dsh Provider（显式 opt-in；构造时注入宿主对象，本包不 import dsh）。

## Known Limitations and Deferred Work

- dsh Provider 以"注入宿主句柄"的形态存在（duck-typing），没有真 dsh 进程可测；
  其接线判据在 Phase 5 的 EL 消费配置里补。
- 三家（History/Spill/Approval）各自要不要第三个 provider 推迟到 n=2
  （ADR 边界写机制不写分类）。
"""

from __future__ import annotations

from .log_local import JsonlLogSink
from .jobs import Job, JobRegistry, JobState
from .spill import LocalSpillStore, SpillStore
from .approval import ApprovalDecision, ApprovalProvider, LocalApproval
from .skills import SkillCatalog, SkillEntry
from .codeact import Action, CodeAct, CodeActResult, SubCallLog

__all__ = [
    "JsonlLogSink",
    "Job", "JobRegistry", "JobState",
    "LocalSpillStore", "SpillStore",
    "ApprovalDecision", "ApprovalProvider", "LocalApproval",
    "SkillCatalog", "SkillEntry",
    "Action", "CodeAct", "CodeActResult", "SubCallLog",
]
