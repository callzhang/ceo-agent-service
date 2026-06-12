# CEO Agent OKR Review Runner Design

Date: 2026-06-08

## Goal

在本地 CEO Agent Service 里增加一个 OKR review runner。当员工在钉钉私聊或群聊中请求审核 OKR 时，服务自动读取该员工当前季度的实时叮当 OKR，基于 KR 进度更新提取员工主张，再用本地文件、Memory Connector 和 DWS 可访问的企业资料核实，最后在原会话给出逐 KR 的打分、依据、证据缺口和优化建议。

目标不是复盘旧季度或给过去翻案，而是根据当前季度进展给员工快速、证据化反馈。未明确周期时默认当前日期所属季度。

## Current Context

当前服务已经具备这些基础能力：

- `reply_tasks` 和 `reply_attempts` 管理钉钉消息处理、发送和审计。
- `conversations.codex_session_id` 保存每个钉钉会话对应的 Codex session。
- 普通 reply agent 会从 `conversations.codex_session_id` resume，同一会话的后续回复能看到前文。
- OA 审批已有专用 runner：专用 skill、结构化输出 schema、Codex session 和 audit tool events 记录，worker 负责执行 DWS 审批动作。
- `task-maintenance` 线程可以处理不适合阻塞普通消息链路的后台任务。
- DWS、Memory Connector、本地 workspace 已经是服务可用的知识入口。

OKR review 需要复用这些机制，不能在旁边另造一套独立 agent、独立 session 或独立消息系统。

## Hard Constraints

- 不允许 fallback。
- 不允许逻辑降级。
- 不允许在新逻辑里用 try/catch 吞掉错误或改写成看似成功的结果。
- 内部错误必须 fail fast，并在 request 状态、审计记录和面向用户的失败回复中暴露。
- 外部系统错误允许有限、显式、可审计重试。外部系统包括 DWS、Memory Connector、叮当 OKR 实时源、Codex CLI 进程和网络 I/O。
- 外部重试不能更换数据源、不能使用历史导出兜底、不能改变业务判断、不能隐藏最终错误。
- 实时叮当 OKR 获取在外部重试后仍失败时，直接说明当前无法获取实时 OKR 数据，不使用历史导出、workbook 或 raw JSON 兜底。
- Codex 输出 JSON 不合法时，不能自动修补成近似结果。该 run 失败。
- 证据源读取失败时，不能把该源当作缺失证据继续乐观评分。对应 KR 必须记录失败证据源；如果失败阻断必要核实，该 run 或该 KR 失败。
- 同一个 Codex session 同一时间只能有一个任务运行。

最外层服务循环可以继续使用现有的任务失败记录机制。它可以对明确的外部瞬时错误执行有限重试，但只能记录失败、通知和暴露最终错误，不能把失败转换为成功路径。

## External Retry Boundary

重试只属于外部 I/O 边界，不属于内部逻辑。

允许重试：

- DWS 命令和 DWS-backed API 请求。
- Memory Connector MCP 调用。
- 叮当 OKR 实时源请求。
- Codex CLI 进程启动、网络或服务端瞬时失败。
- DingTalk 消息发送。

不允许重试：

- schema 文件缺失。
- skill 文件缺失。
- `AgentEnvelope` JSON 非法。
- `domain_payload` 不符合对应 task schema。
- session lock 获取失败。
- 状态机非法转换。
- OKR 评分字段缺失或越界。
- 业务校验失败，例如 OA task 不属于当前用户。

外部重试要求：

- 固定最大次数，默认最多 3 次。
- 每次失败写入 audit tool events 或 request error trail。
- 最终失败必须保留最后一次错误和失败次数。
- 不能换到另一个来源，不能使用旧数据，不能返回部分成功冒充成功。

## Architecture

引入一套共享的结构化 Codex 执行机制，而不是为 reply、OA、OKR 分别维护互相隔离的 runner。

### StructuredCodexRunner

新增共享 `StructuredCodexRunner`，负责所有结构化 Codex 任务的共同能力：

- 按 `conversation_id` 读取 `conversations.codex_session_id`。
- 获取会话级 Codex session lock。
- 构建 Codex command。
- 注入 task spec 指定的 skill 和 developer instructions。
- 使用 task spec 指定的 output schema。
- 调用 Codex。
- 解析统一 `AgentEnvelope`。
- 提取 `codex_session_id`、transcript line range 和 audit tool events。
- 将新的 `codex_session_id` 写回 `conversations`。
- 释放 session lock。

