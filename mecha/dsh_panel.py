"""监控面板：把**声明**（``Service.monitor``）翻成 dsh 侧那一份参数 + **框架自己的插件 patch**。

三层架构 §7.3（尤其 §7.3.3 命名定案 / §7.3.6 字段形状 / **§7.3.8 施工补记**）定案：

| 项 | 定案 |
|---|---|
| 参数的**唯一家** | ``mecha_site.py`` 的声明（``Service.monitor``）⇒ 框架**只生成与转交**，不解释 |
| **谁带框架的插件** | ⭐ **框架自己的 patch**（``mecha-panel.patch.yml``）—— 否则"新项目还得自己挂框架插件" = **没预付** |
| 参数怎么到浏览器 | ⚠ **env 到不了 client 半**（它在浏览器里，读不到 ``process.env``）⇒ ``env → 框架的 host 插件 → 同源只读路由 → client 半 fetch``（§7.3.8 第 1 条，据实更正） |
| ⚠ **端口号** | **不进这份参数**（它给浏览器、也可能进日志）⇒ 端口一律由**读端口文件现取** |

## 为什么路由常量有"两个家"（以及为什么这不算破纪律）

``PANEL_SERVICES_ROUTE`` 有**两处**文本：本模块（权威）与资产 ``panel/services.ts``
（浏览器侧的镜像）——⚠ 浏览器**只能**从自己那份 bundle 里拿字面量，读不到 Python。
⇒ 按本仓对跨语言常量的既有口径（``.mcp-port`` 同族）：**一个家 + 一条钉住相等的判据**
（``checks/phase13_panel_decl_selfcheck.py`` 读那份 TS 并断言相等）。
⚠ 写歪的症状是"浏览器 fetch 404 ⇒ 一个 tab 都没有"——那正是最该被钉住的一类假绿。

## Known Limitations and Deferred Work

- ⭐ **项目的监控地址路由已上提进框架**（2026-10-09）：框架的 host 插件按
  `MECHA_PANEL_PORT_FILES`（**绝对**端口文件路径）**替项目注册那条路由并现取端口**
  ⇒ 新项目**连 host 半都不用写**（§7.3.8 末段当初登记的下一步，已落地）。
  ⚠ 因此项目侧**不该**再自己注册同一条 `routePath`（重复注册同一路径是错的）。
- ⭐ **项目的 dsh host 半现在只是"空载体"**（2026-10-10 用户裁决 = 方案 A，见三层架构 §7.2）：
  工具面**只有一条腿**——框架的聚合端点。项目侧的自愈 MCP 桥（资产的 `mcp-bridge.ts` /
  `host/mount-host-plugin.ts` / `register-tools.ts` / `mcp-session-http.ts` / `config.ts`）
  **整块删除**：⚠ 代价是"不经 mecha 单独跑 dsh"时**不再出现任何软件工具**（用户已认）。
  ⇒ 载体保留的唯一理由是 **dsh 的加载形状**（一条 patch entry 带一个包，
  client 半靠它进浏览器）；聚合端点和工具热同步验证实际调用路径。
- **不校验** 声明里的 ``route_path`` 是否真的有人注册（那是**运行期事实**，只有真 dsh 知道）
  ⇒ 面板上那种"tab 在但取数 404"由 tab 自己如实报（资产既有语义）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

from .errors import MechaError
from .site import ExtraPage, MonitorSpec, Service, Site

#: ⭐ **那一个**同源只读路由：框架的 host 插件注册它、client 半 fetch 它。
#: ⚠ 资产的 ``panel/services.ts`` 是它的**镜像**（判据钉相等），改一处必须改两处。
PANEL_SERVICES_ROUTE = "/mecha/panels"

#: 递给框架自己那份 dsh 插件的两个变量（§7.3.8 第 2 条）。
PANEL_ROUTE_ENV = "MECHA_PANEL_ROUTE"
PANEL_SERVICES_ENV = "MECHA_PANEL_SERVICES"
#: ⭐ **第三个变量：只给框架的 host 插件**（node 侧），`{svc_id: 端口文件**绝对**路径}`。
#: ⚠ **它绝不进给浏览器的那份参数**（那份里只有 `portFile` 文件名）：
#: 绝对路径是本机信息，浏览器不需要 —— 而 host 插件**需要**它才能**替项目取端口**
#: （§7.3.8 末段的上提：新项目连 host 半那条地址路由都不用写）。
#: ⚠ 为什么必须是**绝对**路径：dsh 的 `cwd` 只有一个（§1.1.1 的已知限制）
#: ⇒ 相对路径在 dsh 进程里会按**它的** cwd 解析，那是另一个项目的地盘。
PANEL_PORT_FILES_ENV = "MECHA_PANEL_PORT_FILES"

#: 框架自己的 patch 与它带上的插件（都在资产目录里，**磁盘上只有一份**）。
ASSET_DIR_NAME = "dsh-panel"
PATCH_FILENAME = "mecha-panel.patch.yml"
HOST_PLUGIN_REL = "host-plugin/mecha-panel-host.mjs"
#: patch 里那个条目的 id（dsh 的行身份；⚠ 与别人不许撞）。
PANEL_PLUGIN_ENTRY_ID = "mecha-panel-host"


def asset_dir() -> Path:
    """资产目录（框架仓里的 ``mecha/dsh-panel/``）——⭐ **绝对路径**（§1.1.1）。"""
    return Path(__file__).resolve().parent / ASSET_DIR_NAME


def framework_patch_path() -> Path:
    """⭐ 框架自己的 patch（**静态文件**，不是生成物）。

    ⚠ 为什么静态而不是像聚合端点 overlay 那样生成：patch 里的 ``name:`` 是**相对路径**，
    而 dsh 把 rel 名**锚在 patch 文件自己所在目录**（``anchorInsertedPluginNames``）
    ⇒ 这个文件住在资产旁边就能指到资产里的插件，**无需按机器生成**。
    ⚠ 顺带避开一个真陷阱：生成物必须写在 ``_mecha_runtime/``，而 ``--dry-run``
    **零副作用**（判据 ⑫）⇒ 生成会在 dry-run 里造目录。静态文件没有这个问题。
    """
    return asset_dir() / PATCH_FILENAME


def framework_plugin_artifact() -> Path:
    """要进 ``plugin_artifacts`` 的那份产物（**不存在 ⇒ 响亮失败**，别等 dsh 起不来）。"""
    return asset_dir() / HOST_PLUGIN_REL


def panel_kind(svc_id: str) -> str:
    """⭐ §7.3.3：**每个服务一个 `kind`**（契约拒绝**同 tier 同 kind** 二次注册）。"""
    return f"mecha:{svc_id}"


def panel_tab_id(svc_id: str) -> str:
    """⭐ §7.3.3：**每个服务一个 `id`**（契约拒绝**重复 id**）。"""
    return f"mecha-panel:{svc_id}"


def _extra_page_wire(page: ExtraPage) -> dict:
    """附加页 → wire（**camelCase**；⚠ 只有数据，见 §7.3.8 第 4 条）。"""
    return {
        "id": page.id,
        "label": page.label,
        "path": page.path,
        "title": page.title or page.label,
        "emptyText": page.empty_text,
        "unreadableText": page.unreadable_text,
        "note": page.note,
        "columns": [{"label": c.label, "field": c.field} for c in page.columns],
    }


def services_with_monitor(services: Sequence[Service]) -> tuple[Service, ...]:
    """⭐ 只挑**声明了监控面**的服务（顺序 = 声明顺序；§7.3.6）。"""
    return tuple(s for s in services if s.monitor is not None)


def panel_services(services: Sequence[Service]) -> list[dict]:
    """那份参数本体的**浏览器视图**（``[{id, kind, tabId, routePath, portFile, title, extraPages}]``）。

    ⚠ 两条不许破：
    1. **不含端口号**（端口由路由现取）——这里连一个数字端口都没有；
    2. **不含任何本机绝对路径**（它会给浏览器）——所以只有 ``portFile``（文件名），
       **没有** ``root``/``cwd``。⭐ 那条**绝对路径**走**另一个变量**
       （:data:`PANEL_PORT_FILES_ENV`，**只给框架的 host 插件**）——
       两者**必须分开**，否则绝对路径会顺着这条路由漏进浏览器。
    """
    out: list[dict] = []
    for svc in services_with_monitor(services):
        spec: MonitorSpec = svc.monitor  # type: ignore[assignment]
        out.append({
            "id": svc.id,
            "kind": panel_kind(svc.id),
            "tabId": panel_tab_id(svc.id),
            #: ⭐ 标题在这里**已经补齐**（缺省 = ``Service.id``，见 ``Service.__post_init__``）
            "routePath": spec.route_path,
            "portFile": spec.port_file,
            "title": spec.title,
            "extraPages": [_extra_page_wire(p) for p in spec.extra_pages],
        })
    return out


def panel_payload(services: Sequence[Service]) -> dict:
    """递给框架插件的**整份**参数：路由 + 服务表（:data:`PANEL_SERVICES_ENV` 的值）。"""
    return {"routePath": PANEL_SERVICES_ROUTE, "services": panel_services(services)}


def panel_port_files(services: Sequence[Service]) -> dict[str, str]:
    """⭐ `{svc_id: 端口文件**绝对**路径}` —— 给框架的 host 插件用它**替项目取端口**。

    ⚠ **只含声明了 `monitor` 的服务**（与那份浏览器参数同一批、同一个顺序）。
    ⚠ 路径一律**绝对**：dsh 只有一个 cwd（§1.1.1）⇒ 相对路径会在 dsh 进程里按
    **别人的** cwd 解析（症状是"读不到端口 ⇒ 那个 tab 永远 503"）。
    ⚠ 解析的根是 **`data_root`**（框架注入的 `MECHA_SOFTWARE_ROOT`，即软件真正写文件的根）
    —— 见 `Service.monitor_port_file()`。
    ⚠ 它与 :func:`panel_services` 那份**必须分开**：那份给浏览器，**不许**带绝对路径。
    """
    out: dict[str, str] = {}
    for svc in services_with_monitor(services):
        p = svc.monitor_port_file()
        if p is not None:
            out[svc.id] = str(Path(p))
    return out


def panel_env(services: Sequence[Service]) -> dict[str, str]:
    """框架要注入 dsh 的**三个**变量（⚠ **JSON 单行**、``ensure_ascii=False``）。

    | 变量 | 谁读 | 带什么 |
    |---|---|---|
    | `MECHA_PANEL_ROUTE` | host 插件（并交给浏览器） | 那**一个**只读路由的路径 |
    | `MECHA_PANEL_SERVICES` | host 插件（并交给浏览器） | 那份按服务的参数（⚠ **不含端口号 / 绝对路径**） |
    | ⭐ `MECHA_PANEL_PORT_FILES` | ⚠ **只有 host 插件**（**绝不交给浏览器**） | `{svc_id: 端口文件绝对路径}` |

    ⚠ 用 ``ensure_ascii=False``：标题/文案是中文，转义成 ``\\uXXXX`` 会让日志/``--dump-config``
    里那行**人读不了**（它本来是用来排错的）。
    """
    return {
        PANEL_ROUTE_ENV: PANEL_SERVICES_ROUTE,
        PANEL_SERVICES_ENV: json.dumps(panel_payload(services),
                                       ensure_ascii=False, separators=(",", ":")),
        PANEL_PORT_FILES_ENV: json.dumps(panel_port_files(services),
                                         ensure_ascii=False, separators=(",", ":")),
    }


def with_panel_contributions(site_dsh, sites: Sequence[Site]):
    """⭐ 把**框架自己的** dsh 贡献并进那**一个** dsh：patch + 插件产物 + 两个变量。

    | 项 | 值 | 为什么 |
    |---|---|---|
    | ``patches`` | ``+ mecha-panel.patch.yml`` | ⭐ 框架的插件由**框架的 patch** 带上（§7.3.3）——新项目**不挂**框架插件 |
    | ``plugin_artifacts`` | ``+ host-plugin/…mjs`` | 不存在就**响亮失败**（而不是起一个没有面板插件的 dsh） |
    | ``env`` | ``+ MECHA_PANEL_ROUTE / MECHA_PANEL_SERVICES / MECHA_PANEL_PORT_FILES`` | 参数从**声明**来（§7.3.6）；⚠ 第三个**只给 host 插件** |

    ⚠ **这三个变量归框架**：项目若在 ``DSHSpec.env`` 里给了**不同**的值 ⇒ **响亮失败**
    （静默取一家 = "我以为面板读的是我的那份"）。⚠ 与 ``merge_dsh`` 对站点间同名的口径一致。

    ⚠ **恒加上**（哪怕没有任何服务声明 ``monitor``）：那样 host 插件的路由恒在，
    浏览器拿到的就是**明确的一句"没有监控面"**（空表）——不是 404（铁律 8：
    "确实没有"与"读不到"必须分开）。
    """
    services = tuple(svc for site in sites for svc in site.services)
    env = dict(site_dsh.env)
    for key, value in panel_env(services).items():
        if key in env and env[key] != value:
            raise MechaError(
                f"{key} 是**框架**的变量，站点声明给了不同的值",
                kind="panel_env_reserved",
                hint="监控参数只从 `Service.monitor` 来（声明是唯一那份真值）；"
                     "删掉 DSHSpec.env 里那个键",
            )
        env[key] = value
    patches = tuple(site_dsh.patches) + ((framework_patch_path(),)
                                         if framework_patch_path() not in site_dsh.patches else ())
    artifacts = tuple(site_dsh.plugin_artifacts) + (
        (framework_plugin_artifact(),)
        if framework_plugin_artifact() not in site_dsh.plugin_artifacts else ())
    return replace(site_dsh, patches=patches, plugin_artifacts=artifacts, env=env)
