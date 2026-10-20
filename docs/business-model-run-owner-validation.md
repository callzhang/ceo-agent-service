# C2：共同 Logical Run owner

本切片把三种真实专业运行历史归入已有 `runs[agent_runs]`。准确来源是
`45f3324be3c568b9f91709c25306e19d73a15087` 的 Store `2026-10-19.9`／Email53，
目标是 Store `2026-10-19.10`／Email53。当前是局部实现与定向验证候选；未提交、
未推送、未部署，也没有从实现 worktree 调用原生 Agent CLI 或真实 provider。
父线程负责独立 SPEC→QUALITY、全量副本、真实 native／UI、标准发布及生产读回。

实现范围仅为 Run 归属。C1 的 Task 单一生命周期、A5a 的 prepared Action
所有权、既有 Consumer／Audit 采用、业务事件、真实多键业务映射、scheduled
occurrence／first／last／snapshot／execution／dedupe／watermark 均保留。
A5b 的 Reply 局部结果与 D 的旧 Project／Todo 归属不在本切片。

## 原始事实与字段归属

预检用实际 `git archive 45f3324be` 的 Store 构造器建立源库；未用目标源码模拟
旧构造器。Python 为 3.12.11，SQLite 为 3.53.4。原 Runs 有 81 个物理字段、
52 个 SQL 对象。目标移除 `summary_input_id`、`request_id` 两个可精确反推的
物理字段，保留 79 个物理字段和 49 个 SQL 对象；物理记录种类从 11 种减至 8 种。
没有新增持久编码、旧行影子或专业 Run 副本。

| 原事实 | 共同 owner | 逆迁依据 |
| --- | --- | --- |
| 三种原 `record_type` | `record_type='agent_runs'`，原名称进入已有 `source_type` | `source_type` 恢复原种类；该字段在原专业行中为 NULL |
| 原物理 `id`、原 `legacy_key` | 原 SQL 值与类型不变 | 原值直接写回，不按 public ID 重新编号 |
| Task `summary_input_id` | 真实 `tasks[work_summary_inputs]` 的物理 `task_id` | 按该类型父记录的整数 public key 恢复；源必须为可精确往返的 INTEGER |
| OKR `request_id` | 真实 `tasks[okr_review_requests]` 的物理 `task_id` | 同上，限 OKR 父类型 |
| Meeting `task_id`、nullable `job_id` | 原物理引用与原值不变 | 保留现有预留缺父引用及原 FK finding，禁止制造 Task |
| Task／Meeting `finished_at` | `completed_at` | 原 SQL 值与类型移回，其他 Run 的这两个字段均不改 |
| Task `status, session_id, error, projection_json, created_at, updated_at` | 同名字段 | 原值、原 JSON 文本、空串与 BLOB 保留 |
| Meeting `status, session_id, transcript_start, transcript_end, error, created_at, updated_at` | 同名字段 | 原值直接写回 |
| OKR `session_id, transcript_start, transcript_end, created_at` | 同名字段 | 原值直接写回 |
| 专业来源没有的角色、代际、revision、turn、operation、lease、start、parent、structured error | SQL NULL | 不推断 Consumer／Audit 角色，不把 free-text error 包成 structured JSON |
| OKR 没有的 status、error、完成／更新时间；Task 没有的 transcript range | SQL NULL | 不从 Request 状态推断 Run 状态或时间 |
| 原 Consumer／Audit `agent_runs` | 所有字段原样，`source_type=NULL` | 不改变 revision／adoption／租约合同 |
| `codex_session_search_index.source_type` | 原索引事实 | 名称、数据及读写含义不变 |

Task 的 `projection_json` 是唯一 TaskAttentionProjectionReceipt owner，包含已有的
非默认采用回执；它不是模型全文缓存。Meeting Job 上已经采用的决定也继续保留。
专业历史 Run 没有 attempt 时仍保留原 Run，不补造模型调用。

预调查副本中的 Meeting orphan 是公开 Run 2298、原 job 4908、物理 Run190042
及 allocator 预留的缺失物理 Task404225；这些身份与原 FK finding 均保留。合成 oracle 使用相同拓扑但独立物理编号：
Run105／public2298 → absentTask9901／job4908，并验证两向 FK multiset
精确为 `("runs",105,"tasks",1)`。源余下有效 Meeting 父引用仍指向真实 Task。
逆迁若丢失原 Task allocator 预留，在任何写入前拒绝。

