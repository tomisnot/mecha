"""mecha v2 —— 这台软件的操作者无关层（总纲 §1.3）。

稳定脊椎四件：Gate（写入门）/ History（域状态史）/ Surface（能力面）/
Monitor（人类监控面）。操作者适配器（GUI、AI）与 Provider 层在其外。

``cockpit`` 是 Monitor 这一层的**只读传输**（零依赖、默认只绑回环），
``portfile`` 是它与其邻居共用的端口发现规则（单一实现）。

本包不 import dsh；dsh 只出现在 adapter/ 与 Provider 的 dsh 侧实现里，
churn 被关在 Provider 层（硬纪律 6）。

## Known Limitations and Deferred Work

- 无默认数据目录：``resolve_data_dir`` 不给就报错（D-6 的处置）。
- 物理演化只经 ``Surface.run``；进程内 Python API 的防护属部署期 sandbox
  policy，库只保证域状态目录不可经通用写通道改（Phase 1 洞 1 的库内部分）。
- 分类内容（哪些 seam 拆包）推迟到 n=2（见 ADR 边界写机制不写分类）。
- 跨进程远程传输只到"回环 HTTP 只读面"为止：工具面的 MCP 传输是 extra
  （``providers.mcp``），核心不 import 它。
"""

from __future__ import annotations

__all__ = [
    "errors",
    "roles",
    "data_layout",
    "format_version",
    "history",
    "authority",
    "gate",
    "monitor",
    "commands",
    "scopes",
    "artifacts",
    "toolhost",
]
