# Business Model Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 退出重复的业务对象、队列状态机和结果写入路径，使同一事实只有一个权威来源；表数量不是验收指标。

**Architecture:** 保留业务对象、待执行任务、逻辑运行、实际调用尝试、已采用方案和外部动作结果的不同语义；统一各业务通道重复实现的生命周期。先生成完整字段/调用方/页面清单，再按动作、邮件、任务运行、旧业务模型四个独立发布单元替换；每单元独立完成副本正迁移、新写入、当前数据逆迁移和旧代码冷启动。

**Tech Stack:** Python、SQLite（含 JSONB / VIRTUAL generated columns）、pytest、React/TypeScript、Vitest、原生 Codex CLI、现有 app.deploy 发布流程。

---

## 0. 计划状态与执行边界

这是实施总计划，A/B/C/D 已通过组合副本验证与回退演练，并随 Store19.11／Email53 上线。最新已核实状态以 `docs/business-model-execution-progress.md`、最终发布验收及 2026-10-21 收尾记录为准。下方未勾选清单保留制定计划时的原文，不表示对应实现仍未完成；逐项证据见总进度表。

- 已部署代码基线：`a3779da46b1563228a0f3de8948227d1e6cfec5b`。
- 计划 worktree：`/Users/derek/.codex/worktrees/business-model-plans/ceo-agent-service`。
- 计划分支：`codex/business-model-plans-20261019`，基线 `ba4fffb09`（包含生产代码及此前完成记录）。
- 主开发目录存在其他会话改动；不从它复制未提交 app/test/frontend 文件。
- 正式生产库：`/Users/derek/Library/Application Support/ceo-agent-service/auto-reply.sqlite3`；只读盘点。实验写入只能使用 SQLite backup 生成的数据库副本。
- 实施前核对届时生产 HEAD/schema；本文件中的统计是本次观察值，不作为迁移时固定行数。
- 用户已要求继续以 provenance、必要状态、业务事实和执行回执为保留原则；原生 trajectory 不另存副本。邮件本地预览上限仍为 1,000 Unicode 字符。
- 本计划不增加任何审查、授权、确认、effect reconciliation 或其他策略；只保留并迁移现有行为。任何策略变化需独立提议。

**执行粒度：** 本项目跨四个子系统，不能把此前分析中的推测直接变成一个总迁移。配套 `2026-10-19-business-model-ownership-contracts.md` 是首个可独立执行的详细子计划：完整代码、测试、只读盘点和页面覆盖契约。它完成后，下面四个发布单元分别形成其精确变更补丁与副本验证；不得用“表已经合并”代替这些退出条件。下列是各单元的明确范围、字段处理、调用方和验收任务，不是允许跳过代码审查直接执行的 DROP 清单。

## 1. 已查实的现状与不能误删的边界

| 观察 | 实测值/代码依据 | 决策 |
|---|---|---|
| 业务模型并行 | work_project 975、work_todo 3,327；project 32、task 735 | 停止维护两套权威业务系统，不按标题强行合并 |
| 旧待办导入状态 | 3,327 个 work_todo 的 import_task_id 全为空 | 不可声称已迁完后直接删除 |
| 业务键映射 | 7,500 行、7,485 个 parent；4 个 parent 有多个业务键，最多 7 个 | 保留真实别名语义；不能整体删 business_object_tasks |
| 邮件成对元数据 | 已分类 9,483 行，sender/subject/received_at/folder 分别全部一致 | 可以计划单一消息元数据权威；需改读写契约与测试 |
| 邮件 RFC ID | 两套字段有 21 行不同 | 不纳入首轮字段删除；单独比对原生来源 |
| 状态事件 | 1,157 行，183 行错误 JSON 与父 run 当前值相同 | 不能按“都是重复错误”删除 |
| 外部回执重复写入 | reviewed_candidate_store.record_candidate_external_action 同写 external result 与 action attempt | 外部结果归 external_action_results；尝试只拥有尝试状态及未确认错误 |
| 邮件动作重复写入 | EmailStore.append_action_attempt 同写 attempt 和 action 最近结果 | 去掉第二份结果所有权；当前进度必须仍可索引查询 |
| 专用运行旧字段 | run_storage 的 TASK/MEETING/OKR *_RETIRED 与常量投影 | 删除失效 API 参数/输出，不是再次清理已不存在的物理正文 |

