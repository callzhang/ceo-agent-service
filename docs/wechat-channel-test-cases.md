# WeChat Channel Test Cases

本文用于验证 CEO Agent Service 的个人微信通道是否连接成功、功能是否正确，并说明测试覆盖的代码实现边界。

## 实现功能分析

微信通道不是微信官方 API，也不是网络私有协议。当前实现分成两个本地专用辅助应用：

- `CEO WeChat Reader.app`：通过 owner-only Unix socket 提供微信账号发现、能力探测、联系人/群列表、消息读取。主服务只连 socket，不直接读微信数据库、passphrase 或明文镜像。
- `CEO WeChat Sender.app`：通过 owner-only Unix socket 封装 macOS Accessibility，控制官方 WeChat UI 完成目标打开、文本写入和发送。

主要代码路径：

- Reader IPC：`app/wechat/reader_ipc.py`
- Reader 能力与规范化：`app/wechat/reader.py`
- WCDB/SQLCipher 解密和消息读取：`app/wechat/backend.py`
- 微信账号和数据目录发现：`app/wechat/discovery.py`
- 触发入队：`app/wechat/producer.py`
- Codex 决策和 `ready_to_send` delivery 创建：`app/wechat/consumer.py`
- 发送绑定、发送状态和失败保护：`app/wechat/accessibility.py`
- confirm/auto 模式、approve/reject/recall：`app/wechat/service.py`
- CLI：`app/wechat/cli.py`，通过 `ceo-agent wechat ...` 调用

核心状态机：

```text
Reader ready
  -> 选定 direct/group reply scope
  -> Producer 读取新消息并按规则入队 reply_tasks(channel=wechat)
  -> Consumer 调 Codex 决策
  -> send_reply/ask_clarifying_question 创建 wechat_deliveries(status=ready_to_send)
  -> confirm 模式人工 approve 后 Sender 发送
  -> sent / send_unknown / failed
```

触发规则：

- 选中的单聊：每条 inbound text 可以进入决策。
- 选中的群聊：只有结构化 `@当前账号` 的 inbound text 可以进入决策。
- outbound、图片、文件、系统消息、未选中会话、禁用 scope 均不能触发自动回复。
- Consumer 永远不直接发送，只创建 `ready_to_send`。
- Sender 只有在目标绑定 `verified` 时才发送；未验证或冲突目标必须 fail-closed。
- `CEO_WECHAT_SEND_MODE=confirm` 时，待发送消息必须人工 approve；不会自动发。

## 测试环境准备

建议先保持安全配置：

```bash
cd /Users/macbook/Desktop/shuzifenshen/ceo-agent-service

grep -E 'CEO_WECHAT|CEO_NOT_SEND_MESSAGE|CEO_DRY_RUN' .env
```

期望初始值：

```text
CEO_WECHAT_READER_ENABLED=0
CEO_WECHAT_SENDER_ENABLED=0
CEO_WECHAT_SEND_MODE=confirm
CEO_NOT_SEND_MESSAGE=1
```

运行自动化回归：

```bash
.venv/bin/python -m pytest tests/wechat -q
```

这些单元测试不触碰真实微信，用 fake reader/sender 验证核心规则。真实连接测试从下面的手工集成用例开始。

## 停止条件

出现以下任一情况，停止测试，不进入发送阶段：

- `ceo-agent wechat status` 不是 `ready`。
- Reader 或 Sender socket 不存在或权限不是 `0600`。
- `read-recent --target-id filehelper` 读不到文件传输助手消息，或方向 inbound/outbound 明显错误。
- 目标绑定不是 `verified`。
- `CEO_WECHAT_SEND_MODE` 不是 `confirm`，但还没有完成文件传输助手发送回读验证。
- 任意测试需要展示真实聊天内容给无关人员。

## 测试用例

### WX-CONN-001 Reader 未安装时失败保护

目的：确认未安装 Reader 时，主服务不会尝试直接读取微信数据库。

前置条件：不要启动 `CEO WeChat Reader.app`。

步骤：

```bash
.venv/bin/ceo-agent wechat status
```

预期：