## 身份、约束和分配器

共同物理 ID 不等于旧公共 Run ID。专业读写以准确的
`(source_type, cast(legacy_key as integer))` 定位，DTO 同时公开 `common_run_id`
和原 `source_key`。三来源和 Consumer／Audit 同号可以并存；错误 namespace
不能读到另一个来源。`idx_runs_public_id` 保持源的整数 cast 唯一性语义，
仅把原专业 namespace 纳入共同 owner 的唯一性键。

原三个专业 INSERT 均使用 `runs` 原生 AUTOINCREMENT，并由原 public-key
trigger 写入 `legacy_key=new.id`，由既有 `trg_runs8_sequence` 维护真实
`runs:published`。新记录继续使用这条路径，不引入专业序列。所有
`sqlite_sequence` 行的 rowid、name、seq、缺失与零值语义都保留。

当前 CHECK 对 Consumer／Audit 保留原字段合同，对三种来源仅允许源本来有的
事实；没有的字段必须为 NULL。Task／Meeting 共享按物理 `task_id` 的 active
唯一索引，各自仍至多一个 running Run。原角色 turn 唯一性、实际 invocation
约束、route／retry／attempt-number 索引不变。

由于专业来源现在也叫 `agent_runs`，原 typed-parent 边界明确写作
`agent_runs AND source_type IS NULL`：两个 Run logical-parent trigger 与四类
Actions Run 引用的八个 insert／update trigger 均保持原 Consumer／Audit
合同。四类字段为 `consumer_run_id`、`audit_run_id`、`first_agent_run_id`、
`agent_run_id`。专业同号 Run 不能被错当为这些父引用；真实 Consumer／Audit
引用仍可写。这是恢复原类型边界，不增加业务授权或审核策略。逆迁恢复
actual45f 的原触发器文本。

历史 Runs8／Runs11、Actions 默认及 A5a 两种已知完整源 DDL 的 Factory 输出
通过 actual45f／目标两进程 SHA256 对照。旧默认 factory 保留明确的历史语义；
当前构造器显式选择 `logical_run_current_schema` 与 `logical_owner=True`，
运行时没有旧 schema 探测、fallback 或读时修复。

## 实际 writer 与读取迁移

`logical_run_storage` 只提供共同的 create、finish、恢复与来源投影；
`LogicalRunStoreMixin` 提供 `create_logical_run`、`claim_logical_run`、
`finish_logical_run`、`get_logical_run`。Task／Meeting claim 仍检查真实 typed
Task 的 processing 状态、复用原 running Run，finish 保留原允许终态及幂等
重放检查。OKR 只记录源本来存有的历史，不引入 claim／finish 状态。调用者
传入源不存在的非默认事实，在写入前失败。

退休旧 Task／Meeting begin、record、finish、get 及 OKR record、get、Meeting
metadata-get；原三套状态 UPDATE 合并至共同恢复写入器。九个 ignored 参数与
固定输出全部退出当前接口：

- Task：`decision_json`、`audit_summary`、`memory_recall_used`。
- Meeting：`decision_json`、`audit_tool_events_json`、`audit_summary`。
- OKR：`envelope_json`、`audit_tool_events_json`、`audit_summary`。

实际业务迁移包含 `task_agent.process_work_item`／直接采用、
`meeting_alignment` 分析结束、`okr_review` 结构结果记录；Store 中的恢复、
终态关闭、History／search／codex session index hydration；Web Meeting 详情与
History card、Console API、Task project repair、inspect／replay 脚本及相关 eval
读取。Consumer／Audit 的 Store、native trajectory、Email、adoption、rerun、
deferral、recovery、质量与发布读者统一使用角色专用投影。

模型正文仍从原生 trajectory 读取。`latest_task_ref`、`latest_standalone_ref`、
`exact_standalone_ref` 和原选择顺序不变；没有可用 native 源时仍返回原 unavailable
分支。删除 SQL 固定结果列没有删掉真实 native 结果，也没有回填空结果假装成功。
历史迁移使用的旧投影仍是显式 source oracle。

## 实际 invocation 不随 Logical Run 改写

