# 飞书官方 Bot 通道运维手册

本文说明 CEO Agent Service 的可选飞书消息通道如何以最小权限安装、启停、
轮换凭证、排障和回滚。该通道使用企业自建应用的 Bot 身份和官方 WebSocket
Channel SDK；它与用于读取飞书文档的 `lark-cli` 无关，不代表员工本人发言，
不读取飞书本地数据库，也不操作飞书桌面客户端。

## 1. 安全状态和停止边界

仓库中的所有飞书开关默认关闭：

```dotenv
CEO_FEISHU_ENABLED=0
CEO_FEISHU_SENDER_ENABLED=0
CEO_FEISHU_MEDIA_ENABLED=0
CEO_FEISHU_REACTION_ENABLED=0
CEO_FEISHU_RECALL_ENABLED=0
CEO_FEISHU_HANDOFF_ENABLED=0
CEO_FEISHU_REPLY_MENTION_SENDER=0
CEO_FEISHU_SEND_MODE=confirm
CEO_FEISHU_SECURITY_MODE=strict
```

安装 optional dependencies、运行默认测试、填写 App ID 或把 App Secret 写入
Keychain，都不会单独建立飞书连接或发送消息。只有明确把
`CEO_FEISHU_ENABLED=1` 并重启服务后，listener 才可以连接飞书。

真实发送还需要同时满足：

1. `CEO_NOT_SEND_MESSAGE=0`；
2. `CEO_FEISHU_SENDER_ENABLED=1`；
3. 应用已获批并发布完整的 `reply_send` 权限组：
   `im:message:send_as_bot` 与 `im:message:readonly`；
4. 默认 `confirm` 模式下，delivery 已通过本机人工批准。

`auto` 会明确放弃逐条人工批准，只保留前述出站门禁、allowlist、速率限制和幂等
状态机，因此不属于第一阶段启用范围，也不能因为一次测试成功而自动开启。

开发和离线验收的停止边界是：完成代码、迁移、文档与 fake/fixture 测试，保持
上述两个飞书开关为 `0`，不创建应用、不连接飞书、不接收真实消息、不发送消息。
创建应用、管理员审批、receive-only 和第一次真实发送属于后续独立授权阶段。

## 2. 官方组件与最小权限

飞书依赖固定在 `feishu` optional dependency：

```text
lark-channel-sdk==1.2.0
lark-oapi==1.7.1
```

安装依赖不会启用通道：

```bash
.venv/bin/pip install -e '.[dev,feishu]'
```

企业自建应用的 receive-only 基线只申请租户身份下的以下能力：

| 权限或事件 | 用途 |
| --- | --- |
| `im:message.p2p_msg:readonly` | 接收用户发给机器人的私聊消息 |
| `im:message.group_at_msg:readonly` | 接收指定群内结构化 `@机器人` 消息 |
| `im.message.receive_v1` | 消息接收事件 |

不要申请完整通讯录、完整聊天历史、云盘、邮件或用户 access token 权限。不要
勾选“接收群内所有消息”；`@所有人` 或正文中出现机器人名字不等于结构化
`@机器人`。如果租户后台要求新增组合权限，先记录和审核差异，不要静默扩权。

富消息能力按需追加 scope，不能为了“以后可能用到”一次性全开：

| 功能组 | 额外权限 | 默认 |
| --- | --- | --- |
| 回复与发送前复核（`reply_send`，必须整组授予） | `im:message:send_as_bot`、`im:message:readonly` | 关闭 |
| 入站媒体读取 | `im:message:readonly` | 关闭 |
| Emoji reaction 新增 | `im:message.reactions:write_only` | 关闭 |
| 撤回 bot 已发消息 | `im:message:recall` | 关闭，且逐项确认 |
| 接收所有群消息 | `im:message.group_msg` | 未实现，禁止申请 |
| 上传并发送媒体 | `im:resource` | 未实现，禁止申请 |

