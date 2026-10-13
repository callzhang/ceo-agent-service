# 已批准字段映射的当前来源补充

原2026-10-11字段映射有1611项，保留为当时基线。本次只读核对当前生产b9572061/Store14/Email51，排除已迁为typed结构的tasks/runs及SQLite内部/FTS影子表后，发现两个未映射的实际表、七个字段；其余仍存在的旧表字段均在原CSV中。补充CSV只给方向，不能直接生成迁移SQL。精确DDL/FK/trigger见同名前缀source-delta.json，不含业务原文。

|源表|真实字段|代码行为与迁移去向|
|---|---|---|
|email_unsubscribe_cancellations|action_identity、decision_json|已有明确取消决定，按identity阻止原效果继续；不是provider退订成功回执。进入actions专用类型，保留决定JSON及精确TEXT身份，无源FK时不制造claim父记录。|
|email_direct_retry_requests|action_id、attempt_number、source_readback_digest、account_configuration_snapshot、requested_at|已有显式重试事实；(action_id,attempt_number)复合主键、action_id→email_actions的RESTRICT外键、原UPDATE/DELETE及重复INSERT不可变trigger、准确digest和九项配置array约束保留。随email_actions一组进入actions，不能独立删表或把请求当执行成功。|

已核对app/email_store.py的声明、取消读写、重试请求写入与恢复读取。此补充只覆盖现有业务，不新增授权/审查/恢复政策；不是这两个表已迁移的声明。后续邮件actions单元需纳入精确映射、调用方、逆迁与对外替身验收。完整10+1目标保持不变。
