# 多来源项目关注第一版验收

状态：baseline 完整九样本为 4/9 通过；已验证完整九样本的候选版本 `fc58e803` 为 9/9，后续最新代码不能继承该结果。当前 `c87752c0` 的独立项目名称竞争样例通过，但真实 W39 新副本仍无 Attention 提案，业务验收失败。重复副本回放及上线未完成。本文件不表示已发布，不用样本通过或 native 运行完成替代实际业务效果。

最新进展：标题和 Project 身份修复均经独立复核；`c87752c0` 新副本实际更新六个原 Task，
无新增 Task，正式 Project 中汽创智和岚图登记正确；但 proposal_count=0，receipt=no_proposal，
没有卡片。当前输出不能区分已评估后不关注与漏评，也没有给出这两项风险不关注的理由。
不得将工具 input 模式 passed=true 或领域提交完成当作真实业务通过。详见末尾本轮结果。

## 四个独立门槛

| 门槛 | 当前证据 | 仍需完成 |
| --- | --- | --- |
| 代码测试 | Tasks 1–6 已独立复核。Task 6 的 `128ea5b4` / `f92d0a92`：7 项 API、22 项页面测试与构建通过；主 Agent 检查模拟列表及 decision/watch 详情，桌面及 390×844 窄屏亮/暗色可读。Task 7 的固定输入、精确回放、旧 run 保留、幂等卡片/事件、落库依据与 expected 隔离回归先 RED 后 GREEN。 | 合并前重跑本次有关文件；模拟页面及 fake runner 不证明真实业务效果。 |
| 语义评估 | 固定 version 1 的 9 个 case；expected 与输入分开。baseline `7bf7be5e6dcdb181b0674e79ace17a598dcf87e0` 为 4/9 通过。最新冻结候选 `fc58e803` 为 9/9 通过，全部同一 code/Skill、模型/路由、timeout、concurrency=1。两聊天实际引用历史和当前原文；同项目两行动确为两 Task 同一卡成员，四负例无误关注。具体比较及旧失败保留于末尾。 | 真实副本尚未通过；如再改核心、schema 或 Skill，最新修订须重新验证，不能继承本轮通过。 |
| W39 数据库副本 | SQLite backup 初态完整性验证通过，259 Tasks、16 Projects、0 Attention。精确输入 27465/ref、原 run 10634 已保留。候选首次回放生成 run 10662，提交前因 title 缺失失败，无领域部分写入；见末尾。 | 修正已确认契约问题并诊断 Attention 提案缺失后，再实际回放及重复验证 IDs、关联、依据和事件。不得为落卡编造字段变化、强行合并简称或忽略额外 Task。 |
| 上线 | 全局权威 Skill 仍为 version 2，SHA256 `5c2bcbee182ed0872a55f35193c8815a51dd3e0ba0ca92b8e1eaa55e20cc1992`；候选使用隔离 `ci/shared-skills` version 3。 | PR + 固定 eval 对比通过后按既有部署流程发布、读回运行健康及队列、发布权威 Skill、单输入生产回放及真实页面核对。尚未上线。 |

SQLite backup 初态由主 Agent 持有：
`/var/folders/74/yj2lxqs162q7rqzm0mj8nv1c0000gn/T/ceo-attention-eval-40j4skvv/baseline.sqlite3`。
此文件不作为脚本直接运行目标；候选回放使用另一个由 SQLite backup 生成的副本。最终报告后按既有备份清理规则处理。

## 原生 runner 调用

脚本仅处理一个 ID 或一个固定 case，不调用业务 CLI 的 pending 遍历、扫描器、outbound DWS 或恢复流程。它取得所传副本的现有 TaskAgentSessionLease，调用现有 process_work_item，并通过 scoped TaskAgentRunner 使用 `task-agent:attention-eval:v1`，不续接生产 scope 的 session 指针。

执行环境先注入生产 launchd 的环境参数，并指定 `CEO_ENV_FILE` 为真实生产 `.env`；脚本在选定 `--code-root` 后才 import app 并读取该配置。`CEO_WORKER_DB` 必须保留生产路径，`--db` 独立选择评估副本：脚本在打开 store 前拒绝等于 worker_db_path 的路径。`CEO_SERVICE_MCP_CONFIG_PATH` 等现有相对配置由调用者解析为实际绝对路径；不建立临时 MCP 配置。须明确提供生产 `CEO_TASK_CODEX_TIMEOUT_SECONDS` 和 `CEO_TASK_CODEX_IDLE_TIMEOUT_SECONDS`；脚本按相同版本业务 CLI 的 TASK_AGENT_MAX 常量取 min，输出实际有效值。现有 prompt 的外部写禁止仍为 best-effort，不声称硬隔离。

以下是 Task 7 原九样例当时的历史调用：baseline checkout 与其 version 2
Skill、候选 checkout 与 version 3 Skill 分别用 fresh case DB 顺序执行，
concurrency=1，不用候选 app 冒充 baseline。这两条 `--case-id` 命令只对应
当时的源码/样例，保留作历史证据；当前 assessment fixture 的已有卡 seed
包含新 `assessment_json` 事实，pinned baseline constructor 不能直接构造它，
不得从当前脚本重放这两条命令来宣称新配对评测完成。

```sh
CEO_SKILLS_ROOT=/Users/derek/Projects/ceo-agent-service/.worktrees/attention-eval-baseline/ci/shared-skills \
python /Users/derek/.codex/worktrees/task-attention-multisource/ceo-agent-service/scripts/replay_task_attention.py \
  --code-root /Users/derek/Projects/ceo-agent-service/.worktrees/attention-eval-baseline \
  --fixtures /Users/derek/.codex/worktrees/task-attention-multisource/ceo-agent-service/tests/fixtures/task_attention_multisource.json \
  --db /ABSOLUTE/EVAL/baseline-w39-project-risk.sqlite3 --case-id w39-project-risk

CEO_SKILLS_ROOT=/Users/derek/.codex/worktrees/task-attention-multisource/ceo-agent-service/ci/shared-skills \
python /Users/derek/.codex/worktrees/task-attention-multisource/ceo-agent-service/scripts/replay_task_attention.py \
  --code-root /Users/derek/.codex/worktrees/task-attention-multisource/ceo-agent-service \
  --fixtures /Users/derek/.codex/worktrees/task-attention-multisource/ceo-agent-service/tests/fixtures/task_attention_multisource.json \
  --db /ABSOLUTE/EVAL/candidate-w39-project-risk.sqlite3 --case-id w39-project-risk
```

