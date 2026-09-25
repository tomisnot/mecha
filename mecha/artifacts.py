"""Artifact Provider：宿主产物落盘 + 不透明定位符（总纲 §5.2⑤ Provider 层）。

Definition：宿主把**自己的大对象/运行产物**（任意 bytes）交给本层，拿回一个
**不透明定位符**；此后用同一个 locator 取字节、读元数据、问存在。核心
**不解释** ``kind``/``name``（那是宿主领域分类），也不暴露真实路径——宿主
数据目录的布局是宿主的私事。

与 Spill 的区别（两者都产"不透明定位符"，但不是一件事）：

- **Spill** 处理的是**核心自己产生的文本**：回执/日志太大要截断落盘，判断
  依据是体积阈值（``should_spill``），载荷是 str，属于"回程投递"。
- **Artifact** 处理的是**宿主的产物**：核心从不知道它是什么、有多大、什么
  格式，只负责原样保存与按引用取回，载荷是 bytes，属于"产物托管"。
  把两者合并会逼核心理解宿主产物语义（越界），也会让阈值语义污染宿主产物。

## Known Limitations and Deferred Work

- **不做删除 / GC**（写进 ADR 与本节）：删除策略是宿主的保留策略（保留几个
  版本、什么时候清、清完历史里的引用怎么办），核心不猜。代价是磁盘只增不减，
  由宿主的运维策略兜底。
- 内部映射 ``_map`` 是**进程内**的：进程重启后新实例不知道旧 locator。跨重启
  找回需要扫描 ``artifacts/`` 目录按 id 重建索引，等第二个宿主真实要跨重启时
  再做（n=1 推迟）；元数据 sidecar 已落盘，重建所需的全部字段都在。
- 并发只在**进程内**用一把锁串行化；跨进程多写宿主仍靠既有写租约
  （``write_lease``，与 History 同一约束）。
- locator 是 ``artifact-<n>`` 形式的不透明句柄：**不承诺稳定跨实例**，宿主
  不得解析、不得自行拼路径（核心无"按路径取"的入口）。
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
import threading
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from .data_layout import DataLayout
from .errors import MechaError

#: locator 形状：``artifact-<正整数>``。核心只用来做**格式**校验（拒绝路径、
#: 拒绝任意字符串），不用它推出路径——路径由进程内映射给出（不透明）。
LOCATOR_RE = re.compile(r"^artifact-(\d+)$")

#: locator 前缀。给宿主日志/UI 一个可读形状，同时**不含路径分隔符**。
LOCATOR_PREFIX = "artifact-"


class ArtifactStore:
    """Artifact Definition（具体服务，不是接口——总纲 §5.2③）。"""

    def put(self, payload: bytes, *, kind: str = "", name: str = "") -> str:
        """存一份宿主产物，返回不透明 locator。``kind``/``name`` 是宿主标签，
        核心只原样保管、不解释。"""
        raise NotImplementedError

    def retrieve(self, locator: str) -> bytes:
        """按 locator 取回**原始字节**（逐字节一致，无隐式编解码）。"""
        raise NotImplementedError

    def describe(self, locator: str) -> Mapping[str, object]:
        """按 locator 读元数据（kind/name/size/sha256/created_at）。"""
        raise NotImplementedError

    def exists(self, locator: str) -> bool:
        """locator 是否指向一份**可取回**的产物（数据与元数据都在）。"""
        raise NotImplementedError


class LocalArtifactStore(ArtifactStore):
    """local 出厂默认：写文件 + 同名 JSON 元数据，回 ``artifact-<n>`` 句柄。

    盘上布局（**宿主的私事，消费者不许假设**）：``<data_dir>/artifacts/``
    下 ``artifact-<n>.bin``（字节）与 ``artifact-<n>.json``（元数据）。
    """

    def __init__(self, layout: DataLayout) -> None:
        self._dir = layout.data_dir / "artifacts"
        self._ids = itertools.count(1)
        self._map: dict[str, Path] = {}
        # 进程内串行化：locator 分配与落盘在同一临界区（与 JobRegistry 同款）。
        self._lock = threading.Lock()

    def put(self, payload: bytes, *, kind: str = "", name: str = "") -> str:
        data = bytes(payload)                    # 拒收 str/内存视图，形状单一
        with self._lock:
            self._dir.mkdir(parents=True, exist_ok=True)
            n = next(self._ids)
            # 同一目录被新实例接管时，跳过已被占用的编号（不覆盖既有产物）。
            while self._bin_path(n).exists() or self._meta_path(n).exists():
                n = next(self._ids)
            locator = f"{LOCATOR_PREFIX}{n}"
            self._bin_path(n).write_bytes(data)
            meta = {
                "locator": locator,
                "kind": str(kind),
                "name": str(name),
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._meta_path(n).write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            self._map[locator] = self._bin_path(n)
        return locator

    def retrieve(self, locator: str) -> bytes:
        n = self._resolve_number(locator)
        self._require_pair(n, locator)
        return self._bin_path(n).read_bytes()

    def describe(self, locator: str) -> Mapping[str, object]:
        n = self._resolve_number(locator)
        self._require_pair(n, locator)
        meta = json.loads(self._meta_path(n).read_text(encoding="utf-8"))
        if not isinstance(meta, dict):
            raise MechaError(
                f"locator {locator!r} 的元数据形状非法（不是对象）",
                kind="bad_artifact_locator",
                hint="元数据由 put 写出；手改过的 sidecar 不受支持",
            )
        return dict(meta)

    def exists(self, locator: str) -> bool:
        """不可取回的 locator 一律 False（**不抛错**——问存在不是问错）。

        格式非法的 locator 也返回 False：它显然不存在，且调用方通常是在
        "这个引用还有效吗"的语境里问（抛错会逼出 try/except）。
        """
        try:
            n = self._resolve_number(locator)
        except MechaError:
            return False
        return self._bin_path(n).exists() and self._meta_path(n).exists()

    # --- 内部：路径与校验（绝不对外暴露） -----------------------------------

    def _require_pair(self, n: int, locator: str) -> None:
        """确认编号对应的产物与元数据**成对存在**。

        "盘上文件被外部删掉"与"未知 locator"都 fail loud
        （kind=bad_artifact_locator）——静默返回空产物会把"引用丢了"变成
        "产物是空的"，是最坏的一类失败。
        """
        if not self._bin_path(n).exists() or not self._meta_path(n).exists():
            raise MechaError(
                f"locator {locator!r} 的产物已不在盘上",
                kind="bad_artifact_locator",
                hint="产物文件与元数据 sidecar 必须成对且不可外部改动；"
                     "请宿主重新 put 一份",
            )

    def _resolve_number(self, locator: object) -> int:
        """把 locator 解析成编号；**只认格式与进程内映射**，不认路径。"""
        if not isinstance(locator, str) or not LOCATOR_RE.match(locator):
            raise MechaError(
                f"locator {locator!r} 形状非法",
                kind="bad_artifact_locator",
                hint=f"locator 只能由 put 返回（形如 {LOCATOR_PREFIX}<n>）；"
                     f"不得含路径分隔符，也不得自行拼接",
            )
        if locator not in self._map:
            raise MechaError(
                f"locator {locator!r} 在本实例内无对应产物",
                kind="bad_artifact_locator",
                hint="locator 由 put 返回；进程重启后旧 locator 不可用"
                     "（跨重启找回见模块 Known Limitations）",
            )
        return int(LOCATOR_RE.match(locator).group(1))

    def _bin_path(self, n: int) -> Path:
        return self._dir / f"artifact-{n}.bin"

    def _meta_path(self, n: int) -> Path:
        return self._dir / f"artifact-{n}.json"
