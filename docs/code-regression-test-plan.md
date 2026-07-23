# CEO Agent Service 回归测试用例设计

这份清单用于后续研发改代码后的回归测试。目标不是追求用例数量，而是覆盖真实生产风险：不会误发消息、不会漏处理任务、不会污染记忆、不会因旧库升级崩溃、不会因为本机配置影响服务内 Codex 决策。

## 推荐分层

| 层级 | 目标 | 执行频率 | 命令 |
| --- | --- | --- | --- |
| Smoke | 验证 CLI、Web、核心导入不崩 | 每次提交 | `.venv/bin/python -m pytest tests/test_cli.py tests/test_audit_web.py -q` |
| Core unit | 验证数据库、Worker、TaskAgent、Codex 命令契约 | 每次提交 | `.venv/bin/python -m pytest tests/test_store.py tests/test_task_agent.py tests/test_worker.py -q` |
| WeChat unit | 验证微信 Reader/Producer/Consumer/Sender/Memory 规则 | 每次提交 | `.venv/bin/python -m pytest tests/wechat -q` |
| Full regression | 全仓库回归 | 合并前 | `.venv/bin/python -m pytest -q` |
| macOS WeChat integration | 验证真实微信连接、授权、读取、发送 | 发布前或微信相关改动后 | 按 `docs/wechat-channel-test-cases.md` 手工执行 |

## 必须自动化的代码测试用例

### 1. 启动与配置

| ID | 用例 | 断言 |
| --- | --- | --- |
| CFG-001 | `.env` 缺失时使用安全默认值 | 不自动发送消息，不自动启动微信 |
| CFG-002 | `CEO_DRY_RUN` / `CEO_NOT_SEND_MESSAGE` 非法值 | CLI fail-fast，错误信息明确 |
| CFG-003 | `CEO_WORKSPACE` 使用 `~` | 路径正确展开 |
| CFG-004 | 微信 Reader 默认关闭 | `_wechat_service_components()` 返回空 |
| CFG-005 | Reader ready 但 Sender 未开启 | 只启动 producer/consumer |
| CFG-006 | Reader ready 且 Sender 显式开启 | 启动 producer/consumer/sender |

### 2. 数据库与迁移

| ID | 用例 | 断言 |
| --- | --- | --- |
| DB-001 | 新库初始化 | 所有表、索引创建成功 |
| DB-002 | 同一路径重复初始化 | 每进程只初始化一次 |
| DB-003 | 旧 `follow_up_drafts` 表升级 | 先补字段再建索引，不能出现 `no such column: updated_at` |
| DB-004 | 旧 `reply_tasks` 唯一键迁移到 channel 维度 | 钉钉和微信同 message id 不互相覆盖 |
| DB-005 | `wechat_deliveries` 外键迁移 | 旧 delivery 仍能关联原任务 |
| DB-006 | 并发读写 | reader transaction 存在时 writer 可提交或有明确 backoff |

### 3. DWS / 钉钉安全边界

| ID | 用例 | 断言 |
| --- | --- | --- |
| DWS-001 | 未登录或 token 失效 | Worker 不启动 Codex，不发送消息，记录可操作错误 |
| DWS-002 | 机器人缺少 `botOpenDingTalkId` | `run-once` 失败原因明确，不吞异常 |
| DWS-003 | `--not-send-message` | 不调用真实发送接口，attempt 状态为 dry_run/skipped |
| DWS-004 | 单聊发送目标解析 | 使用 trigger sender 或近邻消息，不误发给群 |
| DWS-005 | 召回失败 | 不自动重试误召回，记录失败原因 |

### 4. Codex Runner 与 TaskAgent

