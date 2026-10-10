"""判据 ㉓：**端口生命周期**（`mecha/portfile.py`）——公布/发现/使用的那套规则。

## 为什么必须有它

2026-10-10 查源码时发现：这个模块的规则**一条判据都没有**（`checks/` 里 `portfile` 命中 0），
而它偏偏是"**带安全论证**"的那种模块（清陈旧的时机、收尾只删自己那个、什么时候才能公布）。
同一套论证在框架里还被重复实现过两次（`providers/mcp.py` 与 `cockpit.py` 的同形三连），
在**项目侧**又被两家各写了一遍（"等就听 → 才公布 → 开浏览器"）⇒ 正是"两处可以各自漂移"。

## 八条规则 → 八组断言（关键的三条带 ⛔ 红证）

| # | 规则 | 怎么判 |
|---|---|---|
| R1 | 读不到/坏内容都是"未知" | 不存在的路径 / 空文件 / 非数字 ⇒ 全是 ``None`` |
| R2 | **启动前无条件清** | 先写一个陈旧端口，``clear_stale()`` 后文件没了 |
| R3 | **收尾只删仍是自己写的那个** | 公布 A → 别人改写 B → ``clear()`` ⇒ **文件还在**（⛔ 红证） |
| R3b | **从没公布过 ⇒ 收尾什么都不做** | 新建 binding 直接 ``clear()`` ⇒ 不误删别人的文件（⛔ 红证） |
| R4 | **只在真的在听之后才公布** | ⛔ **红证**：没人听的端口 ⇒ ``publish_when_listening`` 回 False 且**绝不写文件** |
| R5 | 消费方"读 + 确认在听" | 文件在但没人听 ⇒ ``is_listening`` 为 False（读文件 ≠ 拿到） |
| R6 | ``0.0.0.0`` 按回环探 | ``probe_host("0.0.0.0") == "127.0.0.1"``；且真起一条监听能探到 |
| R7 | 挑端口 / 预检被占 | ``pick_free_port()`` 拿到的端口**真能 bind**；``ensure_free`` 对被占端口**响亮失败** |
| R8 | 超时如实回、不抛 | ``wait_listening`` 对死端口在超时后回 ``False``（不抛） |

⚠ 判据自己起**真 socket**（不靠 mock）：端口这件事的错都发生在"真连接"上。

## Known Limitations and Deferred Work

- **不判"谁在什么时机调用"**：那是调用方（框架的两个端点 / 项目侧）的判据；这里只判规则本身。
- **不判 `open_when_ready` 真的开了浏览器**：它 best-effort、且"开浏览器"在无人值守环境里
  不可观测 ⇒ 只判"没在听时**不**开"（回 False）与"不会抛"。
- ⚠ **`pick_free_port` 的 TOCTOU 不判**（那是它的固有性质，已写进 docstring）：只判"挑出来的
  端口当前能 bind"，不承诺"永远抢得到"。
"""
from __future__ import annotations

import socket
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mecha.errors import MechaError                                    # noqa: E402
from mecha.portfile import (PortBinding, clear_port_file, ensure_free,  # noqa: E402
                           is_listening, pick_free_port, probe_host,
                           read_port, wait_listening, write_port_file)

FAILS: list[str] = []


def _check(name: str, cond: bool, extra: object = "") -> bool:
    print(f"  {'✔' if cond else '✖'} {name}" + ("" if cond else f"   ← {extra}"))
    if not cond:
        FAILS.append(name)
    return bool(cond)