`StructuredCodexRunner` 不负责业务动作。它只负责“同一个会话 session 上的一次结构化推理任务”。

### AgentSpec

每类任务通过 `AgentSpec` 定义差异：

- `name`: `reply`、`oa_approval`、`okr_review`
- `primary_skill_paths`: 完整注入给专用任务的 skill
- `reply_visible_skill_paths`: 注入普通 reply agent 的领域 skill 或摘要
- `schema_path`: 输出 schema
- `prompt_builder`: 输入 prompt 构造器
- `safety_mode`: 只读、允许工具、允许现实动作建议
- `result_model`: `AgentEnvelope` 的领域 payload 模型

OA、OKR、普通回复共享 runner，只更换 spec、prompt 和后处理。

### Domain Handlers

仍然保留轻量领域 handler，因为业务动作不同：

- reply handler 负责普通回复、leak check、发送策略。
- OA handler 负责识别 OA 消息、读取审批详情、校验 task 是否属于当前用户、执行 DWS 审批或评论。
- OKR handler 负责识别 OKR review 请求、实时读取叮当 OKR、创建 review request、发送完整 OKR review 结果。

handler 不直接做语义判断。语义判断由对应 spec 的 Codex task 完成。

## Session Continuity

`conversation_id` 是 Codex 上下文连续性的单位。

所有 agent spec 都必须使用同一个会话级 `codex_session_id`：

1. 从 `conversations.codex_session_id` 读取 session。
2. 在运行前获取 `conversation_id` 的 session lock。
3. 使用该 session resume。
4. 运行完成后写回最新 session。
5. 释放 lock。

这样 OKR review 的长审核历史会进入同一个 Codex session。后续员工继续在同一个会话追问时，普通 reply agent resume 同一个 session，可以看到前面的审核上下文。

OA 审批也必须迁移到同一机制。OA 不再维护独立 Codex runner。

## Session Lock

新增会话级 Codex session lock，保证同一个 Codex session 同一时间只有一个任务运行。

建议以 SQLite 表实现：

```sql
create table codex_session_locks (
  conversation_id text primary key,
  owner text not null,
  locked_at text not null default current_timestamp
);
```

获取锁失败时，该任务 fail fast，并由既有队列边界记录为失败。第一版不做静默等待、不做并发抢占、不在锁不可用时启动新 session。

如果需要重试，必须由队列层显式调度，并保留错误记录；runner 本身不做循环等待。

## Unified Output Envelope

所有 spec 输出统一外层结构 `AgentEnvelope`。领域差异放进 `domain_payload`，现实动作建议放进 `system_actions`。

```json
{
  "kind": "reply | oa_approval | okr_review | no_action | error",
  "user_response": {
    "mode": "send_reply | ask_clarifying_question | no_reply",
    "text": "",
    "sensitivity_kind": "general | internal_personnel | external_candidate"
  },
  "system_actions": [],
  "domain_payload": {},
  "audit": {
    "summary": "",
    "documents": [],
    "confidence": 0.0
  }
}
```

`system_actions` 是 worker 后处理入口。Codex 只提出结构化动作，不直接执行 DWS 现实动作。

OA 示例：

```json
{
  "type": "dws_oa_approval_action",
  "process_instance_id": "proc-1",
  "task_id": "task-1",
  "action": "通过",
  "remark": "同意，材料完整。"
}
```

OKR 示例：

```json
{
  "type": "persist_okr_review",
  "request_id": 123
}
```

普通回复示例：

```json
{
  "type": "send_dingtalk_reply",
  "reply_text_ref": "user_response.text"
}
```

worker 永远先解析 envelope，再按 `system_actions[].type` 分发动作。新增任务不再发明完全不同的顶层 JSON。

## Skill Injection

领域规则来自 skill，而不是散落在 handler 或 prompt 字符串里。

### OKR Skill

OKR review 使用 `dingtang-okr-review` skill 作为流程和评分规则来源。专用 `okr_review` spec 注入完整 skill。

该 skill 需要包含：

- 实时叮当 OKR 是 source of truth。
- 未明确周期时默认当前季度。
- KR 进度更新是待核实主张，不是最终事实。
- 必须输出员工主张分和事实核实分。
- 必须按完成时间、时差、业务影响和表述可衡量性打折。
- 证据不足必须保守。

