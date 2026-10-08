"""站点声明：**一个 site = 一组软件 + 一个 dsh**（框架不持有任何项目字面量）。

## 这是什么

从 mecha 启动 = **永远是 AI 组**（没有"启动模式"这个东西）。项目选择在**浏览器选择页**
上勾，默认全勾；纯 GUI 用户不走 mecha。⇒ 需要一个**声明**告诉 supervisor：
"这台机器上有哪些软件、各自怎么起、什么时候算就绪、dsh 怎么起"。

**声明从哪来**：按约定发现 **``mecha_site.py``**、里面暴露模块级 ``SITE: Site``
（见 :func:`launch.load_sites`）。**框架只认形状，不认项目**——名字、路径、端口文件名
全在声明里（§5.4「写机制不写分类」）。

## 字段为什么长这样（每条都对着一条既有纪律）

- ``Service.data_root``：**各软件自己的写租约根**（``write_lease`` 按数据根独占）。
  它同时是"**换腿互斥**"的依据：软件本体与它的 human adapter 同根 ⇒ 不可能同时跑。
- ``Service.manual``：**"接手动" = 某项目提供了 human adapter**（§4.5 既有词，
  **不造新词**）。它的类型是 ``ChildSpec`` 而不是裸 argv——因为 GUI 也要**探活 / 记日志 /
  能收尾**（Qt 起不来要能报出来）；裸 argv 会逼步骤 3 再写第二套 spawn。
- ``DSHSpec.command`` 是 ``tuple`` 而不是单个字符串：dsh 有时要经 ``cmd /c`` 起
  （EL/ML 的实测形态），有时是裸可执行——由声明决定，框架不猜。
- ``Strategy`` 的取值与 OTP **同名**（``one_for_one`` / ``rest_for_one`` / ``one_for_all``）；
  ``auto_shutdown`` 同理。本步不驱动编排（步骤 2 才用），但**先声明清楚**免得步骤 2 改签名。

## Known Limitations and Deferred Work

- **不含 aggregator**：``Site`` 里没有聚合端点的位置——那是步骤 2 的事。修正 1 要求的
  "启动顺序 + 就绪后钩子"已经由 :class:`launch.StartupPlan` 与 ``Supervisor`` 的钩子留好出口。
- 一个站点只声明**一个 dsh**：多 dsh 的形态没有实测依据（n=1 不预建）。
- 不做"服务之间的依赖图"：实测只有一条链 ``svc* → aggregator → dsh``，
  用**显式顺序**表达即可，引入通用 DAG 是给不存在的需求造机制。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from collections.abc import Mapping

from .errors import MechaError
from .supervise import ChildSpec, ReadySpec

#: 服务名的形状（它将来也是**工具前缀**，所以现在就要约束住）。
_SERVICE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


@dataclass(frozen=True)
class Service:
    """一个软件（"各自一条脊椎"）。"""

    #: 全局唯一；将来也是工具前缀（``mcp__<id>__<tool>``）。
    id: str
    #: 选择页上的一句话简介（人看的）。
    brief: str
    argv: tuple[str, ...]
    cwd: Path
    ready: ReadySpec
    #: 该软件**自己的**写租约根（不跨软件共享）⇒ 也是"换腿互斥"的依据。
    data_root: Path
    #: **"接手动" = 本项目的 human adapter**（§4.5 既有词）；``None`` = 该项目没有。
    #: ⚠ 它是"**换腿时要起的那个 child**"，与本体**互斥**（同一数据根同一时刻一个写宿主）。
    manual: ChildSpec | None = None
    default_on: bool = True
    env: Mapping[str, str] = field(default_factory=dict)
    #: 日志/child 名；``None`` ⇒ 用 ``id``。
    log_id: str | None = None

    def child(self) -> ChildSpec:
        """把声明翻成 supervisor 要的 :class:`ChildSpec`（一处翻译，别处别抄）。"""
        return ChildSpec(
            id=self.log_id or self.id,
            argv=self.argv, cwd=self.cwd, env=dict(self.env),
            ready=self.ready, data_root=self.data_root,
        )

    def validate(self) -> None:
        if not _SERVICE_ID_RE.match(self.id):
            raise MechaError(
                f"服务 id 不合法：{self.id!r}",
                kind="bad_service_id",
                hint="id 将来是工具前缀 ⇒ 必须 ^[A-Za-z][A-Za-z0-9_-]*$",
            )
        if self.manual is not None:
            want = f"{self.id}:manual"
            if self.manual.id != want:
                raise MechaError(
                    f"human adapter 的 child id 必须是 {want!r}，收到 {self.manual.id!r}",
                    kind="bad_manual_id",
                    hint="约定 id 形如 <svc_id>:manual ⇒ 换腿时能机械认出这对腿",
                )
            if self.manual.data_root != self.data_root:
                raise MechaError(
                    f"{self.id} 与它的 human adapter 数据根不同",
                    kind="manual_root_mismatch",
                    hint="换腿的前提是**同一个**数据根（账不断）；不同根意味着它们本可同时跑",
                )


@dataclass(frozen=True)
class DSHSpec:
    """dsh（AI 的宿主）怎么起。字段全是声明，框架不猜。"""

    #: ```("cmd", "/c", "dsh", "web", ...)``` 这类 argv 片段；第一段按 PATH 解析。
    command: tuple[str, ...]
    cwd: Path
    #: ``--patch`` 列表（**顺序即命令行顺序**）。
    patches: tuple[Path, ...] = ()
    #: ⚠ **端口偏好**，不是"实际端口"：实际端口一律**从 bind 结果回读**
    #: （ML 的 ``:762`` 把预选值写进运行期描述符，就是这条的反例）。
    port_preference: int = 3081
    port_tries: int = 10
    #: 插件产物（就绪前置检查用）：**不存在就响亮失败**，别等 dsh 起来报一个看不懂的错。
    plugin_artifacts: tuple[Path, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    ready: ReadySpec | None = None
    stop_timeout: float = 8.0
    #: dsh **不持**软件的写租约（它不碰域状态）：显式 ``None`` 表明"它不该被判成写宿主"。
    data_root: None = None

    def argv(self) -> tuple[str, ...]:
        """把声明拼成命令行（``--patch`` 与端口偏好在这里落成 argv）。"""
        out = list(self.command)
        for p in self.patches:
            out += ["--patch", str(p)]
        return tuple(out)

    def validate(self) -> None:
        if not self.command:
            raise MechaError("DSHSpec.command 为空", kind="empty_dsh_command",
                             hint="至少要有第一段（可执行）")
        missing = [str(p) for p in self.plugin_artifacts if not Path(p).exists()]
        if missing:
            raise MechaError(
                f"dsh 的插件产物不存在：{missing}",
                kind="plugin_artifact_missing",
                hint="先在 dsh 目录跑构建（产物是 dsh 侧插件的入口，少了它 dsh 起不来）",
            )


class StrategyMode(str, Enum):
    """重启策略（与 OTP **同名**，不造新词）。"""

    ONE_FOR_ONE = "one_for_one"
    REST_FOR_ONE = "rest_for_one"
    ONE_FOR_ALL = "one_for_all"


class AutoShutdown(str, Enum):
    """整站何时自动收尾（步骤 2 用；本步只声明）。"""

    NEVER = "never"
    ANY_SIGNIFICANT = "any_significant"
    ALL_SIGNIFICANT = "all_significant"


@dataclass(frozen=True)
class Strategy:
    mode: StrategyMode = StrategyMode.ONE_FOR_ONE
    max_restarts: int = 5
    period_s: float = 60.0
    auto_shutdown: AutoShutdown = AutoShutdown.NEVER


@dataclass(frozen=True)
class Site:
    """一个站点：一组软件 + 一个 dsh + 一份策略。"""

    name: str
    services: tuple[Service, ...]
    dsh: DSHSpec
    strategy: Strategy = Strategy()

    def validate(self) -> None:
        if not self.name.strip():
            raise MechaError("Site.name 不能为空", kind="empty_site_name",
                             hint="站点名用于状态页与日志归属")
        if not self.services:
            raise MechaError(
                "Site.services 为空：没有任何软件可起",
                kind="empty_site",
                hint="站点声明至少要有一条 Service（空站点不是合法状态，不许静默起个空站）",
            )
        seen: set[str] = set()
        for svc in self.services:
            svc.validate()
            if svc.id in seen:
                raise MechaError(
                    f"服务 id 重复：{svc.id!r}",
                    kind="duplicate_service_id",
                    hint="id 是全局唯一的（将来也是工具前缀）",
                )
            seen.add(svc.id)
            if svc.manual is not None and svc.manual.id in seen:
                raise MechaError(
                    f"human adapter id 与服务 id 撞了：{svc.manual.id!r}",
                    kind="duplicate_child_id",
                    hint="约定 manual 叫 <svc_id>:manual ⇒ 不会撞",
                )
        seen.update(s.id for s in self.services if s.manual is not None)
        self.dsh.validate()
