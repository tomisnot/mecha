"""Phase 8 判据：Artifact Contract（产物契约）——宿主产物的不透明定位符。

第二个真实宿主（ML Toolbox）的运行产物（一次运行的全部输出）住在宿主进程
内存里，无法交给 History / ``result_ref``。Artifact Contract 让宿主把产物落到
**自己的数据目录**，只把 locator 交出去；领域真值仍在宿主，机制不解释内容。

本判据只测机制，不测任何真宿主的产物内容：

  A 不透明：locator 形状稳定、**不含路径分隔符**、不是真实可打开路径，
    也绝不含数据目录的真实前缀。
  B 往返：put → retrieve **逐字节**一致（含二进制与空载荷）。
  C 元数据：describe 给全 kind/name/size/sha256/created_at；size 与
    sha256 与内容独立算出的一致（元数据不撒谎）。
  D fail loud：未知/畸形 locator 抛 ``MechaError(kind="bad_artifact_locator")``；
    exist 只对可取回的 locator 为真。
  E 大载荷：至少 1MiB 的产物往返一致（宿主产物是大对象，不是小文本）。
  F 多产物：同一实例多个 locator 互不覆盖（并发 put 亦不串号）。
  G L7 对偶：经 ``assemble`` 的合规路径全绿；坏 locator 必须被抓。

用法：python checks/phase8_artifact_contract_selfcheck.py
退出码：0 全绿；1 有失败。

## Known Limitations and Deferred Work

- 不测跨进程/跨重启找回 locator（需要扫 ``artifacts/`` 重建索引；等第二个宿主
  真实要跨重启时再做，ADR 与模块 Known Limitations 里登记）。
- 不测删除/GC：机制明确不做（保留策略归宿主），本判据只断言"不做"，不断言
  清理行为。
- 不测真实宿主的产物语义（kind/name 是自由标签，机制不解释——测了就说明
  核心开始理解领域）。
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mecha.artifacts import LocalArtifactStore  # noqa: E402
from mecha.assembly import assemble  # noqa: E402
from mecha.data_layout import resolve_data_dir  # noqa: E402
from mecha.errors import MechaError  # noqa: E402
from mecha.gate import accept_all  # noqa: E402
from mecha.surface import Engine  # noqa: E402

BAD_KIND = "bad_artifact_locator"
ONE_MIB = 1024 * 1024


class ToyEngine(Engine):
    def schema(self):
        return {"run_output_required": ["ok"], "keys": []}

    def run(self, spec, *, context, **opts):
        return {"ok": True}

    def summary(self):
        return {"ok": True}

    def estimate(self, spec):
        return {"est_sec": 0.1}


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _fail(out: list[str], msg: str) -> None:
    print(f"  ✗ {msg}")
    out.append(msg)


class _Store:
    """临时目录里的一个 store（顺带记住 data_dir，供"不暴露路径"断言用）。"""

    def __init__(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="phase8-")
        self.layout = resolve_data_dir(Path(self._td.name), "p8data")
        self.store = LocalArtifactStore(self.layout)

    def __enter__(self) -> LocalArtifactStore:
        return self.store

    def __exit__(self, *exc) -> bool:
        self._td.cleanup()
        return False


def _with_software():
    td = tempfile.mkdtemp(prefix="phase8-sw-")
    return assemble(root=Path(td), data_dir_name="p8data", engine=ToyEngine(),
                    summarizer=lambda evs: {}, validate=accept_all,
                    project="toyhost")


def check_opaque_locator(out: list[str]) -> None:
    with _Store() as store:
        loc = store.put(b"payload", kind="blob", name="first")
        data_dir = store._dir.parent
        if "/" in loc or "\\" in loc:
            _fail(out, f"locator 含路径分隔符（泄漏布局）：{loc!r}")
        else:
            _ok(f"locator 不含路径分隔符：{loc!r}")
        if Path(loc).exists() or Path(data_dir, loc).exists():
            _fail(out, f"locator 指向真实可打开路径：{loc!r}")
        else:
            _ok("locator 不是真实路径（消费者无法据此打开文件）")
        leaked = [p for p in (str(data_dir), data_dir.name, "artifacts")
                  if p in loc]
        if leaked:
            _fail(out, f"locator 泄漏数据目录片段 {leaked}：{loc!r}")
        else:
            _ok("locator 不含数据目录/子目录片段（不透明）")
        # 形状可分辨但不可解析成路径：put 两次得到两个不同句柄
        loc2 = store.put(b"payload2", kind="blob")
        if loc != loc2:
            _ok("每次 put 得到独立句柄（不是同一个复用 id）")
        else:
            _fail(out, f"两次 put 返回同一 locator：{loc!r}")


def check_roundtrip_bytes(out: list[str]) -> None:
    with _Store() as store:
        for label, payload in (
            ("二进制（含 NUL 与非 UTF-8 字节）", bytes(range(256)) * 4),
            ("空载荷", b""),
            ("UTF-8 文本", "产物内容 with mixed 中英文".encode("utf-8")),
        ):
            loc = store.put(payload, kind="blob", name="rt")
            got = store.retrieve(loc)
            if got == payload:
                _ok(f"put→retrieve 逐字节一致：{label}（{len(payload)} B）")
            else:
                _fail(out, f"{label} 往返不符：给 {len(payload)} B，回 {len(got)} B")


def check_metadata(out: list[str]) -> None:
    with _Store() as store:
        payload = b"metadata-probe" * 100
        loc = store.put(payload, kind="toy_kind", name="toy-name")
        meta = dict(store.describe(loc))
        required = ("kind", "name", "size", "sha256", "created_at")
        missing = [k for k in required if k not in meta]
        if missing:
            _fail(out, f"describe 缺字段 {missing}：{meta}")
        else:
            _ok(f"describe 元数据齐备：{sorted(required)}")
        if meta.get("kind") == "toy_kind" and meta.get("name") == "toy-name":
            _ok("kind/name 原样保管（核心不解释、不改写）")
        else:
            _fail(out, f"kind/name 被改写：{meta}")
        if meta.get("size") == len(payload):
            _ok(f"size 与载荷一致（{len(payload)} B）")
        else:
            _fail(out, f"size 不符：{meta.get('size')} ≠ {len(payload)}")
        want = hashlib.sha256(payload).hexdigest()
        if meta.get("sha256") == want:
            _ok("sha256 与内容独立算出的一致（元数据不撒谎）")
        else:
            _fail(out, f"sha256 不符：{meta.get('sha256')} ≠ {want}")
        again = store.describe(loc)
        if again is not meta and dict(again) == meta:
            _ok("describe 每次返回独立投影（改不坏内部记录）")
        else:
            _fail(out, "describe 返回了内部可变记录")


def check_unknown_locator(out: list[str]) -> None:
    with _Store() as store:
        good = store.put(b"x", kind="blob")
        bads = [
            ("从未发放", "artifact-999999"),
            ("路径形态", "artifacts/artifact-1.bin"),
            ("绝对路径形态", str(Path(tempfile.gettempdir()) / "artifact-1.bin")),
            ("另一种前缀", "$1"),
            ("空串", ""),
            ("非字符串", 1),
        ]
        for label, bad in bads:
            try:
                store.retrieve(bad)
                _fail(out, f"坏 locator（{label}）竟取回成功：{bad!r}")
            except MechaError as e:
                if e.kind == BAD_KIND:
                    _ok(f"坏 locator（{label}）fail loud 且 kind 正确")
                else:
                    _fail(out, f"坏 locator（{label}）kind 不符：{e.kind}")
            except Exception as e:  # noqa: BLE001 - 非 MechaError 即形状违规
                _fail(out, f"坏 locator（{label}）抛了非教学错误："
                           f"{type(e).__name__}: {e}")
        if store.exists(good) and not any(store.exists(b) for _, b in bads):
            _ok("exists：合规 locator 真、坏 locator 假（不抛错）")
        else:
            _fail(out, "exists 判定不符（好 locator 或坏 locator 判错）")
        # 盘上文件被外部删掉 ⇒ 引用失效也必须 fail loud（不静默返回空产物）
        victim = store.put(b"will-be-removed", kind="blob")
        for p in store._dir.glob(f"{victim}.*"):
            p.unlink()
        try:
            store.retrieve(victim)
            _fail(out, "产物文件被删后 retrieve 仍成功（静默返回坏数据）")
        except MechaError as e:
            if e.kind == BAD_KIND and not store.exists(victim):
                _ok("盘上产物被外部删除 ⇒ retrieve fail loud、exists 转假")
            else:
                _fail(out, f"删除后语义不符：kind={e.kind} exists={store.exists(victim)}")


def check_large_payload(out: list[str]) -> None:
    with _Store() as store:
        payload = bytes((i * 7 + 13) % 256 for i in range(ONE_MIB + 1234))
        loc = store.put(payload, kind="blob", name="big")
        got = store.retrieve(loc)
        if len(got) == len(payload) and got == payload:
            _ok(f"≥1MiB 大载荷往返逐字节一致（{len(payload)} B）")
        else:
            _fail(out, f"大载荷往返不符：给 {len(payload)} B，回 {len(got)} B")
        meta = store.describe(loc)
        if meta.get("size") == len(payload) \
                and meta.get("sha256") == hashlib.sha256(payload).hexdigest():
            _ok("大载荷元数据仍与内容一致（不是先截断再算）")
        else:
            _fail(out, f"大载荷元数据不符：size={meta.get('size')}")


def check_no_overwrite(out: list[str]) -> None:
    with _Store() as store:
        payloads = [f"payload-{i}".encode() * (i + 1) for i in range(6)]
        locs = [store.put(p, kind="blob", name=f"n{i}")
                for i, p in enumerate(payloads)]
        if len(set(locs)) == len(locs):
            _ok("同实例多次 put 得到互不相同的 locator")
        else:
            _fail(out, f"locator 有重复：{locs}")
        bad = [i for i, (l, p) in enumerate(zip(locs, payloads))
               if store.retrieve(l) != p]
        if not bad:
            _ok("每个 locator 都取回自己的载荷（互不覆盖）")
        else:
            _fail(out, f"{len(bad)} 个 locator 被覆盖：{bad}")
        metas = [store.describe(l)["name"] for l in locs]
        if metas == [f"n{i}" for i in range(6)]:
            _ok("每个 locator 的元数据与自身产物配对（sidecar 不串号）")
        else:
            _fail(out, f"元数据串号：{metas}")
        # 并发 put：locator 不撞号、内容不串（进程内一把锁串行化）
        n = 8
        with ThreadPoolExecutor(max_workers=n) as pool:
            clocs = list(pool.map(
                lambda i: store.put(f"concurrent-{i}".encode(), kind="blob"),
                range(n)))
        if len(set(clocs)) == n and all(
                store.retrieve(l) == f"concurrent-{i}".encode()
                for i, l in enumerate(clocs)):
            _ok(f"{n} 路并发 put：句柄不撞号、内容不串")
        else:
            _fail(out, f"并发 put 结果损坏：{clocs}")


def check_assembled_path(out: list[str]) -> None:
    """L7 对偶：经装配点的合规路径全绿（不误报）。"""
    with _with_software() as sw:
        loc = sw.artifacts.put(b"assembled", kind="blob", name="sw")
        if sw.artifacts.retrieve(loc) == b"assembled" and sw.artifacts.exists(loc):
            _ok("assemble 装配出的 store 可用（Software.artifacts 接上了）")
        else:
            _fail(out, "装配点产出的 store 不可用")
        landed = sw.layout.data_dir / "artifacts" / f"{loc}.bin"
        if landed.exists() and landed.read_bytes() == b"assembled":
            _ok("产物落在宿主自己的数据目录下（领域真值不出宿主目录）")
        else:
            _fail(out, f"产物未落在预期目录：{landed}")
        # 只读消费者：describe/exists 不改盘上字节数（无隐式迁移/重写）
        before = sorted(p.name for p in (sw.layout.data_dir / "artifacts").iterdir())
        sw.artifacts.describe(loc)
        sw.artifacts.exists(loc)
        after = sorted(p.name for p in (sw.layout.data_dir / "artifacts").iterdir())
        if before == after:
            _ok("describe/exists 是只读的（不产生额外文件）")
        else:
            _fail(out, f"读操作改动了盘上布局：{before} → {after}")
        try:
            sw.artifacts.retrieve("artifact-424242")
            _fail(out, "装配路径上坏 locator 竟成功（守卫不载重）")
        except MechaError as e:
            if e.kind == BAD_KIND:
                _ok("装配路径上坏 locator 同样 fail loud（L7 对偶）")
            else:
                _fail(out, f"装配路径坏 locator kind 不符：{e.kind}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out: list[str] = []
    print("Phase 8 产物契约判据：")
    check_opaque_locator(out)
    check_roundtrip_bytes(out)
    check_metadata(out)
    check_unknown_locator(out)
    check_large_payload(out)
    check_no_overwrite(out)
    check_assembled_path(out)
    if out:
        print(f"phase8_artifact_contract_selfcheck: FAIL（{len(out)} 条）")
        return 1
    print("phase8_artifact_contract_selfcheck: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
