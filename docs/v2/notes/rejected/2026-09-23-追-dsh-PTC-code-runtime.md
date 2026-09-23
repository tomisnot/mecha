# 否决：用 dsh 的 PTC / code-runtime 替换自研 codeact

> **状态**：rejected — PTC 20 天内三次破坏性改名，Python runtime 在 experimental/
> **作者**：mecha v2
> **日期**：2026-09-23

## Problem

v1 有自研 `codeact.py`。dsh 有 code-runtime seam（`ctx.codeRuntime`）+ worker-thread/python provider。要不要直接换过去、删掉自研？

## Decision

**不追 PTC/code-runtime**（总纲 §4.4 只能观察档：20 天内三次破坏性改名，Python runtime 在 `experimental/`）。**保留自研 codeact**，但补"逐子调用记日志"（§7 Phase 3 表末行）。dsh 的实现细节**不许当契约**（§4.3）。

## Consequences

换来：不被 dsh 特性层的日更破坏性改名打到。付出：继续维护自研 codeact 的正确性。

**重新考虑条件**：code-runtime 脱离 experimental、连续两个 release 无破坏性改名，且其 Definition 稳定到可当地基档时。

## Alternatives considered

**否决：现在就换。** 会把 churn 越过 Provider 层灌进稳定脊椎（违反硬纪律 6）。
**否决：删自研不补。** 丢掉 CodeAct 能力，Phase 4"像手脚"的判据受损。