对真实 W39 副本执行两次相同命令；它核对固定 source_ref 后，只将这条副本输入标为 processing（现有 begin_task_agent_run 要求该状态），保留 attempts、payload 及所有旧 runs，每次创建独立新 run。

```sh
CEO_SKILLS_ROOT=/Users/derek/.codex/worktrees/task-attention-multisource/ceo-agent-service/ci/shared-skills \
python /Users/derek/.codex/worktrees/task-attention-multisource/ceo-agent-service/scripts/replay_task_attention.py \
  --db /ABSOLUTE/EVAL/w39-candidate.sqlite3 --input-id 27465
```

固定 source_ref 为：
`dingtalk-doc:a9E05BDRVQvy7QEacPZLB4anJ63zgkYA#sha256=21661643562265ca27e3369112a7ce3e91d9cbb6d21733050b5c3e7a9d42bf1e`。

stdout JSON 保存 code revision、实际 Skill 路径/hash、配置路由/model、有效 timeout、proposal 数、独立 projection 回执、实际卡片及精确 evidence、Task/Project/事件 ID 变化、失败原因。baseline 缺少新 projection 列时回执为 `{}`，不伪造成功；旧 response/schema 失败按其真实版本保留，不改写成候选 evidence。expected 只在原生 turn 之后比较，不能进入 prompt 或历史种子。真实输入模式不自动断言 W39 两卡业务正确，需主 Agent 依据实际 IDs、项目、任务及原文回读验收。

## 接受标准

固定正例全部达到项目卡预期；负例无误关注、伪项目或伪 Task；真实卡片引用有可核对的 signal/source_ref/连续原文/来源时间及支持 Task；每张目标项目卡都必须包含 expected.required_source_refs 指定的当前来源，聊天综合及来源冲突样本还须包含历史周报来源，不以一张历史卡或来源数量替代本轮证据。required_source_refs 仍只在落库后断言，不进入 Agent 输入。当前有关注提议时，已记录的 projection 必须 completed；pending/partial/failed/no_proposal、rejected/error outcome 或 recompute_error 都使本轮评估失败。无提议时已记录回执只允许 completed/no_proposal 且无失败结果；实际 baseline 的未记录空回执 `{}` 保持独立诊断，不补造也不因此自动判失败。同项目两行动保留两 Task 一卡；会议/聊天更新复用当前卡；来源冲突保留双方证据而不改写登记字段。W39 两次回放不重复 Task/Project/card；卡片关联中汽创智及岚图的真实任务和原文；第二次卡 ID 不变且未变化不追加事件。任何真实模型失败都记录原 case 并修正 prompt/context 后重做相同对比，不用 fake 输出替代模型门槛。

Task 7 规格复核补强：`2abb82fd` 的评估工具曾可能把仅历史证据的更新或未成功投影的旧卡计为通过；已用真实 Store/full process_work_item 的确定性失败回归复现并修正。此修正仅改变一次性评估断言，不改变生产 Task Agent、投影或权限行为；真实语义比较仍待执行。

Task 7 质量复核补强：同一项目两个行动的 fixture 在 expected.required_project_member_counts 中明确要求该项目卡包含两个不同 Task；实际已有 task_count=2 不能替代卡片成员验证。full process_work_item 回归包含两个引用真实原文且确认关联同一 Project 的 Task，只有首项提出关注时曾被误计通过；新增落库后成员数量断言将其记录为 missing_project_task_member。两个行动都实际成为同一卡成员时通过；不按 Task 标题固定措辞判定，也不将 expected 送进 Agent。尚未据此更改生产 prompt 或核心行为。

## 首个真实模型失败与限定修订

2026-10-02，主 Agent 以同一固定 `w39-project-risk` 输入、`codex_oauth` 路由、
`gpt-5.6-luna` 模型和 concurrency=1 跑完 baseline 与候选 `4f0e06e5`；实际 Skill
路径/hash 已读回，候选使用隔离 revision 3。两侧运行均 completed，均没有关注提议或卡片；
baseline 落库 4 Tasks/2 Projects，候选落库 3 Tasks/2 Projects。此处是固定脱敏样本，
不是精确生产输入 27465 的数据库副本回放。

候选固定样本库只读核对路径：
`/var/folders/74/yj2lxqs162q7rqzm0mj8nv1c0000gn/T/ceo-attention-eval-40j4skvv/candidate-w39-project-risk.sqlite3`。
run 1 的顶层 update_summary 明说项目带报告注册提案，但任务缺明确负责人而保留候选，
且未创建 CEO Attention。两个项目 Task 的逐项 update_summary 明确写出
“当前没有已注册项目锚点或可验证的新增材料触发”。原 prompt 首句要求 registered official Project anchor，
后文才允许同一 decision 的 null anchor；Skill 第 9 步也先要求 confirmed Project。
此外原规则笼统排除 static facts，容易把首次观察到的当前未解决重大风险当作没有新变化。
这支持一个需模型重跑核验的资格解释假设，不证明只改文字已经解决行为。

限定修订仅同步 build_task_agent_prompt 与隔离 Skill：正式 Project 可为已有已确认对象，
或同一 decision 的有效当前权威 ProjectProposal 本轮解析出的对象；首次观察到有具体
经营影响的未解决重大风险可 watch，无需已有卡或对不存在的旧评估证明新变化。
已有卡已反映同一事实时不重复提案，标签、普通进展和日期临近仍不足；真实候选 Task
无需补造负责人或承诺即可支持风险。保持引用核验、真实 Task 和当前无需处理的 watch 表述。
不修改 Project 注册、投影、parser、transition 或全局 Skill，不增加 Agent/队列/策略层，
不把样本业务名、金额或 expected 注入规则。

两个新回归先因缺少资格文字失败，再验证 prompt 与 fresh module 通过 CEO_SKILLS_ROOT
实际选择并完整加载的 Skill 内容；相关 task_agent/retrieval/session 142 项通过。
此轮回归只证明文字契约和实际加载路径；后续 W39 固定样本两卡已验证，最新关联修订的全 9 样本及生产副本回放仍待验证。

## 同项目两个新行动的原生成员失败与限定修订

