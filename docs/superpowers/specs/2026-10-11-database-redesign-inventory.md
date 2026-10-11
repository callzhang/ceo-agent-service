# 当前表清单与目标去向（修订草案）

基线：生产代码 d171fb722380f551f1e71e6933a222dfce1a8aa7；只读数据库测量。MB 为十进制，表占用含归属索引；不是迁移后预测。

修订：objects→business；取消通用 sources/relations/business_links；独有来源内联，共享版本才进入 provenance；实际多对多证据/关联作为 actions 明确类型行。
| 现有表 | 行数 | 表和索引 MB | 目标 | 处理说明 |
|---|---:|---:|---|---|
| `scheduled_task_runs` | 150,874 | 65.745 | `runs` | 保留触发事件身份/CAS/跳过原因；普通成功轮询压缩为统计及最近记录，失败/外部动作/审查任务保留具体行。 |
| `email_classifications` | 8,477 | 45.298 | `emails` | 与 messages 按 stable_message_identity 合并；删除重复 header/locator 和 action_plan_json，保留 model_text 及分类 ID；允许 cache-only 阶段。 |
| `email_provider_observations` | 72,003 | 36.278 | `provider_state` | 当前 provider 状态，不是扫描历史；保留独立 account+identity 记录，删除冗余 lookup 索引。 |
| `email_messages` | 8,477 | 34.185 | `emails` | 与 classifications 合并共享 metadata；活动原文只临时保留，完成后按准确来源读取，撤回 30 天正文缓存。 |
| `reply_attempts` | 14,662 | 27.369 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `meeting_alignment_jobs` | 679 | 18.731 | `tasks` | 终态 source_json 9.46 MB 候选：来源可验证时改引用；目标/最终消息/送达状态保留。 |
| `email_action_plans` | 9,511 | 14.238 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `reply_tasks` | 7,250 | 13.591 | `tasks` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `email_actions` | 9,876 | 12.935 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `work_updates` | 5,468 | 12.366 | `actions` | 迁入 actions 的 legacy_work_update，保留实际 summary/changes_json/来源；不发明 before/after。 |
| `reply_task_inputs` | 7,347 | 12.243 | `tasks` | 版本来源只存一份；任务当前指针引用版本。活动输入仍保留恢复必需正文。 |
| `follow_up_drafts` | 2,477 | 11.207 | `actions` | 全部终态但业务问答/送达回执有价值；移入 actions 不重新排队。 |
| `agent_runs` | 21,576 | 9.482 | `runs` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `sent_replies` | 2,290 | 9.224 | `actions` | 含撤回/反馈可变业务状态；与不可变 provider receipt 分为同表不同类型，不丢 recall 字段。 |
| `errors` | 15,202 | 8.397 | `actions` | 已解决 detail 4.45 MB 候选精简；保留 kind/time/resolution；未解决正文保留。 |
| `agent_runtime_attempts` | 22,688 | 7.995 | `runs` | 保持 workload_kind/key 原文；不得把 OKR request 或 workbench UUID 误当 agent_run_id。 |
| `work_projects` | 975 | 6.685 | `business` | 保留为 legacy_project 类型；不得凭名称等同正式项目。memory_context_json 可重建缓存停止持久化。 |
| `work_summary_inputs` | 9,080 | 6.189 | `tasks` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `email_action_attempts` | 11,842 | 5.104 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `business_source_documents` | 161 | 5.022 | `provenance` | 取消可准确链接溯源的 body 副本；共享来源身份/版本才独立进入 provenance。 |
| `review_candidates` | 290 | 4.567 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `runtime_skill_load_receipts` | 3,876 | 4.186 | `actions` | 加载事实保留，重复 loaded_json 内容放 configs 一份、回执引用。 |
| `managed_skill_revisions` | 145 | 3.916 | `configs` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `email_agent_classification_tasks` | 1,348 | 3.064 | `tasks` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `okr_review_requests` | 20 | 2.724 | `tasks` | 终态 okr_source_json 2.67 MB 候选：必须有确切可定位来源；业务审查结论保留。 |
| `work_todos` | 3,327 | 2.609 | `business` | 保留为 legacy_todo 类型及真实 deadline/link/follow-up；不能自动升级 formal TODO。 |
| `work_todo_dingtalk_links` | 875 | 2.572 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `email_unsubscribe_receipts` | 1,066 | 2.277 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `outbound_postfixes` | 952 | 2.077 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `business_object_tasks` | 7,064 | 1.782 | `tasks` | key 到当前任务定位归入 tasks 的索引/直接归属，取消独立映射表。 |
| `task_agent_runs` | 10,714 | 1.585 | `runs` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `business_task_events` | 785 | 1.528 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `dispatcher_claim_leases` | 10,054 | 1.491 | `state` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `email_unsubscribe_claims` | 1,066 | 1.360 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `seen_messages` | 7,031 | 0.958 | `state` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `okr_review_items` | 413 | 0.938 | `tasks` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `memory_write_events` | 476 | 0.901 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `business_task_signals` | 733 | 0.872 | `actions` | 信号中已采用业务事实进入 actions；来源内联或直接 provenance FK，context 原文不复制。 |
| `email_historical_candidates` | 924 | 0.864 | `tasks` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `daily_scan_state` | 9 | 0.860 | `state` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `email_unsubscribe_effects` | 1,153 | 0.758 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `email_feedback_requests` | 1,413 | 0.692 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `candidate_reviews` | 283 | 0.684 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `conversation_runtime_sessions` | 5,287 | 0.680 | `state` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `meeting_alignment_runs` | 2,526 | 0.680 | `runs` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `business_tasks` | 510 | 0.463 | `business` | 保留 formal/candidate、commitment、formal_basis 约束；只有现有 TODO 有 deadline_at。 |
| `outbound_postfix_receipts` | 542 | 0.438 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `email_classifier_runtime_samples` | 2,048 | 0.426 | `runs` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `external_action_results` | 278 | 0.356 | `actions` | 独立全局回执；278 行中 219 没有 candidate attempt，不强制挂在 execution 下。 |
| `email_unsubscribe_steps` | 812 | 0.319 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `managed_skill_export_receipts` | 1,920 | 0.315 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `scheduled_task_config_versions` | 67 | 0.283 | `configs` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `follow_up_send_attempts` | 65 | 0.262 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `workbench_events` | 810 | 0.246 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `agent_effect_intents` | 214 | 0.238 | `actions` | 214 行且 deferral 仍读取；迁入 actions，不因没有当前写入点删除。 |
| `email_training_snapshots` | 122 | 0.201 | `configs` | DB 只保留被当前模型/回执引用的 metadata，实际训练文件仅最新；不回填 observation 全集。 |
| `agent_run_state_events` | 1,151 | 0.172 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `codex_session_search_index` | 662 | 0.164 | `取消` | 取消独立会话索引物理表；已存在的 session/source/title 元信息归入 runs/tasks，继续原生文件内存检索，不保存 FTS/embedding/摘要副本。 |
| `wechat_deliveries` | 130 | 0.143 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `candidate_executions` | 72 | 0.135 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `agent_execution_receipts` | 115 | 0.127 | `actions` | 115 行且 deferral 仍读取；迁入 actions，不删除证据。 |
| `meeting_memory_write_events` | 535 | 0.127 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `task_todo_sync_outbox` | 242 | 0.111 | `actions` | 202 completed/40 skipped 全部终态；长输入改引用，保留 operation_key 与结果回执。 |
| `conversations` | 499 | 0.102 | `state` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `business_task_evidence` | 788 | 0.098 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `feedback_events` | 48 | 0.098 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `email_historical_classification_history` | 183 | 0.086 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `candidate_action_attempts` | 73 | 0.082 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `service_state` | 215 | 0.078 | `state` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `task_memory_write_events` | 325 | 0.078 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `oa_notification_events` | 11 | 0.070 | `tasks` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `weekly_okr_analysis_jobs` | 169 | 0.061 | `tasks` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `workbench_turns` | 37 | 0.057 | `tasks` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `todo_evidence_candidates` | 3 | 0.045 | `business` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `workbench_confirmations` | 0 | 0.045 | `取消` | 已停用入口且当前零行；删除旧确认接口及表，CLI 原生机制不变 |
| `business_attention_events` | 9 | 0.037 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `org_user_profiles` | 74 | 0.033 | `state` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `business_anchors` | 50 | 0.029 | `business` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `business_attention_items` | 7 | 0.029 | `business` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `business_project_context_revisions` | 5 | 0.025 | `business` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `business_task_anchor_links` | 71 | 0.025 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `feedback_processing_items` | 48 | 0.025 | `tasks` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `native_reply_dispatches` | 45 | 0.025 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `runtime_skill_bindings` | 227 | 0.025 | `configs` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `scheduled_tasks` | 15 | 0.025 | `schedules` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `agent_run_events` | 0 | 0.020 | `取消` | 原生 trajectory 唯一完整事件源；停用 DB 事件正文副本 |
| `business_legacy_links` | 0 | 0.020 | `business/actions：内联已证明的旧身份` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `codex_session_search_fts_data` | 6 | 0.020 | `取消` | 取消独立会话索引物理表；已存在的 session/source/title 元信息归入 runs/tasks，继续原生文件内存检索，不保存 FTS/embedding/摘要副本。 |
| `email_category_description_revisions` | 24 | 0.020 | `configs` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `feedback_processing_rounds` | 3 | 0.020 | `tasks` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `sent_reply_observers` | 269 | 0.020 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `business_projects` | 32 | 0.016 | `business` | 保留 registry/anchor/CRM 事实；无 project deadline 字段；CRM 不能仅因当前为空删除。 |
| `business_task_relations` | 0 | 0.016 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `email_category_configs` | 11 | 0.016 | `configs` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `email_reply_dispatch_claims` | 0 | 0.016 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `email_training_snapshot_observations` | 0 | 0.016 | `取消` | 当前为零，训练全集不入 DB；停用 SQL observation 写入及表 |
| `feedback_processing_transitions` | 5 | 0.016 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `setup_wizard_events` | 22 | 0.016 | `runs` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `workbench_artifacts` | 0 | 0.016 | `tasks` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `workbench_attachments` | 0 | 0.016 | `tasks` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `business_attention_proposal_tasks` | 2 | 0.012 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `business_attention_tasks` | 2 | 0.012 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `business_task_dingtalk_links` | 0 | 0.012 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `business_task_follow_up_send_attempts` | 0 | 0.012 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `business_task_follow_ups` | 0 | 0.012 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `business_task_todo_sync_outbox` | 0 | 0.012 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `business_work_cluster_tasks` | 16 | 0.012 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `codex_session_search_fts_docsize` | 654 | 0.012 | `取消` | 取消独立会话索引物理表；已存在的 session/source/title 元信息归入 runs/tasks，继续原生文件内存检索，不保存 FTS/embedding/摘要副本。 |
| `email_category_folder_bindings` | 22 | 0.012 | `configs` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `email_historical_operations` | 0 | 0.012 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `email_reply_receipts` | 0 | 0.012 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `feedback_iteration_decision_items` | 0 | 0.012 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `scheduled_task_skill_refs` | 34 | 0.012 | `configs` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `service_bugfix_candidates` | 3 | 0.012 | `business` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `workbench_tasks` | 20 | 0.012 | `tasks` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `business_project_candidates` | 10 | 0.008 | `business` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `business_project_evidence` | 22 | 0.008 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `business_task_date_evidence` | 0 | 0.008 | `actions` | actions 日期事实；保留日期/角色，直接 task/source FK；可溯源原话不长期存储。 |
| `candidate_selections` | 0 | 0.008 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `channel_login_reservations` | 0 | 0.008 | `state` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `codex_session_locks` | 0 | 0.008 | `state` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `corpus_sources` | 0 | 0.008 | `state` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `email_accounts` | 2 | 0.008 | `configs` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `email_historical_traversals` | 2 | 0.008 | `state` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `email_provider_folder_observation_generations` | 55 | 0.008 | `provider_state` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `email_scan_cursors` | 2 | 0.008 | `state` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `email_unsubscribe_continuations` | 0 | 0.008 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `feedback_iteration_decisions` | 0 | 0.008 | `tasks` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `feedback_processing_batches` | 3 | 0.008 | `tasks` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `managed_skills` | 21 | 0.008 | `configs` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `org_cache_metadata` | 2 | 0.008 | `state` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `runtime_route_pauses` | 1 | 0.008 | `state` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `setup_wizard_steps` | 12 | 0.008 | `state` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `wechat_memory_candidates` | 0 | 0.008 | `business` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `wechat_memory_import_jobs` | 0 | 0.008 | `tasks` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `wechat_read_state` | 1 | 0.008 | `state` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `wechat_reply_scopes` | 4 | 0.008 | `configs` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `business_work_clusters` | 16 | 0.004 | `business` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `candidate_supplements` | 0 | 0.004 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `codex_session_search_fts_config` | 1 | 0.004 | `取消` | 取消独立会话索引物理表；已存在的 session/source/title 元信息归入 runs/tasks，继续原生文件内存检索，不保存 FTS/embedding/摘要副本。 |
| `codex_session_search_fts_idx` | 4 | 0.004 | `取消` | 取消独立会话索引物理表；已存在的 session/source/title 元信息归入 runs/tasks，继续原生文件内存检索，不保存 FTS/embedding/摘要副本。 |
| `email_model_mode_transitions` | 0 | 0.004 | `configs` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `email_model_promotion_configs` | 4 | 0.004 | `configs` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `email_schema_migrations` | 33 | 0.004 | `email_schema_migrations` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `meeting_alignment_delivery_claims` | 0 | 0.004 | `actions` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `okr_review_runs` | 22 | 0.004 | `runs` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `runtime_feedback_iteration_capabilities` | 18 | 0.004 | `configs` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `runtime_skill_configs` | 18 | 0.004 | `configs` | 字段按实际语义迁移；保留被引用的状态、业务结果和约束。 |
| `codex_session_search_fts` | 662 | 0.000 | `取消` | 取消独立会话索引物理表；已存在的 session/source/title 元信息归入 runs/tasks，继续原生文件内存检索，不保存 FTS/embedding/摘要副本。 |