来源：`app/task_semantic_import.py`、`app/task20_definitions.py`、`app/run_storage.py`、`app/reviewed_candidate_store.py`、`app/email_canonical_definitions.py`、`app/email_store.py`。路径均相对于上面的隔离 worktree；盘点输出将给出绝对路径、函数与行号。

## 2. 文件结构与职责

| 位置 | 职责 |
|---|---|
| `tools/business_model_inventory.py`（配套首计划创建） | 纯只读 schema/类型/重复值/调用点/路由清单，不导入 Store，不打开生产写连接 |
| `tests/test_business_model_inventory.py`（首计划创建） | 合成库验证统计、路由提取、缺失表、只读不变性 |
| `docs/business-model-ownership.json`（首计划创建） | 从实际 schema 导出的字段级处置；每项 source/owner/consumer/inverse/场景，不是新 DB 表 |
| `docs/business-model-ui-coverage.json`（首计划创建） | 实际所有 Console 路由及交互场景的验收清单，不是运行时策略 |
| `app/action_storage.py`, `app/reviewed_candidate_store.py` | 候选/审查/执行/外部结果定义与权威写入 |
| `app/action_reply_storage.py`, `app/action_delivery_storage.py`, `app/action_postfix_storage.py` | 当前 Reply/Sent/Postfix 的存储边界及迁移依据 |
| `app/email_canonical_definitions.py`, `app/email_canonical_storage.py` | 邮件单一身份及字段所有权；不要继续复制旧 classification/message 全投影 |
| `app/email_preview.py`, `app/email_classification_reader.py`, `app/email_store.py` | 预览、源读取、邮件分类/动作调用方 |
| `app/task20_definitions.py`, `app/task_storage.py`, `app/task8_storage.py`, `app/run_storage.py` | 队列、输入版本和运行字段；修改同时删除专用接口 |
| `app/dispatcher/adapters.py`, `app/dispatcher/service.py` | 调度/领取/续租/完成：保留语义，只统一实现 |
| `app/store.py` | 只改明确的业务方法；不借此重写整个大文件 |
| `app/task_semantic_import.py`, `app/task_semantic_service.py`, `app/task_agent.py` | 旧对象事实判断、正式任务采用、业务证据 |
| `app/task_retrieval.py`, `app/todo_completion.py`, `app/todo_sync.py` | 退出旧模型的真实读写入口 |
| `app/web_api/tasks.py`, `app/audit_web.py`, `app/history.py`, `app/history_actions.py` | Console API、历史身份与查询；不以恢复旧 DB 副本提供兼容 |
| `frontend/src/app/router.tsx`, `frontend/src/pages/TasksPage.tsx`, `frontend/src/pages/TaskParts.tsx`, `frontend/src/pages/AttemptDetailPage.tsx`, `frontend/src/pages/email/` | 页面和交互调用方改造 |
| `app/store_schema_release.py`, `app/email_schema_release.py`, `app/deploy.py` | 在既有配对迁移框架注册每次发布；不实现第二个部署器 |
| `docs/architecture.md`, `docs/runtime-mechanism.md` | 与运行代码同提交更新，删除已退出机制的描述 |

## 3. 单元 A：动作只有一个结果权威

### A1. 取消选中分支的全文副本

**修改：** `app/reviewed_candidate_store.py:select_candidate_option`、`app/action_storage.py`、读取 `branch_json` 的调用方。**测试：** `tests/test_reviewed_candidate_store.py`、`tests/test_action_storage.py`、`tests/test_reviewed_candidates.py`。

- [ ] 在原有候选 fixture 增加两个选项；选择其中一个，读取结果仍与所选候选分支一致。
- [ ] 测试同 candidate/同 option 重复选择返回原选择；改 option 仍保留现有冲突行为。
- [ ] 测试候选被修订后，旧选择仍绑定原候选；不从“最新候选”取内容。
- [ ] 用现有不可变 candidate_json + option_key 查询分支；删除 branch_json 的写入、schema 字段和使用点。
- [ ] 副本核验每条旧 branch_json 与其候选 option 一致；不一致项逐项保留现有已采用事实，不能默认覆盖。
- [ ] inverse 以每条选择绑定的不可变 candidate/option 重建旧 branch_json。没有绑定的历史行不静默补空。
- [ ] 跑上述三个文件；更新两份机制文档；提交 `refactor(actions): derive selected branch from immutable candidate`。