2026-10-02，固定 `same-project-two-actions` 原生候选运行 completed；只读核对
`/var/folders/74/yj2lxqs162q7rqzm0mj8nv1c0000gn/T/ceo-attention-eval-40j4skvv/candidate-same-project-two-actions.sqlite3`
run 1：三个候选 Task、一个 Project、一张 Attention 卡，卡成员只有 Task 1。
Task 1 引用项目登记行的负责范围，成为泛化的验收/付款协调 Task；Task 2 与 Task 3
分别引用两条明确行动，但 attention_proposal 均为空，未成为卡片成员。
这是实际成员缺失证据，不以一张卡或总 Task 数替代关联核对。

原因对应两处文字缺口：登记范围在具体行动已覆盖同一工作时仍被提取为额外 Task；
“每项目每轮最多一个关注提案”与仅接受已有 ID 的 related_task_ids 组合，使本轮多个
新 Task 难以同时表达支持成员。既有投影已把除 anchor/related IDs 外完全一致的提案
折叠为一张卡，并合并每项实际应用 Task ID，无需修改领域或投影代码。

限定文字修订同步 prompt 与隔离 revision 3 Skill：登记范围/目标/类别不在具体来源行动
已覆盖时另建泛化 Task；任何章节中真实明确行动仍可保留，不限制为下周重点。
每轮每 Project 一个唯一 assessment/卡片，多个新建且实际支持该风险的 TaskDecision
各附完全相同的 attention_proposal，沿用既有折叠行为完成成员合并。
related_task_ids 继续仅接受真实已有 ID，无关项目 Task 不加入，冲突内容仍拒绝。
不修改核心/API/模型、全局 Skill 或固定样本 oracle，不写业务名、金额或预期数量进规则。

两个通用回归分别验证 prompt/Skill 的登记范围与新 Task 成员表述，共四个参数化用例
先 RED 后 GREEN；fresh Skill 加载回归也验证新增文字实际进入 runner。
相关 task_agent/retrieval/session 146 项通过。此处只证明规则契约与加载，
后续 fresh case 9 已验证两真实 Task 同一卡两成员；最新关联修订及其他样本效果仍待验证。

固定计数另经原生结果后的来源/独立交付契约复核，由主 Agent 单独修订评估 oracle：
W39 显式允许 [3,4,5]，meeting-new-risk 允许 [1,2]，其他样本保持原精确计数，
不改为全局最小数量门槛。W39 的五项都有真实行动依据：进展章节的“需同步”不能因
所在章节被排除，同句对账与方案沟通可以是独立可完成结果；Task 3 的描述仍含方案沟通，
与 Task 4 有范围重叠，保留人工身份复核注意点，不据计数放行把重复身份说成已排除。
两侧 baseline/candidate 以同一修订后 oracle 重新评估，expected 仍仅用于运行后比较，
不进入 Agent 输入、routing 或规则。首次资格修订后的 W39 已实际生成两张卡且依据有效；
同项目多新行动的实际成员门槛未放宽，后续 case 9 fresh rerun 已通过该门槛。

## 已有 Project 下新会议/聊天 Task 的关联缺口与契约修订

后续原生结果由主 Agent 保存并复核：`1ed0f3f2` 的隔离 Skill hash 以 `3ec776` 开头，
同路由/model 的 candidate-final-w39-project-risk 为 3 Tasks/2 卡；
candidate-same-project-two-actions-rerun1 为 2 Tasks/1 卡且两实际成员。baseline 9 样本
已完成，4 负例通过、5 正例失败。它们是该修订前的模型证据，不证明本节新增契约效果。

新的失败来自 `candidate-final-meeting-new-risk.sqlite3` run 1，已独立只读核对。
该 run completed，两个 Task、两个相同 Attention 提案，projection_json 为 failed，
project_link_count=0，两项 rejected 原因均为支持 Task 必须 relevant、open/waiting
并确认到同一活动 Project。既有 Task 1 已 relevant/open 且 confirmed anchor 1；
新 Task 2 是 unknown/open、无 Project link。两决定的 anchor_match_proposals 都为空，
Attention.anchor_id=1 只选择卡片目标，并未确认 Task 关联。

代码检查证明仅改 prompt 不足：原 TaskAnchorMatchProposal 只有 anchor_id/reason，
应用只产生 proposed；新 Task 决定也不能直接设置 business_relevance，因为该字段须
update_fields。普通会议/聊天当前来源不能为关联而伪造项目立项提案。

因此在原已批准的已有项目关联目标内新增 TaskProjectLinkProposal：严格正整数 anchor_id、
非空 source_excerpt/reason，TaskDecision.project_link_proposal 可选；与登记 proposal
互斥，存在 Attention 时其 anchor 须与链接一致。领域应用要求当前来源引用/原始证据，
目标为活动已注册正式 Project；逐字引用与 Task 的行动引用互相包含并包含存储的
Project/anchor 标题。引用/标题检查是最小来源和名字引用证明，完整身份语义仍由 Agent
判断，不推断简称别名或凭前缀自动匹配。通过后调用既有 confirm_anchor_match，只确认
实际应用 Task 的关联并派生 relevant，计入 project_link_count；不注册或改写 Project，
不升级 Task stage，不补造负责人/日期/承诺。旧 uncertain anchor proposal 仍 proposed。
原无字段实际变化的 update guard 在关联/投影前保留；不能以新增链接绕过该 guard。
update 信号效果身份包含实际关联目标/引用，不包括 reason；创建身份不因关联说明而改变。

新增模型、prompt/Skill、update 信号身份回归先 RED 后 GREEN；full process_work_item
会议及聊天均使同一已有卡包含原 Task 与新 Task、当前原文引用、新 Task relevant/confirmed，
且不新建 Project 或升级候选。缺失/非正式/非活动目标、其他 Project、不在当前源的引文、
其他段落而非本行动的引文、来源引用不符均原子回滚；session/memory、同时登记/链接和
Attention 目标不符被模型拒绝。未确认既有 Task 的真实字段更新可确认，restating 更新仍
跳过且保持 rejected 回执，旧 uncertain 匹配不确认。相关 305 项通过，ruff 及服务 imports
通过。此处是确定性契约验证；新增契约后的原生会议/聊天、全部候选 9 样本、W39
生产副本两次回放及上线仍待主 Agent 的独立复核、fresh 原生比较和发布验收。

## 已交付历史事实的比较归因遗漏

