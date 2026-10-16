# Task20 之后的剩余表去向

这是已确认 spec 的路由清单，不是新增字段设计，也不是下一阶段已完成的证据。依据：2026-10-11-database-redesign-design.md 第3、4节与原字段 CSV；候选表集合来自已安装 e29560247 的完整副本演练（116→55→116→55）。生产未切换。

2026-10-16 进度：旧 Workbench 确认功能已安装到隔离分支 `0ca8454b9`，副本完成116→54→116→54演练；下表中的 `workbench_confirmations` 已退休。当前54表仍有45张旧表，后续移除/归并45张、创建 emails 与 provenance 后为11张；生产未切换。原55表基线和完整去向清单保留如下，避免把阶段完成误报为最终完成。

55表中已存在8张目标核心表和1张Email账本，另外46张旧表待收敛；取消/合并这46张并新增 emails、provenance 后为11张。30张归入现有actions，8张归入business，2张运行记录归入runs，2张邮件表合为emails，1张来源表按共享身份原则归入provenance/内联，1张旧身份表直接归属business/actions，2张辅助表取消独立存储。最终数不包括SQLite内部对象、FTS虚拟/影子表；后者也按spec退休。

| 当前旧表 | 已确认去向 |
| --- | --- |
| `business_attention_events` | actions |
| `business_attention_items` | business |
| `business_attention_proposal_tasks` | actions |
| `business_attention_tasks` | actions |
| `business_legacy_links` | business/actions：内联已证明的旧身份 |
| `business_project_candidates` | business |
| `business_project_evidence` | actions |
| `business_source_documents` | 仅真实共享来源身份/版本进入 provenance；独有来源内联，可准确溯源原文退休 |
| `business_task_anchor_links` | actions |
| `business_task_date_evidence` | actions |
| `business_task_dingtalk_links` | actions |
| `business_task_events` | actions |
| `business_task_evidence` | actions |
| `business_task_follow_up_send_attempts` | actions |
| `business_task_follow_ups` | actions |
| `business_task_relations` | actions |
| `business_task_signals` | actions |
| `business_task_todo_sync_outbox` | actions |
| `business_work_cluster_tasks` | actions |
| `business_work_clusters` | business |
| `codex_session_search_index` | 取消独立表；实际元信息归入 runs/tasks，原生文件临时语料检索保留 |
| `email_classifications` | emails |
| `email_classifier_runtime_samples` | runs |
| `email_historical_classification_history` | actions |
| `email_historical_operations` | actions |
| `email_messages` | emails |
| `errors` | actions |
| `feedback_events` | actions |
| `feedback_iteration_decision_items` | actions |
| `feedback_processing_transitions` | actions |
| `follow_up_drafts` | actions |
| `follow_up_send_attempts` | actions |
| `meeting_alignment_delivery_claims` | actions |
| `meeting_memory_write_events` | actions |
| `service_bugfix_candidates` | business |
| `setup_wizard_events` | runs |
| `task_memory_write_events` | actions |
| `task_todo_sync_outbox` | actions |
| `todo_evidence_candidates` | business |
| `wechat_deliveries` | actions |
| `wechat_memory_candidates` | business |
| `work_projects` | business |
| `work_todo_dingtalk_links` | actions |
| `work_todos` | business |
| `work_updates` | actions |
| `workbench_confirmations` | 取消旧表与旧确认 API；保留原生 CLI 机制 |

下一步先移除旧确认功能的当前创建/校验、恢复、Store、API及前端依赖，不能仅DROP表。只读冻结副本中确认行、waiting_confirmation turn与确认事件均为0；上线前还须复核最新源库。

独立搜索表的700条冻结副本元信息不能直接当冗余丢弃：其中2条source/session/title与按source_id选择的现有run/job不一致，54条有session的run不属于原候选集合，created_at700条、updated_at650条不同。原7字段、session唯一upsert、候选成员和实际关联/排序事实需精确映射进runs/tasks，不能盲目枚举所有run；映射尚未设计/实施。FTS是派生缓存，物理shadow字节与当前元信息等价不是同一验收标准；必须明确退休/逆迁重建的边界，不保存旧shadow行快照。

原CSV是2026-10-11方向清单。后续每组必须重新对齐当前代码、准确字段/约束/写入事务，再写完整迁移与当前事实逆迁；不得将此表视为字段完整映射或许可静默丢失业务事实。
