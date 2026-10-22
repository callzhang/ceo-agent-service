# 旧业务九字段退休验证

> Status reconciliation, 2026-10-21: this is a scoped historical implementation/acceptance record. Its publication-pending statements are preserved as history; the subsequent Store19.11／Email53 combined copy, inverse and production deployment are recorded in [final release evidence](database-gpt6-release-validation.md). See [current execution progress](business-model-execution-progress.md) for unit status; historical FAIL results are not rewritten.

2026-10-20，本地候选 Store `2026-10-19.11`／Email53 已完成九字段退休、当前事实中性逆迁及相关历史 reader 的有界实现验证。此记录不表示已部署，不把 provider 替身或本地 fixture 当作原生/生产证据。根任务随后完成独立 SPEC / QUALITY、两套实际全量副本往返、容量和只读浏览器验证，详见[根任务验收](business-model-legacy-fields-root-acceptance.md)；标准生产发布仍未执行。此前 A5 的两个原生 FAIL 仍为 FAIL，不由本批测试改写。

## 批准与来源

批准范围和完整 51→42 映射见[字段处置](business-model-legacy-fields-mapping.md)。永久删除的九个旧值不保存副本；仅 schema inverse 补中性默认值。保留 follow_up_question、changes_json 及全部其它业务事实/来源/回执的 SQL 值、类型、身份和关系。

根任务持有的只读输入来自实际生产 Store19.8／Email53，源 HEAD `763caecd3ccb550aa3c2608eac6746d8ff3296d4`，副本 SHA-256 `60ee5d5b88395235768868deda215b5b371facf931522932463a29d474f539a2`。原 FK multiset 为 `runs / 190042 / tasks / 1` 一项；不能因这是既有 finding 而将其忽略。根任务已准备19.10副本及独立原 SQL 列名 oracle，本实现者未写该输入。源清单将975/3327/5468个来源身份全部分类为history_only；不能由此推断未来正式导入也必须为history_only。

本地合成数据库使用三个真实 Git 版本的 app 工厂，而非伪造版本 marker：19.8 为 `c4a9e794d0fe7b8954a8746b01fec118337dc188`，19.9 为 `45f3324be3c568b9f91709c25306e19d73a15087`，19.10 为 `8ff29625154988e4ff32f6a076ffc7595a1af120`。19.8合成来源与生产HEAD不同，实际生产旧代码冷读属于根任务的全量副本验证。19.10同时覆盖actual-no-memory-FK及fresh-memory-FK两个声明变体。

## 公共发布路径与逆迁边界

现有 `app.store_schema_release.prepare_database_schema_release(root, path)` 在目标19.11/Email53选择 `BusinessLegacyFieldSchemaRelease`；也可明确调用 `prepare_legacy_business_field_schema_release(root, path)`。准备阶段只读，要求准确19.8/19.9/19.10源与Email53。`publish()`在一个事务内按来源调用既有A5、C2和九字段转换原语，最后更新marker并只运行最终构造器；不在19.11代码中加载中间19.9/19.10 runtime。`verify_loaded()`核对目标，`finalize()`复核完成状态。

调用者必须保留prepare得到的release对象。`rollback()`读取逆迁时的当前数据库，恢复原声明、原Store marker及原时钟、Email账本，保留正迁后新增的保留业务事实、当前Task和provider receipt。只有九项补中性默认值。不能将源数据库文件覆盖回去作为schema inverse；不能在19.11重新prepare后推断原源版本。若根任务另建逆迁副本，可用 `dataclasses.replace(release, database_path=inverse_path)` 保留已核实的原发布元数据。

release保存的字段是root/path、原marker/clock、Email账本、reply声明变体及原FK findings；转换阶段只返回 `{"reply_variant": ...}`。没有旧九字段值、业务行快照或归档。转换所需TEMP行在复制前已删除九字段，并在提交前移除；sequence从当前库捕获并恢复原ROWID、名称、值及类型。历史19.10 Business/Actions工厂仍与真实8ff输出一致。

## RED 与 GREEN

固定解释器为 `/Users/derek/miniforge3/bin/python`（Python3.12.11、SQLite3.53.4），工作目录为隔离的 `business-model-legacy-todo-monitor-retirement/ceo-agent-service` worktree。所有数据库均为临时合成fixture；测试不使用真实provider。测试运行期间没有编辑源码，没有运行全套pytest。下列批次有重叠，不能相加当作唯一样例总数。

当前临时证据目录 `/tmp/ceo-legacy-field-core.iws38jpa` 由根任务保留到审查和发布验收结束；原始日志、精确节点清单、命令与文件哈希随冻结清单交接。它不是永久数据库备份目录。

| 批次/日志 | 结果及边界 |
| --- | --- |
| `red.log` | 五个正确RED：三个DTO、目标DDL/索引、WorkUpdate布局尚未退休 |
| `red-migration-source-ready.log` | 四个正确RED：真实源fixture已建立，尚无新release选择 |
| `red-consumers-valid-api.log` | 四个正确RED：实际API、retrieval、HTML、import仍访问旧字段 |
| `green-initial.log` | 初始五个退休断言通过 |
| `green-core-consumers.log` | 13 passed；19.8/19.9组合链、19.10两变体往返、四个实际consumer通过 |
| `green-scoped-domain.log` | 133 passed / 16 failed；其中三项直接相关，随后精确修复；其余13项真实8ff基线同原因失败 |
| `green-selectors-strict-focused.log` | 28 passed / 1 failed；三个相关点、guarded update、fresh/cold Store/Email/Workbench、strict声明、quality selector、只读repair planner通过；一个Email fixture在真实8ff同原因失败 |
| `green-ui-publication.log` | 79 passed / 6 failed；页面、provider替身、quiet/maintenance有界节点；六项旧fixture随后精确修复 |
| `green-ui-fixtures-exact.log` | 5 passed / 1 failed；旧字典priority及risk排序fixture已退休；剩余为测试本身旧WorkUpdate投影 |
| `green-ui-fixture-projection.log` | 最后一个投影fixture修复后1 passed；上述85个UI/publication节点最终均有通过记录 |
| `frontend-focused.log` | console API及TaskDetailPage两个文件，55 passed |
| `npm-ci.log`、`frontend-build.log` | worktree内按原lock安装，lock未变；TypeScript检查与Vite构建通过 |
| `ruff-final.log` | 45个变更Python文件全部通过，最终冻结时重核文件与diff |