截至 2026-07-22，飞书官方[应用权限列表](https://open.larksuite.com/document/ukTMukTMukTM/uYTM5UjL2ETO14iNxkTN/scope-list?fb=2&lang=en-US)
仍将“撤回消息” API 标记为 `im:message:recall`。如租户控制台展示的权限名称或
组合与官方列表不同，停止启用 recall 并记录差异，不能静默扩权。

`im:message.urgent` 及短信/电话加急没有钉钉 DING 确认与升级语义的精确等价，
当前没有执行路径，也不出现在权限 manifest；不要为占位功能预先申请这些权限。

权限变更后必须由管理员审核并发布新的应用版本。程序只输出 manifest，不会自动修改
开放平台配置、发布版本或安装机器人。

应用管理员准备流程：

1. 在飞书开放平台创建企业自建应用并启用机器人能力；
2. 只添加 receive-only 基线权限和 `im.message.receive_v1` 事件；需要启用
   sender 时，再单独审核并整组添加 `reply_send`，重新发布应用版本；
3. 选择长连接接收事件，不配置公网 webhook；
4. 将应用可用范围限制为测试用户，发布版本并完成管理员审批/安装；
5. 只把机器人加入专用测试群；
6. 第一阶段保持本机 `CEO_FEISHU_ENABLED=0`，直到明确进入 receive-only 验证。

官方资料：

- [接收消息事件](https://open.feishu.cn/document/server-docs/im-v1/message/events/receive)
- [发送消息](https://open.feishu.cn/document/server-docs/im-v1/message/create)
- [回复消息](https://open.feishu.cn/document/server-docs/im-v1/message/reply)
- [撤回消息](https://open.feishu.cn/document/server-docs/im-v1/message/delete)
- [应用权限列表](https://open.feishu.cn/document/ukTMukTMukTM/uYTM5UjL2ETO14iNxkTN/scope-list)
- [官方 Channel SDK](https://github.com/larksuite/channel-sdk-python)

## 3. 凭证存储

App ID 可以写入受 Git 忽略、权限受控的本机 `.env`：

```dotenv
CEO_FEISHU_APP_ID=cli_xxx
```

App Secret 优先存入 macOS Keychain：

- service：`ceo-agent-service/feishu`
- account：`app_secret`

以下命令通过无回显提示写入，不把 Secret 放进命令参数或 shell history：

```bash
.venv/bin/python -c 'import getpass,keyring; keyring.set_password("ceo-agent-service/feishu", "app_secret", getpass.getpass("Feishu App Secret: "))'
```

只检查是否已配置，不输出值或任何片段：

```bash
.venv/bin/python -c 'from app.config import feishu_app_secret; print("configured" if feishu_app_secret() else "missing")'
```

`CEO_FEISHU_APP_SECRET` 只作为临时本地调试 fallback。不要把它加入
`.env.example`、Git、launchd plist、shell history、日志、SQLite 或审计页面。
Keychain backend 出错时，状态只能报告 `missing`/`configured` 或通用错误，不能
输出异常中可能包含的凭证材料。首次访问 Keychain 可能需要当前 macOS 用户确认；
应从实际服务用户上下文验证访问，不要为排障放宽为所有进程可读。

## 4. 配置

```dotenv
CEO_FEISHU_ENABLED=0
CEO_FEISHU_SENDER_ENABLED=0
CEO_FEISHU_MEDIA_ENABLED=0
CEO_FEISHU_REACTION_ENABLED=0
CEO_FEISHU_RECALL_ENABLED=0
CEO_FEISHU_HANDOFF_ENABLED=0
CEO_FEISHU_REPLY_MENTION_SENDER=0
CEO_FEISHU_SEND_MODE=confirm
CEO_FEISHU_SECURITY_MODE=strict
CEO_FEISHU_STALE_EVENT_SECONDS=300
CEO_FEISHU_CONTEXT_LIMIT=20
CEO_FEISHU_CONTEXT_LOOKBACK_SECONDS=86400
CEO_FEISHU_MAX_SENDS_PER_MINUTE=10
CEO_FEISHU_EVENT_RETENTION_DAYS=30
CEO_FEISHU_MEDIA_RETENTION_DAYS=7
CEO_FEISHU_MEDIA_MAX_ASSETS=8
CEO_FEISHU_MEDIA_MAX_BYTES=20971520
CEO_FEISHU_MEDIA_EVENT_MAX_BYTES=33554432
CEO_FEISHU_HANDOFF_OPEN_IDS=
CEO_FEISHU_APP_ID=
```

| 变量 | 说明 |
| --- | --- |
| `CEO_FEISHU_ENABLED` | listener/consumer 总开关；默认关闭 |
| `CEO_FEISHU_SENDER_ENABLED` | sender 独立开关；默认关闭 |
| `CEO_FEISHU_MEDIA_ENABLED` | 批准 scope 内附件的受控下载；还要求总开关开启 |
| `CEO_FEISHU_REACTION_ENABLED` | Emoji reaction mutation；还要求 sender 开启 |
| `CEO_FEISHU_RECALL_ENABLED` | 人工审核后的 bot 消息撤回；还要求 sender 开启 |
| `CEO_FEISHU_HANDOFF_ENABLED` | 向本地受信 allowlist 发接管通知；还要求 sender 开启 |
| `CEO_FEISHU_REPLY_MENTION_SENDER` | 群聊/话题回复时结构化 @ 已验证的入站发送人；还要求 sender 开启，模型不能指定目标 |
| `CEO_FEISHU_SEND_MODE` | `confirm` 或 `auto`；无效值安全回落到 `confirm` |
| `CEO_FEISHU_SECURITY_MODE` | `strict` 或临时测试用 `audit`；无效值安全回落到 `strict` |
| `CEO_FEISHU_STALE_EVENT_SECONDS` | 超过此时间的事件只审计、不触发回复 |
| `CEO_FEISHU_CONTEXT_LIMIT` | 单会话最多注入的本地已接收消息数，默认 20，硬上限 100 |
| `CEO_FEISHU_CONTEXT_LOOKBACK_SECONDS` | 以触发事件时间为准的上下文回看窗口，默认 86400 秒；必须为正数，且不得超过事件保留期与 30 天中的较短者 |
| `CEO_FEISHU_MAX_SENDS_PER_MINUTE` | 当前 App 的本地 mutation 共享速率上限；reply 每个 wire chunk、reaction、recall 和 handoff 共用同一滑动窗口 |
| `CEO_FEISHU_EVENT_RETENTION_DAYS` | 归一化 `feishu_events` 的应用级保留窗口；不是 task/attempt/delivery/audit/WAL/备份的物理擦除期限 |
| `CEO_FEISHU_MEDIA_RETENTION_DAYS` | 已验证附件的本地保留窗口，默认 7 天；维护先安全清理媒体文件和引用，再清理事件 |
| `CEO_FEISHU_MEDIA_MAX_ASSETS` | 单事件最多资源数，默认与硬上限均为 8 |
| `CEO_FEISHU_MEDIA_MAX_BYTES` | 单资源实际字节上限，默认与硬上限均为 20 MiB |
| `CEO_FEISHU_MEDIA_EVENT_MAX_BYTES` | 单事件所有资源上限，默认与硬上限均为 32 MiB |
| `CEO_FEISHU_HANDOFF_OPEN_IDS` | 逗号分隔的受信 `open_id`；最多 20 个，模型不能提供或修改 |
| `CEO_FEISHU_APP_ID` | 企业自建应用 App ID，不是 Secret |

数值配置必须是大于零的整数，否则服务应拒绝以无效配置启动。

`CEO_FEISHU_HANDOFF_OPEN_IDS` 在进程启动时规范化。handoff action 入库时会校验一次，
sender claim 后、任何 SDK 调用前还会用当前进程配置复核一次。撤掉目标并重启后，旧的
待执行 action 会失败关闭为 `target_revoked` 并留下 `handoff_target_revoked` 审计，
不会沿用历史 allowlist 发出通知。

Handoff 有一个刻意受限的 receive-only 安全例外：`CEO_FEISHU_ENABLED=1` 时，如果模型的
完整决策已通过校验，但远端 `CEO_FEISHU_HANDOFF_ENABLED=0` 或 allowlist 为空，consumer
会把 `handoff_fallback` 与 attempt/task 结果在同一个 SQLite 事务中写入；不会在事务前
调用系统通知。独立本地 worker 使用配置中的 App ID，在 WebSocket 尚未 ready、断网或
连接失败时也能处理 `immediate` fallback。它只调用有界超时的 macOS 本地可执行程序，
强制 `url=None`，不使用 notification bridge、HTTP 或飞书 SDK；成功、重试、最终失败和
崩溃恢复均写入 outbox 与追加式审计。子进程启动前会先持久化
`mutation_started_at`；超时、非零退出、任意执行后异常或带 fence 的崩溃恢复都进入
`result_unknown`，绝不自动重放。只有能证明子进程未启动的 `OSError` 才能有界重试。
`CEO_FEISHU_ENABLED=0` 时 listener、consumer 和该本地 worker 全部不启动。远端
handoff 仍默认关闭。

配置了远端目标时，本地行先保持 `waiting_remote`。只有全部对应 action 已确定为
`failed/rejected` 才转为本地待发送；任一 `sent` 会取消 fallback，而 `ready`、`sending`、
`retry` 或 `result_unknown` 都继续等待。较新的同话题触发会取消旧 fallback，绝不把
superseded 工作重新通知。

完整对标范围、无精确等价项和逐阶段停止条件见
[飞书能力对标钉钉计划](feishu-dingtalk-parity-plan.md)。

`scripts/run-local-service.sh` 会通过 `app.config` 读取 `.env`。飞书关闭时预检
静默跳过，现有服务照常启动；只有 `CEO_FEISHU_ENABLED=1` 时才在本机检查
`lark_channel`/`lark_oapi` 是否可导入、版本是否与锁定依赖一致，以及 App
ID/App Secret 是否存在。当前 Channel SDK 基线为 `1.2.0`，核心消息归一化依赖
其 `sender_type`、`sender_is_bot` 和 `body_text` 字段。预检只
输出 `configured`/`missing` 状态，任何一项缺失都会在建立 WebSocket 或启动服务
以前失败关闭，绝不输出凭证值、凭证片段或底层异常文本。

运行时只有一条 Channel SDK WebSocket：listener 与可选 sender 共用同一个 asyncio
runtime，避免第二条连接分流事件；Codex consumer 是独立线程，且不持有 SDK client。
CLI 或审计页批准时只在 SQLite 写入 `approved_at`/`approved_by`，不创建 SDK client、
不连接网络；已有 runtime 的 sender loop 在 `confirm` 模式下只原子领取当前认证
App ID 下已批准的 delivery。实际发送前还会再次比较 delivery 的 `app_id` 与
Channel client 的认证 App ID，不匹配时保持未领取并失败关闭。

飞书 Codex consumer 使用独立的 `tool_mode=none` 硬隔离：强制
`--ignore-user-config` 和只读 sandbox，移除危险 bypass，关闭全部 Codex tools 与
web search，不加载或透传任何 MCP，并从子进程环境剥离已知的 Memory、DWS、飞书和
外部检索凭证。新建决策与 JSON 修复命令使用相同策略；若 JSONL 或 session 审计中
出现任何 tool lifecycle event，任务失败关闭，不生成 delivery。当前 Codex 配置只
能可靠限制单个 MCP 服务器内部的 `enabled_tools`，无法离线证明“仅开放
`memory_recall`、同时硬关闭 shell/web/其它系统工具”的全局语义，因此飞书通道将
Memory recall 安全降级为不可用，只依据已归一化并注入 prompt 的当前会话上下文。

### 4.1 CLI 与本机审计页

以下命令只做本地检查或数据库操作，不连接飞书：

```bash
.venv/bin/ceo-agent feishu status
.venv/bin/ceo-agent feishu setup
.venv/bin/ceo-agent feishu doctor
.venv/bin/ceo-agent feishu scopes list
.venv/bin/ceo-agent feishu scopes approve --target-type group --target-id oc_xxx --approved-by operator
.venv/bin/ceo-agent feishu scopes disable --target-type group --target-id oc_xxx --approved-by operator
.venv/bin/ceo-agent feishu produce-once
.venv/bin/ceo-agent feishu consume-once
.venv/bin/ceo-agent feishu maintenance-once
.venv/bin/ceo-agent feishu maintenance-once --app-id cli_xxx --batch-limit 500 --max-batches 20
.venv/bin/ceo-agent feishu maintenance-once --all-apps --batch-limit 500 --max-batches 20
.venv/bin/ceo-agent feishu audit-events
.venv/bin/ceo-agent feishu audit-events --all-apps --entity-type delivery --entity-id 1
.venv/bin/ceo-agent feishu deliveries list
.venv/bin/ceo-agent feishu deliveries reject --id 1 --rejected-by operator
```

`setup` 只输出最小权限 manifest；不会创建或更新远端应用。`status`、
`doctor` 和 CLI delivery/action 列表默认不显示 Secret 或草稿正文；需要审核正文时，
优先在同源 loopback 审核页中查看与 `approval_hash` 绑定的预览。CLI 仅在操作员显式传入
`--include-preview` 时输出完整预览；该输出属于本机敏感数据，不要在共享终端、录屏、
shell history 或 CI 日志中使用，也不要重定向到权限不受控的文件。

`maintenance-once` 只操作本地 SQLite，不连接飞书。默认只清理当前配置的 App，
也可显式传入 `--app-id`；只有明确使用 `--all-apps` 才跨 App 清理，两者不能同时
提供。每次最多执行 `--max-batches` 个批次，
每批最多 `--batch-limit` 条；输出中 `more_may_remain=true` 并以状态码 1 退出时，
表示仍可能有后续批次，应继续有界重跑，不要放大成无界事务。服务内定时
维护也是全 App、与网络状态无关的有界清理；发现 backlog 时会在更短的
工作间隔内再跑，否则按日运行。

`audit-events` 读取不含消息正文的追加式状态证据。默认限定当前配置的
App；只有显式传入 `--all-apps` 才跨 App 查看。可用 `--entity-type`、
`--entity-id`、`--before-id` 和有界的 `--limit` 缩小审计范围。

飞书 consumer 每次只领取一条 task，处理完成后才领取下一条；独立运行
`consume-once` 或 consumer loop 时，也会只重排超过模型超时保护窗口的飞书
`processing` task。钉钉与飞书的过期重排按 channel 隔离，不能互相夺取任务。
每次 claim 都带不可复用的 owner lease；模型决策通过安全检查后，attempt、delivery
和 task completion 在同一事务内提交，因此服务重启不会留下可触发第二次模型执行的
attempt-only 窗口。第二个服务进程启动也不会立即重排仍有活跃 lease 的飞书任务。
重启迁移遇到旧版缺失 attempt 或目标身份不一致的 delivery 时，会在本地补建
可验证 attempt 或隔离该 delivery，并写入追加式审计；不会把不可验证记录交给
sender，也不会因为单条坏记录阻断其他会话。

sender 同样逐条 claim，并在 SDK 调用前校验 delivery owner lease。单次 SDK 调用
有 60 秒硬超时；只有 `sending` 状态持续超过 5 分钟才会被恢复为
`send_unknown`。恢复会撤销旧 lease，所以旧 runtime 即使仍持有内存对象，也不能
再调用 SDK 或写回状态。`send_unknown` 后续仍必须按下文人工核验，不能自动重发。

以下命令会建立 receive-only 连接，必须单独授权后再运行：

```bash
.venv/bin/ceo-agent feishu doctor --verify-live
.venv/bin/ceo-agent feishu discover --timeout 60
.venv/bin/ceo-agent feishu receive-test --timeout 60
```

`deliveries approve` 是明确的发送批准动作；出站双开关未同时开启时会在任何
状态变化以前阻断。开关已打开时，该命令仍只写入本地、可审计的 durable approval，
不会新建第二条 WebSocket，也不会在 CLI 进程中直接发送；已有服务 runtime 随后
领取批准项。`--approved-by` 必须填写实际操作人，且 configured App ID 必须与
delivery 的 App ID 完全一致：

```bash
.venv/bin/ceo-agent feishu deliveries approve --id 1 --approved-by operator --approval-hash '<review-page-hash>'
```

`send_unknown` 永远不会被 sender 自动重放。必须先在飞书 UI、消息查询或管理员
审计中核验，然后记录确定结果。已发送时提供从 ordinal 0 开始的
严格连续、有序的飞书 message ID 前缀；`--message-id` 按实际 wire 顺序重复，
`--expected-chunks` 始终填冻结的完整本地计划数。提交的前缀必须严格等于数据库中
已有的 durable receipt 前缀再加恰好一个新核验的远端 ID，也就是只核销那一次结果
不确定的调用；跳号、一次增加多片或原样重复已有前缀都会保持 `send_unknown` 并拒绝
续发。新增这一片后达到完整计划时收敛为 `sent`：

```bash
.venv/bin/ceo-agent feishu deliveries reconcile --id 1 --outcome sent --verified-by operator --evidence-kind feishu_ui --expected-chunks 2 --message-id om_first --message-id om_second
```

新增一片后仍未达到完整计划时，同一命令才会进入 `retry`，随后只从确定尚未尝试的
suffix 续发，不重放已持久化的前缀；整个冻结计划和审批代次未变，因此不需要重新
批准。以下示例假定数据库已经保存 `om_first`，第二片的未知调用被独立核实为
`om_second`，第三片从未尝试：

```bash
.venv/bin/ceo-agent feishu deliveries reconcile --id 1 --outcome sent --verified-by operator --evidence-kind message_lookup --expected-chunks 3 --message-id om_first --message-id om_second
```

只有可以确定“当前不确定的下一片未发送”时，才先记录 `not-sent`，
再使用独立命令显式重排。已确认的回执前缀会保留，重排后仍只续发 suffix：

```bash
.venv/bin/ceo-agent feishu deliveries reconcile --id 1 --outcome not-sent --verified-by operator --evidence-kind message_lookup
.venv/bin/ceo-agent feishu deliveries requeue --id 1 --verified-by operator --evidence-kind admin_audit
```

`not-sent` 的 `reconcile` 和 `requeue` 故意分为两步：前者固化人工证据，
后者才创建可重试状态。重排会原子递增持久化的审核代次、生成新的审批哈希并清除
旧批准；重排前打开的浏览器表单和旧 CLI 哈希都会失效。`confirm` 模式下还必须
重新读取审核页中的当前预览哈希，再执行带 `--approval-hash` 的
`deliveries approve`；实际发送仍受出站双开关限制。
如果证据不足，保持 `send_unknown`；不得猜测 `not-sent`、生成新 UUID 或盲目重发。
`--evidence-kind` 只接受 `feishu_ui`、`message_lookup` 或 `admin_audit`；可选
`--request-log-id` 用于关联请求日志。命令会校验 App ID，不允许跨 App 核销或重排。

消息动作的 `result_unknown` 采用相同的“只核验、不猜测、不自动重放”原则。
核销必须同时提供非空 `verified_by` 和封闭的 `evidence_kind`
（仅 `feishu_ui`、`message_lookup`、`admin_audit`），并按动作种类固化以下最终状态：

| 动作 | 已独立证实效果发生（`applied`） | 已独立证实效果未发生（`not_applied`） |
| --- | --- | --- |
| `add_reaction` | 必须提供真实 reaction ID；动作变为 `sent` | 不得提供 remote ID；动作变为 `failed/verified_not_applied` |
| `recall_message` | 必须证实目标已不存在且不得复制目标 message ID；动作变为 `sent`，对应 receipt 从 `recall_unknown` 原子变为 `recalled` | 必须证实目标仍存在；动作变为 `failed/verified_not_applied`，对应 receipt 从 `recall_unknown` 原子恢复为 `active` |
| `handoff_notify` | 必须提供真实通知 message ID；动作变为 `sent` | 不得提供 remote ID；动作变为 `failed/verified_not_applied` |

撤回只允许作用于 `active` receipt，且其所属 delivery 必须已经是 `sent`、`failed`
或 `rejected` 终态；`ready_to_send`、`sending`、`retry` 和 `send_unknown` 都在
创建、校验和领取边界失败关闭。部分分片已确定送达但 delivery 最终失败时，可在审核页
逐片撤回；若 delivery 仍为 `retry`，必须先由操作员明确拒绝，使其进入 `rejected`
终态后再审核撤回，避免撤回与续发竞争。

动作核销与 recall receipt、追加式审计必须在一个数据库事务内提交；审计只记录
`evidence_kind` 和核验人，不复制消息正文、recipient、remote ID 或 request ID。证据不充分时保持
`result_unknown`。`not_applied` 也不能直接重发；如需重试，必须使用独立 requeue
动作原子轮换审核代次、生成新哈希并清除旧批准，再在 `confirm` 模式重新审核；
R4 撤回始终要求新批准，旧页面或旧 CLI 哈希不能复用。

CLI 同样只修改本地 durable state，不创建 SDK client。Reaction 与 handoff 使用不同
参数，避免把 reaction ID 和 message ID 混淆；撤回核销不接受 remote ID：

```bash
.venv/bin/ceo-agent feishu actions reconcile --id 10 --outcome applied --verified-by operator --evidence-kind message_lookup --reaction-id omr_xxx
.venv/bin/ceo-agent feishu actions reconcile --id 11 --outcome applied --verified-by operator --evidence-kind feishu_ui
.venv/bin/ceo-agent feishu actions reconcile --id 12 --outcome applied --verified-by operator --evidence-kind admin_audit --message-id om_notify_xxx
.venv/bin/ceo-agent feishu actions reconcile --id 10 --outcome not-applied --verified-by operator --evidence-kind feishu_ui
.venv/bin/ceo-agent feishu actions requeue --id 10 --verified-by operator --evidence-kind admin_audit
```

关闭 reaction/recall/handoff 或出站总开关后，本地 `reject`、`reconcile` 与 `requeue`
仍保持可用，便于故障处置；`approve` 和真正发送仍受对应功能开关与出站双开关约束。

服务内的本机审核入口是 `http://127.0.0.1:8765/feishu/review`。它显示连接健康、
待批准目标、触发消息、Codex 原因和 delivery 状态；History 也可按 `feishu`
筛选和查看待发项，但只提供跳转到完整飞书审核页的链接，不在 History 内直接批准或
拒绝。飞书审核与审计台其他所有写操作都要求进程随机
CSRF token、同源 `Origin`/`Referer`、loopback Host 和 loopback 客户端地址；只支持
从同一 `http://127.0.0.1:8765`、`http://localhost:8765` 或 IPv6 loopback 页面提交。缺少或不匹配
任一条件都会返回 403，不要为方便远程访问而移除这些校验或把审计服务绑定到公网。

## 5. 分阶段启用

### 5.1 离线阶段（默认）

保持：

```dotenv
CEO_NOT_SEND_MESSAGE=1
CEO_FEISHU_ENABLED=0
CEO_FEISHU_SENDER_ENABLED=0
```

只运行默认测试。`tests/feishu/` 的非 `live` 测试不得访问飞书：

```bash
.venv/bin/python -m pytest tests/feishu -q
```

### 5.2 Receive-only

只有在应用已审批、凭证已安全配置并获得明确授权后，才改为：

```dotenv
CEO_NOT_SEND_MESSAGE=1
CEO_FEISHU_ENABLED=1
CEO_FEISHU_SENDER_ENABLED=0
CEO_FEISHU_SEND_MODE=confirm
CEO_FEISHU_SECURITY_MODE=strict
```

重启后只验证连接、私聊和群内结构化 `@机器人` 入库、重复事件去重以及未知目标
保持 `pending`。此阶段不批准 delivery，不开放 sender。

### 5.3 Codex dry-run

继续保持 `CEO_NOT_SEND_MESSAGE=1` 和 sender 关闭。只检查草稿、`no_reply`、
`tool_mode=none` 安全降级和审计信息；当前不执行 Memory recall，原始飞书聊天也
不得自动写入长期记忆。

### 5.4 人工确认发送

第一次真实发送需要再次明确授权，并且仅面向已验证测试用户/测试群：

先在开放平台为当前应用完整授予 `reply_send` 权限组
（`im:message:send_as_bot` 与 `im:message:readonly`），完成管理员审核、发布和安装。
只授予 `send_as_bot` 不满足发送前原消息状态复核，sender 必须保持关闭。

```dotenv
CEO_NOT_SEND_MESSAGE=0
CEO_FEISHU_ENABLED=1
CEO_FEISHU_SENDER_ENABLED=1
CEO_FEISHU_SEND_MODE=confirm
```

先人工批准一条低风险测试 delivery，再核对飞书消息 ID 和本地审计状态。不要因
测试成功自动切换 `auto`；`auto` 是单独的风险决策。

配置由进程启动时读取。若服务由 launchd 管理，改动后需重启并核对新进程：

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
```

## 6. 正常停止和紧急停止

正常停止时把飞书总开关、sender 和所有子功能开关都设为 `0`，
然后重启服务：

```dotenv
CEO_FEISHU_SENDER_ENABLED=0
CEO_FEISHU_MEDIA_ENABLED=0
CEO_FEISHU_REACTION_ENABLED=0
CEO_FEISHU_RECALL_ENABLED=0
CEO_FEISHU_HANDOFF_ENABLED=0
CEO_FEISHU_REPLY_MENTION_SENDER=0
CEO_FEISHU_ENABLED=0
```

紧急情况下按以下顺序处理：

1. 先关闭 sender、media、reaction、recall、handoff、reply mention
   和总开关，并立即重启服务；
2. 在飞书后台停用应用或从测试群移除机器人；
3. 如果怀疑凭证泄漏，立即重置 App Secret；
4. 保留 SQLite 审计记录和 `send_unknown`，不要删除数据库或重新生成幂等键；
5. 人工核验已经发出的消息，再决定是否恢复 receive-only。

关闭功能不要求回滚新增数据库表；保留表可以避免丢失幂等和发送证据。

## 7. App Secret 轮换

1. 设置 `CEO_FEISHU_SENDER_ENABLED=0`、`CEO_FEISHU_ENABLED=0` 并重启；
2. 在飞书开放平台重置 App Secret；
3. 使用第 3 节的无回显命令覆盖 Keychain 项；
4. 只运行 `configured/missing` 检查，不打印 Secret 校验；
5. 以 receive-only 配置恢复，验证 Bot identity 和 WebSocket；
6. 完成私聊/群聊入站验证后，才根据单独授权恢复人工确认 sender。

如果 Secret 曾出现在终端、日志、SQLite、审计页或 Git 历史中，必须按泄漏处理；
清理副本不能替代先轮换凭证。

## 8. 故障处理

| 现象 | 安全处理 |
| --- | --- |
| `missing_config` / 无法读取 Secret | 保持两开关关闭；检查 App ID、Keychain service/account 和服务运行用户，不输出 Secret |
| 启动预检显示 `sdk=missing` | 保持通道关闭；在同一虚拟环境安装 `.[feishu]`，不要绕过预检 |
| WebSocket 无法连接或反复重连 | 保持 sender 关闭；检查网络、TLS/代理、应用发布状态；不要降级到明文 `ws://` |
| 收不到私聊 | 检查应用可用范围、机器人能力、私聊权限和应用版本是否已发布 |
| 收不到群消息 | 检查机器人已入测试群、群权限和是否结构化 `@机器人`；不要改成读取群内所有消息 |
| `permission_denied` | 停止重试并关闭 sender；对照最小权限和已发布版本，不静默扩权 |
| 重复事件/回复 | 确认仅有一个 listener、保留 event/message 唯一键和原 delivery UUID；不要删库“重试” |
| `rate_limited` | 关闭 sender 或降低速率，指数退避且保持同一 UUID |
| `send_unknown` | 保持冻结且人工核验；完整 verified prefix 收敛 `sent`，不完整前缀只续发 suffix，已证实不确定的下一片 `not-sent` 才可显式 `requeue`，绝不盲目重放 |
| action `result_unknown` | 保持冻结并按第 4 节的 kind-specific 契约核验；reaction/handoff 的 applied 结果必须有真实 remote ID，recall receipt 必须原子收敛，绝不直接重放 |
| local notification `result_unknown` | 本地 OS effect 已越过 mutation fence 但无确定回执；保留事件/outbox/审计并人工核验，绝不自动重放或清空状态 |
| `handoff_target_revoked` | 本地 allowlist 已在入库后收紧；这是预期的失败关闭，复核目标和审计，不要为了清队列重新加回授权 |
| `maintenance-once` 返回 `more_may_remain=true` | 本次已达有界批次上限；保持原参数继续重跑，或等待服务在短工作间隔再次清理，不要改成单个长事务 |
| CLI 批准后仍为 `ready_to_send` | 这是 durable approval 的预期状态；确认单一 runtime 已就绪、sender 双开关仍开启、认证 App ID 一致，不要另开连接发送 |
| 审核 POST 返回 403 | 必须从同一 loopback 审计页提交并保留 CSRF token；检查 Host/Origin，不要关闭防护 |
| 原消息撤回/目标失效 | 失败关闭；不要自动降级成群内自由消息 |
| 收到未批准目标消息 | 保持 scope 为 `pending/disabled`，不生成 Codex 任务，不保存正文 |
| 出现意外出站消息 | 立即执行紧急停止、保留证据、轮换凭证并检查审批与发送门禁 |

不要通过清空队列、删除幂等记录或提升权限来“修复”未知发送结果。

## 9. 数据、日志和 Memory

### 9.1 数据最小化与上下文隔离

- 不保存 tenant/app access token 或完整原始事件；
- 只保存路由和审计必需的归一化字段；
- 未批准目标不保存消息正文；
- 只有审批 scope 且 `CEO_FEISHU_MEDIA_ENABLED=1` 的图片、文件和音视频
  才会受控下载；默认不下载，sticker 不下载，卡片不在当前入站白名单；
- 媒体资源数、单文件/单事件字节数、下载时间和 MIME 魔数都有硬上限；
  验证后以 App 隔离、内容寻址和 `0600` 权限存储，无法验证时失败关闭；
- 日志不记录 App Secret、token、签名 URL 或附件下载地址；
- 每个会话上下文默认最多 20 条、最多回看 86400 秒。消费者只读取与触发事件完全相同的
  App、chat 和 thread；主会话与话题线程不混用。上下文还以触发事件为 as-of 边界，
  同时校验本地插入顺序和飞书事件时间，只包含
  `trigger_time - lookback <= event_time <= trigger_time` 的消息，不注入触发后的消息、
  超出回看窗口的旧消息或迟到事件；
- reply task 的会话标识包含 App 命名空间，入站、task 和 delivery 也都校验
  App ID；即使不同 App 出现相同 chat/message ID，也不得共用上下文、任务或出站状态；
- 飞书决策运行时不加载 Memory MCP（recall/write 均不可用），也不加载 DWS、外部
  MCP、web、shell 或系统工具；这是无法硬证明 recall-only 全局工具白名单时的安全
  降级，不能通过 prompt 或用户配置绕过。

### 9.2 保留和清理边界

`CEO_FEISHU_EVENT_RETENTION_DAYS` 默认为 30。它只对实时 SQLite 中的归一化
`feishu_events` 行执行应用语义上的逻辑清理（行级 `DELETE`）。清理不级联删除
reply task、reply attempt、delivery 或追加式审计证据，也不更改它们的独立
保留责任。实现不执行 `VACUUM`、SQLite `secure_delete`、WAL 安全擦除或备份删除；
因此 SQLite 空闲页、WAL/journal、文件系统快照和数据库备份中仍可能存在历史字节。
这不是物理擦除或法定删除保证；WAL、快照和备份必须由独立、经审批的生命周期
与安全销毁流程管理。

为了不破坏处理和发送证据，以下归一化事件会被保留：

- reply task 仍为 `pending` 或 `processing`；
- delivery 仍为 `ready_to_send`、`sending`、`retry` 或 `send_unknown`；
- delivery 为 `failed/verified_not_sent`，仍可经过核验 requeue；
- message action 仍为 `ready`、`sending`、`retry` 或 `result_unknown`；
- message action 为 `failed/verified_not_applied`，仍可经过核验 requeue；
- local notification 仍为 `waiting_remote`、`pending`、`sending`、`retry` 或
  `result_unknown`。

因此 `send_unknown` / `result_unknown`、trigger、attempt、delivery、action、local
notification、UUID 和飞书 message ID 等未解决证据不会被事件保留任务自动清理。
服务内定时任务每次只做有界批处理并覆盖所有 App；手动命令默认只操作当前配置的
App，显式 `--app-id` 可限定其它单个 App，只有 `--all-apps` 才跨 App。

### 9.3 追加式审计证据

`feishu_audit_events` 只追加状态证据，不保存原始消息正文、凭证或附件地址。
数据库触发器禁止对已写入的审计行执行 `UPDATE` 或 `DELETE`。范围审批、决策尝试、
delivery 创建/批准/领取/转移、`send_unknown` 核销/重排以及保留清理计数都应产生
新的审计事件，而不是改写旧证据。用 4.1 节的 `audit-events` 命令做有界查询。

### 9.4 旧库事件时间回填

为支持 as-of 上下文，升级会为旧库添加 `event_create_time_ms` 并回填旧的合格
事件。回填查询以 500 条为一批，但第一次打开数据库时会同步执行到当前 backlog
完成，然后才创建相关索引。大型旧库可能因此延长首次升级/启动时间，并产生额外的
WAL 写入与磁盘占用。

升级前应预留维护窗口和足够可用空间，按独立策略创建已授权备份，并在恢复服务前
确认首次初始化完成。若进程中断，下次初始化会继续回填未处理行，但不应通过反复中断
来缩短单次启动。升级备份中的旧数据不会被之后的 `feishu_events` 保留任务物理擦除。

### 9.5 旧库出站状态迁移

本版本把冻结的有序分块计划和审核代次纳入审批身份。首次打开旧库时，程序会为
delivery 重算 `expected_chunks`、`chunk_plan_sha256` 和 `approval_hash`，并为 delivery
与 action 回填 `review_generation=1`；只要旧审批身份与新身份不同，旧的
`approved_at`/`approved_by` 就会被清除并写入 `approval_snapshot_migrated` 审计证据。
这是预期的失败关闭；必须重新查看当前预览并用新哈希批准。之后每次经过人工核验的
requeue 只允许把审核代次原子增加 1，并同步生成新哈希，旧浏览器表单和旧 CLI 输出
不能再次批准。

如果旧行已有 receipt、主 message ID、远端 mutation fence，或处在可能继续发送的
状态，而原持久计划摘要缺失或与当前确定性切分不一致，程序不能证明旧前缀与新 suffix
边界一致。该行会先收敛为 `send_unknown` / `legacy_chunk_plan_unverifiable`，清除 lease
和批准，并追加 `legacy_chunk_plan_quarantined`；这种隔离只允许完整终态核验或撤回/清理，
禁止部分前缀进入 `retry`，也禁止 `not-sent` 后 requeue。旧 `sent` 行在后续回执迁移中
变成不完整前缀时同样受此隔离，不得因迁移顺序绕过。

兼容路径中的旧版 `sent` delivery 如果只有一个主 message ID，而冻结分块计划需要更多
回执，程序仍会把该行收敛为 `send_unknown` 并写入
`legacy_chunk_receipts_incomplete` 审计事件；计划本身不可验证时还会同时使用上述
`legacy_chunk_plan_unverifiable` 隔离。不得为了保留 `sent` 外观而修改迁移、补造回执
或重算 UUID；应按 4.1 节人工核验。

## 10. 回滚验收

回滚完成必须同时满足：

- `CEO_FEISHU_ENABLED=0`；
- `CEO_FEISHU_SENDER_ENABLED=0`；
- 服务已重启到新进程；
- 没有飞书 listener/sender 继续运行；
- `ready_to_send` 不会被自动领取；
- `sending`/`send_unknown` 已逐条保留并标记待核验；
- 必要时应用已停用、机器人已移出测试群、Secret 已轮换；
- 钉钉、微信及现有审计能力不受飞书关闭影响。

在以上条件未满足前，不应宣告回滚完成或重新开放真实发送。