- 命令失败。
- 错误包含 `WeChat reader unavailable`。
- 不应出现微信数据库路径、passphrase、traceback 泄漏到审计页面或日志。

覆盖代码：`service.build_reader()`、`reader_ipc.WechatReaderClient._request()`。

### WX-CONN-002 构建并安装 Reader

目的：确认专用 Reader app 能构建、签名、安装并启动 LaunchAgent。

步骤：

```bash
/Users/macbook/anaconda3/bin/python3.11 -m venv .venv-reader-build
.venv-reader-build/bin/pip install -e '.[reader-build]'
./scripts/create-wechat-reader-signing-identity.sh
CEO_WECHAT_READER_SIGNING_IDENTITY='CEO WeChat Reader Local Signing' \
  ./scripts/build-wechat-reader-app.sh
./scripts/install-wechat-reader-app.sh
```

预期：

- 构建输出 `built ... CEO WeChat Reader.app`。
- 安装输出 `installed ~/Applications/CEO WeChat Reader.app` 和 `started gui/.../com.stardust.ceo-agent.wechat-reader`。
- `codesign --verify --deep --strict` 通过。

失败排查：

- PyInstaller 缺失：检查 `.venv-reader-build/bin/python -m PyInstaller --version`。
- 安装失败：检查 `~/Library/LaunchAgents/com.stardust.ceo-agent.wechat-reader.plist` 和 `~/Library/Logs/ceo-agent-service`。

覆盖代码：`scripts/build-wechat-reader-app.sh`、`scripts/install-wechat-reader-app.sh`、`app/wechat/reader_helper.py`。

### WX-CONN-003 Reader socket 健康检查和权限

目的：确认 Reader 只开放 owner-only socket。

步骤：

```bash
ls -l "$HOME/Library/Application Support/CEO Agent/WeChatReader/reader.sock"
.venv/bin/ceo-agent wechat status
```

预期：

- socket 存在，权限为 `srw-------` 或模式 `0600`。
- 如果权限尚未授权，`status` 可以 blocked，但不能是 reader unavailable。
- 错误信息不得包含 passphrase 或真实数据库绝对路径。

覆盖代码：`WechatReaderUnixServer` socket 权限、IPC 错误脱敏。

### WX-CONN-004 授权 Reader 访问微信数据

目的：确认 macOS 隐私权限满足本地数据库读取。

步骤：

1. 打开系统设置：
   - Privacy & Security
   - Full Disk Access 或 App Data 相关项
2. 添加并启用 `~/Applications/CEO WeChat Reader.app`。
3. 重启 Reader：

```bash
launchctl kickstart -k gui/$(id -u)/com.stardust.ceo-agent.wechat-reader
.venv/bin/ceo-agent wechat status
```

预期：

- 不再报 `Grant App Data permission to CEO WeChat Reader`。
- 若 passphrase 还未准备好，应进入 key/passphrase blocked，而不是权限 blocked。

覆盖代码：`reader_ipc` 对 `PermissionError` 的映射。

### WX-CONN-005 passphrase 缺失时 blocked

目的：确认没有密钥时 Reader 不会猜测读取。

前置条件：`~/.config/wx_read/passphrase.hex` 不存在或为空。

步骤：

```bash
.venv/bin/ceo-agent wechat status
```

预期：

- 账号能力为 `blocked`。
- 原因类似 `passphrase file missing` 或 `passphrase file empty`。
- 不创建 reply task。

覆盖代码：`key_provider.PassphraseFileKeyProvider`、`reader.WechatReader.probe()`。

### WX-CONN-006 passphrase 校验

目的：确认持久化 passphrase 能解密真实微信库。

前置条件：

- 已完成一次性 passphrase 捕获，并保存到 `~/.config/wx_read/passphrase.hex`。
- 文件权限为 `chmod 600`。
- 已知当前微信账号 db_storage 路径。

步骤：

```bash
chmod 600 ~/.config/wx_read/passphrase.hex
.venv/bin/python scripts/wechat_key_probe.py \
  --passphrase-file ~/.config/wx_read/passphrase.hex \
  --account-db-dir "$HOME/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<account>/db_storage"
```

