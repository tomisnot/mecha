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
    # supervisor / 站点声明 / 启动入口 / 两阶段页面的判据（**全用 mock 子进程**：
    # 不依赖任何真实软件，也不需要 EL 仓）。见 checks/launch_selfcheck.py 的 docstring。
    "checks/launch_selfcheck.py",
    # 聚合端点（2a：工具表 / 转发 / 失败语义）。⚠ 也是**零外部依赖**：全用假后端，
    # 不起真进程（真跨进程端到端留给 2b）。
    "checks/aggregate_selfcheck.py",
    # dsh 集成资产（共享资产）的共享自测。⚠ **硬依赖 node**：缺 node 即红（不软跳过）。
    # ⚠ 2026-10-01 起**这里成了资产回归的唯一发现点**：资产改为包依赖（junction）后，
    #   接入项目不再持有副本、也不再跑这份共享单测 ⇒ 本判据不跑或跑假，三家都发现不了。
    #   （此前是三个消费者各跑一遍；减负的代价如实记在 docs/v2/清点-公共部分与上提候选.md §3.6.9。）
    "checks/dsh_panel_selfcheck.py",
    "checks/phase5_el_selfcheck.py",
    "checks/phase5_hub_selfcheck.py",
    "checks/phase5_bridge_selfcheck.py",
    "scripts/gen_capability_map.py --check",
]


#: **需要 EL 仓（示例项目）才能跑的判据**：缺它 ⇒ **跳过**（不是失败，但要**响亮列名**）。
#: ⚠ 为什么归这里判：这 3 条**整条**都依赖 EL（`sys.path.insert(EL_ROOT)` 后 import/运行
#: EL 的代码）⇒ 在 run_all 这一层跳过 = 一处改动覆盖三条，且**不假装它们跑过**。
#: ⚠ `capability_map_selfcheck` **不在**这里：它只有 3 处 EL 依赖，**主体与 L1/L2/L3/L3+
#: 守卫必须照跑** ⇒ 它自己在脚本内做**部分跳过**（整条跳过会让守卫在开源后失效）。
NEEDS_EL = frozenset({
    "checks/phase5_el_selfcheck.py",
    "checks/phase5_hub_selfcheck.py",
    "checks/phase5_bridge_selfcheck.py",
})


def main() -> int:
    import os                       # 只在 main 里用（避免动文件头）
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    el_ready = bool(os.environ.get("MECHA_EL_ROOT"))
    #: 严格模式：**跳过即失败**（给我们自己的 CI —— 有 EL 的机器不该因"跳过"而永远不验那 3 条）。
    #: 严格模式：**跳过即失败**（覆盖全部外部依赖：EL 仓 + tsc）。
    #: ⚠ 名字从 --require-el 换成 --require-external（两个外部依赖，旧名不够用；
    #: 按本仓先例**不留别名**：--open-gate 当年也是直接换掉的）。
    strict = (os.environ.get("MECHA_REQUIRE_EXTERNAL") == "1"
              or "--require-external" in sys.argv)
    failed: list[str] = []
    skipped: list[str] = []
    for spec in CHECKS:
        if spec in NEEDS_EL and not el_ready:
            note = "SKIP: 缺 EL 仓 ⇒ 跳过（设 MECHA_EL_ROOT 可启用）"
            if strict:
                print(f"[SKIP→FAIL] {spec}  —— {note}；**严格模式**"
                      f"（--require-external/MECHA_REQUIRE_EXTERNAL=1）")
                failed.append(spec)
            else:
                print(f"[SKIP] {spec}  —— {note}")
                skipped.append(spec)
            continue
        parts = spec.split()
        r = subprocess.run([sys.executable, *parts], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        tail = (r.stdout or "").strip().splitlines()
        last = tail[-1] if tail else (r.stderr or "").strip().splitlines()[-1:] or ["无输出"]
        last_s = last if isinstance(last, str) else last[0]
        # ⚠ **SKIP 由被调判据自己声明**（末行以 SKIP 开头）——那是"外部依赖缺失"，
        # 与"跑了但失败"是两件事：后者必须仍然是 FAIL（不许拿缺依赖掩盖真失败）。
        if r.returncode == 0 and last_s.startswith("SKIP"):
            if strict:
                print(f"[SKIP→FAIL] {spec}  —— {last_s}；**严格模式**"
                      f"（--require-external/MECHA_REQUIRE_EXTERNAL=1）")
                failed.append(spec)
            else:
                print(f"[SKIP] {spec}  —— {last_s}")
                skipped.append(spec)
            continue
        status = "PASS" if r.returncode == 0 else "FAIL"
        print(f"[{status}] {spec}  —— {last_s}")
        if r.returncode != 0:
            failed.append(spec)
    # ⚠ **按名列出跳过的判据**（只给数字等于把跳过藏起来 —— 本仓纪律：假绿比红更坏）。
    if skipped:
        print("run_all: 跳过的判据（缺 EL 仓，未验）：" + "、".join(skipped))
    total = len(CHECKS)
    n_pass = total - len(failed) - len(skipped)
    if failed:
        print(f"run_all: FAIL（{len(failed)} 项）—— 共 {total} 项："
              f"{n_pass} PASS / {len(failed)} FAIL / {len(skipped)} SKIP")
        return 1
    print(f"run_all: 全部门禁 PASS —— 共 {total} 项："
          f"{n_pass} PASS / 0 FAIL / {len(skipped)} SKIP")
    return 0


if __name__ == "__main__":
    sys.exit(main())