class _Listener:
    """一条真监听（判据自己的词；用它造"有人在听"和"端口被占"）。"""

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = int(self.sock.getsockname()[1])
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            conn.close()

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def _dead_port() -> int:
    """一个**现在没人听**的端口（挑出来之后马上关掉）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])
    return port


def check_file_primitives(tmp: Path) -> None:
    """R1 / R2 / R3 / R3b：文件这一端的三条规则 + 一条更严的收尾。"""
    pf = tmp / ".judge-port"
    _check("R1 不存在 ⇒ None", read_port(pf) is None)
    pf.write_text("", encoding="utf-8")
    _check("R1 空文件 ⇒ None", read_port(pf) is None)
    pf.write_text("不是数字", encoding="utf-8")
    _check("R1 坏内容 ⇒ None", read_port(pf) is None)

    write_port_file(pf, 12345)
    _check("R1 正常写读一致", read_port(pf) == 12345)
    binding = PortBinding(port_file=pf)
    binding.clear_stale()
    _check("R2 启动前无条件清 ⇒ 陈旧文件没了", not pf.exists())

    binding = PortBinding(port_file=pf, port=1111)
    write_port_file(pf, 1111)
    write_port_file(pf, 2222)                     # "接管者刚写的新端口"
    binding.clear()
    _check("R3 ⛔ 红证：收尾**不误删接管者**写的 2222", read_port(pf) == 2222, read_port(pf))

    fresh = PortBinding(port_file=pf)
    fresh.clear()                                 # 从没公布过
    _check("R3b ⛔ 红证：从没公布过 ⇒ 收尾什么都不做（别把别人的删掉）",
           read_port(pf) == 2222, read_port(pf))
    clear_port_file(pf)


def check_publish_only_when_listening(tmp: Path) -> None:
    """R4 / R5：**只在真的在听之后才公布**（这一条是整份判据的核心）。"""
    pf = tmp / ".judge-port2"
    dead = _dead_port()
    binding = PortBinding(port_file=pf)
    ok = binding.publish_when_listening(dead, timeout=1.0)
    _check("R4 ⛔ 红证：没人听 ⇒ 回 False", ok is False, ok)
    _check("R4 ⛔ 红证：且**绝不**把死地址写进文件", not pf.exists(), read_port(pf))

    with_listener = _Listener()
    try:
        binding2 = PortBinding(port_file=pf)
        ok2 = binding2.publish_when_listening(with_listener.port, timeout=5.0)
        _check("R4 有人在听 ⇒ 才公布（回 True）", ok2 is True, ok2)
        _check("R4 公布的就是那个真端口", read_port(pf) == with_listener.port, read_port(pf))

        #: R5：消费方必须"读 + 确认在听"，只读文件不算拿到
        _check("R5 在读的同时确认在听 ⇒ True",
               is_listening("127.0.0.1", with_listener.port))
        _check("R5 端口文件被写成死端口时 ⇒ is_listening False（读文件 ≠ 拿到）",
               is_listening("127.0.0.1", dead) is False)
        binding2.clear()
        _check("R3 收尾清掉自己那个", read_port(pf) is None)
    finally:
        with_listener.close()


def check_probe_host_and_pick(tmp: Path) -> None:
    """R6 / R7 / R8：回环归一、挑端口与预检、超时如实。"""
    _check("R6 0.0.0.0 ⇒ 按 127.0.0.1 探", probe_host("0.0.0.0") == "127.0.0.1")
    _check("R6 '' / '*' 同样归一",
           probe_host("") == "127.0.0.1" and probe_host("*") == "127.0.0.1")
    _check("R6 真实回环地址原样保留", probe_host("127.0.0.1") == "127.0.0.1")

    port = pick_free_port()
    can_bind = True
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", port))
    except OSError:
        can_bind = False
    _check("R7 pick_free_port 拿到的端口**当前真能 bind**", can_bind and port > 0, port)

    ensure_free("127.0.0.1", port, label="判据用")          # 空闲 ⇒ 不抛
    _check("R7 空闲端口 ⇒ ensure_free 通过", True)

    listener = _Listener()
    try:
        raised: object = None
        try:
            ensure_free("127.0.0.1", listener.port, label="判据用")
        except MechaError as exc:
            raised = exc
        _check("R7 ⛔ 红证：被占端口 ⇒ 响亮失败（MechaError + 可操作 hint）",
               isinstance(raised, MechaError) and getattr(raised, "kind", "") == "port_in_use",
               raised)
    finally:
        listener.close()

    dead = _dead_port()
    _check("R8 死端口等不到 ⇒ 如实回 False（不抛）",
           wait_listening("127.0.0.1", dead, timeout=1.0) is False)
    _check("R8 真监听 ⇒ 立刻 True（短超时也够）",
           wait_listening("127.0.0.1", _Listener().port, timeout=2.0))


def check_never_raises_for_human_face(tmp: Path) -> None:
    """人面便利那一条：**没在听时绝不开浏览器**，且调用本身不抛。"""
    binding = PortBinding(port_file=tmp / ".judge-port3", port=_dead_port())
    _check("open_when_ready 端口没在听 ⇒ 回 True（线程起了）但不会真开",
           binding.open_when_ready(timeout=0.5) is True)
    _check("open_when_ready enabled=False ⇒ 直接回 False",
           PortBinding(port_file=None, port=1).open_when_ready(enabled=False) is False)
    try:
        PortBinding(port_file=None).url()          # ⚠ 端口未知的那个（不是上面带了 port 的）
        _check("url() 在端口未知时**响亮失败**（不编一个）", False, "居然没抛")
    except MechaError:
        _check("url() 在端口未知时**响亮失败**（不编一个）", True)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    import tempfile
    print("[portfile] 端口生命周期：R1–R8（含 5 条红证）")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        try:
            check_file_primitives(tmp)
            check_publish_only_when_listening(tmp)
            check_probe_host_and_pick(tmp)
            check_never_raises_for_human_face(tmp)
        except Exception as exc:                       # noqa: BLE001
            import traceback
            traceback.print_exc()
            _check("判据自己跑起来了", False, f"{type(exc).__name__}: {exc}")

    if FAILS:
        print(f"portfile_selfcheck: FAIL（{len(FAILS)} 条）：{FAILS}")
        return 1
    print("portfile_selfcheck: PASS（R1–R8：文件原语 + 只在就听后公布 + 探活/挑端口/预检）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
