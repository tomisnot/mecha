<!-- 本文件由 scripts/gen_capability_map.py 从角色声明生成，禁止手工编辑。
     改能力归属请改 mecha/assembly.py 的 declare_roles，然后重新生成。
     字段格式固定，供第二个项目直接并表（总纲 §5.4 交付 2/3）。 -->
# Capability Map（生成）

| 能力 | 角色 | provider | 消费方 |
|---|---|---|---|
| `adapter.ai` | consumer | mecha | ai |
| `adapter.gui` | consumer | mecha | human |
| `gate` | definition | mecha | — |
| `history` | definition | mecha | — |
| `monitor` | definition | mecha | — |
| `provider.approval` | definition | mecha | — |
| `provider.approval.local` | provider | fail-closed | gate |
| `provider.jobs` | definition | mecha | — |
| `provider.jobs.local` | provider | thread | surface.run |
| `provider.log` | definition | mecha | — |
| `provider.log.local` | provider | jsonl | history |
| `provider.spill` | definition | mecha | — |
| `provider.spill.local` | provider | local-file | tools |
| `surface` | definition | mecha | — |
| `surface.engine` | provider | unconfigured | surface |
| `tools.registry` | consumer | mecha | adapter.ai |
| `visibility.policy` | definition | mecha | — |
