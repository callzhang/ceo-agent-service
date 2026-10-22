# 旧业务九字段处置与当前字段映射

> Status reconciliation, 2026-10-21: this is a scoped historical implementation/acceptance record. Its publication-pending statements are preserved as history; the subsequent Store19.11／Email53 combined copy, inverse and production deployment are recorded in [final release evidence](database-gpt6-release-validation.md). See [current execution progress](business-model-execution-progress.md) for unit status; historical FAIL results are not rewritten.

Derek 于 2026-10-20 明确批准永久退休九个旧运营控制或评估字段，不保存其旧值副本；schema inverse 只恢复这些字段的声明及中性默认值。其余业务事实、来源、关系和 provider 回执保留当前 SQL 值与类型。Store `2026-10-19.11`／Email53 实现这一边界。

批准来源绑定到 Store19.8／Email53 副本 SHA-256 `60ee5d5b88395235768868deda215b5b371facf931522932463a29d474f539a2`。该副本的严格来源导入清单 `c98c046df64a8c4c681314d2c4142dff5df047f8b2bee6f01c51049dcd2c3a6e` 将 975 个 WorkProject、3,327 个 WorkTodo 和 5,468 个 WorkUpdate（共 9,770 个有来源限定的身份）全部归为 `history_only`；逐身份处置记录绑定各自 source digest。这是该来源的结果，不是将未来记录一概归为历史的规则。

当前 `business.import_task_id` 是 WorkTodo 已采用到语义 Task 的唯一关联，来源副本该列全部为 NULL。字段退休不按标题、共享整数 ID、旧 open/active 状态创建或关联 Task/Project。原严格 `task-semantic-import` plan/apply 的条件性正式导入仍保留；只有原来源证据与审阅清单成立时才可采用。清单版本从 3 升为 4，因为来源摘要现在只覆盖保留字段；旧清单不能被当作新投影的清单使用。`business_legacy_links` 已物理退休，两个方向都不重建。

下面 51 行按旧来源字段逐一列出：42 个保留，9 个退休。`Business` 表中的物理行 ID 与公开 `public_id` 独立保留；`Actions` 的公开 ID 仍由原 `legacy_key` 读取。下表的 payload 路径是该 record_type 的唯一字段位置，SQL 投影继续使用原 codec/affinity；逆迁按当前值保留包括 NULL、TEXT、INTEGER、REAL、BLOB 的实际 SQL 类型，不把事实重新解释为新模型。

## work_project：975 行，24 → 19 字段

