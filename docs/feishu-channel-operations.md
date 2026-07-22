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
CEO_FEISHU_SEND_MODE=confirm
CEO_FEISHU_SECURITY_MODE=strict
```

安装 optional dependencies、运行默认测试、填写 App ID 或把 App Secret 写入
Keychain，都不会单独建立飞书连接或发送消息。只有明确把
`CEO_FEISHU_ENABLED=1` 并重启服务后，listener 才可以连接飞书。

真实发送还需要同时满足：

1. `CEO_NOT_SEND_MESSAGE=0`；
2. `CEO_FEISHU_SENDER_ENABLED=1`；
3. 默认 `confirm` 模式下，delivery 已通过本机人工批准。

`auto` 会明确放弃逐条人工批准，只保留前两道开关、allowlist、速率限制和幂等
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

企业自建应用首版只申请租户身份下的以下能力：

| 权限或事件 | 用途 |
| --- | --- |
| `im:message.p2p_msg:readonly` | 接收用户发给机器人的私聊消息 |
| `im:message.group_at_msg:readonly` | 接收指定群内结构化 `@机器人` 消息 |
| `im:message:send_as_bot` | 以机器人身份回复或发送消息 |
| `im.message.receive_v1` | 消息接收事件 |

不要申请完整通讯录、完整聊天历史、云盘、邮件或用户 access token 权限。不要
勾选“接收群内所有消息”；`@所有人` 或正文中出现机器人名字不等于结构化
`@机器人`。如果租户后台要求新增组合权限，先记录和审核差异，不要静默扩权。

应用管理员准备流程：

1. 在飞书开放平台创建企业自建应用并启用机器人能力；
2. 添加上表权限和 `im.message.receive_v1` 事件；
3. 选择长连接接收事件，不配置公网 webhook；
4. 将应用可用范围限制为测试用户，发布版本并完成管理员审批/安装；
5. 只把机器人加入专用测试群；
6. 第一阶段保持本机 `CEO_FEISHU_ENABLED=0`，直到明确进入 receive-only 验证。

官方资料：

- [接收消息事件](https://open.feishu.cn/document/server-docs/im-v1/message/events/receive)
- [发送消息](https://open.feishu.cn/document/server-docs/im-v1/message/create)
- [回复消息](https://open.feishu.cn/document/server-docs/im-v1/message/reply)
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
CEO_FEISHU_SEND_MODE=confirm
CEO_FEISHU_SECURITY_MODE=strict
CEO_FEISHU_STALE_EVENT_SECONDS=300
CEO_FEISHU_CONTEXT_LIMIT=20
CEO_FEISHU_MAX_SENDS_PER_MINUTE=10
CEO_FEISHU_EVENT_RETENTION_DAYS=30
CEO_FEISHU_APP_ID=
```

| 变量 | 说明 |
| --- | --- |
| `CEO_FEISHU_ENABLED` | listener/consumer 总开关；默认关闭 |
| `CEO_FEISHU_SENDER_ENABLED` | sender 独立开关；默认关闭 |
| `CEO_FEISHU_SEND_MODE` | `confirm` 或 `auto`；无效值安全回落到 `confirm` |
| `CEO_FEISHU_SECURITY_MODE` | `strict` 或临时测试用 `audit`；无效值安全回落到 `strict` |
| `CEO_FEISHU_STALE_EVENT_SECONDS` | 超过此时间的事件只审计、不触发回复 |
| `CEO_FEISHU_CONTEXT_LIMIT` | 单会话最多注入的本地已接收消息数 |
| `CEO_FEISHU_MAX_SENDS_PER_MINUTE` | 本地发送速率上限 |
| `CEO_FEISHU_EVENT_RETENTION_DAYS` | 归一化 `feishu_events` 的应用级保留窗口；不是 task/attempt/delivery/audit/WAL/备份的物理擦除期限 |
| `CEO_FEISHU_APP_ID` | 企业自建应用 App ID，不是 Secret |

