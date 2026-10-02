# 多来源项目关注第一版验收

状态：开发分支实现及确定性测试已验证；首个真实模型比较暴露候选失败，首次资格修订后 W39 两卡及依据已验证；同项目多个新行动的成员仍失败，后续文字修订待 fresh rerun。W39 副本两次回放及上线均尚未验证。本文件不表示已发布，不用 fake runner 结果证明模型判断有效。

## 四个独立门槛

| 门槛 | 当前证据 | 仍需完成 |
| --- | --- | --- |
| 代码测试 | Tasks 1–6 已独立复核。Task 6 的 `128ea5b4` / `f92d0a92`：7 项 API、22 项页面测试与构建通过；主 Agent 检查模拟列表及 decision/watch 详情，桌面及 390×844 窄屏亮/暗色可读。Task 7 的固定输入、精确回放、旧 run 保留、幂等卡片/事件、落库依据与 expected 隔离回归先 RED 后 GREEN。 | 合并前重跑本次有关文件；模拟页面及 fake runner 不证明真实业务效果。 |
| 语义评估 | 固定 `tests/fixtures/task_attention_multisource.json` version 1，9 个 case；work_item、existing_context、expected 分开。baseline 固定实际生产代码 `7bf7be5e6dcdb181b0674e79ace17a598dcf87e0`；候选包含该 baseline。baseline 56 项模型/检索测试通过。首个 `w39-project-risk` 实际比较：baseline 4 Tasks/2 Projects/0 Attention，候选 `4f0e06e5` 3 Tasks/2 Projects/0 Attention，候选未达到两项目卡预期。 | baseline/candidate 各实际运行同一 9 个 case；同路由、模型、timeout、初始事实、concurrency=1；记录 readback、失败及 revision。首次风险资格文字已修订，需相同输入和模型 fresh copy 重跑；其余样本比较尚未完成。 |
| W39 数据库副本 | 主 Agent 用 SQLite backup 创建唯一完整性验证副本，`integrity_check=ok`，3,223,863,296 bytes；259 Tasks、16 Projects、0 Attention。精确输入 27465 已核对 source_ref，状态 done、attempts=1。原有 W39 Tasks 129–134；中汽 anchor 23 不代表中汽创智别名。 | 从这份不可变初态另建候选副本，回放两次；比较真实 Task/Project/card IDs、关联、依据及事件。不得为落卡编造字段变化、强行合并简称或忽略额外 Task。副本尚未回放。 |
| 上线 | 全局权威 Skill 仍为 version 2，SHA256 `5c2bcbee182ed0872a55f35193c8815a51dd3e0ba0ca92b8e1eaa55e20cc1992`；候选使用隔离 `ci/shared-skills` version 3。 | PR + 固定 eval 对比通过后按既有部署流程发布、读回运行健康及队列、发布权威 Skill、单输入生产回放及真实页面核对。尚未上线。 |

SQLite backup 初态由主 Agent 持有：
`/var/folders/74/yj2lxqs162q7rqzm0mj8nv1c0000gn/T/ceo-attention-eval-40j4skvv/baseline.sqlite3`。
此文件不作为脚本直接运行目标；候选回放使用另一个由 SQLite backup 生成的副本。最终报告后按既有备份清理规则处理。

## 原生 runner 调用

脚本仅处理一个 ID 或一个固定 case，不调用业务 CLI 的 pending 遍历、扫描器、outbound DWS 或恢复流程。它取得所传副本的现有 TaskAgentSessionLease，调用现有 process_work_item，并通过 scoped TaskAgentRunner 使用 `task-agent:attention-eval:v1`，不续接生产 scope 的 session 指针。

执行环境先注入生产 launchd 的环境参数，并指定 `CEO_ENV_FILE` 为真实生产 `.env`；脚本在选定 `--code-root` 后才 import app 并读取该配置。`CEO_WORKER_DB` 必须保留生产路径，`--db` 独立选择评估副本：脚本在打开 store 前拒绝等于 worker_db_path 的路径。`CEO_SERVICE_MCP_CONFIG_PATH` 等现有相对配置由调用者解析为实际绝对路径；不建立临时 MCP 配置。须明确提供生产 `CEO_TASK_CODEX_TIMEOUT_SECONDS` 和 `CEO_TASK_CODEX_IDLE_TIMEOUT_SECONDS`；脚本按相同版本业务 CLI 的 TASK_AGENT_MAX 常量取 min，输出实际有效值。现有 prompt 的外部写禁止仍为 best-effort，不声称硬隔离。

基线命令使用固定实际 baseline checkout 与其 version 2 Skill，候选使用候选 checkout 和 version 3 Skill；两者显式读取同一 fixture 文件。每个 case 一个全新不存在的数据库路径，顺序执行 9 个命令，即 concurrency=1；不能用候选 app 模块执行“baseline”。以下路径由主 Agent 在实际运行时替换，不能将有现存事实的库作为 fresh case DB。

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
这只证明文字契约和实际加载路径，修订后原生模型效果、全部 9 样本及生产副本回放仍待验证。

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
修订后固定样本的原生卡片成员、额外 Task 是否消除及其他样本效果仍待 fresh rerun。

固定计数另经原生结果后的来源/独立交付契约复核，由主 Agent 单独修订评估 oracle：
W39 显式允许 [3,4,5]，meeting-new-risk 允许 [1,2]，其他样本保持原精确计数，
不改为全局最小数量门槛。W39 的五项都有真实行动依据：进展章节的“需同步”不能因
所在章节被排除，同句对账与方案沟通可以是独立可完成结果；Task 3 的描述仍含方案沟通，
与 Task 4 有范围重叠，保留人工身份复核注意点，不据计数放行把重复身份说成已排除。
两侧 baseline/candidate 以同一修订后 oracle 重新评估，expected 仍仅用于运行后比较，
不进入 Agent 输入、routing 或规则。首次资格修订后的 W39 已实际生成两张卡且依据有效；
同项目多新行动的实际成员门槛未放宽，上述 case 9 成员失败仍须修订后原生验证。