选择分支的实际内容关系：

```python
import json

def selected_branch(candidate_json: str, option_key: str) -> dict:
    candidate = json.loads(candidate_json)
    return next(option for option in candidate["decision_options"] if option["key"] == option_key)
```

这是确定的查找算法，不新增 DB 字段；既有选择冲突检查保持在原事务内。

### A2. 外部结果只保存一次

**修改：** `app/reviewed_candidate_store.py:record_candidate_external_action`、`app/action_storage.py`、`app/history.py`、`app/history_actions.py`、`app/store.py` 内相应详情查询。

- [ ] 测试动作首次成功后，external_action_results 有完整 provider_result_json；candidate_action_attempts 不再有同一份已确认回执正文。
- [ ] 测试失败但未产生 effect 的尝试仍保留自己的错误；不能把失败错误误当作可重复外部结果。
- [ ] 测试 existing external_action_key 被另一修订引用仍返回原已发生事实；不能按 task/candidate cascade 删除。
- [ ] 同一事务写权威结果并推进尝试状态；尝试通过已有 external_action_key 查结果。
- [ ] 所有展示、恢复、幂等查询读权威结果；停用第二处已确认结果写入。
- [ ] inverse 用 external_action_key 从当前权威结果恢复旧 attempt 投影；副本上验证迁移后新增结果也能恢复。
- [ ] 运行 `python -m pytest -q tests/test_reviewed_candidate_store.py tests/test_action_receipt_schema_release.py tests/test_history_actions.py`；三文件及文档同提交。

字段所有权：

```text
external_action_results:
  external_action_key, business_object_key, action_identity, operation,
  target_identifiers_json, provider_result_json, result_digest,
  first_agent_run_id, created_at
candidate_action_attempts:
  execution_id, action_index, external_action_key, status,
  created_at, updated_at；失败/未确认结果仍属于本次尝试
```

### A3. 一对一候选/执行合并，Reply/Sent/Postfix 退出重复所有权

- [ ] 对 review_candidates ↔ candidate_executions 按 candidate_id 统计 0/1/多，列出实际选择/审查引用；多对一是阻止直接折叠的事实，不能修数据凑一致。
- [ ] 在同一不可变候选版本上复用现有 status/lease_owner/lease_expires_at/result_json/updated_at 的执行事实；保留 candidate_reviews 的独立历史和 audit_run_id。
- [ ] 不改候选修订、审查反馈次数或 terminal state；status 的候选有效性和执行进度不能拼成含糊的单一状态。
- [ ] reply_attempts 的全链路展示改为读取 Task/Run/Candidate/外部结果；逐项归属其独有字段后退出写入类型。尚未找到权威归属的字段仍保留，不能只改列表页。
- [ ] sent_replies 的 recall_key/recall_status/recall_error/recalled_at/feedback_token 保留已存在的消息业务语义；send_result_json 从权威结果获取。
- [ ] outbound_postfixes 保留 final_body/feedback_token/postfix_version；outbound_postfix_receipts 的 provider 结果转权威结果，不能删除未知发送事实。
- [ ] 所有旧公共 ID 先转向现有稳定引用；旧链接在同一次发布有明确去向，不长期保留两套 writer。
- [ ] 用 `tests/test_reply_attempt_action_storage.py tests/test_sent_delivery_action_storage.py tests/test_action_postfix_schema_release.py tests/test_console_attempt_detail_api.py tests/test_failed_history_projection.py` 验证；提交完整边界。

**单元 A 完成判据：** 退出的记录类型不再有 writer/allocator/生成列/启动补写；UI 能显示所有保留事实；未知外部结果不会重新发送；新增事实逆迁移后可由旧代码读取。

## 4. 单元 B：邮件只有一套消息事实和一套动作机制

### B1. 消息元数据权威

**修改：** `app/email_canonical_definitions.py`、`app/email_canonical_storage.py`、`app/email_preview.py`、`app/email_classification_reader.py`、`app/email_store.py`。**测试：** `tests/test_email_canonical_storage.py`、`tests/test_email_canonical_current12.py`、`tests/test_email_preview.py`、`tests/test_email_classification_reader.py`、`tests/test_email_store.py`。

