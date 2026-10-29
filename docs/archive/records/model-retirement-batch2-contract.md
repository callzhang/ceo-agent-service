# 第二批 schema 退休：实施前合同

第一批无DDL代码已作为c5f39aa8e标准部署。本文件记录第二批只读预检所得的实际
依赖，不代表迁移已实现或验证。源Store26.1/Email54，目标拟Store28.1/Email54；
实施开始仍重新核对最新主线/生产声明，若版本已变化则重新冻结，不套用旧计数。

## 目标与精确规模

只移除审计列出的10个重复索引、空email_model_mode_transitions家族的11列/3trigger/
一个复合FK，以及run_entity_identity_map恒NULL的allocator_highwater。重建Configs
与Run map，其他表仅退休对应索引；同一事务完成，同一显式inverse恢复。
其他所有行、类型、身份、引用和当前序列水位保留，不新增业务字段或备用JSON。

以当前冻结源计算，paired476→463对象，bare475→462；Configs149→138列
（22普通、116VIRTUAL）；Run map7→6普通列；全库1246→1234列。Email版本仍54。
当前启动直接创建正面目标声明，不加载历史字段全集再相减。

07:50Z最新只读前置：旧Configs家族0行，map非NULL0；370个非空旧session的当前
OAuth关联均存在且相同，26个旧空hash仍有完整权威值；54个反馈来源/解决时间
全部一致，46个无当前Round的解决事实保留，8个有当前Round的镜像逐值一致。
会话和反馈不在第二批DDL范围，这些数据不作为硬编码迁移行数。

## 必须先固定的历史依赖

1. 把精确Store26.1/Email54的476项无业务内容schema catalog冻结为版本化fixture及
   小型明确loader，作为本次release source和inverse目标。compactJSON摘要为
   8d87a56c15429d971564a59de6b752d655500a46b407b0fef435180a1a0c882f。
2. scheduled_task_notification_schema_source的previous_current_objects与旧schedule
   release的26.1目标校验/建表/index必须引用该冻结catalog，不能引用新的current。
   这样后续Signal/trigger/reply-reference显式历史链的原摘要不随当前目标漂移。
3. predecessor_database_schema的Configs构造切为冻结来源catalog的Configs切片，
   原505对象/固定摘要不变；历史Action夹具同样使用该明确来源。
4. configs_schema_transfer与其历史专用测试、run_entity_schema的旧_TARGETS和空列
   写入存在对可变当前定义的耦合。不得把旧转换器改成声称迁移至28.1，也不把旧
   转换源事实删出fixture。优先退休已关闭转换的专用入口/测试；仍供当前功能测试
   造数的调用先切native creator，必要历史声明用冻结catalog。此依赖处理属于已批准
   第五批闭包退休的提前必要子集，逐文件记录，不另造一套旧运行时builder/writer。

## 当前代码与发布链

Configs正面声明、存储与Email启动验证同时移除该空家族。删除精确record/get/list
model-mode-transition DAO与专用validator；保留registry的实际切换/历史、promotion
config、category revision、共同_next_model_control_timestamp和其他家族约束。
Run定义只删空元数据列及三个指定行号索引；Task/Adoption定义只删指定索引。

新显式发布只接收精确26.1/54来源，目标28.1/54；普通constructor只接受精确目标。
current_publication_sources的前一发布读取改为准确26.1源catalog。自动发布退出
25.3/25.2旧窗口，必要历史工具保持显式；不增加按旧表存在与否的兼容分派。

## 迁移与验收

同事务使用显式普通列映射在TEMP中转移两张表，安装精确目标DDL/index/trigger，
不通过JSON重新序列化业务行。每次正反转换现场捕获并保留sqlite_sequence原
rowid/name/seq，包括高于max(id)的物理/分家族水位和其他自定义行；inverse使用
迁移后当前水位，不恢复成迁移前低值。恢复两个退休普通Configs列/Run空列为NULL，
其余列逐值复制；旧家族为空且新目标不允许写入该家族。

前置断言旧家族为空、空列无非NULL；不满足则停止补证据。副本forward后新增与
删除正常事实并推进水位，inverse、真实26.1旧代码冷启动读写、reforward；比较
保留事实、SQLite类型、身份、引用、水位、FK集合和完整声明，不只检查行数。
当前构造/约束/registry、保留FK/查询计划与历史冻结摘要均要定向测试通过。
更新当前目标SQL工件；无实际字段映射变化的field-map JSON不得为了测试改写。
通过独立审查、标准gate/部署、生产读回后，再验证并清理本批备份/临时数据。