| ID | 用例 | 断言 |
| --- | --- | --- |
| CODEX-001 | 通用 Codex 命令构建 | 禁用 hooks/plugins，设置受控 approval policy |
| CODEX-002 | TaskAgent 使用独立 schema | 命令包含 `task_agent_decision.schema.json` |
| CODEX-003 | TaskAgent 隔离用户配置 | 命令包含 `--ignore-user-config` |
| CODEX-004 | TaskAgent timeout | 抛出明确 timeout reason |
| CODEX-005 | JSONL 输出解析 | 能从 `item.completed` 中解析结构化结果 |
| CODEX-006 | Memory recall 审计强制 | 非 discard 决策必须有真实 memory_recall 工具事件 |
| CODEX-007 | Memory connector 不可用 | 不失败、不转人工，决策中写明替代证据 |

### 5. 微信 Reader / 连接

| ID | 用例 | 断言 |
| --- | --- | --- |
| WXR-001 | Reader 未运行 | 客户端 fail-closed，提示 unavailable |
| WXR-002 | Reader IPC 只暴露白名单方法 | 非法方法报错，不执行 |
| WXR-003 | IPC 参数边界 | 超大 limit / 非法 target 被拒绝 |
| WXR-004 | Unix socket owner-only | socket 权限为 0600 |
| WXR-005 | App Data 权限被拒绝 | 主循环记录 `wechat_data_permission_required` 并暂停 |
| WXR-006 | Reader 返回权限包装错误 | 主循环同样暂停，不能刷屏重试 |
| WXR-007 | 多账号 ready | 自动流程拒绝猜账号 |
| WXR-008 | ready 账号缺少 self wxid | 自动流程不启动 |

### 6. 微信 Producer 入队规则

| ID | 用例 | 断言 |
| --- | --- | --- |
| WXP-001 | 首次启用 scope | 只建立 watermark，不回放历史消息 |
| WXP-002 | 单聊 inbound text | 入队一次 |
| WXP-003 | 单聊 outbound | 不入队 |
| WXP-004 | 图片/文件/非文本 | 不入队 |
| WXP-005 | 群聊普通消息 | 不入队 |
| WXP-006 | 群聊结构化 @ 当前账号 | 入队 |
| WXP-007 | 同一秒边界消息 | 后续扫描不丢、不重复 |
| WXP-008 | 批处理失败 | 不推进 watermark |

### 7. 微信 Consumer 与待发送

| ID | 用例 | 断言 |
| --- | --- | --- |
| WXC-001 | `SEND_REPLY` | 创建 `wechat_deliveries.ready_to_send` |
| WXC-002 | `ASK_CLARIFYING_QUESTION` | 创建待发送 delivery |
| WXC-003 | `NO_REPLY` | 完成任务，不创建 delivery |
| WXC-004 | `HANDOFF_TO_HUMAN` | 完成任务，不创建 delivery |
| WXC-005 | `STOP_WITH_ERROR` | 任务失败，有 bounded retry |
| WXC-006 | DingTalk-only `system_actions` | 不创建 delivery，attempt 标记 failed |
| WXC-007 | leak check 修改文本 | delivery 保存清洗后的文本 |
| WXC-008 | 读取上下文失败 | Consumer 继续使用空上下文决策 |

### 8. 微信 Sender / 发送安全

| ID | 用例 | 断言 |
| --- | --- | --- |
| WXS-001 | confirm 模式 | 不自动发送，只保留 pending |
| WXS-002 | auto 模式且 sender disabled | 不发送 |
| WXS-003 | auto 模式且 sender enabled | 调用 Sender 一次 |
| WXS-004 | 未验证绑定 | 发送前阻断，状态 failed |
| WXS-005 | verified 绑定 | 调用辅助功能发送 |
| WXS-006 | 发送后无法确认 | 状态 `send_unknown`，不自动重发 |
| WXS-007 | orphan `sending` 恢复 | 只 reconcile，不重发 |
| WXS-008 | reject | 标记 failed，不调用 Sender |
| WXS-009 | approve 指定 delivery | 只发送指定 delivery |
| WXS-010 | recall 无能力 | 返回 false，不改成成功 |

### 9. 微信记忆导入

