"""V2Bridge：GUI ↔ v2 Gate 的操作者适配器（EL 消费配置，Phase 5 第 2 步）。

让 EL 的 GUI（`_mecha_bind.Binder` + `app_entry`）经**同一道 v2 写入门**读写域
状态——与 AI（MCP Hub）共享**一个 Gate、一份 History、一份真值**。这是"人机同
一道门"（L1）在真项目里落地的那块砖。

接口对齐 v1 的 AppBridge（Binder 零改动即可复用）：
``read / write / subscribe / can_write / mode / switch_mode / save / load /
cache / session``。actor 字符串 → 通道的映射在**这里**（适配器层）做，库不认
'gui'/'mcp' 这些宿主词。

## Known Limitations and Deferred Work

- actor→通道映射表写死在此（gui/human→human 通道；ai/mcp/codeact/job→ai 通道）；
  新操作者类别出现时改这张表，不改库。
- ``session.set`` 兼容垫片只服务启动种子（面板初值回填，不经写权门）；正常写
  一律走 write→Gate。种子用 actor='session' 记史，归因上区别于 human/ai。
- save/load 的 JSON 格式是本适配器的读档格式（快照 dict），非 v1 的
  'mecha-session-v1'——不兼容读 v1 旧存档（迁移是另一件事，需 ADR）。
- 线程：Gate 写口用一把锁串行（GUI 主线程 + Hub 线程并发写）；读快照无锁
  （CPython dict 读原子性足够展示用）。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from mecha.authority import Authority, Mode
from mecha.errors import GateDenied, MechaError, UnknownKey
from mecha.gate import Channel, Gate
from mecha.history import Event, History, fold
from mecha.monitor import Claim, Monitor
from mecha.providers.jobs import JobRegistry


# actor 字符串 → 通道。宿主词表在这层，不在库。
_ACTOR_CHANNEL: dict[str, str] = {
    "gui": "human", "human": "human",
    "ai": "ai", "mcp": "ai", "codeact": "ai", "job": "ai",
}

_AI_LABELS = {"ai", "mcp", "codeact", "job"}


def el_summarizer(events: list[Event]) -> dict:
    """EL 领域的人话概括（Monitor 概括层，总纲 §5.2④）。

    派生、可被原始层证伪（硬纪律 5）：复述键值的条目一律用
    ``Claim(value, target=原始键)`` 显式指向原始键——人话键名也能对账
    （审查 P3-b 的机制）。只复述**史里出现过**的键（target 不存在 ⇒
    disputed，所以宁缺毋滥）。元信息（计数/最近一条）不是键值复述，
    不进对账面。
    """
    if not events:
        return {"状态": "尚无变更（出厂快照未动）"}
    snap = fold(events)
    n_ai = sum(1 for e in events if e.actor in _AI_LABELS)
    n_human = sum(1 for e in events if e.actor in {"gui", "human"})
    last = events[-1]
    out: dict = {
        "总变更": len(events),
        "人写": n_human,
        "AI 写": n_ai,
        "最近一条": f"#{last.seq} [{last.actor}] {last.key} ← "
                    f"{last.reason or '（无 reason）'}",
    }
    # 关键配置的人话复述（值与原始折叠比对；键不在史里就不复述）
    _HUMAN_KEYS = [
        ("磁场 (G)", "field.B_gauss"),
        ("场强 (V/cm)", "field.E_vpcm"),
        ("演化方法", "dynamic.method"),
        ("演化时长 (ns)", "dynamic.t_max"),
        ("采样点数", "dynamic.npts"),
    ]
    for label, target in _HUMAN_KEYS:
        if target in snap:
            out[label] = Claim(snap[target], target=target)
    return out


def _build_gui_tools(engine, gate, authority, ai, jobs):
    """GUI 进程内工具面（复用 tools.build_tool_registry，Surface 现建）。"""
    from examples.energy_level.tools import build_tool_registry
    from mecha.surface import Surface
    surface = Surface(engine)
    return build_tool_registry(engine, gate, surface, authority, ai, jobs)


class _SessionShim:
    """兼容 v1 的 bridge.session.set（仅启动种子用）。"""

    def __init__(self, bridge: "V2Bridge") -> None:
        self._b = bridge

    def set(self, key, value, *, actor="session", reason=""):
        self._b._seed(key, value, actor=actor, reason=reason)


class V2Bridge:
    """GUI 适配器：把 AppBridge 接口翻译成 v2 Gate/History/Authority 操作。

    构造走 ``for_gui``（装配全栈 + cache/ops 接线）；直接构造仅供测试注入。
    """

    def __init__(self, gate: Gate, history: History, authority: Authority,
                 *, engine=None, cache=None, artifact_root: Path | None = None,
                 summarizer=el_summarizer,
                 monitor: Monitor | None = None,
                 jobs: JobRegistry | None = None) -> None:
        self._gate = gate
        self._history = history
        self._authority = authority
        self._engine = engine
        self._lock = threading.Lock()
        self.cache = cache if cache is not None else (engine._cache if engine else None)
        self._artifact_root = artifact_root or (
            Path(".mecha-v2/artifacts") if engine else Path(".mecha-v2/artifacts"))
        self.session = _SessionShim(self)
        self.ops = ElOps(self) if engine is not None else None
        # Monitor（第一交付物的后端读面，总纲 §5.2④）：只读，无写回。
        # for_* 路径注入 assemble() 建好的那份（D-33：唯一具名装配点）；
        # 直接构造（测试注入）缺省自建。
        self.monitor = monitor or Monitor(history.events, summarizer)
        # 单一 Job 注册表（本进程唯一，消 D-21）：GUI 与 MCP 线程共用它。
        self.jobs = jobs or JobRegistry()

    @classmethod
    def _assembled(cls, *, el_root: str | Path, data_root: str | Path,
                   data_dir_name: str, mode: Mode, engine, cache):
        """经 mecha.assembly.assemble() 具名装配点建栈（D-33）。

        真实部署路径与 ToyEngine 端到端判据走**同一个** assemble——装配漂移
        在真引擎上就会红，不再存在"判据用替身、真路径手搭"的双装配。
        """
        from mecha.assembly import assemble
        from examples.energy_level.engine import make_validator
        sw = assemble(root=Path(data_root), data_dir_name=data_dir_name,
                      engine=engine, summarizer=el_summarizer,
                      validate=make_validator(engine), mode=mode,
                      project="energy-level")
        return cls(sw.gate, sw.history, sw.authority, engine=engine, cache=cache,
                   artifact_root=sw.layout.data_dir / "artifacts",
                   monitor=sw.monitor, jobs=sw.jobs)

    @classmethod
    def for_gui(cls, *, el_root: str | Path, data_root: str | Path,
                data_dir_name: str = ".mecha-v2", mode: Mode = Mode.HUMAN) -> "V2Bridge":
        """GUI 进程内装配：v2 引擎（cache 建在主线程，own_thread=False）+ Gate。

        调用方（app_entry）在主线程构造它 ⇒ cache 的 SQLite 亲和落在主线程，
        与 v1 的 AppBridge 同构（prepare 主线程、execute worker）。
        """
        from examples.energy_level.engine import ElEngine
        engine = ElEngine(el_root, own_thread=False)
        return cls._assembled(el_root=el_root, data_root=data_root,
                              data_dir_name=data_dir_name, mode=mode,
                              engine=engine, cache=engine._cache)

    @classmethod
    def for_shared(cls, *, el_root: str | Path, data_root: str | Path,
                   data_dir_name: str = ".mecha-v2",
                   mode: Mode = Mode.HUMAN) -> "V2Bridge":
        """单进程"人机同一道门"装配：GUI 与（后台线程的）MCP 服务器共享一个 Gate。

        引擎用 own_thread=True（cache 建在专属池线程），GUI 拿到的 ``cache`` 是
        CacheProxy —— GUI 主线程的直接 ``cache.E_mhz(...)`` 与 MCP handler（anyio
        线程）的 protocol 调用都汇流到同一池线程，SQLite 连接永不被跨线程触碰
        （EL B-1①）。这是"同一道门"在真项目里的落地形态。
        """
        from examples.energy_level.engine import ElEngine, CacheProxy
        engine = ElEngine(el_root, own_thread=True)
        return cls._assembled(el_root=el_root, data_root=data_root,
                              data_dir_name=data_dir_name, mode=mode,
                              engine=engine,
                              cache=CacheProxy(engine._cache, engine._on_engine))

    def serve_mcp(self, *, host: str = "127.0.0.1", port: int = 0,
                  port_file: str | Path | None = None):
        """在本进程后台线程起 MCP 服务器（AI 连它，写经**同一个** Gate）。

        返回 (thread, server, actual_port)。GUI 与服务器共享 self._gate ⇒ 人机同门。
        """
        import threading
        from examples.energy_level.hub import to_mcp, _free_port
        ai = Channel("ai")
        tools = _build_gui_tools(self._engine, self._gate, self._authority,
                                 ai, self.jobs)
        server = to_mcp({"tools": tools, "authority": self._authority})
        actual = port or _free_port(host)
        t = threading.Thread(
            target=lambda: server.run(transport="streamable-http",
                                      host=host, port=actual),
            daemon=True, name="mecha-mcp")
        t.start()
        if port_file:
            Path(port_file).write_text(str(actual), encoding="utf-8")
        return t, server, actual

    # ---- 写（经门）----

    def write(self, key, value, *, reason="", actor="gui", call_id=""):
        chan = self._channel_for(actor)
        with self._lock:
            ev = self._gate.set(chan, key, value, reason=reason, call_id=call_id)
        # v1 AppBridge.write 的回执形状（GUI 多数忽略，这里如实提供）
        return {"ok": True, "resolved": ev.value, "readback": self.read(key),
                "seq": ev.seq}

    def write_many(self, items, *, reason="", actor="gui", call_id=""):
        """原子批量写（GUI 运行前同步配置用）。任一键非法 ⇒ 一个都不写。"""
        chan = self._channel_for(actor)
        with self._lock:
            return self._gate.set_batch(chan, dict(items), reason=reason, call_id=call_id)

    def _seed(self, key, value, *, actor="session", reason=""):
        """启动种子：经 Gate.seed 落快照 + 记史，不走写权门（面板初值回填）。"""
        with self._lock:
            return self._gate.seed(key, value, actor, reason=reason or "seed")

    @staticmethod
    def _channel_for(actor: str) -> Channel:
        label = str(actor).split(":")[0]
        side = _ACTOR_CHANNEL.get(label)
        if side is None:
            raise GateDenied(
                f"未知 actor {actor!r}", kind="unknown_actor",
                hint="actor 必须映射到 human/ai 通道",
                suggest=f"已知 actor：{sorted(_ACTOR_CHANNEL)}",
            )
        # 标签进史做归因（gui/mcp/…），side 定写权（human/ai）
        return Channel(label, side)

    # ---- 读 ----

    def read(self, key):
        return self._gate.snapshot.get(key)

    @property
    def history(self) -> History:
        """权威史只读访问口（监控面板原始层用；写不进——append 只在 Gate 内）。"""
        return self._history

    def close(self) -> None:
        """关停：释放数据目录的写租约（宿主退出/租约交接用）。幂等。"""
        self._history.close()

    @property
    def snapshot(self) -> dict:
        return self._gate.snapshot

    def can_write(self, actor: str) -> bool:
        side = _ACTOR_CHANNEL.get(str(actor).split(":")[0])
        if side is None:
            return False
        try:
            self._authority.gate(side)
            return True
        except GateDenied:
            return False

    @property
    def mode(self) -> str:
        return self._authority.mode.value

    def switch_mode(self, new_mode: str, *, actor="human"):
        # actor 标签 → side（复用写权映射表）；authority 只认 side（审查 2.4）。
        # 未知标签报错；细标签（gui/human）映到 human 侧 ⇒ 可切，ai/mcp 映到
        # ai 侧 ⇒ 被 authority 拒（AI 不能自解锁）。
        side = _ACTOR_CHANNEL.get(str(actor).split(":")[0])
        if side is None:
            raise GateDenied(
                f"未知 actor {actor!r}", kind="unknown_actor",
                hint="切模式者必须映射到已知通道",
                suggest=f"已知 actor：{sorted(_ACTOR_CHANNEL)}",
            )
        self._authority.switch_mode(Mode(new_mode), side)

    # ---- 订阅（GUI 面板刷新）----

    def activity(self, since: int = 0) -> list[dict]:
        """v1 Session.activity 兼容口：seq >= since 的事件（v1 事件 dict 形状）。

        GUI 判据与面板用它对账"谁在什么时候写了什么"；数据源就是权威史，
        与 subscribe 的推送形状一致（同一适配，两种取法）。
        """
        return [{"kind": "set", "actor": e.actor, "target": e.key,
                 "value": e.value, "seq": e.seq, "call_id": e.call_id}
                for e in self._history.events() if e.seq >= since]

    def subscribe(self, callback):
        """把 v2 Event 适配成 v1 事件 dict（{kind,actor,target,value,seq,call_id}）。"""
        def _ev(e: Event) -> None:
            callback({"kind": "set", "actor": e.actor, "target": e.key,
                      "value": e.value, "seq": e.seq, "call_id": e.call_id})
        return self._history.subscribe(_ev)

    # ---- 存档（原子批量：真实调用方，兑现 set_batch 的存在理由）----

    def save(self, path, *, reason="GUI 保存状态"):
        Path(path).write_text(
            json.dumps(self._gate.snapshot, ensure_ascii=False, default=str),
            encoding="utf-8")
        return path

    def load(self, path, *, reason="GUI 恢复状态", actor="gui"):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise MechaError("存档格式非法（顶层须是对象）", kind="bad_archive",
                             hint="save 写出的是 {键:值} 快照")
        chan = self._channel_for(actor)
        with self._lock:
            self._gate.set_batch(chan, data, reason=reason)   # 原子：坏一键全不写
        return len(data)

    # ---- v1 AppBridge 兼容面（GUI 消费配置所需的其余口）----

    def effective(self) -> dict:
        """当前域快照（v1 同名口的语义：GUI 读配置现值都走这里）。"""
        return self._gate.snapshot

    def params(self, *, include_internal: bool = False, page_size: int = 60) -> dict:
        """参数表（v1 list_params 的形状：{params, count}）。

        tier='internal' 的引擎常量默认不露面（渐进披露，M2/D-B）；GUI 检视器
        用 include_internal=True + 大 page_size 拉全量。
        """
        schema = self._engine.schema()
        rows: list[dict] = []
        for fam in ("config", "solve"):
            for key, rec in dict(schema[fam]).items():     # type: ignore[arg-type]
                rows.append({**rec, "name": key, "family": fam})
        if include_internal:
            # engine 族用**裸名**（v1 registry 约定：family='engine' + name='R_CRIT'，
            # 不带 engine. 前缀）——GUI 的精度旋钮回显按裸名查记录，前缀名查不到。
            for key, rec in dict(schema["engine"]).items():  # type: ignore[arg-type]
                rows.append({**rec, "name": key, "family": "engine"})
        rows.sort(key=lambda r: r["name"])
        return {"params": rows[:page_size], "count": len(rows)}

    def describe(self, key: str) -> dict:
        """单键参数记录（v1 同名口；未入册抛 UnknownKey）。"""
        bare = key[len("engine."):] if key.startswith("engine.") else key
        schema = self._engine.schema()
        for fam in ("config", "solve"):
            rec = dict(schema[fam]).get(key)               # type: ignore[call-overload]
            if rec:
                return {**rec, "name": key, "family": fam}
        rec = dict(schema["engine"]).get(bare)             # type: ignore[call-overload]
        if rec:
            return {**rec, "name": f"engine.{bare}", "family": "engine"}
        raise UnknownKey.typo(
            key,
            list(dict(schema["config"])) + list(dict(schema["solve"]))
            + [f"engine.{k}" for k in dict(schema["engine"])],
            message=f"键 {key!r} 未入册",
            hint="参数表见 params()；拼错键名会被拒（fail loud）")


class ElOps:
    """v1 Ops 兼容的计算门面（GUI 运行路径：prepare/execute/load_artifact/root）。

    线程模型照 v1 的定论（B-1①）：**prepare 必须建 cache 的主线程**（ARC
    SQLite 认线程，GUI 主线程就是主线程）；**execute 可进 worker 线程**
    （evolve_run 纯数值不碰 cache）。artifact 落盘格式与 v1 同（GUI 的
    load_artifact 取 (run, arrays, manifest) 元组第 1 项）。
    """

    def __init__(self, bridge: "V2Bridge") -> None:
        self._b = bridge
        self.root = bridge._artifact_root

    def prepare(self, *, actor: str = "gui", reason: str = "",
                diag: bool = False, pump=None, vdd: bool = True) -> dict:
        p = self._b._engine._protocol
        snap = self._b.effective()
        run = p.build_run(self._b.cache, snap, pump=pump, vdd=vdd, diag=diag)
        problems = p.check_run(run)
        return {"run": run, "problems": problems, "actor": actor, "reason": reason}

    def execute(self, prepared: dict, *, actor: str = "gui", reason: str = "",
                progress_arr=None, land_artifact: bool = True) -> dict:
        p = self._b._engine._protocol
        run = prepared["run"]
        res = p.evolve_run(run, progress_arr=progress_arr, cache=self._b.cache)
        summary = dict(p.result_summary(run, res))
        problems = list(prepared.get("problems") or [])
        summary["problems"] = problems
        aid = ""
        if land_artifact and res is not None:
            aid = self._land(run, res)
            summary["artifact_id"] = aid
        # problems 同时放**顶层**：v1 的 RunWorker 消费 `res.get('problems')`
        # 发状态栏旁注（evolve_worker.py:45），只嵌 summary 会让旁注静默丢失。
        return {"summary": summary, "problems": problems,
                "artifact_id": aid, "ok": bool(summary.get("ok"))}

    def _land(self, run: dict, res: dict) -> str:
        import pickle
        import uuid
        self.root.mkdir(parents=True, exist_ok=True)
        aid = uuid.uuid4().hex[:12]
        arrays = {k: v for k, v in res.items()
                  if k in ("pops", "t", "loss", "pops_2d", "states_2d",
                           "loss_A", "loss_B", "om_mhz", "diag")}
        with (self.root / f"{aid}.pkl").open("wb") as f:
            pickle.dump((run.get("labels"), arrays), f, protocol=4)
        return aid

    def load_artifact(self, aid: str):
        import pickle
        path = self.root / f"{aid}.pkl"
        if not path.exists():
            return None
        with path.open("rb") as f:
            return pickle.load(f)
