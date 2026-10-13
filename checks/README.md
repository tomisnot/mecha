# 验证的运行方式

默认运行核心契约，约束与结果按受影响范围验证。文档格式、领域词黑名单、历史文件存在性不属于测试套件。

```powershell
python checks/run_all.py                              # 核心
python checks/run_all.py --only authority              # 名称筛选
python checks/run_all.py --only authority --list       # 只查看范围
python checks/run_all.py --suite integration           # 进程、HTTP/MCP、启动与重连
python checks/run_all.py --suite assets                # 面板测试与 TypeScript 类型
python checks/run_all.py --suite projects              # EL 的真实接入
python checks/run_all.py --all                         # 全部软件套件
python checks/run_all.py --suite release               # 发布前个人路径扫描
```

`--suite` 和 `--only` 可重复使用。未知名称会报错，不会出现空跑成功。
每项显示耗时；`--timeout` 控制单项预算。失败打印完整输出，缺项目环境显示 SKIP；
`--require-external` 或 `MECHA_REQUIRE_EXTERNAL=1` 将选中项的 SKIP 视为失败。

核心只需 Python 标准库；MCP 集成需要 `mecha[mcp]`。
资产套件需要 Node 和 TypeScript；`MECHA_TSC_ROOT` 指向含 `node_modules` 的目录。
项目套件需要 `MECHA_EL_ROOT` 指向 EL 仓。
真 dsh 启动单独运行 `python checks/dsh_argv_selfcheck.py --live`，需要 dsh 与 `MECHA_SITE_ROOTS`；普通集成不启动真实宿主。

结构测试保住重要关系，结果测试保住可观察承诺。已有测试已覆盖的机制不用在每个调用方重测。
红证或故障注入按风险使用，不要求每条测试配套。

能力表维护工具仍可独立使用：`python scripts/gen_capability_map.py`；其 `--check` 为可选新鲜度检查，不在软件套件中。
