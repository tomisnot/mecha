"""Phase 5 第 2 步判据（无头半）：V2Bridge 满足 GUI 的消费契约。

GUI（Qt）本体在 CI 里跑不了，但 GUI 对 bridge 的**调用面**是可测的契约——
本判据按 EL 消费方实际调用的形状逐项验（read/write/write_many/subscribe/
can_write/mode/switch_mode/effective/params/describe/save/load + ops 计算面
prepare/execute/load_artifact）：

1. 写经门 + 回执形状 {ok,resolved,readback}（v1 兼容）。
2. 订阅实时刷新：AI 通道写 → gui 侧回调收到 {kind,actor,target}（面板刷新依据）。
3. 写权互斥：ai 模式下 gui 写被拒（can_write=False + write 抛 GateDenied）；
   切回 human 放行。AI 不能自己解锁。
4. write_many 原子：一批里埋一个坏键 ⇒ 一个都不落地（v1 存档教训）。
5. save/load 往返：扁平快照；load 原子恢复。
6. params 渐进披露：默认不露 internal；include_internal 拉全量；describe 单键。
7. ops 计算面（主线程，own_thread=False）：prepare→execute→artifact 取回数组，
   与 GUI 绘图消费的形状一致（res['pops']/res['t']）。
8. Monitor 读面（第一交付物 §5.2④）：bridge.monitor.read() 出两层；诚实概括
   不误报（disputed=False），谎报概括被原始证伪（disputed=True，硬纪律 5）。

缺 EL 即红。Qt 相关的活体部分（真开窗口点按钮）需用户本地验收（README 步骤）。

用法：python checks/phase5_bridge_selfcheck.py
退出码：0 全绿；1 有失败。

## Known Limitations and Deferred Work

- 不测 Qt 信号槽/控件刷新本身（无头环境起不了 QApplication）；测的是它依赖的
  bridge 契约。活体 GUI 验收清单在 EL 仓 src/v2_bind.py 顶部注释。
- execute 用最小配置（单体 mixed 小网格，~2s）；两体/多体路径归活体验收。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EL_ROOT = Path(os.environ.get("MECHA_EL_ROOT", ""))   # 只读环境变量，缺了不猜
# 领域实现归所属项目（ADR 领域参考实现搬回所属项目）：EL 适配器从 EL 仓 import，
# 不再寄存在框架 examples/ —— 缺 EL 即红（下方 exists 检查守）。
sys.path.insert(0, str(EL_ROOT))

from mecha.authority import Mode  # noqa: E402
from mecha.errors import GateDenied, MechaError  # noqa: E402

from mecha_v2.bridge import V2Bridge  # noqa: E402

SKEL = {
    "groups.list": [{"n": 60, "l": 0, "j": 0.5}, {"n": 60, "l": 1, "j": 1.5}],
    "field.B_gauss": 1.0, "field.E_vpcm": 0.0,
    "dynamic.mode": 0, "dynamic.method": "mixed",
    "dynamic.t_max": 5.0, "dynamic.npts": 50,
    "dynamic.decay_index": 0,
}


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _fail(out: list[str], msg: str) -> None:
    print(f"  ✗ {msg}")
    out.append(msg)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out: list[str] = []
    print("Phase 5 第 2 步判据（无头：GUI 消费契约）：")
    if not (EL_ROOT / "src" / "core" / "protocol.py").exists():
        print(f"  ✗ EL 根不存在：{EL_ROOT}（缺 EL 即红）")
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="mecha-bridge-"))
    br = V2Bridge.for_gui(el_root=EL_ROOT, data_root=tmp)

    # 1. 写经门 + 回执形状
    rec = br.write("field.B_gauss", 21.7, reason="人调磁场")
    if rec["ok"] and rec["resolved"] == 21.7 and rec["readback"] == 21.7:
        _ok("write 经门成功 + 回执 {ok,resolved,readback}（v1 形状）")
    else:
        _fail(out, f"write 回执不符：{rec}")

    # 2. 订阅：AI 写 → gui 回调收到事件（面板实时刷新的依据）
    seen: list[dict] = []
    unsub = br.subscribe(lambda ev: seen.append(ev))
    br.switch_mode(Mode.AI, actor="human")
    br.write("field.E_vpcm", 3.0, reason="AI 调场", actor="ai")
    if seen and seen[-1]["kind"] == "set" and seen[-1]["actor"] == "ai" \
            and seen[-1]["target"] == "field.E_vpcm":
        _ok("subscribe 收到 ai 写事件（GUI 实时刷新 + 归因可用）")
    else:
        _fail(out, f"订阅事件不符：{seen}")
    unsub()

    # 3. 写权互斥：AI 模式下 gui 写被拒
    if br.can_write("gui") is False:
        _ok("AI 模式 can_write(gui)=False（据此设控件只读）")
    else:
        _fail(out, "AI 模式下 gui 竟可写")
    try:
        br.write("field.B_gauss", 99.0, reason="GUI 越权", actor="gui")
        _fail(out, "越权写竟落地")
    except GateDenied:
        if br.read("field.B_gauss") == 21.7:
            _ok("GUI 越权写被拒且值未动（写权互斥）")
        else:
            _fail(out, "被拒但值变了")
    try:
        br.switch_mode(Mode.HUMAN, actor="ai")
        _fail(out, "AI 通道竟能切模式（自解锁）")
    except GateDenied:
        _ok("AI 不能给自己解锁（switch_mode 拒非 human）")
    br.switch_mode(Mode.HUMAN, actor="human")
    if br.can_write("gui"):
        _ok("人类夺回：can_write(gui) 恢复 True")
    else:
        _fail(out, "切回 human 后 gui 仍不可写")
    # 审查 2.4：适配器层细标签 'gui' 切模式 → 映射到 human 侧成功（不撞库层墙）
    br.switch_mode(Mode.AI, actor="gui")
    if br.mode == "ai":
        br.switch_mode(Mode.HUMAN, actor="gui")
        _ok("bridge.switch_mode(actor='gui') 经适配器映射到 human 侧放行（2.4）")
    else:
        _fail(out, "gui 标签切模式未生效（适配器映射断）")

    # 4. write_many 原子：坏一键全不写
    before = br.effective()
    try:
        br.write_many({"field.B_gauss": 5.0, "dynamic.nope": 1},
                      reason="批量含坏键", actor="gui")
        _fail(out, "含坏键的批量竟通过")
    except MechaError:
        after = br.effective()
        if after.get("field.B_gauss") != 5.0:
            _ok("write_many 原子：坏一键 ⇒ 好键也没落地（无半拉子）")
        else:
            _fail(out, "批量失败但部分落地（原子性破了）")

    # 5. save/load 往返
    p = tmp / "state.json"
    br.save(p, reason="存档")
    br.write("field.B_gauss", 0.1, reason="改乱")
    br.load(p, reason="恢复")
    if br.read("field.B_gauss") == 21.7:
        _ok("save→改乱→load 往返恢复（原子批量）")
    else:
        _fail(out, f"load 未恢复：{br.read('field.B_gauss')}")

    # 6. params 渐进披露 + describe
    lp_def = br.params()
    lp_all = br.params(include_internal=True, page_size=100000)
    if lp_all["count"] > lp_def["count"]:
        _ok("params 默认藏 internal，include_internal 拉全量（M2/D-B 形状）")
    else:
        _fail(out, f"渐进披露不符：{lp_def['count']} vs {lp_all['count']}")
    d = br.describe("field.B_gauss")
    if d.get("name") == "field.B_gauss":
        _ok("describe 单键可查")
    else:
        _fail(out, f"describe 不符：{d}")
    try:
        br.describe("no.such_key")
        _fail(out, "describe 未入册键竟通过")
    except MechaError as e:
        if e.kind == "unknown_key":
            _ok("describe 未入册键报错（fail loud）")
    # 审查 2.5：打错键名自动 did_you_mean（L4 头号教学特征，difflib 在库层）
    try:
        br.describe("field.B_gaus")           # 差一个字母的真·手滑
        _fail(out, "拼错键竟通过")
    except MechaError as e:
        if "field.B_gauss" in (e.suggest or ""):
            _ok(f"拼错键自动猜你想写（suggest={e.suggest!r}）")
        else:
            _fail(out, f"did_you_mean 未生效：suggest={e.suggest!r}")

    # 7. ops 计算面（主线程）：prepare → execute → artifact 取回
    br.write_many(SKEL, reason="骨架", actor="gui")
    lv = br._engine.list_levels(br.effective())
    key0 = lv["states"][0]["key"]
    br.write("dynamic.psi_single", key0, reason="初态", actor="gui")
    prepared = br.ops.prepare(actor="gui", reason="GUI 运行演化")
    if isinstance(prepared.get("run"), dict) and "problems" in prepared:
        _ok("ops.prepare 回 {run,problems}（v1 handle 形状）")
    else:
        _fail(out, f"prepare 形状不符：{list(prepared)}")
        return 1 if out else 0
    res = br.ops.execute(prepared, actor="gui", reason="GUI 运行演化")
    aid = res.get("artifact_id")
    loaded = br.ops.load_artifact(aid) if aid else None
    if "problems" not in res:
        _fail(out, "execute 回执缺顶层 problems（RunWorker 消费 res['problems']"
                    "发状态栏旁注，只嵌 summary 会静默丢）")
    elif res["problems"] != prepared.get("problems"):
        _fail(out, f"顶层 problems 与 prepare 不一致：{res['problems']}"
                   f" != {prepared.get('problems')}")
    else:
        _ok("execute 回执顶层带 problems（v1 RunWorker 消费形状对齐）")
    if res["ok"] and loaded is not None:
        _labels, arrays = loaded
        if "pops" in arrays and "t" in arrays:
            _ok(f"ops.execute→artifact 取回 pops/t（GUI 绘图消费形状一致，"
                f"pops.shape={arrays['pops'].shape}）")
        else:
            _fail(out, f"artifact 缺绘图数组：{list(arrays)}")
    else:
        _fail(out, f"计算面往返失败：ok={res.get('ok')} aid={aid}")

    # 8. journal 独立落盘（GUI 侧 v2 权威史，不依附 dsh）
    jr = tmp / ".mecha-v2" / "journal.jsonl"
    if jr.exists() and jr.read_text(encoding="utf-8").strip():
        _ok("GUI 侧 v2 journal 独立落盘（人机同史的地基）")
    else:
        _fail(out, "v2 journal 未落盘")

    # 9. 同一道门（无 Qt 半）：for_shared + serve_mcp ⇒ GUI 线程与 MCP 线程共一 Gate
    tmp2 = Path(tempfile.mkdtemp(prefix="mecha-shared-"))
    br2 = V2Bridge.for_shared(el_root=EL_ROOT, data_root=tmp2)
    # CacheProxy：主线程直接碰 cache（GUI 的用法）经代理汇流到引擎线程
    g0 = {"n": 60, "l": 0, "j": 0.5}
    e0 = br2.cache.E_mhz(g0["n"], g0["l"], g0["j"])
    if isinstance(e0, float):
        _ok(f"CacheProxy：主线程 cache.E_mhz 经引擎线程汇流成功（{e0:.1f} MHz）")
    else:
        _fail(out, f"CacheProxy 直通失败：{e0!r}")
    # 新契约（EL 迁移到框架 McpEndpoint 之后）：返回 ``(host, 实际端口)`` 二元组；
    # 旧的三元组 ``(thread, server, port)`` 已随薄壳化退役。判据对齐新契约，而不是
    # 要求下游保留旧形状——**判据该断言契约，不该断言下游的内部返回形状**。
    # R10：解包成功本身**不算**"验到服务起来"；下面仍要真连一次 MCP 客户端并真调
    # 一个工具（比 list_tools 更强），故断言强度不降。
    mcp_host, port = br2.serve_mcp(port_file=tmp2 / ".mcp-port")
    if getattr(mcp_host, "port", None) == port \
            and str(getattr(mcp_host, "url", "")).endswith("/mcp"):
        _ok(f"serve_mcp 返回 (host, 端口)：host 自报同一端口 {port}、url 以 /mcp 结尾")
    else:
        _fail(out, "serve_mcp 返回对象与端口不符："
                   f"{getattr(mcp_host, 'port', None)!r} vs {port!r}")
    import asyncio

    async def ai_side():
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        # 等服务器线程真正 bind（port 文件先写、bind 后到）
        for _try in range(40):
            try:
                async with streamable_http_client(f"http://127.0.0.1:{port}/mcp") as st:
                    async with ClientSession(st[0], st[1]) as s:
                        await s.initialize()
                        br2.switch_mode(Mode.AI, actor="human")   # 人开闸，AI 才能写
                        return await s.call_tool(
                            "set_config", {"key": "field.B_gauss", "value": 42.0})
            except Exception:                                     # noqa: BLE001
                await asyncio.sleep(0.5)
        raise RuntimeError("连不上共享 MCP 服务器")
    try:
        r = asyncio.run(asyncio.wait_for(ai_side(), timeout=90))
        ok = not getattr(r, "is_error", False)
    except Exception as e:                                # noqa: BLE001
        ok = False
        _fail(out, f"MCP 往返异常：{e}")
    if ok and br2.read("field.B_gauss") == 42.0:
        _ok("同一道门：AI 经 MCP 线程写 42.0，GUI 线程读同一快照即见（一份真值）")
    else:
        _fail(out, f"同门读写不符：ok={ok} read={br2.read('field.B_gauss')}")
    br2.switch_mode(Mode.HUMAN, actor="human")
    br2.write("field.B_gauss", 7.0, reason="人再改", actor="gui")
    jr2 = tmp2 / ".mecha-v2" / "journal.jsonl"
    actors = []
    for line in jr2.read_text(encoding="utf-8").splitlines():
        if line.strip():
            actors.append(__import__("json").loads(line).get("actor"))
    if "ai" in actors and "gui" in actors:
        _ok("journal 同一条史里既有 ai 又有 gui 事件（人机同史，互引可查）")
    else:
        _fail(out, f"journal actor 不全：{actors}")
    # （旧的 ``server_task = None`` 占位随三元组契约一起删：服务的存活现在由上面
    # 那次**真往返**证明，daemon 线程随进程退出，不需要一个空变量作注脚。）

    # 10. Monitor 读面（第一交付物 §5.2④）：两层 + 可对账（硬纪律 5）
    from mecha.monitor import Claim, Monitor
    mv = br.monitor.read()
    if mv.raw_snapshot and "磁场 (G)" in mv.summary \
            and isinstance(mv.summary["磁场 (G)"], Claim):
        _ok("monitor.read 出两层（原始快照 + 人话概括 Claim）")
    else:
        _fail(out, f"monitor 两层形状不符：{dict(mv.summary)}")
    # 诚实概括不误报：人话键 '磁场 (G)' 的 Claim 指向 field.B_gauss，值一致
    if mv.disputed is False:
        _ok("诚实概括 disputed=False（人话键经 Claim.target 对账通过，不误报）")
    else:
        _fail(out, f"诚实概括竟存疑：{mv.dispute_reason}")
    # 谎报概括被证伪：注入一个值与原始不符的 Claim ⇒ 原始赢、disputed=True
    def _liar(events):
        return {"磁场 (G)": Claim(999999.0, target="field.B_gauss")}
    lie = Monitor(br.history.events, _liar).read()
    if lie.disputed and "field.B_gauss" in lie.dispute_reason:
        _ok(f"谎报概括被原始证伪 disputed=True（{lie.dispute_reason}）")
    else:
        _fail(out, f"谎报未被抓到：disputed={lie.disputed} reason={lie.dispute_reason}")

    if out:
        print(f"phase5_bridge_selfcheck: FAIL（{len(out)} 条）")
        return 1
    print("phase5_bridge_selfcheck: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