### Reply-visible Skills

普通 `reply` spec 也注入相关领域 skill 或摘要，包括：

- OA 审批流程规则。
- OKR 审核规则。
- 钉钉材料读取规则。
- 人事和保密边界。

这样即使消息没有被 OKR 专用 handler 命中，普通 reply agent 也知道不能泛泛回复 OKR 审核请求，而应提示需要实时读取当前季度叮当 OKR，或引导进入 OKR review flow。

### Shared Skill Loader

实现一个共享 skill loader：

- 读取 spec 指定的 skill 文件。
- skill 缺失时 fail fast。
- 不做路径猜测或 fallback。
- 不做秘密信息输出。
- 拼接 developer instructions。

OA 审阅也迁移到该 loader，不再在 OA 模块里单独读 skill。

## OKR Request Flow

1. Worker 读取钉钉消息。
2. 在普通 reply 前识别 OKR review 请求，例如“帮我审核 OKR”“审核当前季度 OKR”“看看我的 KR 进度”。
3. 若未明确周期，使用当前季度。
4. 通过实时叮当 OKR API 或当前可用的正式读取通道获取该员工当前季度 OKR。
5. 如果实时 OKR 获取在外部重试后仍失败，创建失败记录并回复原会话：当前无法获取实时 OKR 数据。
6. 如果读取成功，创建 `okr_review_requests` 记录。
7. OKR 后台处理器获取 conversation session lock。
8. `okr_review` spec resume 当前 Codex session。
9. Codex 按 KR 提取主张、核实证据、打分、建议。
10. worker 校验 envelope 和 `domain_payload`。
11. 持久化 review run 和逐 KR items。
12. worker 将完整 review 结果发送回原会话。
13. 发送和 review 审计都保留。

第一版不使用历史导出作为兜底，也不允许从旧 workbook 自动生成 review。

## OKR Data Model

新增 OKR 专用表，不复用 OA 字段。

### okr_review_requests

保存一次员工请求。

字段建议：

- `id`
- `conversation_id`
- `conversation_title`
- `trigger_message_id`
- `trigger_sender`
- `trigger_sender_user_id`
- `trigger_text`
- `period_label`
- `period_start`
- `period_end`
- `okr_source_json`
- `status`: `pending | processing | done | failed`
- `error`
- `codex_session_id`
- `created_at`
- `updated_at`

### okr_review_runs

保存一次 Codex review run。

字段建议：

- `id`
- `request_id`
- `codex_session_id`
- `codex_transcript_start_line`
- `codex_transcript_end_line`
- `envelope_json`
- `audit_tool_events_json`
- `audit_summary`
- `created_at`

### okr_review_items

一行一个 KR。

字段建议：

- `id`
- `request_id`
- `objective_title`
- `objective_weight`
- `kr_title`
- `kr_weight`
- `self_progress`
- `kr_progress_update`
- `claim_text`
- `claim_completion_time`
- `deadline`
- `claim_base_score`
- `claim_discount_factor`
- `claim_discount_reason`
- `claim_score`
- `verified_completion_time`
- `verified_base_score`
- `verified_discount_factor`
- `verified_discount_reason`
- `verified_score`
- `evidence_used_json`
- `evidence_gap`
- `review_comment`
- `suggested_follow_up`

## Scoring Model

每个 KR 输出两套分数。

### 员工主张信息打分

`claim_score` 基于“如果员工填写的 KR 进度更新成立”的假设。

步骤：

1. 从 KR 进度更新提取可核实主张。
2. 提取产出、指标、数量、完成时间、交付对象、业务影响。
3. 如果主张成立，按 KR 目标完成度给 `claim_base_score`。
4. 如果主张含糊、不可衡量、没有明确产出，打折。
5. 如果员工提供的完成时间晚于 KR 要求时间，打折。

### 事实核实后打分

`verified_score` 只基于独立证据。

证据来源：

- 本地文件。
- `memory_recall`。
- DWS 文档、知识库、AI 听记、聊天、日程、TODO、日志、联系人等。

步骤：

1. 用 KR 进度更新中的主张作为检索线索。
2. 用证据核实主张是否成立。
3. 提取实际完成时间。
4. 按已核实完成度给 `verified_base_score`。
5. 如实际完成时间晚于 KR 要求时间，打折。
6. 如证据不足，保守评分并写明缺口。

