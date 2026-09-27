"""全门禁 runner：一条命令跑完所有 selfcheck + 生成物新鲜度。

用法：python checks/run_all.py
退出码：0 全绿；1 任一失败（逐项列出）。

## Known Limitations and Deferred Work

- 串行跑（判据总耗时秒级，并行是过早优化）。
- **两条硬外部依赖**（缺即红，都不假绿）：① `phase5_*` 需要 **EL 仓**在位
  （它们直接 import `mecha_v2`）；② `dsh_panel_selfcheck` 需要 **`node`**（跑
  `mecha/dsh-panel/` 这份共享资产的 TS 自测，Node ≥22.6 原生剥类型）**与一份可用的
  `tsc`**（借接入项目装的那份：`node --test` 只剥类型、**看不见只在类型位置的错**）。
  ⇒ **跑完 `run_all` 需要 EL 仓 + node + 一份可用的 `tsc`**。
- Phase 6 命令面判据用中性假宿主（命令名与状态键都是判据自己的中性值），
  不依赖任何真宿主的 adapter；真宿主的命令表对齐归各宿主自己的判据。
- Phase 5 的 EL 侧判据（el/hub/bridge）在列——EL 领域实现住本仓外的 EL 项目
  （`mecha_v2` 包，ADR 领域参考实现搬回所属项目）；缺 EL 根时对应判据自己报红
  （不假绿），耗时主要在 cache 建与真演化。
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
    "checks/l8_prose_count_selfcheck.py",
    "checks/personal_paths_selfcheck.py",
    "checks/phase6_command_surface_selfcheck.py",
    "checks/phase7_job_contract_selfcheck.py",
    "checks/phase8_artifact_contract_selfcheck.py",
    "checks/phase9_toolhost_selfcheck.py",
    "checks/phase10_monitor_contract_selfcheck.py",
    "checks/phase11_cockpit_selfcheck.py",
    "checks/phase12_mcp_projection_selfcheck.py",
    # dsh 面板参考实现（共享资产）的共享自测。⚠ **硬依赖 node**：缺 node 即红
    # （不软跳过）——理由见该脚本 docstring（R10：资产是三份副本的源头，
    # 框架不跑它的自测 ⇒ 三份一起错，而"逐字一致"判据还会强制那个错）。
    "checks/dsh_panel_selfcheck.py",
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
