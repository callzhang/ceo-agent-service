# 模型退休第二批：schema 与当前数据 inverse 验收

状态：第二批已标准部署、生产读回、精确归属备份/临时目录清理完成。
第一批c5f39验收独立完成，本批不复述其上线结果。源码基线f7a8e16e7；
Store26.1/Email54→Store28.1/Email54，目标463paired/462bare。

## 变化与固定来源

退休10个重复索引；Configs空email_model_mode_transitions家族及其2普通列、
9VIRTUAL列、3trigger/1复合FK/旧DAO和启动验证；Run map恒NULL元数据列。
Configs138列（22普通116virtual），全库1246→1234列，表数仍48。
registry模型切换/历史、promotion/category有效配置/immutable约束继续使用。

准确476项26.1/54源catalog仅为无业务数据的schema代码fixture，compact摘要
8d87a56c15429d971564a59de6b752d655500a46b407b0fef435180a1a0c882f。
paired工具以固定源和2个正面目标DDL重建，固定目标compact摘要
d70f359f2076f4ea4842247879d6c0d4ee0a2a5ebedf100f790d362adba4d1d3；
正常constructor仍使用独立正面当前声明。冷进程已断言不能加载旧catalog。
必要历史schedule/Signal/trigger/reply-reference和505来源保持原摘要。
已关闭Configs转换器及专用测试、旧selection转换API/专用测试随依赖退休；
当前candidate_option/selected_branch、promotion/category历史约束测试保留。
当前SQL工件同步；Action field-map只更新“当前目标”描述和SQL摘要，字段映射
及原历史说明不变。独立SPEC最初发现该current SQL工件遗漏，修正后通过复审。

## 测试与实际副本

定义/存储/Email/来源/Run相关分组149+46+12+67+33项通过；随后声明7与固定来源15
通过。根代理发布/选择器/声明集成31通过，最终paired/旧来源11通过，当前完整
SQL工件/声明/release17通过。分组有重叠，不宣称相加后的唯一case总数。
包含中途重建失败原子回滚、非空退休家族和非NULL元数据拒绝、部分来源拒绝、
正常constructor拒绝旧库、当前/前序精确分派、部署账本正反读写、替身回执。

`tools/validate_model_retirement_copy.py`在一个当前生产派生副本运行：

1. 正向后比较49张原生表（含sqlite_sequence）的全部保留值、SQLite类型和hidden
   rowid hash，不输出原文；仅按合同排除退休中性列与更新的schema marker。
2. 当前代码冷启动创建新Task、Run、业务key、Config、Action和provider替身回执。
3. inverse后新增事实完整；实际f7a8e16e7旧代码冷启动读回这些身份/回执并继续写入。
4. 再次正向迁移、当前代码再次冷启动读写，原始行子集逐值hash始终不变。

以上全部通过；迁移后当前EmailStore冷启动/durable验证也通过，原生事实不变。
10项实际查询形状的EXPLAIN显示保留的索引/UNIQUE/rowid路径可用，三项倒序页
沿INTEGER PRIMARY KEY扫描且无额外排序。QUALITY独立核对归档旧代码460个app文件与f7a8e16e7完全一致。
测试子进程拒绝网络和应用CLI；本批没有生产测试任务或真实发送。初次副本测试
失败来自测试guard过早阻断croniter本地architecture探测及替换Popen类影响类型
注解，已修正依赖bootstrap/仅阻断Popen初始化并通过回归；迁移本身未修改生产。

同一冻结数据、同4096page size、加入任何测试行之前的dbstat已分配字节：
480530432→477175808，少3354624字节（819页，约3.20MiB）。文件仍125725页，
freelist8408→9227；没有VACUUM，不宣称文件变小。临时副本删除不计容量收益。

## 标准部署、读回与清理

正常合并主线后46项schema/Skill定向测试通过。精确提交
44c13bc91f975c87a8a6f8e60cd5c1b1a05320b4通过完整强制gate、默认安静等待、
验证备份/paired发布/重启，09:18:45Z账本succeeded，operation
`deploy-b731a646-e249-4c6e-8eda-baddcea3e60c`；PID25260/health200。
前端stamp与安装提交frontend tree一致；实际完整463项SQL/hash d70f359f…/FK[]
与冻结目标逐项相同。API状态/Attention/History1、2页/done/历史Attempt/session均200。
实际UI翻页21-40、done筛选22897条、Attempt16220及receipt20893、Email当前列表通过。
19条历史failed身份集合无增减；summary failed28/Attention0，pending/processing
随真实工作变动，不要求固定。既有未解决反馈健康项仍为degraded1，native读取400/
不可用0。本批验收没有生产测试任务或真实发送。

发布原备份已被同workflow的daily替换；持目录锁重新验证最新代次CEOB完成标记、
integrity_check ok、精确463项schema/FK[]、内嵌operation对应本批并检查无打开引用，
以inode831639265/mtime1793179233748454289/514969600字节护栏删除并确认缺失。
明确归属临时根3380文件/1228015575字节及标准gate根均确认不存在，原生trajectory
和生产库保留。第三批独立副本不属于本批清理，不把临时清理计为数据库空间收益。

第三批会话候选已开始开发，尚未部署；反馈owner及剩余历史闭包仍未退休。
精确证据见[结构化验收](validation/2026-10-28-model-retirement-batch2.json)。
