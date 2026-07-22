# 飞书能力对标钉钉：分阶段交付与验收边界

本文是 `ceo-agent-service` 飞书适配的交付合同。对标范围以本仓库已经存在且有
测试或运行路径的钉钉能力为唯一基线；飞书平台独有功能可以后续增强，但不能用来
掩盖钉钉已有能力的缺口。

## 总目标与停止条件

停止条件同时满足以下四项：

1. 每项钉钉能力都有经过测试的飞书实现，或有飞书官方资料支持的
   `verified_no_equivalent` 结论；
2. 所有远端写操作均绑定飞书应用身份、可信目标、不可变动作哈希、审批策略和最终
   状态验证；不确定结果不得盲目重放；
3. 飞书功能默认关闭，模型运行时没有 CLI、网络或 MCP 写权限，且不能提供任意会话、
   用户、消息、文件或应用标识；
4. 每个阶段都有独立分支、离线自动化测试和 Draft PR，最终全量回归相对上游基线
   不新增失败。

## 分阶段 PR

| 阶段 | 交付范围 | 验收边界 |
|---|---|---|
| 1. Foundation | 应用/会话隔离、队列 lease、outbox、审计、迁移、恢复 | PR #6；重复事件/并发 claim/崩溃恢复/未知发送全部 fail closed |
| 2. Rich IM | 富消息、媒体、话题、长回复、mention、reaction、撤回、handoff | 本地验收完成：IM-01 至 IM-12，`tests/feishu` 596 项通过；真实租户 smoke test 仍保持 opt-in |
| 3. Read-only | 通讯录、文档/Wiki、云盘、Base、日历、审批、妙记及关联任务状态只读 | TAT/UAT 边界明确；权限最小化；分页、限流、脱敏和快照一致性通过 |
| 4. Reviewed writes | 文档、日历、待办、邮件等经审核写入 | 每项写入有 preview、审批哈希、幂等和最终态核验 |
| 5. High-risk | 审批响应、人员/组织及其他高风险业务动作 | UAT 用户隔离；四眼或逐项确认；拒绝跨身份和批量隐式写入 |
| 6. Workflow parity | OKR、handoff、memory、定时/恢复流程和最终对标 | 能力注册表无未解释 `planned`；全量测试、迁移和回滚演练完成 |

后续 PR 可以在一个阶段内继续拆小，但不得降低该阶段的最终验收条件。所有 PR 默认
为 Draft，基于上一个阶段提交堆叠，并在合并前重新检查 `origin/main`。

## Stage 2：Rich IM 验收矩阵

### IM-01 入站消息

- `text`、`post`、`image`、`file`、`audio`、`media`、`sticker` 按官方 typed
  contract 安全归一化；未知/系统/红包类型明确拒绝或标记不完整，不能误判为文本。
- 持久幂等键为 `(app_id, message_id)`；`event_id` 仅作审计证据。
- 只保存安全文本投影和闭集元数据；SDK raw、Post AST、Card JSON、token、临时 URL
  永不进入数据库、prompt、日志或审计。
- 单聊、群聊和话题均校验应用、发送人、会话 scope 和结构化机器人 mention。

### IM-02 引用、话题和上下文

- 持久化 `root_id`、`parent_id`、`thread_id`，并以应用和会话共同隔离上下文。
- 上下文有条数、时间和触发时点边界；同一引用根只处理最新有效触发。
- 回复保留在原引用/话题；目标不存在或已撤回时失败关闭，不能降级成群内新消息。

### IM-03 文本、Post 与结构化 mention

- 短文本和 Markdown/Post 由受限本地 payload 生成，禁止任意 SDK JSON。
- mention 只能来自默认空的本地 `open_id` 身份映射，使用 `open_id` 结构化发送；不解析
  模型生成的 `@名字`，也不允许模型提供用户 ID。consumer 入队和 sender 每次真实
  mutation 前都会校验当前功能门及映射，排队后撤销授权会失败关闭。
- 本阶段不支持通用主动私聊或群发；普通回复目标必须来自持久化触发事件，handoff
  仅能直发给本地 allowlist 中的受信用户 `open_id`。