- [ ] 用 sender/subject/received_at/folder 不同的合成输入明确改变后的显示契约：分类列表读取消息权威元数据；历史“执行当时”的必要事实从绑定方案/版本读取，不能从当前邮件还原。
- [ ] 普通邮件/已分类邮件均只保留现有 message_preview，最多 1,000 字符；迁移把已分类 class_preview 搬到 message_preview，不重新获取原文。
- [ ] 普通列表/默认搜索不得触发原文 provider 读取；full 检索和详情按原来源接口读取，源失效明确显示不可用。
- [ ] 删除 class_sender/class_subject/class_received_at/class_folder/class_preview 的物理独立列及独立写入所有权，改所有 current reader；HTTP 字段名可保持业务 API 名，不算新增存储副本。
- [ ] 不删除 class_rfc_message_id，先逐项解释 21 条不同来源；不把分类 model_text 当作 normalized_text 的同义副本，它可能是特征输入。
- [ ] 在现有 EmailSchemaRelease 框架提供配对 inverse：旧成对元数据由当前权威字段重建；必要历史差异必须在迁移前证明已在已采用事实中保留。
- [ ] 跑五个测试文件和 frontend/src/api/email.test.ts；同步文档，提交 `refactor(email): make message metadata authoritative`。

### B2. 邮件动作退出独立生命周期

**修改：** `app/email_store.py:_persist_action_plan/_ensure_direct_action_rows/append_action_attempt/complete_direct_action_attempt`、`app/email_provider_actions.py`、`app/email_action_reconcile.py`、`app/email_unsubscribe.py`、`app/action_email_definitions.py`、单元 A 的动作 owner。

- [ ] 列出 email_action_plans → email_actions → email_action_attempts 的全部引用、真实 action_id、计划版本与最近结果副本；与单元 A 的候选/动作映射逐项比对。
- [ ] 分类改变产生新方案版本；旧执行绑定旧版本，不读取新规则替换历史 authorization_snapshot_json/config_version。
- [ ] move/mark/delete/reply/unsubscribe 逐个迁入同一动作领取/重试接口。无需模型的 provider 动作不能伪造 Agent run、审查或 session。
- [ ] 删除 email_actions 对最近尝试 provider_operation/provider_target/provider_result_id/error/finished_at 的第二次保存；当前展示从权威尝试和回执获得。
- [ ] email_reply_receipts/email_unsubscribe_receipts 的独有 outgoing_message_id/receipt_id/provider_receipt_json/effect_digest 等保留在对应权威动作事实；动作未知时不重新触发 effect。
- [ ] 已完成 unsubscribe 的浏览步骤/continuation 退休到原生来源；未完成项保留真实恢复数据。不能把网页“已提交”误当完成。
- [ ] 历史处理队列保留 model_id/uidvalidity/last_seen_uid 和进行中身份，终态历史记录由同一动作结果查询；不因合并队列重新跑历史邮件。
- [ ] 运行 `python -m pytest -q tests/test_email_action_storage.py tests/test_email_action_reconcile.py tests/test_email_unsubscribe.py tests/test_email_unsubscribe_direct.py tests/test_email_unsubscribe_evidence.py tests/test_email_history_native_parity.py`；再跑邮件组件测试和副本链路。

**单元 B 完成判据：** metadata 单写；无 email 专用平行 plan/action/attempt 状态机；保留预测/人工确认/采用结果区别；预览不会成为模型全输入；重分类不重放历史发送。

## 5. 单元 C：任务调度和运行接口统一

**修改：** `app/task20_definitions.py`、`app/task_storage.py`、`app/task8_storage.py`、`app/store.py`、`app/run_storage.py`、`app/dispatcher/adapters.py`、`app/dispatcher/service.py`、`app/task_agent.py`、`app/meeting_alignment.py`、`app/okr_review.py`、`app/native_standalone.py`。