| 原字段 | 19.11 当前存储/处置 | schema inverse |
| --- | --- | --- |
| `id` | `business.public_id` | 保留逆迁时的当前 SQL 值与类型 |
| `title` | `business.title` | 保留逆迁时的当前 SQL 值与类型 |
| `category` | `business.payload_json.category` | 保留逆迁时的当前 SQL 值与类型 |
| `tags_json` | `business.payload_json.tags_json` | 保留逆迁时的当前 SQL 值与类型 |
| `status` | `business.status` | 保留逆迁时的当前 SQL 值与类型 |
| `priority` | 永久退休；当前 DTO、payload、生成列及对应约束均无此字段 | 只补中性默认值 `'none'` |
| `risk_level` | 永久退休；当前 DTO、payload、生成列及对应约束均无此字段 | 只补中性默认值 `'none'` |
| `needs_derek_attention` | 永久退休；当前 DTO、payload、生成列及对应约束均无此字段 | 只补中性默认值 `0` |
| `owner_user_id` | `business.payload_json.owner_user_id` | 保留逆迁时的当前 SQL 值与类型 |
| `owner_name` | `business.payload_json.owner_name` | 保留逆迁时的当前 SQL 值与类型 |
| `related_people_json` | `business.payload_json.related_people_json` | 保留逆迁时的当前 SQL 值与类型 |
| `goal` | `business.payload_json.goal` | 保留逆迁时的当前 SQL 值与类型 |
| `background` | `business.payload_json.background` | 保留逆迁时的当前 SQL 值与类型 |
| `facts_json` | `business.payload_json.facts_json` | 保留逆迁时的当前 SQL 值与类型 |
| `current_state` | `business.payload_json.current_state` | 保留逆迁时的当前 SQL 值与类型 |
| `blocker` | `business.payload_json.blocker` | 保留逆迁时的当前 SQL 值与类型 |
| `next_step` | `business.payload_json.next_step` | 保留逆迁时的当前 SQL 值与类型 |
| `next_follow_up_at` | 永久退休；当前 DTO、payload、生成列及对应约束均无此字段 | 只补中性默认值 `''` |
| `follow_up_mode` | 永久退休；当前 DTO、payload、生成列及对应约束均无此字段 | 只补中性默认值 `'none'` |
| `source_conversations_json` | `business.payload_json.source_conversations_json` | 保留逆迁时的当前 SQL 值与类型 |
| `created_at` | `business.created_at` | 保留逆迁时的当前 SQL 值与类型 |
| `updated_at` | `business.updated_at` | 保留逆迁时的当前 SQL 值与类型 |
| `last_activity_at` | `business.payload_json.last_activity_at` | 保留逆迁时的当前 SQL 值与类型 |
| `owner_evidence_json` | `business.payload_json.owner_evidence_json` | 保留逆迁时的当前 SQL 值与类型 |

## work_todo：3,327 行，18 → 16 字段

| 原字段 | 19.11 当前存储/处置 | schema inverse |
| --- | --- | --- |
| `id` | `business.public_id` | 保留逆迁时的当前 SQL 值与类型 |
| `project_id` | `business.payload_json.project_id` | 保留逆迁时的当前 SQL 值与类型 |
| `title` | `business.title` | 保留逆迁时的当前 SQL 值与类型 |
| `owner_user_id` | `business.payload_json.owner_user_id` | 保留逆迁时的当前 SQL 值与类型 |
| `owner_name` | `business.payload_json.owner_name` | 保留逆迁时的当前 SQL 值与类型 |
| `status` | `business.status` | 保留逆迁时的当前 SQL 值与类型 |
| `priority` | 永久退休；当前 DTO、payload、生成列及对应约束均无此字段 | 只补中性默认值 `'none'` |
| `deadline_at` | `business.payload_json.deadline_at` | 保留逆迁时的当前 SQL 值与类型 |
| `next_follow_up_at` | 永久退休；当前 DTO、payload、生成列及对应约束均无此字段 | 只补中性默认值 `''` |
| `follow_up_question` | `business.payload_json.follow_up_question` | 保留逆迁时的当前 SQL 值与类型 |
| `blocker` | `business.payload_json.blocker` | 保留逆迁时的当前 SQL 值与类型 |
| `completion_evidence_json` | `business.payload_json.completion_evidence_json` | 保留逆迁时的当前 SQL 值与类型 |
| `created_from_update_id` | `business.payload_json.created_from_update_id` | 保留逆迁时的当前 SQL 值与类型 |
| `created_at` | `business.created_at` | 保留逆迁时的当前 SQL 值与类型 |
| `updated_at` | `business.updated_at` | 保留逆迁时的当前 SQL 值与类型 |
| `completed_at` | `business.completed_at` | 保留逆迁时的当前 SQL 值与类型 |
| `description` | `business.payload_json.description` | 保留逆迁时的当前 SQL 值与类型 |
| `owner_evidence_json` | `business.payload_json.owner_evidence_json` | 保留逆迁时的当前 SQL 值与类型 |

## work_updates：5,468 行，9 → 7 字段