### IM-04 长回复和回执

- 单个 wire chunk 内的 Markdown 可确定性转换为 Post；超过单片上限时必须先
  降级为纯文本，再由本地确定性分片，稳定 UUID 不超过 50 字符。
- 冻结的有序分片计划、计划摘要、首个 `message_id` 和所有 `chunk_ids` 按序持久化；
  审批身份和逐片 UUID 均绑定计划摘要，每片均可用于对账和撤回。
- 中途超时、取消或结果不明进入 `send_unknown`，不得自动重发。

### IM-05 图片与文件

- 入站资源必须通过 `message_id + file_key + resource type` 绑定的官方消息资源接口获取；
  不使用任意 URL，也不使用“本 bot 上传资源”下载接口代替。
- 默认最多 8 个资源、单资源 20 MiB、单事件 32 MiB；实际字节、魔数/MIME、常规文件、
  路径和应用身份均需验证。
- 图片仅以已验真的服务管理路径传给无工具模型；文件、音视频只注入可信元数据和明确
  “未读取/未转写”提示。临时文件有保留期限且不能越界或跟随符号链接。
- 下载失败可审计，并明确禁止模型猜测内容。

### IM-06 Reaction

- 只允许闭集 Emoji，且只对当前持久化触发消息执行；模型不能指定目标。
- 动作有稳定执行 ID、应用/消息绑定、回执、审计和幂等；超时为 unknown。
- 任意“文字表情”登记为 `verified_no_equivalent`，不能伪装成 Emoji reaction。

### IM-07 撤回

- 只能撤回当前应用通过本地 outbox 发送且已保存 `active` receipt 的消息；所属 delivery
  必须为 `sent`、`failed` 或 `rejected`，续发或结果不确定状态全部失败关闭。
- 始终要求明确人工确认；多 chunk 撤回逐片记录最终状态。
- 已删除视为幂等终态；权限/所有权/状态无法确认时失败关闭。

### IM-08 Handoff 与加急语义

- 飞书远端接管通知只能直发给本地 allowlist 中的受信用户 `open_id`；
  不支持会话 allowlist，模型也不能指定目标。
- 决策必须先完整校验，并把 attempt、远端 action 和本地 fallback 原子持久化；事务提交前
  不允许触发系统通知。远端 action 全部确定 `failed/rejected` 后才允许本地回退；
  `sent` 取消回退，`ready/sending/retry/result_unknown` 均继续等待，superseded 永不发送。
- 本地回退只使用有界超时的 offline-only macOS sink，固定 `url=None`，不访问 bridge、
  HTTP 或飞书 SDK；调用前持久化 mutation fence。超时、非零退出、执行后异常或带
  fence 的崩溃恢复进入 `result_unknown` 且不得自动重放；只有可证明进程未启动时才可
  有界重试。claim、lease、结果和恢复均有持久状态与审计。
- 飞书 `urgent_app/sms/phone` 只能作用于 bot 自己发送的既有消息，不能声称完全等价于
  钉钉 DING；未覆盖的确认/催办语义登记为无精确等价。

### IM-09 入站撤回防护

- 实际发送前使用官方消息查询重新确认触发消息仍存在且可访问。
- 已撤回、删除或无权访问时停止，不能改为新消息。

### IM-10 可靠性与隔离

- event、task、attempt、delivery、chunk、action 均有稳定身份，幂等域包含 `app_id`。
- 每个 mutation 都复核 lease、目标、payload hash 和审批；`send_unknown` 阻塞同一会话的
  后续动作，直到确定性对账。
- listener、媒体 resolver 和 sender 共享一个已连接 Channel runtime；审核和 CLI 不得
  新建第二个 WebSocket。

### IM-11 默认关闭与权限

- 所有新增功能 flag 默认关闭；高于普通文本回复风险的操作默认逐项确认。
- `CEO_FEISHU_ENABLED=0` 是绝对停止边界，不启动 listener、consumer 或本地 fallback
  worker。显式开启 receive-only 后，远端 handoff 仍默认关闭；gate 关闭或 allowlist
  为空时，仅允许 IM-08 的 offline-only 持久化本地兜底，且不依赖 WebSocket readiness。