- [ ] 分别为 reply、work_summary、meeting、email_classification、weekly_okr、feedback、workbench 列现有状态集合和转换，不先发明通用状态枚举。
- [ ] 对共享的 enqueue/claim/lease/retry/complete 操作采用现有公共字段语义；会议结束等待、周报缓存命中、Workbench turn 等特有参数保留各自业务含义。
- [ ] 将 task_agent_runs/meeting_alignment_runs/okr_review_runs 的状态/session/range/projection 迁入共用运行 owner；父任务和实际运行不能按相同整数 ID 误连。
- [ ] 删除旧方法中不再存储的 decision_json/audit_summary/audit_tool_events_json/memory_recall_used 参数及固定空返回字段；必要真实结果仍从精确 native range 读取。
- [ ] agent_runs 保留逻辑执行/审查关系；agent_runtime_attempts 保留多次模型调用/路由与失败；不能因行数多压成一个最后状态。
- [ ] reply_task_inputs 保留当前/已领取输入版本身份；删除重复当前元数据前验证运行期间新输入到达不会改写旧 run 的实际输入。
- [ ] business_object_tasks 的单一身份映射可用已有 business_object_key 索引替代，真正多键归并必须保留现有输入上的原始业务身份，不能删除 OA 临时键升级行为。
- [ ] codex_session_search_index 改为由实际任务/运行/native source 生成；删除独立注册 writer；验证搜索相关性与原文不可用分支。
- [ ] agent_run_state_events 的历史终止原因逐条归属现有运行，不覆盖原始运行错误；未能保留的差异项不纳入删除批次。
- [ ] scheduled_task_runs 的 occurrence_count/first_scheduled_for/scheduled_for/snapshot_id/实际 execution 引用保留。未 dispatch 的 skipped 记录可按现有压缩语义处理；187,948 行不是无效记录证明。
- [ ] 运行 `python -m pytest -q tests/test_task20_definitions.py tests/test_task20_current_schema.py tests/test_run_storage.py tests/test_run_storage_readers.py tests/test_native_standalone.py tests/test_dispatcher_idle_claims.py tests/test_consumer_dispatcher.py tests/test_console_scheduled_tasks_api.py`。

**完成判据：** 每个任务处理器调用同一生命周期接口；专用运行类型和旧 API 真正消失；任务失败/needs_feedback/needs_human/skipped 语义、修订关系、原生 session/range 可用性与当前一致。

## 6. 单元 D：退出旧业务系统

2026-10-20 九字段子单元：用户已明确批准永久删除 WorkProject 五项、WorkTodo 两项、
WorkUpdate 两项（包含 merge_reason），不复制旧值；逆迁仅恢复声明与中性默认值。
[完整51→42映射](../../business-model-legacy-fields-mapping.md)绑定 fresh19.8 来源 SHA 与
9,770 个历史身份处置，保留 follow_up_question、changes_json、全部业务事实和回执。
实现提供明确19.8/19.9/19.10→19.11事务链，仅最终构造器加载新声明；源支持的条件性
正式导入保留，manifest升为4。历史 reader/URL 继续有确定结果，不将同号/同标题身份晋升。
有界测试、真实旧代码中性逆迁和前端构建证据见[验证记录](../../business-model-legacy-fields-validation.md)。
这不表示完整D、独立全量副本、原生验收或正式发布完成；此前A5原生失败仍保持失败，待根任务新证据。

2026-10-20 用户确认的旧 provider Todo 处置：540 条 recorded-active 旧链接没有可核验的
current Task 身份，仅按原身份保留来源、业务事实和执行回执；不自动晋升、取消、完成或
重发真实外部 Todo。D1b 退休旧 provider 执行代码及 dispatcher adapter 队列，保留物理事实和历史 reader；
独立 D1c 已退出旧 `DingTalk Todos` Status 聚合、专属 retryable 分支和旧 link/outbox
小时质量检查来源；后续 DDL 独立处理。当前 BusinessTask 路径保持现有语义。
D1b/D1c 的本地回归与源码冻结不表示完整 D、root 副本回退验证、正式迁移或上线验收完成。

**修改：** `app/task_semantic_import.py`、`app/task_semantic_service.py`、`app/business_definitions.py`、`app/task_agent.py`、`app/task_retrieval.py`、`app/todo_completion.py`、`app/todo_sync.py`、`app/action_followup_sync_definitions.py`、`app/store.py`、`app/web_api/tasks.py`、`frontend/src/pages/TasksPage.tsx`、`frontend/src/pages/TaskParts.tsx`、`frontend/src/app/router.tsx`。