### Discount

折扣系数范围是 `0.3-0.8`。

- `0.8`: 轻微晚于要求，且业务影响小。
- `0.6`: 明显晚于要求，影响协作、交付或复盘节奏。
- `0.3-0.5`: 严重超期，导致业务窗口错过或目标价值大幅下降。

含糊不可衡量的 KR 进度更新也打折。折扣不仅适用于事实核实分，也适用于员工主张理论分。

输出必须分别保存：

- `claim_base_score`
- `claim_discount_factor`
- `claim_discount_reason`
- `claim_score`
- `verified_base_score`
- `verified_discount_factor`
- `verified_discount_reason`
- `verified_score`

## Error Handling

新 OKR 和 unified runner 的内部逻辑采用 fail-fast。外部 I/O 先按 External Retry Boundary 做有限重试，重试耗尽后 fail。

失败类型：

- skill 文件不存在。
- output schema 不存在。
- Codex command 外部重试耗尽。
- Codex 输出不是合法 `AgentEnvelope`。
- `domain_payload` 不符合对应 task schema。
- 实时 OKR 读取外部重试耗尽。
- 必需证据读取外部重试耗尽。
- session lock 获取失败。
- DWS 发送外部重试耗尽。

处理方式：

- 当前操作失败。
- 错误写入对应 request/run/error 表。
- 需要回复员工时，回复明确失败原因。
- 不转成部分成功。
- 不用旧数据兜底。
- 不自动改走普通 reply。
- 不自动新开独立 session 绕过锁或 stale session。

## Migration Plan

第一阶段建立统一 envelope 和共享 runner，并接入 OKR、OA、reply 三类任务：

1. 新增 `AgentEnvelope` 模型和 schema。
2. 新增 `StructuredCodexRunner`。
3. 新增 session lock。
4. 将 OKR review 建在 `StructuredCodexRunner` 上。
5. 将 OA 审阅迁移到 `StructuredCodexRunner`，并让 OA handler 读取 `system_actions` 执行 DWS。
6. 将普通 reply spec 迁移到 `AgentEnvelope`，下游发送逻辑可以通过 adapter 继续复用现有 reply delivery 代码。
7. 将 OKR、OA、材料读取、人事保密等领域 skill 注入普通 reply spec。
8. 新增 OKR tables 和 store 方法。
9. 新增 OKR handler 和 task-maintenance 处理。
10. 增加测试覆盖。

第二阶段删除重复旧路径：

1. 移除 OA 专用 command builder 里重复的 skill 注入逻辑。
2. 移除旧 `CodexDecision` 直接作为 runner 顶层输出的路径。
3. 保留必要的 delivery adapter，直到所有调用方都直接使用 `AgentEnvelope`。

## Testing

必须覆盖：

- OKR 请求在普通 reply 前被识别。
- OKR 请求默认当前季度。
- 实时 OKR 获取外部重试耗尽时 request failed，且不读取旧导出。
- OKR review 使用 `conversations.codex_session_id` resume。
- OKR review 完成后写回新的 `codex_session_id`。
- 后续普通 reply resume 同一个 session。
- session lock 阻止同会话并发 runner。
- lock 获取失败 fail fast。
- skill 缺失 fail fast。
- schema 缺失 fail fast。
- 非法 envelope fail fast。
- 外部系统瞬时错误按固定次数重试，并记录每次失败。
- 外部系统重试耗尽后 request failed，暴露最终错误。
- claim score 和 verified score 都会应用超时折扣。
- 含糊不可衡量的 KR 进度更新会降低 claim score。
- 事实核实分不使用自评补分。
- OKR review items 保存证据、缺口、折扣原因和建议。
- OA 迁移后仍通过 `system_actions` 执行 DWS 审批或评论。

## Fixed Decisions

- 普通 reply、OA 审批、OKR review 都使用 `AgentEnvelope` 作为 runner 顶层输出。现有 reply 发送代码可以通过 adapter 复用，但 runner 输出不再分裂。
- 实时叮当 OKR 读取通过单一配置的正式实时源完成。第一版不实现多源优先级和失败切换；如果配置的实时源不可用，request 失败。
- OKR review 完整结果发回原会话。如果超过 DWS 单条消息长度限制，按 KR 边界确定性分段发送。分段不是降级；它只改变传输切片，不改变内容。任一分段发送失败，request 标记失败并暴露错误。
