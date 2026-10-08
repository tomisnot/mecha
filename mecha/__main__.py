"""``python -m mecha`` 的入口：转调 :func:`mecha.launch.main`。

为什么单独一个文件而不是把逻辑写这里：启动逻辑要能被**判据**直接调用
（``import mecha.launch``），而 ``__main__`` 是"给命令行用"的那一层薄壳。

## Known Limitations and Deferred Work

- **只做转调**：本文件不含任何逻辑，所以它没有自己的判据面——
  行为判据全在 ``checks/launch_selfcheck.py``（它直接调 ``launch`` 的函数，
  不经 ``__main__``）。⇒ 这里**没有**"测试入口脚本"的判据：入口脚本要真起进程，
  而"起进程"已经被 ``supervise`` 那一层用 mock 钉住了。
- 不注册 console_scripts 入口点：本仓**不装第三方**、也不假设安装方式，
  ``python -m`` 是零配置的调用形式。
"""

from __future__ import annotations

import sys

from .launch import main

if __name__ == "__main__":
    sys.exit(main())