后续专用关联修订的实际原生样本已由主 Agent 复核通过；上述缺口记录保留为此前失败事实，
不再代表该修订的当前状态。本轮是新的证据归因原因，不是重复关联假设。
只读核对 `candidate-verified-chat-with-report-context.sqlite3` run 1：completed，复用并更新
既有卡，当前证据引用有效，但 evidence 只有当前聊天，评估报 missing_evidence_source 与
missing_required_source。update_summary 明确比较已有风险并升级，why_attention 使用再次/仍未
解决等延续判断；这些判断没有同时引用原始历史报告。

实际 Codex transcript
`/Users/derek/.codex/sessions/2026/10/02/rollout-2026-10-02T03-26-09-01a0fc26-622a-7481-8ace-103ebae9e6a7.jsonl`
中交付的 Current semantic Task context.source_signals 已含真实 Signal 1，来源
project_weekly_report、ref `eval:historical-report`、source_time `2026-09-24T12:00:00Z`，
原文记录验收延迟两周及回款推迟导致供应商付款协调；与数据库该 Signal 原文一致。
因此是已有上下文的输出归因遗漏，未据此改变检索、领域投影、路由/model 或评估 expected。

prompt 和隔离 CI Skill 同步明确：比较、延续、升级或冲突判断要选择实际用于判断的原始
历史 Signals，与当前证据一并引用，保留真实正 ID、source_ref 与逐字原文。当前来源对旧报告
的转述不能代替原报告引用；不要求周报、不全量引用检索结果，首次仅凭当前事实仍可评估。
原历史不可用则比较标明不确定，不编造引文或使用 session/memory 作为原始证明。
通用 prompt/Skill 断言及 fresh Skill 交付断言先 RED（3 项缺少此规则）后 GREEN；
Task Agent、混合来源投影、检索和 session 的聚焦回归共 268 项通过，ruff 及 diff 检查通过。
上述文字修订后的真实模型效果未通过，详见以下 fresh rerun；原确定性测试结果不证明引用行为已修复。

## 比较归因文字修订后的原生复测失败

候选 revision `dd073b2578ebfdeec2f7678b7e93fef34e745e5d`、CI Skill SHA256
`4ab5fb97f86004d160b61817f7e449d0ffc0936dcf203ea431f8cfe38bc9c1b4`，
实际路由/model 均为 `codex_oauth` / `gpt-5.6-luna`，concurrency=1；两次独立 fresh
固定样本复测的 runner 均正常 completed，没有切换模型或放宽 expected。

| case | 实际落库和回执 | 失败原因 |
| --- | --- | --- |
| chat-with-report-context | 2 Tasks；Task 1 更新已纳入补充内容，Task 2 又记录相同新增内容且 unknown/未确认 Project link；两项相同 Attention 提案因支持成员不合格全部 rejected，旧卡未更新。 | projection_not_successful、unverifiable_attention_evidence、task_count_mismatch、missing_evidence_source、missing_required_source |
| newer-conflicting-chat | 1 Task、1 卡更新，projection completed/applied_count=1；当前原文引用有效，登记字段未改写。state/inference 描述与上期正式周报冲突，evidence 却只有当前聊天。 | missing_evidence_source、missing_required_source |

对应副本为上述评估目录下的 `candidate-citations-chat-with-report-context.sqlite3` 与
`candidate-citations-newer-conflicting-chat.sqlite3`。原始历史 Signal 1 已交付给 Agent；
当前 schema 允许 optional link=None 与单当前证据，因此输出形状有效，既有纠正机会未触发。
下一修订针对输出契约及任务交付边界，不新增 Agent、队列、关键词判定或自动补链。

独立业务复核确认：聊天行动的“增加供应商停交风险及延期付款协商结果”是既有复核内容的
补充，且本轮 Task 1 已包含该内容，第二项造成重复覆盖；保留精确 Task 数量 1，不放宽。
供应商本周不能付款将停交的当前事实可以独立支持 watch，并不要求历史周报才能形成风险。
但原始历史的延续/升级/冲突比较需要比较两侧的实际引用，本固定样本专门验证此能力，
保留双来源要求；不能把“当前风险有效”等同于“多来源综合归因已通过”。

## 必填评估依据与新行动关联的输出契约修订

在两次真实失败之后，新增必填 assessment_basis，不提供旧输出兼容默认：
current_observation 只断言当前事实，需要 null-ID 当前引用，允许历史佐证；
historical_comparison 依赖历史比较、延续、升级或冲突，必须同时引用当前 null-ID 与
实际持久化原始 positive-ID 来源。只检查此输出形状，不查询 Store 或用关键词机器分类。
原始 ID/ref/原文及支持成员有效性仍由现有投影核验。assessment_json 保存 basis，
同轮提案折叠自然包含该字段，API 原样读回，不新增 UI 标签或服务判断规则。

新行动 record_candidate/create_task 对已有正 anchor 提出 Attention 时必须有同目标
project_link_proposal；有效同决定新注册继续走 project_proposal，已知 confirmed Task
按真实 ID 更新并复用链接。检查既有 dedupe 路径：仅按同来源信号的 dedupe key 与事件
重放原创建结果，不按标题自动复用；有效新行动提案保留链接可重放，不增加 linkless fallback。
字段旁描述明确独立可完成交付才创建 Task，既有范围/内容补充更新同 Task；不用同引文
机械去重。prompt/CI Skill 替换旧比较段，要求已交付匹配原始 Signals 的显式来源比较实际
核对两侧，不以复述当前转述替代；相关历史缺失则比较待核对，仅当前事实有效。

必填模式、双侧引用及新行动链接的通用模型回归先 RED 后 GREEN；模拟现有同会话纠正
回归验证缺 link 或历史比较缺历史引用均成为具体字段错误，原输出拒绝、一次修正后接受。
合法当前/比较、当前附带历史佐证、已有 confirmed Task 复用、新注册、创建重放、无变化
update guard、混合来源存储及 API basis 读回均保留聚焦验证。真实模型效果仍待主 Agent
独立规格/质量复核后冻结相同输入/model 原生重跑，不将确定性测试当作语义通过或上线。
本次指定 9 文件聚焦验证 805 项通过（32.56 秒），补充 API basis 读回后该文件 7 项通过；
ruff、diff 检查及服务 CLI/worker/supervisor imports 通过。未执行全套测试或本次原生 turn。

## 冻结契约修订的完整原生比较：8/9，尚未通过发布门槛

