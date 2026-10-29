# 生产数据库逐表分解（2026-10-15）

当前生产数据库仍为 **121 张非 SQLite 内部表**，尚未达到已确认的 **10 张核心表加 1 张迁移账本**。冻结副本的 104 表候选及未接入运行入口的 7 类 configs 存储均不能算作生产完成。

观测时间：`2026-10-15T20:09:56.632921+00:00`；只读、query_only、单读事务。Store schema `2026-10-13.5`，Email schema `51`；生产 checkout 前后均为 `35d1636fd0b7ce83b77642120b62127fe0a3cb5e`。此任务未部署。

下文 MB = 1,000,000 字节，KB = 1,000 字节；JSON 证据保留精确字节数。表占用含 B-tree 及溢出页；索引单列，不能再把它们重复加一次。

## 总量与增长

| 项目 | 字节 | MB |
| --- | ---: | ---: |
| 主文件 / 事务可见逻辑大小 | 486,330,368 | 486.330 |
| 121 张表的数据页 | 387,354,624 | 387.355 |
| 这些表的索引页 | 98,549,760 | 98.550 |
| SQLite 内部表及 schema 页 | 425,984 | 0.426 |
| 空闲页（本次事务） | 0 | 0.000 |

20:07 UTC 的另一观察中 WAL 为 4,148,872 字节、空闲页为 4,096 字节；20:09 事务空闲页为零。WAL 是单独时点的物理文件观测，不应当作上述逻辑数据再加一次。

与同日冻结 970a 源副本相比，主文件和逐表加索引净增均为 **1,118,208 字节**。主要是 runs +552,960、business_source_documents +217,088、managed_skill_revisions +106,496。这是表级分配页变化；尚未归因到某类输入、输出或具体 writer。

## 按已确认目标归组

此表仅汇总现存物理表的目标去向，不表示相关调用方、迁移或上线已经完成。已存在 tasks/runs/actions 中仍有后续类型要并入。

| 最终去向 | 当前物理表数 | 当前表加索引 MB |
| --- | ---: | ---: |
| actions | 46 | 175.677 |
| runs | 3 | 125.981 |
| emails | 2 | 75.399 |
| tasks | 13 | 56.828 |
| provider_state | 2 | 24.965 |
| business | 12 | 10.019 |
| provenance | 1 | 6.377 |
| configs | 15 | 5.526 |
| state | 16 | 4.805 |
| 已批准退出 | 8 | 0.279 |
| schedules | 1 | 0.025 |
| 内联归属，不保留独立表 | 1 | 0.020 |
| 迁移账本 | 1 | 0.004 |

归组主要来自已确认字段 CSV。当前 tasks/runs/actions 是已上线核心表。后续新增的 email_direct_retry_requests（5 个实际字段）和 email_unsubscribe_cancellations（2 个实际字段）未在最初冻结 CSV 中列出；其 actions 归属由已实现的 app/action_email_definitions.py、Email13 源合同和迁移核实。本表显式补充该去向，未增加业务字段。内联身份元信息和已批准退出的 8 表不属于最终永久例外。

## 全部 121 表

按当前总占用排序。净增 KB 对比同名冻结源表，零值不代表零写入或没有页内空隙。