预期：

- `valid=true` 或等价成功字段。
- 输出只包含指纹、schema 概况，不输出 passphrase 明文。
- 若失败，不能继续后续连接测试。

覆盖代码：`scripts/wechat_key_probe.py`、`cipher.validates()`、`backend.probe()`。

### WX-CONN-007 账号 ready 和 self wxid 持久化

目的：确认 Reader 能发现唯一 ready 账号，并检测当前账号自身 wxid。

步骤：

```bash
.venv/bin/ceo-agent wechat status
```

预期：

- 输出 `<account_id>: ready`。
- 输出含 `self=<wxid>`。
- SQLite 中 `wechat_read_state` 有一条 `capability_status=ready` 且 `self_user_id` 非空。

可选检查：

```bash
sqlite3 data/auto-reply.sqlite3 \
  "select account_id, capability_status, self_user_id from wechat_read_state;"
```

覆盖代码：`WechatReader.detect_self_username()`、`service.ready_account_state()`。

### WX-CONN-008 多账号保护

目的：确认多微信账号时不会静默选错账号。

前置条件：本机有多个 `xwechat_files/<account>/db_storage`。

步骤：

```bash
.venv/bin/ceo-agent wechat status
```

预期：

- 每个账号分别输出状态。
- `ready_account_state` 只有在 exactly one ready 且 self_user_id 非空时返回账号。
- 若多个 ready，需要在 UI/配置中明确选择，不应自动启动 producer/consumer。

覆盖代码：`service.capability_ready_account_state()`、`WechatSetupService.connect()`。

### WX-READ-001 文件传输助手读取：默认脱敏

目的：验证能读消息元数据，同时默认不打印正文。

步骤：

```bash
.venv/bin/ceo-agent wechat read-recent \
  --target-id filehelper \
  --limit 20
```

预期：

- 输出 `N messages in filehelper (direct)`。
- 每行包含时间、direction、kind、len。
- 不显示真实消息正文。

覆盖代码：`wechat.cli.cmd_read_recent()` 默认 redacted 输出。

### WX-READ-002 文件传输助手读取：显式正文验证

目的：人工核对读取顺序、方向和正文。

步骤：

1. 在微信文件传输助手中手动发送一条测试消息，例如：

```text
CEO Agent 微信读取测试 2026-07-22 15:30
```

2. 执行：

```bash
.venv/bin/ceo-agent wechat read-recent \
  --target-id filehelper \
  --limit 20 \
  --include-text
```

预期：

- 能看到刚发送的测试文本。
- 方向应为 `outbound`。
- 时间接近当前时间，时区正确。
- `--include-text` 只用于文件传输助手或明确测试联系人，不用于真实敏感会话。

覆盖代码：`backend.detect_self_username()`、`reader._normalize()` direction 修正。

### WX-READ-003 目标列表搜索

目的：确认 Config 页面/Reader 能列出 direct 和 group。

步骤：

打开审计页面：

```text
http://127.0.0.1:8765/config
```

在 WeChat 选择器里搜索：

- 一个测试联系人昵称
- 一个测试群名

预期：

- direct 结果显示 `target_type=direct`。
- group 结果显示 `target_type=group`。
- 不展示消息预览。
- 重名联系人应作为独立行，不能只按 display name 保存。

覆盖代码：`audit_web` 的 `/config/wechat/conversations`、`reader.list_targets()`、`backend.list_targets()`。

### WX-SCOPE-001 保存单聊 scope

目的：确认单聊保存为 every inbound text。

步骤：

1. 在 Config -> WeChat 中勾选一个测试联系人。
2. 保存。
3. 查询：

```bash
sqlite3 data/auto-reply.sqlite3 \
  "select target_type,target_id,display_name,trigger_mode,enabled,last_active_at from wechat_reply_scopes;"
```

预期：

- `target_type=direct`。
- `trigger_mode=every_inbound_text`。
- `enabled=1`。
- `last_active_at` 已设置为保存时刻附近，避免回放历史消息。

覆盖代码：`WechatReplyScope.validate_trigger()`、store scope 持久化。