- [ ] 对每个旧 work_todo 按现有 formal_task/history_only 规则生成来源绑定处置；逐条保存实际 task 身份映射；不能按标题或共享整数 ID 合并。
- [ ] 正式任务、候选任务、历史噪声区分处理：旧 open 不自动变 formal；已完成且没有采用价值的记录可按已授权保留原则退休，但不可丢回执。
- [ ] work_project 只在正式注册证据成立时绑定 project；未确定的一组工作使用已有 work_cluster/候选语义，不伪造 CRM 关系。
- [ ] 负责人/截止日期/完成证据迁入 task 与现有证据关系；旧 priority/blocker/follow_up 等不能默默丢弃，逐字段决定保留业务结果、转现有关注/跟进机制或取消功能。
- [ ] work_updates 的已采用结论进入 task/project_context_revision 与现有业务事件；来源仍是 provenance；旧摘要和 changes_json 不另建历史镜像。
- [ ] 停止 create/update_work_project/work_todo/create_work_update；替换 task_retrieval、todo_completion、todo_sync 和 Console reader；删除启动补写旧模型代码。

2026-10-20 D2 有界进展：源码确认五个旧 Store writer 无运行时调用者并已移除；历史测试改用 typed 事实种子。reader、物理事实及正式来源支持的 `task-semantic-import-apply` 保留。上项的完整 reader/URL 迁移与 D 总体验收尚未完成；本地验证见 `docs/business-model-legacy-write-retirement-validation.md`。
- [ ] 当前钉钉 link 和 sync outbox 只使用同一权威 business task；旧记录无法核验 current Task 身份时仅保留原身份的来源、业务事实和回执。退出其运营查询、调度和写入，保留历史查询，不按共享整数 ID 关联或晋升，也不取消、完成或重发真实外部 Todo。D1b 执行代码及 dispatcher adapter 队列、D1c 旧 Status 聚合/retryable 分支及旧来源质量检查、后续 DDL 分别验收。
- [ ] legacy-project URL 不再回读旧表：可唯一映射时转正式目标；多对象分解时提供该历史身份已有事实的列表；退休且无业务价值时明确不存在，不返回空白成功。
- [ ] 对 project/task/anchor/证据/关系/关注项分别验证：stage/formal_basis/commitment_status/status 的语义、合并 lineage、来源引用、多项目关系仍完整。
- [ ] 运行 `python -m pytest -q tests/test_task_semantic_import.py tests/test_task_semantic_service.py tests/test_task_semantic_store.py tests/test_task_retrieval.py tests/test_todo_sync.py tests/test_business_console_source_reader.py tests/test_task_console_candidate_decision.py tests/test_task_console_project_candidate.py`；前端 Tasks/TaskParts/API 测试与真实副本浏览器走查通过后提交。

**完成判据：** work_project/work_todo/work_updates/todo_evidence_candidate 旧链路退出运营写入、运营查询和补写；每个旧身份有明确处置，旧 URL 有确定结果。按用户确认保留的旧 provider 来源、业务事实和执行回执仍可按原身份历史查询；本批不代表上述旧业务模型整体迁移完成。

## 7. 每个单元都必须完成的迁移/回退步骤

