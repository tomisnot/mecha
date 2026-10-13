"""按改动范围运行验证；默认只跑核心契约，额外环境和慢链路显式选择。

python checks/run_all.py                    # 核心
python checks/run_all.py --suite integration
python checks/run_all.py --suite assets     # Node + TypeScript
python checks/run_all.py --suite projects   # EL 接入，MECHA_EL_ROOT
python checks/run_all.py --suite release    # 发布前个人路径扫描
python checks/run_all.py --all               # 核心 + 集成 + 资产 + 项目
python checks/run_all.py --only authority --list

缺项目依赖明确 SKIP；--require-external 将选中项的 SKIP 视为失败。
文档格式、词汇和历史实现形状不属于运行门禁。
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent

SUITES = {
    "core": (
        "phase1_assembly_selfcheck", "phase2_surface_selfcheck",
        "phase3_provider_selfcheck", "phase4_adapter_selfcheck",
        "phase6_command_surface_selfcheck", "phase7_job_contract_selfcheck",
        "phase8_artifact_contract_selfcheck", "phase9_toolhost_selfcheck",
        "phase10_monitor_contract_selfcheck", "roles_selfcheck",
        "aggregate_selfcheck", "authority_read_selfcheck",
        "authority_shared_mode_selfcheck", "phase13_panel_decl_selfcheck",
        "portfile_selfcheck",
    ),
    "integration": (
        "assembly_endtoend_selfcheck", "phase11_cockpit_selfcheck",
        "phase12_mcp_projection_selfcheck", "launch_selfcheck",
        "control_client_selfcheck", "authority_aggregate_selfcheck",
        "aggregate_runtime_selfcheck", "endtoend_selfcheck",
        "dsh_argv_selfcheck", "aggregate_tools_hot_sync_selfcheck",
    ),
    "assets": ("dsh_panel_selfcheck",),
    "projects": (
        "phase5_el_selfcheck", "phase5_hub_selfcheck", "phase5_bridge_selfcheck",
    ),
    "release": ("personal_paths_selfcheck",),
}
SOFTWARE_SUITES = ("core", "integration", "assets", "projects")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--suite", action="append", choices=SUITES,
                        help="可重复；默认 core")
    parser.add_argument("--all", action="store_true", help="全部软件验证，不含发布扫描")
    parser.add_argument("--only", action="append", metavar="NAME",
                        help="按名称子串筛选，可重复；未指定套件时搜索全部套件")
    parser.add_argument("--list", action="store_true", help="只列选中项，不运行")
    parser.add_argument("--timeout", type=float, default=300,
                        help="单项超时秒数，默认 300")
    parser.add_argument("--require-external", action="store_true",
                        help="选中项的 SKIP 也记为失败")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout 必须大于 0")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    suites = (SOFTWARE_SUITES if args.all else args.suite
              or (tuple(SUITES) if args.only else ("core",)))
    selected = list(dict.fromkeys((suite, name) for suite in suites for name in SUITES[suite]))
    if args.only:
        for pattern in args.only:
            if not any(pattern in name for _, name in selected):
                parser.error(f"没有匹配的验证项：{pattern}")
        selected = [(suite, name) for suite, name in selected
                    if any(pattern in name for pattern in args.only)]
    if args.list:
        for suite, name in selected:
            print(f"{suite:11} checks/{name}.py")
        return 0

    strict = args.require_external or os.environ.get("MECHA_REQUIRE_EXTERNAL") == "1"
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    started = time.monotonic()
    for suite, name in selected:
        t0 = time.monotonic()
        output = ""
        el_root = os.environ.get("MECHA_EL_ROOT", "")
        if suite == "projects" and not (el_root and (Path(el_root) / "mecha_v2").is_dir()):
            status, output = "SKIP", "缺 EL 项目：请设置 MECHA_EL_ROOT"
        else:
            try:
                result = subprocess.run([sys.executable, "-X", "utf8", "-B",
                                         str(ROOT / "checks" / f"{name}.py")],
                                        cwd=ROOT, env=env, capture_output=True, text=True,
                                        encoding="utf-8", errors="replace", timeout=args.timeout)
                output = "\n".join(x.strip() for x in (result.stdout, result.stderr) if x.strip())
                lines = result.stdout.strip().splitlines()
                status = ("FAIL" if result.returncode else
                          "SKIP" if lines and lines[-1].startswith("SKIP") else "PASS")
            except subprocess.TimeoutExpired:
                status, output = "FAIL", f"超过单项预算 {args.timeout:g}s"
        counts[status] += 1
        shown = "SKIP→FAIL" if status == "SKIP" and strict else status
        last = output.splitlines()[-1] if output else "无输出"
        print(f"[{shown}] {name} ({time.monotonic() - t0:.1f}s) — {last}", flush=True)
        if status == "FAIL":
            print(output, flush=True)
    print(f"run_all: {counts['PASS']} PASS / {counts['FAIL']} FAIL / {counts['SKIP']} SKIP"
          f" — {time.monotonic() - started:.1f}s")
    return int(bool(counts["FAIL"] or strict and counts["SKIP"]))


if __name__ == "__main__":
    sys.exit(main())