| 原字段 | 19.11 当前存储/处置 | schema inverse |
| --- | --- | --- |
| `id` | `actions.legacy_key` 的原公开整数投影 | 保留逆迁时的当前 SQL 值与类型 |
| `project_id` | `actions.payload_json[0]` | 保留逆迁时的当前 SQL 值与类型 |
| `source_type` | `actions.payload_json[1]` | 保留逆迁时的当前 SQL 值与类型 |
| `source_ref` | `actions.payload_json[2]` | 保留逆迁时的当前 SQL 值与类型 |
| `summary` | `actions.payload_json[3]` | 保留逆迁时的当前 SQL 值与类型 |
| `changes_json` | `actions.payload_json[4]` | 保留逆迁时的当前 SQL 值与类型 |
| `merge_reason` | 永久退休；当前 DTO、payload、生成列及对应约束均无此字段 | 只补中性默认值 `''` |
| `confidence` | 永久退休；当前 DTO、payload、生成列及对应约束均无此字段 | 只补中性默认值 `0` |
| `created_at` | `actions.payload_json[5]` | 保留逆迁时的当前 SQL 值与类型 |

## 保留的事实与退出的入口

`follow_up_question` 是未发送的独有文本，仍按原 Todo 身份保存；`changes_json` 是实际业务或 provider 变化，仍按原 WorkUpdate 保存。deadline、owner、owner evidence、description、completion evidence、facts、source conversations、所有原时钟与关联字段均在上表保留。WorkUpdate 的 `merge_reason` 与 `confidence` 两项均在批准范围内，不能遗漏 `merge_reason`，也不能将 `changes_json` 当作它的替代归档。

仅删除 `idx_business_work_project_status_priority` 和 `idx_business_work_todo_follow_up` 两个旧运营索引。历史身份索引、Todo 所属 Project/status 索引及实际关系保留。Provider link/draft 自己保存的 priority snapshot、独立 evidence candidate 的 confidence、当前语义 Task/Project/Attention 的字段和检索排序不属于这九项，保持原有含义。

当前 Store DTO、typed DAO、历史 API、HTML 及前端类型/解析、历史检索文本都不输出退休字段或伪装的空值。旧 priority/risk 排序入口和旧 due-before 跟进筛选随字段退出。History 和 legacy-project 深链接仍能按原身份读取保留事实与回执。没有新归档表、改名 JSON、原始行快照或恢复旧评估值的旁路。

## 迁移与逆迁

`business_legacy_fields.py` 只提供一套明确的当前字段元数据与目标声明。历史 `BUSINESS_FIELDS`、`SYNC_FIELDS`、19.10 Business/Actions 工厂的默认输出冻结；生成器只在当前调用者显式传入保留字段时生成 19.11 声明。

字段转换在调用者拥有的 FK-off 事务中重建 Business/Actions。Business 只移除批准的七个 JSON key；WorkUpdate 只移除两个数组 cell，六个保留 cell 的 JSONB 字节继续使用原 framing。TEMP transfer 只接收转换后的当前行，事务完成前删除；不保存九个旧值。全部其它 Business/Action kinds 的可写 SQL 值、物理 ID/ROWID、关系、sequence 行及其 ROWID/水位逐一核对。对 retained scalar 的值和 `typeof` 单独比较，并保持原 FK finding multiset。

公共 paired release 明确接受 Store19.8、19.9、19.10 与 Email53。19.8 在同一事务内依次调用既有 A5 Prepared Action、C2 logical Run 原语及九字段转换；19.9 省略 A5；19.10 只做字段转换。只在最终 19.11 运行新构造器，不尝试在新代码下启动中间版本。逆迁顺序相反，保留正迁后新增的当前业务事实/Task/provider receipt，恢复原 schema、Store marker 原值及原时钟、Email 账本；九项只能得到上表中性默认值。A5 已定义的 prepared 私有子行物理 ID 例外只属于 A5 原合同，其公开事实仍需独立核对。

具体代码与来源验证范围、失败基线及尚未完成的发布门槛见[验证记录](business-model-legacy-fields-validation.md)。