数值配置必须是大于零的整数，否则服务应拒绝以无效配置启动。

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
.venv/bin/ceo-agent feishu audit-events
.venv/bin/ceo-agent feishu audit-events --all-apps --entity-type delivery --entity-id 1
.venv/bin/ceo-agent feishu deliveries list
.venv/bin/ceo-agent feishu deliveries reject --id 1 --rejected-by operator
```

`setup` 第一阶段只输出最小权限 manifest；不会创建或更新远端应用。`status`、
`doctor` 和默认 delivery 列表不显示 Secret，草稿正文只有加 `--include-text` 才会
从本机 CLI 输出。

`maintenance-once` 只操作本地 SQLite，不连接飞书。不传 `--app-id` 时清理
所有 App；显式传入时只清理该 App。每次最多执行 `--max-batches` 个批次，
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
.venv/bin/ceo-agent feishu deliveries approve --id 1 --approved-by operator
```

`send_unknown` 永远不会被 sender 自动重放。必须先在飞书 UI、消息查询或管理员
审计中核验，然后记录确定结果。已发送时必须提供真实的飞书 message ID：

```bash
.venv/bin/ceo-agent feishu deliveries reconcile --id 1 --outcome sent --verified-by operator --evidence-kind feishu_ui --feishu-message-id om_xxx
```

只有可以确定“未发送”时，才先记录 `not-sent`，再使用独立命令显式重排：

```bash
.venv/bin/ceo-agent feishu deliveries reconcile --id 1 --outcome not-sent --verified-by operator --evidence-kind message_lookup
.venv/bin/ceo-agent feishu deliveries requeue --id 1 --verified-by operator --evidence-kind admin_audit
```

`reconcile` 和 `requeue` 故意分为两步：前者固化人工证据，后者才创建可重试
状态。重排会清除旧批准，`confirm` 模式下还必须执行一次新的
`deliveries approve --id 1 --approved-by operator`，且实际发送仍受出站双开关限制。
如果证据不足，保持 `send_unknown`；不得猜测 `not-sent`、生成新 UUID 或盲目重发。
`--evidence-kind` 只接受 `feishu_ui`、`message_lookup` 或 `admin_audit`；可选
`--request-log-id` 用于关联请求日志。命令会校验 App ID，不允许跨 App 核销或重排。

服务内的本机审核入口是 `http://127.0.0.1:8765/feishu/review`。它显示连接健康、
待批准目标、触发消息、Codex 原因和 delivery 状态；History 也可按 `feishu`
筛选并对待发项逐条批准或拒绝。所有会改变状态的飞书审核 POST 都要求进程随机
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

正常停止时把两个飞书开关都设为 `0`，然后重启服务：

```dotenv
CEO_FEISHU_SENDER_ENABLED=0
CEO_FEISHU_ENABLED=0
```

紧急情况下按以下顺序处理：

1. 先关闭 sender 和总开关并立即重启服务；
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
| `send_unknown` | 保持冻结且人工核验；按 4.1 节执行 `reconcile(sent/not-sent)`，只有已证实 `not-sent` 才可再显式 `requeue`，绝不自动重放 |
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
- 图片、文件、音视频和卡片首版不下载；
- 日志不记录 App Secret、token、签名 URL 或附件下载地址；
- 每个会话上下文默认最多 20 条。消费者只读取与触发事件完全相同的
  App、chat 和 thread；主会话与话题线程不混用。上下文还以触发事件为 as-of 边界，
  同时校验本地插入顺序和飞书事件时间，不注入触发后的消息或迟到事件；
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
- delivery 仍为 `ready_to_send`、`sending`、`retry` 或 `send_unknown`。

因此 `send_unknown`、trigger、attempt、delivery、UUID 和飞书 message ID 等未解决证据不会
被事件保留任务自动清理。定时任务每次只做有界批处理，且默认覆盖所有 App；
手动命令既可保持全 App 范围，也可通过 `--app-id` 限定单个 App。

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
