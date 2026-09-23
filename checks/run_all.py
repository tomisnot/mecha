"""全门禁 runner：一条命令跑完所有 selfcheck + 生成物新鲜度。

用法：python checks/run_all.py
退出码：0 全绿；1 任一失败（逐项列出）。

## Known Limitations and Deferred Work

- 串行跑（判据总耗时秒级，并行是过早优化）。
- 不跑 Phase 5 的 EL 侧判据（EL 项目未接入，n=1）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CHECKS = [
    "checks/adr_format_selfcheck.py",
    "checks/known_limitations_selfcheck.py",
    "checks/phase1_assembly_selfcheck.py",
    "checks/phase2_surface_selfcheck.py",
    "checks/phase3_provider_selfcheck.py",
    "checks/phase4_adapter_selfcheck.py",
    "checks/capability_map_selfcheck.py",
    "checks/assembly_endtoend_selfcheck.py",
    "checks/vocabulary_selfcheck.py",
    "checks/phase5_el_selfcheck.py",
    "checks/phase5_hub_selfcheck.py",
    "checks/phase5_bridge_selfcheck.py",
    "scripts/gen_capability_map.py --check",
]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    failed: list[str] = []
    for spec in CHECKS:
        parts = spec.split()
        r = subprocess.run([sys.executable, *parts], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        tail = (r.stdout or "").strip().splitlines()
        last = tail[-1] if tail else (r.stderr or "").strip().splitlines()[-1:] or ["无输出"]
        status = "PASS" if r.returncode == 0 else "FAIL"
        print(f"[{status}] {spec}  —— {last if isinstance(last, str) else last[0]}")
        if r.returncode != 0:
            failed.append(spec)
    if failed:
        print(f"run_all: FAIL（{len(failed)} 项）：{failed}")
        return 1
    print("run_all: 全部门禁 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
