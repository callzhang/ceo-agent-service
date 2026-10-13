# 第五期D：发送结果与观察关系合入 actions

本单元属于已批准的完整10＋1结构。当前只冻结源事实和独立行为测试，
不修改运行 schema，不占用版本号。第五期B/C正式发布后必须重新核对实际
生产 marker、commit、声明、字段类型、外部依赖与调用点，再实施本单元。

## 准确源范围

仅 `sent_replies` 和 `sent_reply_observers`，共14＋4个实际字段。
schema-only source位于
`../specs/2026-10-13-database-redesign-phase5d-delivery-source.json`。
本次只读观察为2340条delivery、319条observer；这些数量是一次观察，
不是保留政策或迁移时必须相等的硬编码常量。

实际历史库与fresh候选库的sent表声明及列顺序不同，但字段集合、默认值、
索引与行为一致。observer声明相同。唯一入向FK来自本单元的observer，
没有观测到外部入向FK；执行前还需重新检查SQL依赖，不能用当前观察
代替将来发布前预检。

## 字段映射与不可混淆的事实

| 原字段／约束 | actions内映射要求 |
| --- | --- |
| sent_replies.id | numeric legacy identity，保留公开ID和旧History定位；原AUTOINCREMENT独立水位 |
| conversation_id、trigger_message_id、reply_text、sent_at | delivery类型明确同名字段；已发送结果正文和时间保留 |
| send_result_json、recall_key、recall_status、recall_error、recalled_at | 同名有限payload，原始TEXT和nullable时间保留；撤回状态允许原有更新 |
| feedback_token、channel、external_action_key | 同名字段及原默认；channel不增加原表没有的枚举CHECK；external key仅精确空串豁免唯一性 |
| sent_replies.agent_run_id | 普通INTEGER标量，原默认0；在本类型payload保留原值，不能套用同名关系字段的FK、补造run或改成NULL |
| observer.sent_reply_id、agent_run_id | 原复合身份，两项均为整数；delivery端点映射到actions实际父记录，run端点沿用实际runs FK；不同run观察同一delivery必须分别保留 |
| observer.reply_task_id、created_at | 原task直接FK与同名时间；不按delivery或run猜测task，不丢独立task绑定 |
| observer三个FK、四个类型guard | immediate NO ACTION与原logical-run/reply-task类型检查；合法改绑、无引用父键更新／删除继续允许 |

`sent_replies.agent_run_id`与`sent_reply_observers.agent_run_id`名称相同、
schema语义不同。存储定义、payload与引用分配必须按有限record_type区分，
不能向当前全局REFERENCES添加字段后就把所有同名标量视为关系。
`external_action_key`按原文本索引保留，不增加源表没有的global receipt FK。
不能凭正文、title或时间建立发送准备与delivery的身份关系。

## 已冻结的独立源行为

`tests/test_sent_delivery_source.py`直接运行两份捕获声明，使用最小真实FK
端点表而不导入目标Store或actions实现。76项检查覆盖：

- 全部原字段、默认、原始非JSON／NUL文本、nullable recalled_at及recall更新。
- 标量run的0、未知ID和其他类型ID；channel原有无枚举限制。
- 非空external key的BINARY唯一性，精确空串豁免；空格／制表／NUL不是空串。
- native allocator删除／忽略插入的水位、ignored MAX_INT后的SQLITE_FULL。
- 多delivery／多run观察关系、复合唯一性与独立task绑定。
- 四个类型guard、三个NO ACTION FK、合法改绑与未引用父键变更。

这些是后续目标实现的源行为oracle，不是目标实现或上线证据。

## 实施与验收顺序

1. 在已发布C来源上重新捕获两个已知源声明变体及依赖，拒绝未支持变化；
   逐项核对本计划的实际字段，不从方向CSV自动生成迁移。
2. 扩展有限actions定义与CRUD，加入delivery和observer类型，保持原六、九、
   十二类型准确源DDL。observer按原两个整数端点唯一，使用真实父FK，
   不只以任意JSON字符串唯一性代替复合身份；父公开ID改动继续遵循
   原NO ACTION限制。原public-reference更新约束需覆盖新增delivery关系。
3. 为sent独立保留原AUTOINCREMENT水位和失败／ignore分配行为。
   历史source schema及allocator集合必须按准确版本保持固定，不能因
   扩展全局类型常量而改变B/C source manifest或其配对逆迁metadata。
4. 正迁保留原十二类型完整持久值、类型、物理身份、引用和水位，
   转换18字段及observer关系；逆迁重建当前delivery／observer事实与
   原十二类型，不恢复陈旧快照。source／target变体和数据预检均在改动前。
5. 转换Store的记录、已完成投递投影、观察者登记、History关联、feedback、
   recall、settlement、跟进和计数路径；包括audit_web的发送结果读取。
   现有原子写入、重复external key冲突、回执与observer顺序保持。
   fresh/bootstrap/manifest/child guards/column requirements同版本退休旧两表；
   不保留双写、旧表view或原文fallback。
6. 最新生产一致副本做全字段值／类型、父子关系、公开／物理身份、水位、
   完整旧十二类型行以及未受影响表逐项比对。增加、修改、删除、改绑、
   多观察者和新分配ID后逆迁，用实际上一已发布代码新进程打开，再正迁。
7. 配对release演练所有既有发布失败点，保留上线后的新回执／撤回事实。
   运行受影响的History、feedback／recall、meeting、native/system sender、
   settlement和跟进测试及页面；真实CLI使用替身发送，不触发真实对外动作。
8. 独立SPEC/QUALITY和完整CI通过后正常deploy，读回PID、health、队列、
   Attention、History、受影响页面及准确源读取。报告物理表与实际页／索引／
   DB/WAL占用，未回收前不宣称文件已缩小。

本单元仍不能代替reply_attempts／memory_write_events、OA／邮件动作、
business／证据、emails与配置／运行状态以及最后旧结构退休。