`tests/test_business_legacy_field_retirement.py`的17个核心节点最终均有通过记录：九项非默认旧值、19/16/7 DTO字段数、payload键/slot及生成列实际消失、两个索引退出；保留NULL/TEXT/INTEGER/REAL/BLOB和JSON文本；无关Business/Action kinds与provider receipt；新保留事实/Task/receipt后inverse/真实旧构造器/reforward；原marker二进制时钟与sequence ROWID；整个组合事务被中断时数据库字节恢复；旧源由三个当前构造器在写入前拒绝；历史工厂输出冻结。当前writer拒绝九字段，直接SQL也不能塞回额外key/slot。

来源核对与条件性正式导入测试的必要断言保留；原native浮点摘要测试从已退休confidence迁到保留的project_id，继续核对整数/REAL变化、点查询/全量扫描摘要、索引路径和原子性。旧priority/risk/next-follow-up断言按批准功能明确退休；category/sort路由仍检查保留的owner排序。Todo历史保全测试仍逐值比较原历史事实及回执，provider替身仍检查实际调用列表。

## 真实8ff失败基线

仅执行下列精确节点。`baseline13-original8ff.log`记录13项、`baseline-email-original8ff.log`记录一项。app来自根任务逐Git blob核对的真实8ff归档，测试与必要helper/conftest也从同一8ff提取并逐文件SHA-256绑定（13节点需17文件，加入Email后20文件）。`baseline8ff-test-files.json`、`baseline13-nodes.json`、`run-baseline13.py`及`run-baseline-email.py`保留命令/身份依据。所有失败均为相同业务层异常或断言，没有把缺测试支持/资源造成的新错误当作复现；本批不扩修这些基线。

| 精确节点（省略共同 `tests/` 前缀） | 当前与8ff一致的原因 |
| --- | --- |
| `test_task_store.py::test_current_task_execution_schema_repairs_missing_auxiliary_tables` | `KeyError: business_task_dingtalk_links` |
| `test_task_store.py::test_ensure_reply_tasks_rolls_back_group_on_identity_conflict` | `no such column: t.channel` |
| `test_task_store.py::test_ensure_reply_tasks_rolls_back_group_on_database_error` | `no such column: new.trigger_message_id` |
| `test_task_store.py::test_enqueue_and_claim_work_summary_input` | `no such column: t.source_type` |
| `test_task_store.py::test_duplicate_work_summary_input_does_not_reopen_terminal_status[failed]` | `no such column: t.source_type` |
| `test_task_store.py::test_duplicate_work_summary_input_does_not_reopen_terminal_status[skipped]` | `no such column: t.source_type` |
| `test_task_store.py::test_reset_stale_processing_work_summary_inputs_requeues_orphans` | `malformed JSON` |
| `test_task_store.py::test_work_summary_retry_backoff_delays_claim_until_available` | `malformed JSON` |
| `test_task_store.py::test_recent_single_chat_recovery_uses_task_activity_when_seen_row_is_old` | `no such table: seen_messages` |
| `test_history.py::test_history_uses_current_run_while_failed_attempt_is_recovering` | `no such column: t.channel` |
| `test_web_api_task_attention.py::test_detail_routes_show_source_evidence_and_confirmed_membership` | source evidence文本为NULL，与旧测试所期望正文不同 |
| `test_task_api_release_contract.py::test_task_detail_contains_all_relations_and_anchor_links` | `no such table: business_task_relations` |
| `test_task_api_release_contract.py::test_task_detail_contains_all_follow_ups_beyond_store_page` | `no such table: business_task_follow_ups` |
| `test_email_store.py::test_normal_startup_does_not_repair_missing_durable_rows[email_actions]` | `no such column: a.email_attempt_count` |

## 根任务验证契约与结果

冻结后先SPEC再QUALITY审查。实际19.10副本及生产19.8直接组合链必须各自核对全部保留事实/types/identities/relations/ROWID/sequences/FK findings，正迁后添加当前Task与provider替身回执，再inverse，用相应真实旧代码冷读全部历史DTO及新事实，然后reforward。A5私有prepared子行的既定物理ID例外与其公开事实应分别核对。比较需要使用源侧列名和独立oracle，不能直接复用转换结果作为正确性基准。

全量容量、真实来源原生轨迹、独立副本浏览器与provider替身结果、标准发布及生产readback由根任务记录。浏览器副本若只做GET走查，应停用应用lifespan，避免启动真实scheduler/recovery；这是验证启动方式，不改变产品运行策略。没有新的audit、授权、重放或安全策略加入本功能。

上述副本与浏览器契约已完成，结果及限制见[根任务验收](business-model-legacy-fields-root-acceptance.md)。原生 A5 两次 FAIL 仍保留；不以替身、页面可见或 schema 往返代替该证据。
