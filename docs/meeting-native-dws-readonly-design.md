# Meeting Agent 原生 DWS 只读边界设计

状态：研究与验收设计，未实施权限变更。2026-10-21 的选择是保留 Meeting Agent 的原生 `dws` 命令，先研究可验证的只读方案。恢复 `dingtalk-chat` Skill 可见性的 [PR #224](https://github.com/callzhang/ceo-agent-service/pull/224) 保持草稿；本文不改变投递规则或授权。

## 问题和完成条件

最近检查的 12 次已完成 Meeting 运行都没有读取群。精简输入虽要求 Agent 用 DWS 核验群，`MeetingAlignmentCodexRunner` 却把原生 Skill 选择设为 `()`，隐藏了 `dingtalk-chat` 入口。PR #224 只修复此发现问题；匿名替身 DWS 的原生 Codex 运行已能搜索群、读取成员与近期消息。生产受众判断尚未因此验证。

Meeting 判断轮需要保留原生 DWS 的群搜索、完整成员、近期消息和必要身份读取；模型不得直接发消息、邮件、审批或执行其他对外写入。实际投递仍由现有 Meeting 服务流程处理，群身份、完整成员、讨论内容与披露授权的业务校验不能退化。只读边界必须由运行时或服务端执行，不能只靠 prompt、Skill 文案或模型自律。

## 已验证的现状

- 当前 Meeting Codex 命令保留 shell；保存的运行上下文为 `danger-full-access`，且一次运行能使用原生 `exec_command`。本机有 `dws` 可执行文件。这个观察说明能调用 DWS，并不证明写命令被禁止。
- 本机 `dws v1.0.65` 的 leaf Schema 将 `+chat-search`、`+chat-members-list`、`+chat-messages` 标为 `effect=read`，将 `+messages-send`、`+send-to-group`、`+dm` 标为 `effect=write`、`confirmation=user_required`。非交互写入缺少 `--yes` 可被确认门禁挡住，但 `--yes` 可由 Agent 添加；`--dry-run` 只预览请求。因此两者都不是不可绕过的只读权限。[DWS 官方 Skill 的确认语义](https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli/blob/main/skills/mono/SKILL.md)
- `codex execpolicy check` 使用临时 `forbidden` 规则时，直接的 `dws chat +messages-send --help` 被匹配；实际工具形态 `/bin/zsh -lc 'dws chat +messages-send --help'` 没有匹配。按单个 DWS 前缀写规则不能证明拦住 shell、别名、绝对路径或其他对外命令。[Codex 原生 execpolicy 规则说明](https://github.com/openai/codex/blob/main/codex-rs/execpolicy/README.md)
- 同一版 DWS 的底层 `chat message send` 和 `chat message send-by-bot` 标为 `effect=write`、`confirmation=not_required`；`+messages-send` 的确认提示不能代表全部发送入口。Schema 描述的是 CLI 契约，本身也不能证明服务端会拒绝写入。
- DWS 还提供宿主接管的 PAT 行为授权：设置 `DINGTALK_DWS_AGENTCODE` 时，缺少 PAT scope 的调用返回给宿主处理；`chat chmod` 称 chat 写 scope 的授权须经宿主 UI。但当前 Codex 子进程的环境白名单没有传入该标识或 `DWS_CONFIG_DIR`；合成环境探针证实两者均被剔除。PAT 与开放平台应用权限是两套机制，底层发送和已有授权仍需验证。[DWS PAT 说明](https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli/blob/main/skills/multi/dingtalk-misc/references/pat.md)
- DWS 官方文档称应用只能调用已获授权的 API。这支持研究独立的只读应用授权，但不证明当前应用的授权范围，也不证明同一进程无法读取其他可写凭据。`dws auth status --readonly` 仅为**认证状态的本地只读快照**，不是业务命令只读模式。[DWS 官方说明](https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli#security-by-design)
- DWS 源码中，`dws api` 为原始 HTTP 请求单独取得**应用级令牌**，凭据依次来自命令参数、环境或应用配置（后者可取 Keychain）；它不走群消息 Shortcut 的确认入口，也不通过缺少 MCP PAT scope 时的重试分支。本机默认 DWS 目录存在应用配置文件（只检查存在性，未读取内容）。因此只证明宿主 PAT 能约束 MCP 命令，仍不足以证明同一 shell 进程不能用原始 API 写入。[原始 API 令牌路径](https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli/blob/v1.0.65/internal/app/api_command.go)、[PAT 错误处理](https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli/blob/v1.0.65/internal/app/pat_auth_retry.go)
- 本机执行 `codex sandbox -P ':read-only'` 的隔离探针，子进程仍成功 `open()` 当前用户的 `~/.dws/app.json`；探针立即关闭文件，未读取或输出内容。这只证明内置文件系统只读模式**不隔离已有 DWS 应用配置**，不能据此推断远端令牌有效或某项写入实际获准。要保留原生命令并做到业务只读，还需验证独立凭据和进程访问边界。

## 研究路径：验证服务端权限与凭据隔离

1. 在受控测试组织核对群搜索、成员、消息和身份读取所需的实际权限。保留原生 `dws` CLI，分别验证 MCP 的 PAT 行为授权和原始 API 的应用权限；不能从 Schema 的 `effect` 标签推断远端授权。
2. 对 MCP 读取与写入，用隔离身份核对无写授权、已有授权和过期授权时的服务端结果，覆盖 `+` Shortcut、底层原子命令、`--yes` 和 shell 包装。当前 Codex 环境未传入宿主 PAT 标识，若选择接入，属于后续独立权限变更；不能只凭 CLI 帮助声称需要 UI 就跳过实测。
3. 对原始 API，验证独立应用是否能只授予所需读取权限，并让 Meeting 进程只能取得该应用令牌。分别核对群读取、消息/邮件/OA 写入的服务端结果。DWS 的 MCP 用户 OAuth 与原始 API 应用令牌是不同调用路径，任何一个路径能写都不能称为只读。
4. 证明 Meeting Codex 进程无法访问现有写凭据、Keychain 条目或替代网络通道。只换 DWS profile、配置目录或普通文件系统 `read-only` 沙箱不构成此证明；当前进程与生产服务同属一个本机用户。候选实现可研究独立 OS 身份或可验证的原生进程沙箱，确认边界后再选。
5. Codex 命令规则可作辅助约束，但不能单独宣称“只读”。若全路径服务端拒绝或凭据隔离无法证实，带证据回到架构选择，不把此前被否决的新工具方案悄悄上线。

## 固定验收与发布门槛

先用虚构 Meeting 与替身 DWS 检查冷启动和既有的同 session 输出纠错轮：确实执行群搜索、完整成员与近期消息读取，选对业务承接群；群证据不足时不猜测，人员敏感内容不进入未授权受众。然后在受控测试组织核对真实 DWS 授权：上述读取成功；直接调用、shell 包装、别名、绝对路径、`--yes` 和原始 API 等写入尝试均得到可归因的拒绝，且无外部效果。至少覆盖常用 Shortcut、底层原子发送、消息、邮件、审批、已有授权和其他可用的外发入口；不能以单个 `chat send` 规则代表全局禁写。

安全权限实现须按仓库 `AGENTS.md` 单独确认、单独提交和审查。PR #224 在这一边界验收前不合并、不部署。后续若实施，先跑对应本地测试和固定案例，再按正式流程部署并读回版本、PID、健康、队列、Attention、History 及完整 Meeting trajectory；进程成功或 JSON 有效都不单独构成业务通过。
