# 多来源项目关注第一版验收

状态：开发分支实现及确定性测试已验证；真实模型比较、W39 副本两次回放及上线均尚未验证。本文件不表示已发布，不用 fake runner 结果证明模型判断有效。

## 四个独立门槛

| 门槛 | 当前证据 | 仍需完成 |
| --- | --- | --- |
| 代码测试 | Tasks 1–6 已独立复核。Task 6 的 `128ea5b4` / `f92d0a92`：7 项 API、22 项页面测试与构建通过；主 Agent 检查模拟列表及 decision/watch 详情，桌面及 390×844 窄屏亮/暗色可读。Task 7 的固定输入、精确回放、旧 run 保留、幂等卡片/事件、落库依据与 expected 隔离回归先 RED 后 GREEN。 | 合并前重跑本次有关文件；模拟页面及 fake runner 不证明真实业务效果。 |
| 语义评估 | 固定 `tests/fixtures/task_attention_multisource.json` version 1，9 个 case；work_item、existing_context、expected 分开。baseline 固定实际生产代码 `7bf7be5e6dcdb181b0674e79ace17a598dcf87e0`；候选包含该 baseline。baseline 56 项模型/检索测试通过。 | baseline/candidate 各实际运行同一 9 个 case；同路由、模型、timeout、初始事实、concurrency=1；记录 readback、失败及 revision。尚未执行真实模型样本。 |
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

固定正例全部达到项目卡预期；负例无误关注、伪项目或伪 Task；真实卡片引用有可核对的 signal/source_ref/连续原文/来源时间及支持 Task；同项目两行动保留两 Task 一卡；会议/聊天更新复用当前卡；来源冲突保留双方证据而不改写登记字段。W39 两次回放不重复 Task/Project/card；卡片关联中汽创智及岚图的真实任务和原文；第二次卡 ID 不变且未变化不追加事件。任何真实模型失败都记录原 case 并修正 prompt/context 后重做相同对比，不用 fake 输出替代模型门槛。