规格复核独立 22 项通过，质量复核独立 31 项通过；主 Agent 独立模型/API 56 项通过。
随后冻结 code `233b9667cf18eff12cf5e7222150b4d317e47a55`、CI Skill SHA256
`828342187afbd95d2f89b2d89f5161b7a2fd615842dd6991ff43894147f59d22`，
同一 version 1 固定输入、相同 expected、`codex_oauth` / `gpt-5.6-luna`、有效 timeout
900/300 秒、concurrency=1，完成全部九个 fresh 原生候选 case。各调用正常完成，
失败 case 在后续日期领域验证被拒；正常 CLI 退出不等于业务提交成功。

| case | baseline 7bf7be5e | 候选 233b9667 实际结果 |
| --- | --- | --- |
| w39-project-risk | 未通过 | 通过：4 Task、2 卡；中汽创智成员 1，岚图成员 2/3，NPS Task 4 无 Project/Attention。 |
| meeting-new-risk | 未通过 | 通过：2 Task、原卡更新，成员 1/2，既有正式 Project 未重建；当前事实模式。 |
| chat-with-report-context | 未通过 | 通过：更新 Task 1、原卡 ID 1，当前和原始历史引用并列，historical_comparison；无重复 Task。 |
| newer-conflicting-chat | 未通过 | 通过：1 Task/1 卡，双方原文和时间并列，冲突待核对，不改登记字段。 |
| risk-label-only | 通过 | 通过：1 真实行动 Task、0 卡。 |
| routine-progress | 通过 | 通过：1 真实行动 Task、0 卡。 |
| unconfirmed-project | 通过 | 通过：1 Task、0 正式 Project/卡。 |
| no-real-task | 通过 | 通过：0 Task/卡。 |
| same-project-two-actions | 未通过 | 未通过：日期 actor 错误，0 Task/Project/卡；原输出提出了 3 个 TaskDecision，不是 3 个已提交 Task。 |

失败副本 `candidate-typed-same-project-two-actions.sqlite3`，实际 native session
`01a0fc4f-3f15-7993-8f28-0a095d8c7f3d`。完整原始输出在该库
agent_runtime_attempts.result_envelope_json 中保留。额外 Task 使用整个项目登记行的负责范围，
两条具体行动已覆盖该工作；该项还把登记 DDL 作为 external_deadline_at，value 为不完整日期，
quote 为整行，actor_name 填来源类型“项目周报”，而可信 sender/name/ID 均为空。
失败原因 `date actor_name must match the trusted date actor`；不能跳过拒绝条件或放宽计数。

独立隔离复现：原输出拒绝且无提交；仅清空 actor 或移除日期会提交 3 Tasks，仍多余；
仅保留两条明确行动得到 2 Tasks/1 Project/1 卡。说明日期错误遮住了任务粒度问题，
只修日期不能宣称语义修复。当前字段已能表达正确输出，下一轮只限定字段旁的来源/日期
说明，不新增自报标签或业务关键词分类；指导不能确定性证明自然语言独立交付，必须重复
原生验证。本次四负例通过及两个多来源比较通过不代表最新修改或生产已通过。

## 字段指导修订的重复成功与新任务关系端点缺口

`f5541bed` 字段就近指导由规格复核独立 15 项、质量复核独立 24 项通过；九文件聚焦
808 项通过。冻结 code `e4a66ae83d4bd7a44fa43f4793bb78bc347210eb`、CI Skill SHA256
`4945a0e53d4007a653546dacbe7aff457308fa6ef40686137f5cb71f3caa46e1`，
仍为同模型/路由、固定来源、expected、有效 timeout 和 concurrency=1：

- same-project-two-actions 两次独立 fresh case 均通过：2 Tasks/1 Project/1 卡，两个真实
  行动同一卡成员，无额外登记范围 Task 或错误日期。
- w39-project-risk 通过：5 Tasks/2 卡；Task 身份仍须按实际交付边界人工复核，计数允许范围
  不能替代该检查。
- meeting-new-risk 未通过：正常 native 输出后，业务提交因关系字段被拒，实际仍只有
  种子 Task 1 和原卡 1，未更新。其他八项中的聊天及负例尚未跑完这个修订，不能复用上一版
  的通过宣称完整九样本已通过。

失败副本 `candidate-fields-meeting-new-risk.sqlite3`，native session
`01a0fc62-7d27-7d71-8b88-29ecd4859a40`。原始两项决定中，既有 Task 1 的无实际变化
update 按原 guard 跳过；新候选行动附 `relation_proposals` 为 from_task_id=1、to_task_id=1，
两端均填已有 Task 1，而本项真实新 Task ID 尚未分配。服务正确报
`relation proposal must include the Task evidenced by this decision`，事务未提交；原卡不是
本轮更新成功。原始输出保留在 runtime_attempts.result_envelope_json。

当前双端数值关系模型对新 Task 有表达缺口：输出时不能知道服务即将分配的自身 ID。
下一步让关系以本条实际应用 Task 为一端，只引用已存在的另一端及方向，服务在取得实际
Task ID 后派生旧领域命令的 from/to；不猜测编号、不增加 Agent/队列、兼容分支或新业务门禁。
同一原始输出的项目链接 quote 也只选了新行动子句，未包含正式项目名；完整当前行动句
包含项目名及该行动，应作为关联引用。下一修订只明确该字段的既有出处要求，不放宽核验。

## 相对 Task 关系契约：完整九样本通过，真实副本尚未通过

功能 `f9667d2c`，冻结 HEAD `fc58e803cdc260546463aafc6e382a60a1dd65a0`；
CI Skill SHA256 `e5a04572acbe5e9657d50b9a76ff4db3cd8531ea2410ac02a1651c147ddaa8e5`，
fixture SHA256 `c2d0846919ffe9452906a6c08b2d17b729db120c102c7ec00e72c9f2ee703338`。
Worker 九文件聚焦 827 passed，独立规格 31 passed、质量 42 passed、主 Agent 专项
18 passed。质量复核另外在四个隔离数据库验证 merge + relation 双方向及实际 target-self
回滚。均未改变 Graph SQL、原无变化 update guard、计数 oracle 或模型路由。

完整 fresh `candidate-relative-*` 九案例仍使用 codex_oauth / gpt-5.6-luna、有效 timeout
900 秒、idle 300 秒、concurrency=1。两聊天案例沿既有一次形状修正，各有 superseded
和 completed attempt；其他七项单次 completed。没有新增重试或替换模型。