`agent_runtime_attempts` 保持独立实际调用 owner。所有原始 workload_kind、
完整 workload_key、attempt_number、parent_id、session/range、route、retry、
lease 原值不改。operation attempt 的 `parent_id` 继续为 NULL，避免绕过
原 operation owner／expiry fence。

严格复用源 grammar：plain Task key 与 `decision_repair.N` 指向专业 Task Run；
`deadline_backfill[.N]` 是旧 Todo，`memory_backfill` 是旧 Project。没有按
数字前缀联接，也没有 rekey 后调整 attempt_number。Task 的零、单、多 attempt，
repair session 选择、Meeting 的 legacy range 和 OKR 的零 attempt range 均按源
合同读回；专业 finish 不调用 `complete_agent_run` 或制造 candidate／review。

先前只读 C1 副本调查的历史规模如下。它是 schema19.8 的来源事实调查，
不是本切片的 actual45f/19.9 迁移 oracle，也不是本次生产实时读回：

| 来源 | 历史 Run | 有真正对应 attempt 的 Run | 无 attempt 的 Run | 原 range 边界 |
| --- | ---: | ---: | ---: | --- |
| Task | 10,767 | 6,180（350 个有多次调用） | 4,587 | 全部 Run 原本不存精确 range；仅有 session 也不能制造 native 引用 |
| Meeting | 2,608 | 657（27 个有多次调用） | 1,951 | 795 个原存有效 range，继续原 native 选择；不可凭缺 attempt 删除历史 |
| OKR | 22 | 0 | 22 | 21 个有效 range，另一个按原不可用分支处理；无 status/error/finish/update |

这批 Task workload 的 7,279 条实际 attempt 中，6,741 条属于真 Task Run，
包括 43 条 decision repair；另 538 条 deadline backfill 属于旧 Todo，不计入
上述 Run 关联。来源是父线程预调查 `c2-run-owner-mapping.json` 的明确 aggregate，
实现测试另外用 actual45f 合成原始 source facts 验证同样边界。共同 create／claim
本身只持久化 logical Run；actual attempt 只由原实际调用入口建立，二者不会互补。

## 配对发布与当前事实逆迁

`LogicalRunSchemaRelease` 只接受准确 Store19.9／Email53 源和已知的
`fresh_memory_fk`／`actual_no_memory_fk` Actions 变体。先验证源 Runs、Actions、
Tasks、Email 声明、原 FK 以及 typed-parent 可逆性；外部 view／trigger 等 SQL
程序在内存 prospective schema 上 prepare／EXPLAIN，未知或失效依赖在写入前
拒绝。没有把字符串或 BLOB cast 成合格 parent，也没有填默认值消除异常。

实际切换在 caller-owned FK-off transaction 中进行：TEMP 保存当前源行和
sequence，重建 Runs，换入原类型边界等价的八个 Action trigger。双向
`EXCEPT` 同时比较每个原字段的值与 `typeof`，保留原 FK multiset、sequence
行及 marker 原时钟。目标构造器加载后再进行 strict pair、声明和 quick_check。

逆迁读取当前 owner，重建三类旧行、原 typed-public parent 及完成时间列，
包括正迁后新创建的 Run；不恢复旧 DB 快照。目标加载失败后写入的新事实也必须
逆迁保留，重复 rollback 幂等。实际旧 archive 的 Store、strict EmailStore、
WorkbenchStore 冷启动后，使用当前代码再次正迁，要求事实和 allocator 完全一致。

## 验证与证据边界

全部原始失败及绿灯日志、实际 source archive、临时 SQLite 和 DDL 存于
`/private/tmp/ceo-c2-run-owner.i77CCm`，按照父线程要求保留到 fresh review 结束。
只执行定向测试，没有运行整个测试套件。以下数字以各文件原始结尾为准；
不同批次可能覆盖同一 case，不把累计执行数当作不同用例数。

- 新 owner／迁移的 RED：`red-owner-corrected.log`、`parent-red-corrected.log`、
  `reservation-red.log`、`absent-red.log`；guard、预留身份与源缺字段均先观察
  目标不满足断言再实现。中间夹具／语法失败保留原日志，未算成功证据。
- `core4.log`：11 个核心 source／inverse／DAO／声明用例通过，1 个当时引用
  已退休 Action writer 的新增 fixture 失败；随后 `parent-green7.log` 证明
  修正后的 insert／update 边界和所有历史 factory hash 通过。
