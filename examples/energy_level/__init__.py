"""EL（Energy Level）作为 mecha v2 的第一个 Provider（Phase 5 参考实现）。

这里是"消费配置"的家：允许出现 EL 的领域值（B=Gauss、Rb-87 等），因为 §9
反模式禁的是**库里**写死项目值，examples/ 正是放项目值的地方。**本目录是
参考实现，非契约**——EL 的真实值（键名/枚举/标定）随 EL 演进，不代表库的接口。

**只 import `src.core.protocol`**（EL 的领域无关、框架无关纯领域层，docstring
明写"本协议不 import mecha"），绝不碰 v1 的 `src.adapter`/`mecha.app_bridge`
——那是旧形状，读了会锚定（用户纪律 + §11）。

## Known Limitations and Deferred Work

- cache 在构造时建一次（ARC 首次 ~13 s）并进程级复用；多原子种类未参数化
  （EL 当前只 Rubidium87，第二个原子出现再抽）。
- 取消经后台线程把 `progress_arr[1]` 置 1（EL 的取消约定）；引擎不响应取消时
  退化为跑完，不硬杀（同进程无法强杀，与 dsh 工具取消语义一致）。
- 真实部署接线（把 EL 的 app_entry 从 v1 换到本 Provider）是 EL 仓改动，需
  用户显式授权后单独进行；本模块只给可运行的参考实现 + 判据。
"""