- 最小权限按功能分组展示，不要求管理员为未启用功能授予 scope。
- 权限变更只输出 manifest；程序不自动修改远端应用、发布版本或安装机器人。

### IM-12 测试与验收

- 离线 contract test 锁定 `lark-channel-sdk==1.2.0`、`lark-oapi==1.7.1` 的使用形状。
- 覆盖恶意内容、资源超限、路径穿越、身份错配、并发 claim、迁移、崩溃、timeout、
  unknown reconcile、chunk receipt、reaction 和撤回所有权。
- `tests/feishu`、相关 store/CLI/audit 测试和编译检查通过；全仓失败集合不得相对阶段 1
  基线上升。真实租户 smoke test 保持显式 opt-in，不能成为默认测试前提。

## 官方接口依据

- [接收消息事件与 `message_id` 去重](https://open.larksuite.com/document/server-docs/im-v1/message/events/receive)
- [发送消息](https://open.larksuite.com/document/server-docs/im-v1/message/create)
- [消息内容和 Post 结构](https://open.larksuite.com/document/uAjLw4CM/ukTMukTMukTM/im-v1/message/create_json)
- [回复与话题](https://open.larksuite.com/document/server-docs/im-v1/message/reply)
- [消息历史](https://open.larksuite.com/document/server-docs/im-v1/message/list)
- [IM 能力与资源接口总览](https://open.larksuite.com/document/server-docs/im-v1/introduction)
- [撤回消息](https://open.larksuite.com/document/server-docs/im-v1/message/delete)
- [更新消息](https://open.feishu.cn/document/server-docs/im-v1/message/update)
- [官方 Python SDK](https://github.com/larksuite/oapi-sdk-python)

平台 UUID 只提供一小时去重窗口，因此永久幂等仍由本地 outbox、不可变业务键和最终态
对账共同保证。

## Stage 2 本地验收证据

- `tests/feishu`：596 passed；覆盖消息 ID 去重、应用/会话/引用根隔离、富消息投影、
  媒体内容寻址与两阶段清理、reaction、人工撤回、handoff、审核 UI/CLI、并发 claim、
  crash recovery、未知结果互锁、逐片回执、审批哈希和旧库迁移。
- Stage 2 聚焦回归：851 passed、1 deselected；全仓隔离回归：3191 passed、23 failed、
  5 skipped。Stage 1 基线提交 `b8a7cd2` 为 2715 passed、22 failed、5 skipped；22 个
  既有失败仍来自 live eval、Memory Connector 本机环境、macOS/Unix socket 沙箱、
  缺失可选系统模块及非飞书语义。唯一额外失败是当前只读沙箱拒绝
  `tests/test_prompt.py` 向仓库根写临时文件；飞书及相关业务测试没有新增失败。
- `event_id` 仅保留为审计证据；持久身份为 `(app_id, message_id)`，上下文边界使用
  `message_id`，并持久化 `root_message_id`、`parent_message_id` 和归一化版本/截断标志。
- 媒体默认最多 8 个资源，默认保留 7 天；维护流程先安全清理本地文件与媒体引用，
  再删除可清理的事件行。共享内容引用、处理中资源和清理失败均阻止事件提前删除。
- `send_unknown` 与消息动作 `result_unknown` 会在同一 `app_id + chat_id` 双向阻塞，
  必须先完成确定性对账，不能让回复、reaction、撤回或 handoff 相互越过。
- target preflight 使用独立错误域；mutation fence 后的任何查询异常都进入 unknown，
  不会被 SDK 通用错误码误判为可安全重试。回复与 action 使用持久化共享速率预算和
  跨窗口轮转游标，在多进程、重启以及极低配额下仍保持配额原子性与有界公平。
- 旧库升级使用 owner-only、拒绝符号链接的跨进程文件锁，并在双进程竞争、完整性和
  外键检查中通过；管理页对密钥键、递归别名、继承环境和嵌入式密钥统一脱敏，批量
  配置写入在非法 key、控制字符或敏感引用时整批拒绝。
- 所有新增开关仍默认关闭；没有真实租户连接、权限变更或消息发送作为默认验收步骤。