| case | Tasks / Projects / active cards | 实际结果 |
| --- | --- | --- |
| meeting-new-risk | 2 / 1 / 1 | 通过；旧任务与新增供应商付款任务同一卡成员，同时引用当前会议及历史报告。 |
| same-project-two-actions | 2 / 1 / 1 | 通过；两个实际行动均为卡片成员，没有多余登记范围 Task。 |
| w39-project-risk | 4 / 2 / 2 | 通过；中汽创智复核计划一项、岚图对账及法律商务沟通两项；NPS 无 Project/Attention。 |
| chat-with-report-context | 1 / 1 / 1 | 通过；既有 Task 更新，当前聊天及历史原文共同支持升级风险。 |
| newer-conflicting-chat | 1 / 1 / 1 | 通过；保留新旧不同时间的原文，记录验收、回款冲突待核对。 |
| risk-label-only | 1 / 1 / 0 | 通过；单独风险标签不足以进入关注。 |
| routine-progress | 1 / 1 / 0 | 通过；例行进展无误关注。 |
| unconfirmed-project | 1 / 0 / 0 | 通过；不因聊天自动登记项目。 |
| no-real-task | 0 / 1 / 0 | 通过；没有编造任务或关注。 |

全部五正例的 projection 为 completed，四负例为 no_proposal；引文原文、source_ref、
来源时间及成员关联均通过落库核验。此 9/9 相对 baseline 4/9 是同固定样本比较，
不替代真实数据库历史任务的业务效果。

首次真实副本回放：`w39-candidate.sqlite3`，精确 input 27465/source_ref 不变，
保留历史 run 10634；新增 run 10662、attempt 18952。native 正常 completed，
Task run/input 为 failed，错误 `non-skip task decision requires title`。
原输出七项决定：Task 129 promote_candidate 带 title；130–134 的 update_fields
省略 title；另有 Einride POC 新候选。所有决定均未给 Attention 提案。
原输出保留在 attempt.result_envelope_json；没有补造 field、alias 或 card。

实测契约不一致：TaskDecision.title 仍默认空，形状解析接受省略；process_work_item
在 runner 返回后用 _validate_task_agent_decision 要求非跳过 title 非空，因而未走
既有形状一次修正。必须先诊断并统一契约，不能增加另一重试层掩盖。
Attention 缺失原因尚未确认，需比较真实 WorkItem/既有六 Task 的 context 与固定样本。
失败前后领域计数、IDs/events 不变：259 Tasks / 16 Projects / 0 cards / 434 Task events；
原 Task 129–134 未修改。第二次副本回放、PR/合并/push、部署、全局 Skill 发布、
精确生产回放和真实页面验收均未执行。

## 真实副本标题失败的契约修复（native 待重跑）

`daa508a2` / `615e31b7` 区分新建与已有 ID 更新：新候选或正式任务需要非空白标题，
在 TaskDecision 形状解析中拒绝遗漏，沿用已有一次同会话纠正；已有 ID 更新允许省略标题。
仅 update_fields 修改显式提供的标题；晋升、接受和合并沿原命令保留已存标题。
晋升所用 deliverable 信息来自真实持久化 Task，不以省略标题推断没有交付物。
原提交后无条件标题要求已删除，来源/负责人/分配/接受/日期与无变化 guard 不变。

Worker 11 项和追加 3 项均观察旧行为 RED、修复后 GREEN；两文件 209 passed。
主 Agent 九个相关 Python 文件 491 passed，页面两文件 22 passed、类型检查/构建及服务
imports 通过。独立规格 33 passed。质量复核发现显式纯空白更新标题仍在解析后到达
数据库 CHECK，导致整批回滚并绕过已有形状纠正；这是本次必须补齐的边界，尚未质量 PASS。
不把数据库拒绝误写成成功保存了空白标题，也不通过自动 trim 或另增重试掩盖。

边界修复 `c75bb21f` / claim release `c6781619` 已完成：nonempty 纯空白更新标题在
形状解析拒绝；省略、空字符串及合法标题分别验证原有语义。回归旧版 1 failed / 3 passed，
修复后 4 passed；两文件 213 passed。最新修复的规格与质量复核、native 仍待完成。

真实副本上下文实际为 320,176 字符：20 候选、20 正式、20 未验证正式 Task、75 Signals、
16 正式 Projects、0 Attention。Task 129–134 全部交付；真实完整周报保留具体现金流、
结算争议、五行登记及行动。上下文规模差异是已测事实，不证明其导致 Attention 遗漏，
本轮没有因此改检索、模型路由、fixture 或 oracle。最新标题修订仍需 native 回放，
不得继承 `fc58e803` 的九样本通过作为新版本或真实业务通过。

## 标题修复后的真实副本：提交成功但 Project 身份未通过

冻结 `b5f9bd5f7f85d712edced8bcbc1a60ed80a89b94`，CI Skill SHA256
`2c325b786d30832dcab0d79b886e8c8d1cc4e39cd954bb73d5bc1859dfe6b5d0`。
空白边界独立规格 20 passed、质量 15 passed，原复现现在在 shape validation 拒绝；
主 Agent 标题专项 17 passed。精确 input 27465 的副本 run 10663 为 completed/input done。
原有失败 run 10662 不改写。三次 native attempt 均 codex_oauth/gpt-5.6-luna：
18953 normal superseded(runtime_unclassified)，18954 normal superseded
(runtime_result_validation_failed)，18955 既有 result_validation_correction completed。
没有为本实验另增重试或模型切换。

领域实测：259→261 Tasks，16→19 Projects，0→3 cards；旧 Task 129–134 均更新，
129 提升为 assigned_unaccepted 的正式任务，其他五项保持候选。新增 Task 260
「推进一次通过率统计及HLL低通过率改进」、261「补入管理周会文档中的健康度指标及ISO进度」。
岚图 anchor 35、项目管理 anchor 36、抽检包生命周期 anchor 37 来自当前登记行。
三张卡为：中汽(anchor 23, Task129)、岚图(anchor35, Task130)、项目管理(anchor36, Task260)。
receipt 为 completed：task_decision_count=8、proposal_count=3、project_link_count=5、
registry_row_count=5、applied_count=3；引文原文、来源及成员链接检查通过。

