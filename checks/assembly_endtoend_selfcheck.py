"""端到端装配判据（L6 REAL-composition：真装配，不手搭零件）。

用 ``mecha.assembly.assemble`` 起一台完整软件实例，验总纲 §7 Phase 1 的
**致死性验收**：

  洞1：域状态只能经 Gate 改。AI 拿不到可写引用；直写快照无效；伪造 actor 无效。
  洞2：物理演化只经 Surface.run 这一条 executor。绕过 run 直调引擎 = 结构上
       无入口（引擎句柄不外露，run 是唯一路径）。
  归因：任何一次经门的写，History 里都有 actor + call_id，可与操作者行为史双向走查。
  独立：dsh 缺席（本判据全程不碰 dsh）时，写入门/史/监控/重启重建全照常。

L7 对偶：合法装配路径全绿；重启后快照一致。

用法：python checks/assembly_endtoend_selfcheck.py
退出码：0 全绿；1 有失败。

## Known Limitations and Deferred Work

- 用临时目录做真实 JSONL 落盘（覆盖 log_local 的读写闭环），不 mock 文件系统。
- 洞2 的"AI 用 bash 调引擎 Python API"是部署期 sandbox policy 拦的（库外），
  这里只验库内"引擎句柄不外露、run 是唯一 executor"的结构保证。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mecha.assembly import assemble  # noqa: E402
from mecha.authority import Mode  # noqa: E402
from mecha.errors import GateDenied, MechaError  # noqa: E402
from mecha.gate import accept_all  # noqa: E402
from mecha.monitor import Claim  # noqa: E402
from mecha.surface import Engine, ExecutionContext  # noqa: E402


class ToyEngine(Engine):
    def __init__(self) -> None:
        self.runs = 0

    def schema(self):
        return {"run_output_required": ["ok", "method"], "keys": ["power"]}

    def run(self, spec, *, context, **opts):
        self.runs += 1
        return {"ok": True, "method": str(spec.get("mode", "default"))}

    def summary(self):
        return {"state": "ready", "runs": self.runs}

    def estimate(self, spec):
        return {"est_sec": 0.5}


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _fail(out: list[str], msg: str) -> None:
    print(f"  ✗ {msg}")
    out.append(msg)


def _summarizer(events):
    # 动态复述最新 power（概括必须能从原始推出）
    power = [e.value for e in events if e.key == "power"]
    return {"power": Claim(power[-1])} if power else {}


def build(root: Path):
    return assemble(root=root, data_dir_name="projdata", engine=ToyEngine(),
                    summarizer=_summarizer, validate=accept_all, project="toyproj")


def check_hole1_no_bypass_write(out: list[str]) -> None:
    with tempfile.TemporaryDirectory() as td:
        sw = build(Path(td))
        # 出厂 LOCKED：先验"默认收紧"（nothing ships enabled 用在写权上）
        try:
            sw.gate.set(sw.channels["human"], "power", 1, "r")
            _fail(out, "出厂 LOCKED 竟可写（默认放开）")
        except GateDenied:
            _ok("出厂 LOCKED：任何通道默认不可写（不默认放开）")
        sw.authority.switch_mode(Mode.AI, actor="human")
        ai = sw.channels["ai"]
        # 合法：AI 经门写
        ev = sw.gate.set(ai, "power", 100, "setpoint", call_id="c1")
        if ev.actor == "ai" and ev.call_id == "c1":
            _ok("AI 经 Gate 写成功且可归因（actor + call_id）")
        else:
            _fail(out, f"合法写归因缺失：{ev}")
        # 能抓坏：改快照投影不回写（无绕过写口）
        snap = sw.gate.snapshot
        snap["power"] = 999
        if sw.gate.snapshot["power"] == 100:
            _ok("改快照投影不回写门内状态（洞1：无第二条写路径）")
        else:
            _fail(out, "快照投影可回写——门漏在包装层")
        # 能抓坏：伪造 actor 字符串构造通道
        try:
            from mecha.gate import Channel
            Channel("human")  # 合法
            Channel("admin")  # 未知 actor 必须拒
            _fail(out, "未知 actor 通道竟可构造")
        except GateDenied:
            _ok("未知 actor 通道构造被拒（无法伪造人类归因）")


def check_hole2_run_is_only_executor(out: list[str]) -> None:
    with tempfile.TemporaryDirectory() as td:
        sw = build(Path(td))
        # 物理演化经 Surface.run（唯一 executor）
        r = sw.surface.run({"mode": "evolve"}, context=ExecutionContext())
        eng = sw.surface._engine
        if r["ok"] and eng.runs == 1:
            _ok("run 经 Surface 落到引擎（唯一 executor）")
        else:
            _fail(out, f"run 未落引擎：{r}")
        # 引擎句柄不经公开 API 外露：Software 上没有 get_engine/engine()
        exposed = [a for a in dir(sw) if "engine" in a.lower()]
        if not exposed:
            _ok("装配实例不暴露引擎句柄（洞2：无绕过 run 的库内入口）")
        else:
            _fail(out, f"引擎句柄外露：{exposed}")


def check_independent_of_dsh(out: list[str]) -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        sw = build(root)
        sw.authority.switch_mode(Mode.HUMAN, actor="human")
        sw.gate.set(sw.channels["human"], "power", 100, "set", call_id="h1")
        sw.gate.set(sw.channels["human"], "gain", 5, "set", call_id="h2")
        # 重启：同一磁盘目录再装配一次（dsh 全程缺席）
        sw2 = build(root)
        if sw2.gate.snapshot == {"power": 100, "gain": 5}:
            _ok("进程重启后从 JSONL 史重建快照一致（软件不依附 dsh）")
        else:
            _fail(out, f"重启重建不符：{sw2.gate.snapshot}")
        # seq 续上（重启后 authority 回出厂 LOCKED，先由人类通道开闸）
        sw2.authority.switch_mode(Mode.HUMAN, actor="human")
        ev = sw2.gate.set(sw2.channels["human"], "power", 101, "bump", call_id="h3")
        if ev.seq == 3:
            _ok("seq 从盘续（append-only，不重置）")
        else:
            _fail(out, f"seq 未续：{ev.seq}")
        # Monitor 读原始层照常
        v = sw2.monitor.read()
        if v.raw_snapshot["power"] == 101 and not v.disputed:
            _ok("Monitor 原始层独立可读（概括一致不存疑）")
        else:
            _fail(out, f"Monitor 独立读不符：{v}")


def check_history_append_only_on_disk(out: list[str]) -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        sw = build(root)
        sw.authority.switch_mode(Mode.AI, actor="human")
        sw.gate.set(sw.channels["ai"], "k", 1, "r", call_id="a")
        sw.gate.set(sw.channels["ai"], "k", 2, "r", call_id="b")
        journal = root / "projdata" / "journal.jsonl"
        if journal.exists():
            lines = [l for l in journal.read_text(encoding="utf-8").splitlines() if l.strip()]
            if len(lines) == 2 and '"seq":1' in lines[0] and '"seq":2' in lines[1]:
                _ok("磁盘 journal 两行、seq 递增、带格式头（append-only 落盘）")
            else:
                _fail(out, f"journal 行不符：{lines}")
        else:
            _fail(out, "journal.jsonl 未落盘（log provider 没接上）")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out: list[str] = []
    print("端到端装配判据：")
    check_hole1_no_bypass_write(out)
    check_hole2_run_is_only_executor(out)
    check_independent_of_dsh(out)
    check_history_append_only_on_disk(out)
    if out:
        print(f"assembly_endtoend_selfcheck: FAIL（{len(out)} 条）")
        return 1
    print("assembly_endtoend_selfcheck: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
