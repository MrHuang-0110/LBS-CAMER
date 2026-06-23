# core/ — reset 架构公共模块包
#
# 保持包初始化无副作用：`import core.app_runtime` 会先执行本文件。
# 这里不能 eager-import 旧同进程架构模块，否则会拉起已废弃的 UI
# 依赖并在板端启动阶段 fatal。
