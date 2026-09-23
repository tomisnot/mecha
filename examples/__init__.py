"""消费配置示例（随包发，见 pyproject 注释）。非库契约。

## Known Limitations and Deferred Work

- 本目录是"第一个消费项目"的参考实现 + 真实接线素材；库（mecha/）零领域值，
  领域依赖只活在这里。
- energy_level 子包依赖外部 EL 仓在位（路径注入）；缺 EL 时 import 本包不炸，
  import 子包才炸（hub/判据缺 EL 即红是刻意）。
"""
