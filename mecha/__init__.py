"""mecha v2 —— 人机同门（总纲 §1.3）。

软件自己的四件：Gate（写入门）/ History（账）/ Surface（能力面）/
Monitor（人类监控面）。操作者适配器（GUI、AI）与 Provider 层在其外。

``cockpit`` 是 Monitor 这一层的**只读传输**（零依赖、默认只绑回环），
``portfile`` 是它与其邻居共用的端口发现规则（单一实现）。

本包不 import dsh；dsh 只出现在 adapter/ 与 Provider 的 dsh 侧实现里，
churn 被关在 Provider 层（硬纪律 6）。

**刻意不定义 ``__all__``**（台账 A6）：它是"包内公开模块"的**手抄清单**——今天
零消费者，而手抄清单有一个**判据抓不到的漂移模式**（漏列新模块）：任何
"``__all__`` ⊆ 实际子模块"的守卫只能抓"列了不存在的名字"，抓不到"存在但没列"
（ML 刚踩过前者，后者更静默）。包内有哪些模块，单一来源是文件系统本身；下游
一律 ``from mecha.x import y`` 显式导入，不依赖本清单。真要一份可读的公开面
清单，做法是**生成**（同 ``scripts/gen_capability_map.py`` 生成的 capability
map），不是手写——那属后续工单，不在本次收口。

## Known Limitations and Deferred Work

- 无默认数据目录：``resolve_data_dir`` 不给就报错（D-6 的处置）。
- 计算和流水线属于项目；mecha 只提供命令、查询、任务和结果引用的治理机制。
  进程内 Python API 的防护属部署期 sandbox policy，库只保证域状态目录不可经
  通用写通道改（Phase 1 洞 1 的库内部分）。
- 分类内容（哪些 seam 拆包）推迟到 n=2（见 ADR 边界写机制不写分类）。
- 跨进程远程传输只到"回环 HTTP 只读面"为止：工具面的 MCP 传输是 extra
  （``providers.mcp``），核心不 import 它。
- 无 ``__all__`` ⇒ ``from mecha import *`` 不导出任何子模块（包无重导出名）。
  这是标准行为，不是缺陷；需要哪个模块就显式导入它。
"""

from __future__ import annotations