- [ ] 用 sqlite3 backup 从同一时点生产库创建 source/candidate；记录时间、源 HEAD、schema、DB/WAL bytes；不直接复制正在运行的 main 文件。
- [ ] 在 source 上记录业务身份、外键、状态、来源 hash、已采用事实和回执的摘要。严格区分物理字段等价与业务语义等价。
- [ ] 在 candidate 正迁移；integrity_check 返回 ok；foreign_key_check 与源基线比较，不能忽略原有异常或把空查询当全库通过。
- [ ] 用合成新身份完成创建→执行→审查 approve；真实 CLI 与 provider 替身组合验证。CLI 不加载真实发送凭据、写能力 MCP 或可外发命令；确认运行配置和实际 tool trace，没有真实发送。
- [ ] 审查 return→修订→approve、reject→内容修订、needs_human、skipped、运行失败/重试、进程终止续跑、未知外部回执仅核对、输入版本更新分别验证。
- [ ] 保留 candidate 上新增事实，运行 paired inverse；不能拿 source 文件覆盖回去作为“schema 回退通过”。
- [ ] 用该次发布的真实旧代码在逆迁移副本冷启动：新增业务结果/回执可读、未完成任务仍可恢复、已完成动作不会重放。
- [ ] 再正迁移当前数据，检查新写入、主键分配水位、外键和业务结果全部保留。
- [ ] 在执行测试之前/之后核对生产 sender/provider 日志无测试身份；测试副本不连接主服务 worker。
- [ ] 相同数据时点测 main/索引/空闲页/WAL 与各类型计数；如做 VACUUM，只作用副本并明确空闲页回收与语义减少的区别。
- [ ] 代码+测试+architecture/runtime 文档同提交；提交可审查，按现有发布流程集成到 pushed origin/main 后执行 `python -m app.deploy`。
- [ ] 发布后读回 PID、healthz、队列、Attention、History、目标 schema 与已退休 writer 缺失情况。生产仅只读抽查，不创建测试发送。

回退只恢复当前事实所需的旧结构；不长期存旧数据库行影子。若取消的业务功能会使新事实无法表达为旧模型，必须在该单元精确设计中明确旧模型表示方式并通过反例测试，不能临上线再决定。

## 8. 页面与场景必须全量自检

以真实 `frontend/src/app/router.tsx` 为清单源，配套首计划生成路由列表。每个路由填写下列场景的执行结果，禁止一项“页面正常”覆盖全部交互：

| 路由/页面族 | 必须检查的业务场景 |
|---|---|
| `/`、`/status`、`/workers`、`/attention` | 计数、当前故障、空队列、延迟、失败、恢复、详情跳转、刷新后新事实 |
| `/history` 及 `/history/errors/:errorId` | 过滤、分页、顺序、失败与当前结果区别、来源不可用、旧/新 ID 详情 |
| `/history/attempts/:attemptId`、`/attempts/:attemptId`、`/attempts/:attemptId/execution/:role` | 执行/Audit/修订、回执、原生轨迹范围、未知发送状态、重复动作引用 |
| `/history/meeting-attempts/:runId`、`/meeting-attempts/:runId` | 会议输入、业务结果、日历摘要、发送/Memory 回执、原文缺失 |
| `/history/oa-approvals/:processInstanceId`、`/oa-approvals/:processInstanceId` | OA 多业务键、审批身份、历史关联、执行/审查/回执，测试写入只用替身 |
| `/tasks`、`/tasks/item/:taskId` | 正式/候选/合并/完成/取消、负责人、时间证据、关系、来源、动作按钮刷新 |
| `/tasks/project/:projectId`、`/tasks/legacy-project/:legacyProjectId` | 正式注册、CRM、未归属工作、历史处置、旧深链接、无映射、多对象映射 |
| `/tasks/attention/:attentionId` | 关注原因、关联任务、已解决/重开、证据、详情操作 |
| `/scheduled-tasks` | 创建/编辑/启停/删除/手动触发、配置版本、重叠、暂停、错过执行、历史详情；副本替身 |
| `/email` | 列表/分类/分页/过滤、预览/full 搜索、Unicode、原文/附件、反馈重分类、历史扫描、训练/模型版本、移动/标记/删除/回复/退订；写动作仅替身 |
| `/user-feedback` | 待处理/处理中/完成、批次、重新打开、不同 round 证据、提交/测试/重启回执 |
| `/codex`、`/codex/:sessionId` | 原生来源存在/丢失、精确范围、搜索、终态、刷新、历史 ID |
| `/wechat/review`、`/wechat/memory-review`、`/wechat/deliveries`、`/wechat/conversations` | 输入游标、待审/发送/Memory 状态、回执、跨修订关联；provider 替身 |
| `/settings` | runtime/邮箱/类别/skills/配置版本与 secret_reference；不展示 secret 原文 |
| `/notifications`、`/tutorial` | 真实数据加载、空态、详情/导航，无旧表读取错误 |
| `/config`、`/developer-prompt`、`/logs`、`/errors`、`/errors/:errorId`、未知路径 | 重定向、参数、错误详情、404 行为不受旧模型退出破坏 |