- `current2.log`：native、Task repair、Run／Meeting／OKR 外层 reader 和
  History 63 passed／3 deselected；排除项如下，不视为通过。
- `lease1.log`：16 passed，覆盖原 claim、恢复、operation 精确身份及短租约关闭。
- `handlers1.log`：177 passed／2 failed；两项为旧固定 SQL 输出断言与 C1
  被动 State claim fixture，改为当前列与真实 Task claim 后
  `parent-green6.log` 对应两项通过。原 177 项没有依赖改写这些 fixture。
- `callers2.log`：17 passed／1 failed；Task 实际处理链、identity 及回滚节点
  通过，inspect 剩余原固定列断言随后改为真实 Run 字段与独立 native 输出读回。
- `core5.log`：18 passed，完整覆盖本节的 owner、三 namespace／typed parent、
  四类 Action insert/update、值与类型、allocator、orphan、未知外部 SQL 依赖、
  两种 source variant、load-failed 新事实逆迁、actual45f 冷启动／reforward。
- `current3.log`：46 passed／1 failed；唯一失败是仍 monkeypatch 已删除的旧
  Meeting get 方法。改为阻止真实 native selector 后，在下一批中通过；其余
  Consumer／Audit 原采用、租约、revision、UI/search 和唯一 projection 回执均通过。
- `native3.log`：43 passed／133 deselected，覆盖整个 native standalone、Task repair、
  Task project repair、选定 Meeting handler 终态／异常路径，以及上述 History
  metadata-only UI 回归。该批不调用真实 CLI／provider。
- `eval2.log`：7 passed／1 failed；失败的 fake Codex 只返回 DTO，旧 fixture 曾
  依靠已删除 ignored 参数隐式生成 native 输出。改为在 fake 调用返回明确的
  fixture 结果时显式记录该测试 native 文件后，`eval3.log` 1 passed；没有为
  缺失结果补 `{}`。真实 eval 读取仍能拒绝缺 native 源。
- `ruff-final.log`：70 个本次 Python 文件通过；`compile-final.log` 通过；
  `git diff --check` 通过。没有放宽测试、lint 或发布策略。

明确未通过或未覆盖的原有节点：

| 精确 pytest 节点 | actual45f 同输入结果与证据 |
| --- | --- |
| `tests/test_history.py::test_history_uses_current_run_while_failed_attempt_is_recovering` | `source-history-baseline.log`，exit1，旧 Reply Task 投影访问已退休 `t.channel` |
| `tests/test_history.py::test_history_includes_task_updates_and_follow_ups` | 同日志，exit1，旧 `work_updates` 物理表不存在 |
| `tests/test_meeting_storage_readers.py::test_historical_orphan_preserves_public_job_id_without_a_ghost_parent` | `source-orphan-baseline.log`，exit1；原 helper 将 Store13.5／Email51 直接交给 current release，缺少多段旧 paired transition，不能仅补 C2 一跳修复 |

上述旧节点没有改为 skip／xfail，也没有降低原断言。其源文件 hash 与原始 exit1
在来源清单中保留。C2 的 orphan、原缺父 FK 及两个真实19.9源变体另由
`test_actual_source_collisions_orphan_and_current_fact_inverse_cold_reforward`
完整覆盖；这不替代从13.5开始的整条历史链。

原 Action 两秒 deadline、barebody／Postfix 及父级 native 内容断言问题均保留
独立证据，不借 C2 改写外发、审核、重试或策略。局部 storage／DAO／冷启动成功
不能证明真实模型内容正确，或代表完整副本、生产性能、外发和上线已通过。

来源冻结清单见
[`2026-10-20-run-owner-frozen-sources.json`](superpowers/specs/2026-10-20-run-owner-frozen-sources.json)，
包含 16 个逐字节冻结的历史／attempt／native 文件、7 种 Factory 输出 SHA256、
三个原失败节点的源文件与函数 hash、原始 exit1 日志 hash。最终候选文件清单见
[`2026-10-20-run-owner-source-freeze.json`](superpowers/specs/2026-10-20-run-owner-source-freeze.json)；
冻结只证明评审输入固定，不代表 native／全量副本／生产门槛已通过。