| 当前表 | 数据 MB | 索引 MB | 合计 MB | 净增 KB | 目标去向 |
| --- | ---: | ---: | ---: | ---: | --- |
| runs | 76.489 | 49.050 | 125.538 | +552.960 | runs |
| actions | 80.781 | 11.227 | 92.008 | +69.632 | actions |
| tasks | 43.291 | 6.300 | 49.590 | +16.384 | tasks |
| email_classifications | 36.598 | 3.256 | 39.854 | +36.864 | emails |
| email_messages | 33.993 | 1.552 | 35.545 | +28.672 | emails |
| email_provider_observations | 13.328 | 11.629 | 24.957 | +8.192 | provider_state |
| email_action_plans | 13.779 | 1.303 | 15.081 | +12.288 | actions |
| email_actions | 9.060 | 4.833 | 13.894 | +12.288 | actions |
| work_updates | 12.116 | 0.250 | 12.366 | +0.000 | actions |
| follow_up_drafts | 10.596 | 0.610 | 11.207 | +0.000 | actions |
| errors | 7.586 | 0.967 | 8.552 | +0.000 | actions |
| work_projects | 6.644 | 0.041 | 6.685 | +0.000 | business |
| business_source_documents | 6.357 | 0.020 | 6.377 | +217.088 | provenance |
| email_action_attempts | 4.125 | 1.298 | 5.423 | +4.096 | actions |
| managed_skill_revisions | 4.862 | 0.029 | 4.891 | +106.496 | configs |
| runtime_skill_load_receipts | 4.452 | 0.053 | 4.506 | +0.000 | actions |
| email_agent_classification_tasks | 2.732 | 0.483 | 3.215 | +0.000 | tasks |
| work_todos | 2.421 | 0.188 | 2.609 | +0.000 | business |
| work_todo_dingtalk_links | 2.511 | 0.061 | 2.572 | +0.000 | actions |
| email_unsubscribe_receipts | 2.126 | 0.254 | 2.380 | +0.000 | actions |
| business_object_tasks | 1.077 | 0.856 | 1.933 | +0.000 | tasks |
| dispatcher_claim_leases | 0.967 | 0.922 | 1.888 | -4.096 | state |
| business_task_events | 1.671 | 0.037 | 1.708 | +20.480 | actions |
| email_unsubscribe_claims | 1.159 | 0.258 | 1.417 | +0.000 | actions |
| business_task_signals | 0.627 | 0.389 | 1.016 | +20.480 | actions |
| seen_messages | 0.696 | 0.315 | 1.012 | +0.000 | state |
| okr_review_items | 0.938 | 0.000 | 0.938 | +0.000 | tasks |
| daily_scan_state | 0.868 | 0.004 | 0.872 | +0.000 | state |
| email_historical_candidates | 0.692 | 0.172 | 0.864 | +0.000 | tasks |
| email_unsubscribe_effects | 0.582 | 0.238 | 0.819 | +0.000 | actions |
| conversation_runtime_sessions | 0.524 | 0.180 | 0.705 | +0.000 | state |
| email_feedback_requests | 0.369 | 0.324 | 0.692 | +0.000 | actions |
| business_tasks | 0.442 | 0.102 | 0.545 | +12.288 | business |
| email_classifier_runtime_samples | 0.299 | 0.127 | 0.426 | +0.000 | runs |
| email_unsubscribe_steps | 0.246 | 0.094 | 0.340 | +0.000 | actions |
| managed_skill_export_receipts | 0.291 | 0.029 | 0.319 | +0.000 | actions |
| scheduled_task_config_versions | 0.102 | 0.205 | 0.307 | +4.096 | configs |
| follow_up_send_attempts | 0.250 | 0.012 | 0.262 | +0.000 | actions |
| email_training_snapshots | 0.168 | 0.033 | 0.201 | +0.000 | configs |
| oa_notification_events | 0.172 | 0.012 | 0.184 | +0.000 | tasks |
| codex_session_search_index | 0.102 | 0.070 | 0.172 | +0.000 | 已批准退出 |
| wechat_deliveries | 0.131 | 0.012 | 0.143 | +0.000 | actions |
| meeting_memory_write_events | 0.090 | 0.045 | 0.135 | +0.000 | actions |
| task_memory_write_events | 0.070 | 0.053 | 0.123 | +0.000 | actions |
| conversations | 0.082 | 0.037 | 0.119 | +0.000 | state |
| business_task_evidence | 0.045 | 0.066 | 0.111 | +0.000 | actions |
| task_todo_sync_outbox | 0.082 | 0.029 | 0.111 | +0.000 | actions |
| feedback_events | 0.090 | 0.008 | 0.098 | +0.000 | actions |
| service_state | 0.070 | 0.029 | 0.098 | +0.000 | state |
| email_historical_classification_history | 0.066 | 0.020 | 0.086 | +0.000 | actions |
| business_attention_events | 0.057 | 0.004 | 0.061 | +0.000 | actions |
| todo_evidence_candidates | 0.025 | 0.020 | 0.045 | +0.000 | business |
| workbench_confirmations | 0.004 | 0.041 | 0.045 | +0.000 | 已批准退出 |
| org_user_profiles | 0.025 | 0.012 | 0.037 | +0.000 | state |
| business_anchors | 0.012 | 0.016 | 0.029 | +0.000 | business |
| business_attention_items | 0.020 | 0.008 | 0.029 | +0.000 | business |
| business_project_context_revisions | 0.025 | 0.004 | 0.029 | +0.000 | business |
| business_task_anchor_links | 0.020 | 0.008 | 0.029 | +0.000 | actions |
| codex_session_search_fts_data | 0.025 | 0.000 | 0.025 | +0.000 | 已批准退出 |
| feedback_processing_items | 0.012 | 0.012 | 0.025 | +0.000 | tasks |
| runtime_skill_bindings | 0.012 | 0.012 | 0.025 | +0.000 | configs |
| scheduled_tasks | 0.016 | 0.008 | 0.025 | +0.000 | schedules |
| business_legacy_links | 0.004 | 0.016 | 0.020 | +0.000 | 内联归属，不保留独立表 |
| email_category_description_revisions | 0.020 | 0.000 | 0.020 | +0.000 | configs |
| feedback_processing_rounds | 0.004 | 0.016 | 0.020 | +0.000 | tasks |
| business_projects | 0.012 | 0.004 | 0.016 | +0.000 | business |
| business_task_relations | 0.004 | 0.012 | 0.016 | +0.000 | actions |
| email_category_configs | 0.012 | 0.004 | 0.016 | +0.000 | configs |
| email_reply_dispatch_claims | 0.004 | 0.012 | 0.016 | +0.000 | actions |
| email_training_snapshot_observations | 0.004 | 0.012 | 0.016 | +0.000 | 已批准退出 |
| feedback_processing_transitions | 0.004 | 0.012 | 0.016 | +0.000 | actions |
| setup_wizard_events | 0.012 | 0.004 | 0.016 | +0.000 | runs |
| workbench_artifacts | 0.004 | 0.012 | 0.016 | +0.000 | tasks |
| workbench_attachments | 0.004 | 0.012 | 0.016 | +0.000 | tasks |
| business_attention_proposal_tasks | 0.004 | 0.008 | 0.012 | +0.000 | actions |
| business_attention_tasks | 0.004 | 0.008 | 0.012 | +0.000 | actions |
| business_task_dingtalk_links | 0.004 | 0.008 | 0.012 | +0.000 | actions |
| business_task_follow_up_send_attempts | 0.004 | 0.008 | 0.012 | +0.000 | actions |
| business_task_follow_ups | 0.004 | 0.008 | 0.012 | +0.000 | actions |
| business_task_todo_sync_outbox | 0.004 | 0.008 | 0.012 | +0.000 | actions |
| business_work_cluster_tasks | 0.004 | 0.008 | 0.012 | +0.000 | actions |
| codex_session_search_fts_docsize | 0.012 | 0.000 | 0.012 | +0.000 | 已批准退出 |
| email_category_folder_bindings | 0.004 | 0.008 | 0.012 | +0.000 | configs |
| email_historical_operations | 0.004 | 0.008 | 0.012 | +0.000 | actions |
| email_reply_receipts | 0.004 | 0.008 | 0.012 | +0.000 | actions |
| feedback_iteration_decision_items | 0.004 | 0.008 | 0.012 | +0.000 | actions |
| scheduled_task_skill_refs | 0.004 | 0.008 | 0.012 | +0.000 | configs |
| service_bugfix_candidates | 0.004 | 0.008 | 0.012 | +0.000 | business |
| business_project_candidates | 0.004 | 0.004 | 0.008 | +0.000 | business |
| business_project_evidence | 0.004 | 0.004 | 0.008 | +0.000 | actions |
| business_task_date_evidence | 0.004 | 0.004 | 0.008 | +0.000 | actions |
| channel_login_reservations | 0.004 | 0.004 | 0.008 | +0.000 | state |
| codex_session_locks | 0.004 | 0.004 | 0.008 | +0.000 | state |
| corpus_sources | 0.004 | 0.004 | 0.008 | +0.000 | state |
| email_accounts | 0.004 | 0.004 | 0.008 | +0.000 | configs |
| email_direct_retry_requests | 0.004 | 0.004 | 0.008 | +0.000 | actions |
| email_historical_traversals | 0.004 | 0.004 | 0.008 | +0.000 | state |
| email_provider_folder_observation_generations | 0.004 | 0.004 | 0.008 | +0.000 | provider_state |
| email_scan_cursors | 0.004 | 0.004 | 0.008 | +0.000 | state |
| email_unsubscribe_cancellations | 0.004 | 0.004 | 0.008 | +0.000 | actions |
| email_unsubscribe_continuations | 0.004 | 0.004 | 0.008 | +0.000 | actions |
| feedback_iteration_decisions | 0.004 | 0.004 | 0.008 | +0.000 | tasks |
| feedback_processing_batches | 0.004 | 0.004 | 0.008 | +0.000 | tasks |
| managed_skills | 0.004 | 0.004 | 0.008 | +0.000 | configs |
| org_cache_metadata | 0.004 | 0.004 | 0.008 | +0.000 | state |
| runtime_route_pauses | 0.004 | 0.004 | 0.008 | +0.000 | state |
| setup_wizard_steps | 0.004 | 0.004 | 0.008 | +0.000 | state |
| wechat_memory_candidates | 0.004 | 0.004 | 0.008 | +0.000 | business |
| wechat_memory_import_jobs | 0.004 | 0.004 | 0.008 | +0.000 | tasks |
| wechat_read_state | 0.004 | 0.004 | 0.008 | +0.000 | state |
| wechat_reply_scopes | 0.004 | 0.004 | 0.008 | +0.000 | configs |
| business_work_clusters | 0.004 | 0.000 | 0.004 | +0.000 | business |
| codex_session_search_fts_config | 0.004 | 0.000 | 0.004 | +0.000 | 已批准退出 |
| codex_session_search_fts_idx | 0.004 | 0.000 | 0.004 | +0.000 | 已批准退出 |
| email_model_mode_transitions | 0.004 | 0.000 | 0.004 | +0.000 | configs |
| email_model_promotion_configs | 0.004 | 0.000 | 0.004 | +0.000 | configs |
| email_schema_migrations | 0.004 | 0.000 | 0.004 | +0.000 | 迁移账本 |
| meeting_alignment_delivery_claims | 0.004 | 0.000 | 0.004 | +0.000 | actions |
| runtime_feedback_iteration_capabilities | 0.004 | 0.000 | 0.004 | +0.000 | configs |
| runtime_skill_configs | 0.004 | 0.000 | 0.004 | +0.000 | configs |
| codex_session_search_fts | 0.000 | 0.000 | 0.000 | +0.000 | 已批准退出 |