每页面统一检查加载中/空态/正常/接口错误/刷新与返回；适用时另加分页、过滤、权限、并发改变。对该页面不适用的交互写明具体原因，而非跳过整页。路由覆盖率 100% 指列出的路由均有记录，并不声称穷举所有可能状态；所有列出的业务场景必须有 PASS、失败修复记录或明确产品取消决定，未跑不能算通过。

前端命令：`cd frontend && npm test -- --run`，以及 `npm run build`。后端只跑单元相关测试，不在 live 主工作目录串行运行全套 pytest。浏览器连副本服务，生产浏览器仅只读回归；测试服务端启动方式使用现有测试 harness，不能让 production worker 误连副本。

## 9. 验收产物与完成定义

- 每个实际字段的来源、owner、所有消费者、迁移表达和 inverse 都有记录；无法解释的字段不得直接删除。
- 每个退休类型都有 writer/reader/生成列/索引/allocator/启动路径的退出证据；历史迁移声明可留在明确的历史模块，不参与新运行。
- 副本正迁移、迁移后新事实、当前事实 inverse、真实旧代码冷启动、重新正迁移的业务事实一致。
- 全路由/已列场景通过；真实 Codex CLI 和替身 provider 的边界有 trace 证据。
- 同数据容量对比、残余类型和增长来源可解释；不能用表数、测试用例总数或 HTTP 200 代替业务验收。
- 交付时分别报告：计划完成、代码完成、副本验证完成、生产已部署、生产只读回归；不得把前一项写成后一项。

## 10. 建议提交与发布顺序

1. ownership/schema/caller/UI 契约（纯只读工具与测试，不迁移、不部署后台策略）。
2. A1/A2：去选中分支副本、单一外部结果 owner，各自小提交/可独立验证。
3. B1：邮件元数据与预览 owner，独立 paired inverse。
4. A3：候选/执行与 Reply/Sent/Postfix 退出，完整回退单元。
5. B2：邮件动作统一，避免仍依赖已退出旧动作类型。
6. C：统一任务/运行，保留逻辑运行与 runtime attempt 的真实区别。
7. D：旧业务模型退出；其数据语义和 UI 改造与前几项独立，不靠清空历史达成。
8. 每个单元通过其副本/页面/回退后独立发布，不等到末尾才做第一次回退。

执行方式按用户选择：subagent-driven 或 inline。写计划不代表已授权启动任何新的子 agent；当前没有派发。

## 11. 本轮计划自审记录（不是运行重构验收）

- 2 份文档约 700 行；覆盖四个独立发布单元、逐字段处置原则、旧接口退出、副本/新写入/inverse/旧代码冷启动及页面验收。
- 所有 Python fenced blocks 均经 ast.parse；首子计划的工具与测试原样提取到任务拥有的临时目录执行：RED 5 failed（缺工具 FileNotFoundError），GREEN 5 passed。未把这些代码安装进 app 或正式 tests。
- 工具原样只读实跑当前生产库：11 张应用表、115 个已有数据的 record_type、36 条前端路由。引用字符串位置 10,161 个，明确包含历史声明/说明/reader，不是 10,161 个运行 writer。
- 只读检查复现：邮件四组成对元数据各 9,483/9,483 一致，RFC ID 21 行不同；7,500 映射对应 7,485 parent、4 个多键 parent；3,327 旧 TODO 已导入 0；1,157 状态事件中 183 条与父 run 当前错误相同。
- 旧页面/别名/404、训练、来源失效、未知回执、迁移后新事实均有验收条目。所有实际浏览器/CLI/迁移结果仍未执行；不得标为通过。
- 反例自审：没有把 model_text 与 normalized_text、正式 project 与工作聚类、logical run 与 runtime attempt、阶段/承诺/完成状态当同义字段；没有把全部映射或状态事件当重复记录。
- 路径自审：总计划引用的现有源码/测试文件存在；首计划明确标注的新工具/测试文件尚未创建。原样代码测试与只读实跑的两个临时目录均由 TemporaryDirectory 退出清理。
- 文档输出边界：总计划已具体到各单元的范围、文件、主要步骤、命令和验收；完整逐步工具代码属于首个子计划。其余单元的完整目标 DDL/迁移 patch 必须在首子计划的所有权核对后分别固定，不能声称现在已有可直接发布的完整实现补丁。