这仅证明提交与出处核验成功，不证明项目身份正确。Task129 的 project_link_proposal
选旧 anchor23「中汽」，引文来自下周行动「中汽回款计划」；其 Attention current_state
和风险引文却说「中汽创智」，当前正式登记行也明确为「中汽创智」。最终 update_summary
说明输出纠正删除了冲突的 project_proposal，保留旧 anchor23 link。本版没有授权或证明
这两个名称为同一项目；引文包含短标题不能替代该身份。真实副本业务验收因此失败。
工具在 input 模式没有这份人工业务预期，passed=true 不覆盖此项身份验收。

Task260 的统计部分与旧 Task131 有交叠，HLL改进是否构成独立交付尚在复核；
Task261 的健康指标文档更新有来源，标题中额外 ISO 内容也需核对，不因新增数量就判错或忽略。
项目管理卡有低验收率与统计口径缺失的真实引用；第三张卡不是仅凭数量即可认定误关注。
仍不改 alias、静态业务规则、来源、旧 runs 或生产状态；最新完整九样本、精确副本幂等、
PR/合并/push、部署、全局 Skill、生产回放与页面验收均未完成。

## 当前权威 Project 身份：登记与复用修复（效果验证待完成）

功能提交 `f4566806`，释放子 claim 后冻结代码 `394fac8c`。报告与明确会议登记现在
共用 `register_source_project`：复用唯一活动且精确同名的正式 Project 实际 anchor，
保留原登记 provenance；无匹配沿原标题 hash 登记；多个活动同名正式对象使事务因
身份冲突回滚。不改通用 anchor 的 type/ref 身份，也不把短名称推断为别名。
Project proposal 的字段、prompt、CI Skill 与架构/运行/设计文档同步解释为采用当前
权威定义并登记或复用，不仅用于新建。当前不同的正式名称不能因行动简称被旧名称替代。

新增回归旧行为 `7 failed / 2 passed`；开发 Agent 五个相关文件 `351 passed`。
主 Agent 独立运行最终新测试文件 `9 passed in 6.86s`，覆盖报告和会议实际 Attention
投影的 canonical anchor、成员、stable key 与重复卡/事件身份，以及旧 ref 复用、
不同短名称、活动正式对象选择和同名冲突回滚。冻结 `394fac8c` 的独立规格复核
`351 passed in 71.32s`、独立质量复核 `236 passed in 40.89s`，两者均 PASS。
质量复核还逐一比较歧义 report/meeting 失败前后的全部 `business_*` 表，确认完整回滚；
独立单次登记/重复登记实测同 ID、同 anchor、原 provenance 保留。上述复核没有运行 native。

独立 version 1 竞争样例 `tests/fixtures/task_attention_project_identity.json` 的
SHA256 为 `9b56eec4bb74999a40448b1992ea0aed7caa7fff195c7c0c9e8a1acd859dfdf6`；
当前 CI Skill SHA256 为
`2b557b4754c39c688bc0533f04e152157b70fcc519bb4647f12bdbe330bb8d70`。
原九样例和 replay tool 未改；该新增样例的 pinned baseline/candidate native 对比、
最新完整九样例和真实 W39 新副本验证尚未通过，不能继承旧版本结果。
生产、全局 Skill 和原失败副本均未修改。

新增竞争样例 pinned baseline `7bf7be5e` 的 native 结果已完成：codex_oauth /
gpt-5.6-luna、concurrency=1、900s 总时限/300s idle，一次 normal attempt completed，
input done、run completed，但业务验收失败。旧版把已有「示例创智」再次登记成第三个
Project，把已有行动再建为第二个 candidate Task，且没有 Attention；失败项为
`attention_project_mismatch`、`official_project_mismatch`、`task_count_mismatch`、
`project_registry_changed`。这是一份真实失败基线，不以队列 done 替代业务通过。
结果保存在本工作流独立 `baseline-project-identity.sqlite3`，候选最终对比待读回。

真实 W39 新副本 `w39-project-identity-candidate.sqlite3` 从不可变初态通过 SQLite
backup 创建并完成完整性核验 `ok`：259 Tasks、16 Projects、0 Attention；input27465
仍为 done，完整来源 ref 与固定目标一致。它没有继承上轮错误投影；尚未原生重跑。
候选竞争样例已在代码 `70e2cdf6` 上启动；该提交相对复核代码仅增加验证文档，
code/CI Skill 无变化。运行最终结果仍待读回，不将启动或 shape/local 测试视为业务通过。

竞争样例候选已完成并通过，最终读回代码 revision `abaae124`（运行期间新增的文档
提交，行为代码/Skill 与复核冻结 `394fac8c` 一致）。相同 fixture、路由顺序、实际
codex_oauth/gpt-5.6-luna、concurrency=1、900s/300s 时限；一次 normal attempt
completed，无纠正或模型切换。实际 Task 1 保持 candidate/open 且仅更新，无新 Task；
两 Project 不新增、不改写，卡 1 关联「示例创智」真实 anchor2、成员 Task1。
receipt completed、1 proposal/1 applied；风险原句、来源与 source_time 核验通过，
无重复卡、failures=[]。这是同一新增样例 baseline FAIL / candidate PASS，
不是最新九样例或真实 W39 已通过。候选证明保存在 `candidate-project-identity.sqlite3`。

## Project 身份修复后的真实 W39：无重复任务，但关注仍遗漏

冻结 `c87752c0bfe2a7aa75235c607baeebe6f31fcb19`、CI Skill
`2b557b4754c39c688bc0533f04e152157b70fcc519bb4647f12bdbe330bb8d70`。
在新副本 `w39-project-identity-candidate.sqlite3` 精确回放 input27465，run10662、
native attempt18952 normal completed，codex_oauth/gpt-5.6-luna，无纠正或模型切换。
这里的 run/attempt 数字属于这一新副本，不与另一副本中的历史失败互相覆盖。

Task 数保持259，原129–134更新；129为formal/open，其余五项仍candidate/open；
没有新Task260/261，本轮不能据旧副本新增标题就断定新版本仍扩展ISO行动范围。
Project16→20：中汽创智anchor35、岚图anchor36、项目管理anchor37、抽检包生命周期
anchor38来自当前登记；旧「中汽」anchor23原样保留，当前Task129关联完整项目名称。
领域事件434→444。六条决定的attention_proposal全部null，proposal_count=0，
receipt=no_proposal、project_link_count=4、registry_row_count=5，卡片仍0。
工具input模式没有业务expected，因此其passed=true仅证明运行和出处等机械检查，
不覆盖要求中汽创智及岚图目标关注卡的业务验收；本轮业务结果明确FAIL。

