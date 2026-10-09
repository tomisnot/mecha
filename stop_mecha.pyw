# -*- coding: utf-8 -*-
"""⭐ **双击这个 = 把在跑的那个机甲收尾停掉**（无控制台）。

## 为什么需要它（用户实测问到的）

双击 `start_mecha.pyw` 起的是一个**常驻 launcher**（它持有面板 + supervisor + 各软件 + dsh）。
⚠ **关掉浏览器【不会】停它** —— 浏览器只是**客户端**，关标签页只等于"断开连接"。

而无黑框入口下**没有控制台** ⇒ 你既看不到它、也没有 Ctrl+C；再双击 `start_mecha.pyw`
只会得到一句"已经有一个 supervisor 在跑"。⇒ ⚠ 原来的唯一出路是
"重新打开面板点『收尾退出』"或**去任务管理器强杀**（⚠ 强杀**跳过收尾**）。

⇒ 这个文件就是那条**缺的**路：**双击 = 请它收尾**，走的是**面板自己那条 `POST /shutdown`**
（与「收尾退出」按钮**同一条路**）⇒ 收尾语义完全一致（逆序停 child、清端口文件）。

## 它做什么

1. 读 `<本目录>/_mecha_runtime/.page-port`（launcher 起面板时落的端口文件）；
2. 打一次 `POST /shutdown`；
3. **弹一个消息框**告诉你成没成（⚠ 无控制台 ⇒ 结果必须**看得见**）。

⚠ 它**不**自己杀进程：那是 launcher 的职责（它才知道有哪些 child、各自的收尾顺序）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ENTRY_DIR = Path(__file__).resolve().parent


def _tell(title: str, msg: str) -> None:
    """⚠ 无控制台 ⇒ 结果必须用**原生消息框**说出来（失败也不许再抛）。"""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, msg, title, 0x40)   # MB_ICONINFORMATION
    except Exception:                                              # noqa: BLE001
        pass


def main() -> int:
    sys.path.insert(0, str(ENTRY_DIR))
    from mecha.launch import _request_stop, RUNTIME_DIR_NAME

    rc = _request_stop(Path.cwd() / RUNTIME_DIR_NAME)
    if rc == 0:
        _tell("机甲已收尾", "已经请那个常驻 launcher 收尾退出。\n\n"
                            "（逆序停掉 dsh 与各软件，并清掉端口文件）")
    else:
        _tell("没有在跑的机甲",
              "没找到在跑的 launcher（或它已经不在了）。\n\n"
              "若你确定它还在，去任务管理器结束那个 "
              "`pythonw.exe`（命令行含 start_mecha.pyw）。")
    return rc


if __name__ == "__main__":
    sys.exit(main())