| ID | 用例 | 断言 |
| --- | --- | --- |
| WXM-001 | 导入必须有显式时间边界 | 无边界直接拒绝 |
| WXM-002 | 非文本先过滤 | 不进入 Codex prompt |
| WXM-003 | 凭证/医疗/财务内容 | 不成为候选 |
| WXM-004 | Prompt 脱敏 | 不包含 sender id、邮箱、手机号、长数字 |
| WXM-005 | Extraction 禁用所有工具 | read-only、`tools.enabled_tools=[]`、禁用 memory_connector |
| WXM-006 | Codex 尝试工具调用 | fail-closed |
| WXM-007 | Recall matcher 只能调用一次 memory_recall | 查询必须逐字等于候选 statement |
| WXM-008 | Write backend 只允许 memory_write | 不接受伪造成功 |
| WXM-009 | pending 候选不能写入 | 必须人工审核通过 |
| WXM-010 | 写入 unknown | 不自动重试，需人工确认 |

### 10. 审计 Web

| ID | 用例 | 断言 |
| --- | --- | --- |
| WEB-001 | 审计 Web 导入 | Python 3.11 不出现 SyntaxError |
| WEB-002 | 回复历史 | 展示 channel、状态、错误原因 |
| WEB-003 | 微信待发送列表 | 只展示 pending/ready_to_send |
| WEB-004 | approve | 调用发送并重定向安全 |
| WEB-005 | reject | 标记 failed，不开放 open redirect |
| WEB-006 | 记忆审核页面 | 转义敏感文本，拒绝危险人工编辑 |
| WEB-007 | 日志分页 | 不遗漏错误与操作记录 |

## 本次新增/加强的测试

| 测试位置 | 目的 |
| --- | --- |
| `tests/wechat/test_consumer.py::test_dingtalk_system_actions_rejected` | 加强断言：拒绝钉钉专用 action 时，微信审计 attempt 必须是 failed，而不能残留 pending |
| `tests/test_cli.py::test_wechat_sender_component_requires_explicit_sender_flag` | 覆盖 Reader ready 后 Sender 是否需要独立开关 |
| `tests/test_cli.py::test_wechat_loop_stops_after_reader_ipc_permission_denial` | 覆盖 Reader IPC 包装后的 App Data 权限错误，主循环必须暂停 |

## 真实微信集成测试

自动化单测不能替代真实微信验证。以下场景必须在 macOS 桌面执行：

| ID | 场景 | 通过标准 |
| --- | --- | --- |
| INT-WX-001 | Reader App 安装和签名 | 能启动独立 helper，socket 健康检查通过 |
| INT-WX-002 | App Data 权限授权 | 未授权时 blocked，授权后 ready |
| INT-WX-003 | 读取文件传输助手 | 默认脱敏；显式开启正文验证后能看到测试文本 |
| INT-WX-004 | 保存单聊和群聊 scope | 单聊 every inbound，群聊只响应结构化 @ |
| INT-WX-005 | confirm 模式闭环 | 产生待发送，不自动发；人工 approve 后只发一条 |
| INT-WX-006 | Sender Accessibility 授权 | 未授权 blocked，授权后可定位目标 |
| INT-WX-007 | 发送 unknown | UI 无确认时进入 send_unknown，不重复发送 |
| INT-WX-008 | 清理 | 关闭微信开关、删除测试 scope、清空测试 delivery |

## 合并前建议门禁

1. 微信相关改动：必须跑 `tests/wechat -q`，并执行至少 `INT-WX-001` 到 `INT-WX-005`。
2. 数据库 schema 改动：必须跑 `tests/test_store.py -q`，并手工构造一个旧库升级。
3. Codex prompt/runner 改动：必须跑 `tests/test_codex_runner.py tests/test_task_agent.py tests/wechat/test_memory.py -q`。
4. 发送链路改动：必须跑 `tests/test_worker.py tests/test_cli.py tests/wechat/test_send_mode.py -q`，且用 `CEO_NOT_SEND_MESSAGE=1` 做 dry-run。
5. 发布前：必须跑全量 `.venv/bin/python -m pytest -q`；沙箱导致的 Unix socket/macOS 依赖失败要在真实 macOS 环境复核，不能直接忽略。
