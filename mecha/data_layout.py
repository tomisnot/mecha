"""数据目录布局：目录名由项目注入，库不猜（治 D-6）。

v1 把 ``.mecha`` 焊死在 resolve_root。v2 没有任何默认目录名——不给就报错，
错误消息教接入方怎么给（L4）。

## Known Limitations and Deferred Work

- 只解析"根 + 目录名"，不管理子布局（journal 文件、归档目录名等归 History
  与 gc 策略各自声明，见 format_version）。
- 不校验注入值的合法性（如绝对路径要求）：由宿主决定，库只拒绝空串。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import MechaError


@dataclass(frozen=True)
class DataLayout:
    """一台软件的数据目录：root 由宿主给，目录名由项目注入。"""

    root: Path
    dir_name: str

    @property
    def data_dir(self) -> Path:
        return self.root / self.dir_name


def resolve_data_dir(root: Path | str, dir_name: str | None) -> DataLayout:
    """不给 dir_name 就明确报错（不猜路径）。"""
    if not dir_name or not dir_name.strip():
        raise MechaError(
            "数据目录名未注入",
            kind="missing_data_dir",
            hint="resolve_data_dir(root, dir_name) 的 dir_name 必须由项目提供"
                 "（例如项目自己的数据目录名）；库不提供默认值",
        )
    if Path(dir_name).is_absolute() or "\\" in dir_name or "/" in dir_name:
        raise MechaError(
            f"数据目录名必须是相对单段名，收到 {dir_name!r}",
            kind="bad_data_dir",
            hint="dir_name 不能含路径分隔符；层级由 root 表达",
        )
    return DataLayout(Path(root), dir_name.strip())