### WX-SCOPE-002 保存群聊 scope

目的：确认群聊保存为 mention-only。

步骤：

1. 在 Config -> WeChat 中勾选一个测试群。
2. 保存并查询 `wechat_reply_scopes`。

预期：

- `target_type=group`。
- `trigger_mode=mention_current_account`。
- 普通群消息不触发任务。

覆盖代码：`models.WechatReplyScope`、`producer.is_reply_candidate()`。

### WX-PROD-001 首次启用不回放历史

目的：确认选择 scope 后不会把历史消息全部入队。

前置条件：已保存一个测试联系人 scope，且该联系人历史中有旧消息。

步骤：

```bash
.venv/bin/ceo-agent wechat produce-once
sqlite3 data/auto-reply.sqlite3 \
  "select channel,conversation_title,trigger_message_id,trigger_text from reply_tasks where channel='wechat' order by id desc limit 10;"
```

预期：

- 首次启用后没有旧消息任务。
- scope 的 `last_active_at` 被推进到启用时刻。

覆盖代码：`WechatReplyProducer.run_once()` 对空 `last_active_at` 的 watermark 初始化。

### WX-PROD-002 单聊新 inbound text 入队

目的：确认选中的单聊新消息能进入 `reply_tasks(channel=wechat)`。

步骤：

1. 让测试联系人向当前微信发送一条新文本：

```text
测试 CEO Agent 单聊入队，请问下午能给结论吗？
```

2. 执行：

```bash
.venv/bin/ceo-agent wechat produce-once
```

3. 查询：

```bash
sqlite3 data/auto-reply.sqlite3 \
  "select id,status,channel,conversation_title,trigger_sender,trigger_text from reply_tasks where channel='wechat' order by id desc limit 5;"
```

预期：

- 新增 1 条 `channel=wechat` 的 pending/queued task。
- `single_chat=1`。
- `trigger_text` 是测试消息。
- 重复执行 `produce-once` 不新增重复任务。

覆盖代码：`producer.is_reply_candidate()`、`store.enqueue_reply_task()` 幂等。

### WX-PROD-003 outbound 和非文本不入队

目的：确认自己发出的消息、图片、文件、系统消息不会触发自动回复。

步骤：

1. 自己给测试联系人发一条文本。
2. 让测试联系人发图片或文件。
3. 执行：

```bash
.venv/bin/ceo-agent wechat produce-once
```

预期：

- 不为 outbound 创建 task。
- 不为 image/file/system 创建 task。
- scope watermark 可以推进，但没有无效任务。

覆盖代码：`is_reply_candidate()` 的 `direction` 和 `kind` 过滤。

### WX-PROD-004 群聊普通消息不入队

目的：确认群聊必须结构化 @ 当前账号，文本里写名字不算。

步骤：

1. 在已选测试群中发普通消息：

```text
Derek 看一下这个问题
```

2. 执行 `produce-once`。

预期：

- 不新增 WeChat reply task。

覆盖代码：`WechatMessage.mentions_user()`、`is_reply_candidate()`。

### WX-PROD-005 群聊结构化 @ 入队

目的：确认群聊 `@当前账号` 能触发。

步骤：

1. 在已选测试群中真正 @ 当前微信账号并发送：

```text
@<当前微信> 测试 CEO Agent 群聊入队
```

2. 执行：

```bash
.venv/bin/ceo-agent wechat produce-once
```

预期：

- 新增 1 条 `channel=wechat` 且 `single_chat=0` 的 task。
- `trigger_message_json` 中 `mentioned_user_ids` 包含当前 self wxid。
- 重复执行不重复入队。

覆盖代码：`schema.parse_mentions()`、`WechatMessage.mentions_user()`。

### WX-CONS-001 Consumer 创建 ready_to_send

目的：确认 Consumer 不发送，只生成待发送 delivery。

前置条件：已有 `channel=wechat` pending task。

步骤：

```bash
.venv/bin/ceo-agent wechat consume-once
sqlite3 data/auto-reply.sqlite3 \
  "select id,task_id,target_type,target_id,status,reply_text from wechat_deliveries order by id desc limit 5;"
```

