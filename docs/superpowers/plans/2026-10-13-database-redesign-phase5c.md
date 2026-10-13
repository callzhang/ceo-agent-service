# 第五期C：发送准备、native dispatch及provider receipt合入actions

本单元属于已批准的完整10+1结构，不能代替剩余actions/business/emails/config/state迁移。Phase5B已于ebaf0d96正式发布，当前生产Store2026-10-13.2／Email51，准确九类型actions及旧postfix三表声明已只读重新验证通过。实际native水位560／115，三个postfix表行数1079／613／67；仅为来源观察，不作为恒定迁移计数。现在为本单元预留Store2026-10-13.3（Store17），继续caller/bootstrap/release接入；尚未激活运行或发布目标结构。已完成的独立十二类型基础和正逆迁证据仍是先前候选副本范围，正式演练必须使用当前生产一致副本。

## 准确源范围

仅outbound_postfixes、native_reply_dispatches、outbound_postfix_receipts，13个现有列。已观测副本分别1079/67/613行，精确声明见phase5c-postfix-source.json。实际current和origin/main fresh声明与副本逐字相等；source元数据不是业务正文备份。全部inbound只在两子表，无外部SQL schema依赖。

## 字段和关系映射

| 现有事实 | 目标 | 保留内容 |
| --- | --- | --- |
| 三表(channel,delivery_key)复合PK | actions record_type与无损legacy_key；从该身份生成同名channel/delivery_key | BINARY等值；不拼接、trim、改大小写或Unicode规范化；不复制键字段 |
| 两子表复合FK | actions复合自FK，准确指向outbound_postfixes类型与原channel/delivery_key | 默认immediate、NO ACTION更新/删除；parent无子可改键、child可改绑合法父键 |
| parent final_body/feedback_token/postfix_version | 本类型有限payload同名字段 | 原TEXT值、空token默认、首次准备后应用复用；不新增SQL不可变策略 |
| receipt provider_result_json | 本类型有限payload同名字段 | 原始TEXT，不统一json_valid，不重新序列化历史结果 |
| 三表created_at | 类型记录原created_at字段 | 原时间及current_timestamp默认 |

复合自FK需要由两个子类型生成固定父类型判别postfix_type，并使用unique(record_type,channel,delivery_key)及FK(postfix_type,channel,delivery_key)；这是现有外键关系的SQL投影，不是新增业务事实。仅physical parent_id会使原禁止的父键更新成为隐式级联，不能采用。native_reply_dispatches没有parent/receipt的CHECK，不得照抄新增。隐式SQLite rowid不是这三表的业务身份，不新增持久字段保存它。

## 执行步骤

1. 独立实际source SQL oracle先冻结：复合身份、两个FK、父键更新/删除限制、child改绑、raw正文/receipt默认与更新；当前16项已通过。
2. 扩展有限actions声明/CRUD/投影，仅对这三类型支持准确复合身份。现有九类型数据/公开ID/物理ID/allocator/约束完全保留；不得通用JSON任意字段、旧表view或双写。
3. 准确源预检与事务正迁；配对逆迁从当前十二类型actions重建三原表和准确源九类型actions，保留新、改、删事实及原native allocator水位。拒绝不支持的声明/依赖必须发生在改动前。
4. 转换Store所有prepare/get/echo/native claim/receipt和meeting恢复SQL；包括bootstrap中现有WeChat准备backfill。保持原首次准备、首次receipt、provider前dispatch claim及reconcile顺序，不新增恢复/审查策略。业务调用方继续原API。
5. 明确fresh布局、Store marker、manifest/retired清单、publication读取器和配对release；只支持准确前一已发布来源，无长期运行fallback。
6. 对当前生产一致副本做全部值/类型、九类型完整行/关系/水位、复合FK正逆正与旧实际constructor新进程演练。迁移后增加parent无子、dispatch无receipt及receipt记录，更新正文/key/rebind并删除；原受引用父键仍禁止修改/删除。
7. 定向Store/service sender/system action/meeting/WeChat/History/echo检查、独立SPEC/QUALITY，真实CLI批准/修订加替身发送，完整CI。更新architecture/runtime和validation同提交。
8. 正式部署只用app.deploy，读回PID/health/queues/Attention/History/真实受影响页面。最新已验证备份先验证后清旧；明确实际DB/WAL/页占用，未VACUUM不宣称文件缩小。

## 后续闭包与真实差异

sent_replies + sent_reply_observers可独立迁移，分别14/4列；sent_replies.agent_run_id有2021条0且无FK，必须保留scalar，不能虚构run或统一加FK。observer四个实际parent-kind guards与多个run观察一条送达的关系保留。

reply_attempts另58列，旧ID与History URL保留。memory_write_events的生产历史DDL没有attempt_id FK，fresh DDL有：执行该单元时一并冻结和处理，不把无inbound的单一副本推成所有合法source。audit_tool_events_json均[]但native范围仍保留；audit_summary实际供History结论使用，不能因名字直接删。
