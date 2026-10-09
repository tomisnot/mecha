"""端口文件：运行期端口发现的单一实现（写 / 读 / 清，含陈旧文件的清理规则）。

下游宿主各自重实现过这一套（写一个裸端口文本、启动前清陈旧、收尾只删自己的），
而它封装的是**一条带安全论证的规则**，不是三个碰巧写法相同的函数：

- ``terminate()`` / ``taskkill /F`` **不跑** Python 的 ``finally``
  ⇒ "起时写、止时删"不能只靠收尾，必须在**启动前**也清一次陈旧文件。
- 启动前无条件清（``expected=None``）的安全性论证：调用方此时**已装配成功**
  （手握数据目录的写租约，见 ``write_lease``）⇒ 同一数据根不可能还有别的活宿主
  ⇒ 该文件必属死进程，删它不会误伤任何活着的主人。
- 收尾时按 ``expected`` **只删仍是自己写的那个端口**的文件
  ⇒ 不误删下一台权威刚写进去的新端口（交接窗口里两代权威先后写同一路径）。

**安全论证重复两次 = 两处可以各自漂移**，所以它归框架：库拥有这条规则，
宿主只拥有"文件叫什么"。

本模块**不持有任何文件名默认值**：端口文件叫什么、放在哪个根下，是宿主与它的
消费方（插件 / 启动编排 / 人）之间的发现约定，属宿主领域——库内零项目字面量是
既有纪律。调用方给路径，本模块只管规则。

## Known Limitations and Deferred Work

- 只有"一个端口一个文件"的形态。要一次暴露多个端口时由宿主各调一次（不引入
  描述符格式——"模式 / pid / 多个端口写进一个 JSON"是宿主自有的运行期描述符，
  属宿主，不进库）。
- 不抢锁、不探活 pid：``expected`` 语义只比较**文件内容**，不校验那个端口背后
  是否真有进程。真正的单写者不变量由 ``write_lease`` 的 OS 级独占锁保证，本模块
  只负责"别删错文件"这一半。
- 所有 ``OSError`` 静默：端口文件是**发现辅助**，不是真值。读不到返回 ``None``，
  调用方按"未知"处理（不假装知道）；写失败不抛——起不来服务的原因在 bind 那一步，
  不在这里。
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["read_port", "write_port_file", "clear_port_file"]


def read_port(path: str | Path | None) -> int | None:
    """读裸端口；路径为 ``None``、文件不存在、空内容、非数字一律返回 ``None``。

    「非数字也是 ``None``」是刻意的：这里回答的是"端口是多少"，不是"文件合不
    合法"——损坏的发现文件应当被当成"不知道"，由调用方决定要不要清掉重写。
    """
    if path is None:
        return None
    try:
        raw = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(raw) if raw.isdigit() else None


def write_port_file(path: str | Path | None, port: int) -> None:
    """写裸端口（纯十进制文本，无换行以外的装饰）。``path`` 为 ``None`` 则什么都不做。"""
    if path is None:
        return
    try:
        Path(path).write_text(str(int(port)), encoding="utf-8")
    except OSError:
        pass


def clear_port_file(path: str | Path | None, expected: int | None = None) -> None:
    """删端口文件。

    ``expected=None`` → **无条件删**：调用时机是**启动前**清陈旧文件，安全性靠
    "已持写租约 ⇒ 同根无别的活宿主 ⇒ 文件必属死进程"（见模块 docstring）。

    ``expected=<端口>`` → **只在文件内容仍是该端口时才删**：调用时机是**收尾**，
    不可误删接管者刚写的新端口。
    """
    if path is None:
        return
    target = Path(path)
    try:
        if not target.exists():
            return
        if expected is not None:
            raw = target.read_text(encoding="utf-8").strip()
            if raw and raw != str(int(expected)):
                return
        target.unlink()
    except OSError:
        pass