预期：

- 新增 delivery。
- `status=ready_to_send`。
- `reply_text` 非空且没有本地路径、token、session id。
- History 页面出现 WeChat 行。
- 没有真正发出微信消息。

覆盖代码：`WechatReplyConsumer.process()`。

### WX-CONS-002 no_reply 不创建 delivery

目的：确认 Codex 判断无需回复时任务完成但不发送。

步骤：

1. 构造一条低价值测试消息，例如“收到”。
2. `produce-once` 后执行 `consume-once`。
3. 查询 `wechat_deliveries`。

预期：

- task 完成。
- 不创建 delivery。
- History 中 action 为 `no_reply` 或等价记录。

覆盖代码：`consumer` 对 `CodexAction.NO_REPLY` 的处理。

### WX-CONS-003 DingTalk-only system actions 被拒绝

目的：确认微信 Consumer 不执行钉钉专属动作。

建议作为自动化测试执行：

```bash
.venv/bin/python -m pytest tests/wechat/test_consumer.py::test_dingtalk_system_actions_rejected -q
```

预期：

- 不创建 WeChat delivery。
- task 被 fail。

覆盖代码：`WechatReplyConsumer.process()` 中 `system_actions` 拒绝逻辑。

### WX-SEND-001 confirm 模式保持待发送

目的：确认开启 Sender 也不会在 confirm 模式下自动发送。

前置条件：

```text
CEO_WECHAT_SEND_MODE=confirm
CEO_WECHAT_SENDER_ENABLED=1 或 0 均可
```

步骤：

```bash
.venv/bin/ceo-agent wechat pending
```

预期：

- 显示 pending delivery。
- 微信未发出任何消息。

自动化覆盖：

```bash
.venv/bin/python -m pytest tests/wechat/test_send_mode.py::test_confirm_mode_holds_deliveries -q
```

覆盖代码：`service.process_ready_wechat_deliveries()`。

### WX-SEND-002 安装 Sender 并授权 Accessibility

目的：确认发送辅助应用可用。

步骤：

```bash
CEO_WECHAT_SENDER_SIGNING_IDENTITY='CEO WeChat Reader Local Signing' \
  ./scripts/build-wechat-sender-app.sh
./scripts/install-wechat-sender-app.sh
```

然后在 macOS 设置中给 `~/Applications/CEO WeChat Sender.app` 开启 Accessibility。

验证：

```bash
launchctl kickstart -k gui/$(id -u)/com.stardust.ceo-agent.wechat-sender
```

在 Tutorial 或 Config 中执行 WeChat check，或通过页面触发 permission check。

预期：

- Sender preflight 为 `ready`。
- 如果 WeChat 未运行，应为 `wechat_not_running`。
- 如果未授权，应为 `accessibility_not_trusted`。

覆盖代码：`sender_ipc.py`、`MacWechatAccessibility.preflight()`。

### WX-SEND-003 目标绑定验证

目的：确认发消息前必须证明目标 UI 与保存的目标一致。

步骤：

1. 对文件传输助手或测试联系人执行绑定验证。
2. 检查 scope：

```bash
sqlite3 data/auto-reply.sqlite3 \
  "select target_type,target_id,display_name,binding_status,binding_evidence_json from wechat_reply_scopes;"
```

预期：

- 唯一且 UI 标题匹配时 `binding_status=verified`。
- 群名重名或无法唯一证明时 `conflict` 或 `unverified`。
- direct 重名联系人应使用稳定 target id 作为 `navigation_query`。

覆盖代码：`service.verify_wechat_binding()`。

### WX-SEND-004 未验证绑定禁止发送

目的：确认 fail-closed。

步骤：

1. 将测试 scope 的 `binding_status` 保持为 `unverified`。
2. 对 pending delivery 执行 approve：

```bash
.venv/bin/ceo-agent wechat approve --id <delivery_id>
```

预期：

- 不调用真实发送。
- delivery 状态变 `failed`。
- error 为 `target_binding_unverified`。

自动化覆盖：