已亲读当前完整周报和最终决定：原文保留已交付收入确认延迟、回款与供应商付款
节奏、岚图存量结算争议及暂停增量业务。最终update_summary说明未将一般风险另拆
为独立任务，但未说明为何已关联真实Task和正式Project的两项具体风险不提出关注。
代码收到零proposal后没有投影可应用；不属于页面过滤或投影拒绝。
当前可观测性只能判定未提出，不能证明Agent已评估并拒绝，也不能精确证明内部漏评原因。
在用户确认新的判断输出契约前不增Agent、补偿循环、硬编码风险或业务审核层。

当前代码的主Agent相关十个Python文件410passed（121.79s）、两页面22passed，
TypeScript/Vite build与app.cli/worker/email_worker/service_supervisor imports通过。
这些局部验证不替代最新完整九样例、真实副本重复幂等或生产结果。

## Project assessment 来源接入与冻结 oracle（开发完成，native 待执行）

Task 4 只更新当前测试/评估来源和一次性 replay 读回，不改变生产 Task Agent、领域应用、
投影、路由、重试或 timeout。Project 登记、混合来源、Web Project summary 以及四个
`process-work-items` fake producer 现在都显式输出必填 `project_assessments`；正例使用真实
Project selector、Task ID 和原始引文，负例/未知项写出 `not_needed` 或
`insufficient_evidence` 的具体理由。Web summary fixture 以当前周报登记行提供显式
`project_proposal`，不再依赖隐式登记；Project identity 专测仅在不测试 Attention 的分支
移除额外风险句，短名称、活动正式对象、同名冲突、登记/复用和原断言保持不变。

新增 version 1 fixture `task_attention_project_assessments_v1.json`，冻结 9 个 assessment
案例：报告、会议、聊天三类明确关注；仅风险标签、无真实 Task、未确认 Project、例行进展
四类负面/证据不足判断；同一 Project 两个 Task 由一个 assessment 和同一卡成员覆盖；
以及已有卡的真实保存 proof/实际 ID 重放幂等。expected 仍与 Work Item / existing facts
分离，测试确认 secret expected 不进入 payload 或 prompt。原 `task_attention_multisource.json`
九案例及 `task_attention_project_identity.json` 竞争案例未修改，SHA256 仍分别为
`c2d0846919ffe9452906a6c08b2d17b729db120c102c7ec00e72c9f2ee703338` 和
`9b56eec4bb74999a40448b1992ea0aed7caa7fff195c7c0c9e8a1acd859dfdf6`。

冻结 oracle 直接读取 run 的原始 `decision_json`；缺少 `project_assessments` 保持可观测，
不经候选模型默认值补齐。它按唯一 Project title 匹配原始 assessment，再用该原始位置读取
独立 projection receipt，不强制模型输出顺序。逐案例要求已审阅 outcome 与必需的
`source_ref + 经营影响原句` 被实际引文覆盖；允许同一 ref 的更长连续原文和额外真实引文，
但逐一核验所有额外引文。当前 null-signal 引文必须来自 immutable Work Item 并读回原
source_time/空 link；正 signal 必须核对真实 Signal 的 ref/原文/time/link，以及它与回执
支持 Task或已验证卡 proof 的实际关系。原始 assessment 中额外的正 signal 还必须在独立
projection receipt 中以相同 signal/ref/兼容的连续原文出现，且关联 receipt 实际 Task 或
receipt 指向卡片的精确 proof；不能由原始 assessment 自报 Task ID 补足。回执 Task 数按
真实且不重复的 ID 计算，`[1, 1]` 不可冒充两个 Task。回执还核对实际 anchor、Task IDs、
card ID、status 和成员；未知 Project 可由 case 明确要求 `anchor_id=null`。重复已有卡 case
两次回放保持同 card ID 且不新增 Task、Project 或 Attention event。

这些机械检查能证明显式判断覆盖、原始引文、持久化身份和应用结果，不能仅凭非空理由和
正确引用独立证明自由文本理由的业务语义正确。没有加入关键词、正则、短语黑名单、第二 judge
或 fixture-exact reason。主 Agent 仍须逐案例对照原文和预期经营影响，并人工审阅真实 W39
保存理由；机械 `passed=true` 不能标记 business PASS。

确定性 oracle 回归覆盖：原始字段缺失、Project title 覆盖错位、outcome 错误、缺理由、
缺必需原句、伪造额外引文、receipt 缺失/错 status/伪 existing card ID、伪 signal ID、
未关联但元数据相同的真实 signal、错误 time/link，以及正确前缀/额外真实引文正控制。
SPEC 修复进一步以真实未关联第二 Signal 复现“只改 raw assessment、receipt 不变”误通过，
以两真实 Task/两卡成员但 receipt `[1, 1]` 复现重复 ID 误计数；两项均先 RED 后 GREEN，
并保留已关联、receipt 亦保存的更长额外引文正控制。既有 Project link 的七个拒绝 case
改用独立 unknown-clue companion，明确绕过 selector coverage 只测试 application guard，
逐项断言原 missing/unofficial/inactive、cross-project/quote 及 source-ref 错误，不把无效
link anchor 复制进 assessment 造成提前失败。最终 assessment oracle `19 passed / 117 deselected`；
source registration + multisource + Web summary `146 passed`，CLI process-work-items
`22 passed / 254 deselected`。本节未运行
native Agent、provider、真实 W39、生产数据库、全局 Skill 发布、push、PR、合并或部署。

已有卡 case 的新 proof 字段不能由 pinned baseline `7bf7be5e` 的旧 constructor 原生 seed；
不得为此增加旧 schema fallback。当前配对 native 工作流是：先用候选
domain command 预备一份包含 Work Item、Project、Task、Signal 和已核验卡 proof 的
固定事实数据库；用 SQLite backup 复制为 baseline/candidate 两份 fresh 副本；
两边均以各自真实 app 源码和 Skill 走 `--input-id` 且核对 exact source_ref，
不运行 case seed；运行后再由同一冻结 oracle 以候选读取契约分别只读
两份 DB，expected 只在 run 后参与。baseline 缺 raw assessment/receipt 应被如实
记录，不用 candidate runner 代替 baseline 执行。这个公平性组织步骤和 native
结果由主 Agent 后续执行，本轮没有把 seed constructor 不兼容误记成业务失败。