## 具体处理边界

- 账户配置只有两行、8,192 字节（8 KiB）；逐封邮件的当前库存为 72,147 行，observations 表加索引 24,956,928 字节。它们分别归 configs/provider_state，不能以账户配置为理由解释库存占用。
- configs 的 7 类邮件存储已经完成本地独立 SPEC/QUALITY 和真实旧版 fresh 构造器账户修改/级联验证，但仍未被默认构造器选用。剩余 8 类、两类 Skill 回执以及 schedules/runs 的引用要一起闭合。
- 冻结副本的 4,105 个 Skill 加载回执中，3,678,270 UTF-8 正文字节均可由配置引用准确重建。目标是保留回执身份、配置 FK、PID、时间和结果，不再重复正文；目标、逆迁移及发布副本检查尚未完成。当前生产的 receipt 表 4.506 MB 含其他字段和索引，不能把整表大小当作可回收正文。
- runs 两个定时队列完整索引是已测量的优化候选；必须先验证部分索引上的 claim、失败统计、History、tie-break 与 EXPLAIN/耗时，不直接删运行历史，也没有新增 30 天保留规则。
- 原文表、邮件缓存和来源字段的退休依赖准确 locator、实际 reader 转换及采用结果/回执保留。存在未核对来源，当前没有完成全部退休；不使用原文档案副本兜底。
- provider_state 合表保持当前库存和 folder generation 语义；有完整权威刷新证明不可用且无引用时才按既定规则退休对应状态。没有执行按老日期或未分类批量删除。
- 最终验收仍为 11 张物理表、既有约束与真实业务链路、当前事实逆迁移和原生旧代码冷读写、可重取正文/trajectory 无副本，以及文件和索引实测。表数下降不等于文件下降；已验证的 104 表冻结候选活跃页反而增加约 32.13 MB。

## 证据

- [完整只读逐表 JSON](superpowers/specs/2026-10-15-production-table-size-readback.json)
- [已确认设计](superpowers/specs/2026-10-11-database-redesign-design.md)
- [已确认字段 CSV](superpowers/specs/2026-10-11-database-redesign-field-map.csv)
- [配置、状态和回执验证边界](database-redesign-config-state-source-preparation.md)
- [冻结候选容量对比](superpowers/specs/2026-10-15-provider-business-email-capacity.json)