```bash
.venv/bin/python -m pytest tests/wechat/test_accessibility.py::test_unverified_binding_blocks_before_send -q
```

覆盖代码：`WechatSender.send()`。

### WX-SEND-005 文件传输助手确认发送

目的：最小闭环验证：人工 approve 后可以发送，并从 Reader 回读到 outbound。

前置条件：

- 只选择 `filehelper` 或明确测试联系人。
- scope 已 `verified`。
- `CEO_WECHAT_SEND_MODE=confirm`。
- 有一条 `ready_to_send` delivery。

步骤：

```bash
.venv/bin/ceo-agent wechat pending
.venv/bin/ceo-agent wechat approve --id <delivery_id>
.venv/bin/ceo-agent wechat read-recent \
  --target-id filehelper \
  --limit 20 \
  --include-text
```

预期：

- approve 返回 `sent`。
- 微信文件传输助手中看到消息。
- `read-recent` 能回读到完全一致的 outbound 文本。
- `wechat_deliveries.status=sent`。

覆盖代码：`WechatSender.send()`、`MacWechatAccessibility.send()`、Reader 回读。

### WX-SEND-006 发送后 UI 无确认进入 send_unknown

目的：确认动作已执行但无法确认时不会自动重发。

建议使用自动化测试：

```bash
.venv/bin/python -m pytest tests/wechat/test_accessibility.py::test_post_action_ambiguity_becomes_send_unknown -q
```

预期：

- 状态为 `send_unknown`。
- 不自动 retry，不重复发送。

覆盖代码：`AccessibilityResult(action_performed=True, visible_confirmation=False)`。

### WX-SEND-007 orphan sending 恢复不重发

目的：确认服务重启前后不会重复发送。

建议使用自动化测试：

```bash
.venv/bin/python -m pytest tests/wechat/test_accessibility.py::test_recovery_never_resends_sending -q
```

预期：

- `sending` 被恢复为 `sent` 或 `send_unknown`。
- 不调用 Sender 重新发送。

覆盖代码：`reconcile_incomplete_deliveries()`。

### WX-SEND-008 reject 不发送

目的：确认人工拒绝会关闭 delivery。

步骤：

```bash
.venv/bin/ceo-agent wechat pending
.venv/bin/ceo-agent wechat reject --id <delivery_id>
```

预期：

- delivery 状态变 `failed`。
- error 为 `user_rejected`。
- pending 列表不再出现该 id。
- 微信未发出消息。

覆盖代码：`service.reject_wechat_delivery()`。

### WX-MEM-001 手动导入记忆必须有边界

目的：确认历史导入不会无边界扫全量微信。

步骤：

```bash
.venv/bin/ceo-agent wechat import-memory \
  --target-id filehelper \
  --since 2026-07-01 \
  --until 2026-07-22T23:59:59+08:00 \
  --limit 100
```

预期：

- 命令输出读取消息数、pending candidate 数。
- 不调用 `memory_write`。
- 不改变自动回复 scope watermark。

覆盖代码：`WechatMemoryImporter.run()`。

### WX-MEM-002 候选记忆审核

目的：确认候选只进入本地 review 表，需人工 approve。

步骤：

打开：

```text
http://127.0.0.1:8765/wechat/memory-review
```

对候选执行：

- approve 一条，并编辑 final statement
- reject 一条

预期：

- pending 变 approved/rejected。
- rejected 不能写入 Memory。
- approved 未点击写入前，Memory 未变化。

覆盖代码：`store.review_wechat_memory_candidate()`、`WechatMemoryWriter.write()`。

### WX-MEM-003 敏感信息清洗

目的：确认验证码、API key、手机号、邮箱、原始长 transcript 不会进入候选。

建议执行自动化：

```bash
.venv/bin/python -m pytest \
  tests/wechat/test_memory.py::test_credentials_never_become_candidates \
  tests/wechat/test_memory.py::test_clean_rejects_non_normal_and_redacts_and_bounds_fields \
  -q
```

预期：

- secret/验证码类候选被丢弃。
- 邮箱/手机号被脱敏。
- `cleanup_notes` 使用 deterministic marker，不保留模型原始说明。

覆盖代码：`WechatMemoryImporter.clean_candidates()`。

### WX-SVC-001 主服务默认不启动微信线程

目的：确认不开关时微信通道不影响钉钉主服务。

步骤：

确保：

```text
CEO_WECHAT_READER_ENABLED=0
```

启动：

```bash
.venv/bin/python -m app.cli service --host 127.0.0.1 --port 8765
```

预期：

- 没有 `ceo-agent-service-wechat-producer` / `consumer` 线程。
- 钉钉服务正常。

自动化覆盖：

```bash
.venv/bin/python -m pytest tests/wechat/test_service.py::test_no_loops_by_default -q
```

### WX-SVC-002 Reader ready 后启动微信 producer/consumer

目的：确认只有 Reader enabled 且唯一 ready account 时才启动微信循环。

前置条件：

```text
CEO_WECHAT_READER_ENABLED=1
CEO_WECHAT_SENDER_ENABLED=0
CEO_WECHAT_SEND_MODE=confirm
```

步骤：

```bash
.venv/bin/python -m app.cli service --host 127.0.0.1 --port 8765
```

预期：

- 微信 producer/consumer 启动。
- sender 不自动发送。
- 产生的 delivery 停留在 `ready_to_send`。

覆盖代码：`app/cli.py::_wechat_service_components()`、`service.wechat_loop_names()`。

## 推荐执行顺序

1. 自动化回归：`pytest tests/wechat -q`。
2. Reader 未安装失败保护：`WX-CONN-001`。
3. 安装 Reader：`WX-CONN-002`。
4. 授权 Reader：`WX-CONN-003`、`WX-CONN-004`。
5. 准备并校验 passphrase：`WX-CONN-005`、`WX-CONN-006`。
6. 账号 ready 和读取验证：`WX-CONN-007`、`WX-READ-001`、`WX-READ-002`。
7. 保存测试联系人/测试群 scope：`WX-SCOPE-001`、`WX-SCOPE-002`。
8. Producer 入队规则：`WX-PROD-001` 到 `WX-PROD-005`。
9. Consumer 待发送：`WX-CONS-001` 到 `WX-CONS-003`。
10. 安装 Sender 和绑定验证：`WX-SEND-002`、`WX-SEND-003`。
11. confirm 模式下文件传输助手发送闭环：`WX-SEND-001`、`WX-SEND-005`。
12. 异常保护：`WX-SEND-004`、`WX-SEND-006`、`WX-SEND-007`、`WX-SEND-008`。
13. 可选：历史记忆导入：`WX-MEM-001` 到 `WX-MEM-003`。

## 验收标准

可认为微信连接和核心功能正确，当且仅当：

- Reader `status` 为 ready，且 self wxid 非空。
- 文件传输助手可以读到最新消息，方向正确。
- 未选会话、普通群消息、outbound、非文本不会入队。
- 选中单聊 inbound text 能入队。
- 选中群聊只有结构化 @ 当前账号才入队。
- Consumer 只生成 `ready_to_send`，不直接发送。
- confirm 模式不会自动发。
- 未 verified 绑定禁止发送。
- 文件传输助手 approve 后能发送并回读 outbound。
- `send_unknown` 和 `sending` 恢复不会重复发送。
- 历史记忆导入有明确 target/date/limit，不自动写 Memory。

## 回滚和清理

禁用微信通道：

```text
CEO_WECHAT_READER_ENABLED=0
CEO_WECHAT_SENDER_ENABLED=0
```

停止辅助应用：

```bash
launchctl bootout gui/$(id -u)/com.stardust.ceo-agent.wechat-reader
launchctl bootout gui/$(id -u)/com.stardust.ceo-agent.wechat-sender
```

清理明文镜像和 passphrase 时要确认不再需要读取微信：

```bash
# 谨慎执行：会移除本地微信解密镜像和持久化 passphrase
# rm -rf ~/.cache/wx_read/plain
# rm -f ~/.config/wx_read/passphrase.hex
```

不要删除真实微信数据库，不要把 passphrase、明文镜像、聊天导出或 SQLite runtime 数据提交到 Git。
