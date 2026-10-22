# Meeting Agent 原生 DWS 只读边界设计

状态：强制只读权限仍是研究方案，未实施权限变更。2026-10-21 Derek 选择保留 Meeting Agent 的原生 `dws` 命令；Meeting 专属指令只要求 DWS 读群与身份，禁止 Agent 直接向外发送消息、邮件和执行审批的约束由所有 Consumer 共用的指令提供，投递仍由服务处理。恢复 `dingtalk-chat` Skill 可见性的 [PR #224](https://github.com/callzhang/ceo-agent-service/pull/224) 已合并并部署。这是行为约束，不宣称已取得强制只读隔离。

## 问题和完成条件

最近检查的 12 次已完成 Meeting 运行都没有读取群。精简输入虽要求 Agent 用 DWS 核验群，`MeetingAlignmentCodexRunner` 却把原生 Skill 选择设为 `()`，隐藏了 `dingtalk-chat` 入口。PR #224 只修复此发现问题；匿名替身 DWS 的原生 Codex 运行已能搜索群、读取成员与近期消息。生产受众判断尚未因此验证。

Meeting 判断轮需要保留原生 DWS 的群搜索、完整成员、近期消息和必要身份读取；模型不得直接发消息、邮件、审批或执行其他对外写入。实际投递仍由现有 Meeting 服务流程处理，群身份、完整成员、讨论内容与披露授权的业务校验不能退化。只读边界必须由运行时或服务端执行，不能只靠 prompt、Skill 文案或模型自律。

## 已验证的现状

- 当前 Meeting Codex 命令保留 shell；保存的运行上下文为 `danger-full-access`，且一次运行能使用原生 `exec_command`。本机有 `dws` 可执行文件。这个观察说明能调用 DWS，并不证明写命令被禁止。
- 本机 `dws v1.0.65` 的 leaf Schema 将 `+chat-search`、`+chat-members-list`、`+chat-messages` 标为 `effect=read`，将 `+messages-send`、`+send-to-group`、`+dm` 标为 `effect=write`、`confirmation=user_required`。非交互写入缺少 `--yes` 可被确认门禁挡住，但 `--yes` 可由 Agent 添加；`--dry-run` 只预览请求。因此两者都不是不可绕过的只读权限。[DWS 官方 Skill 的确认语义](https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli/blob/main/skills/mono/SKILL.md)
- `codex execpolicy check` 使用临时 `forbidden` 规则时，直接的 `dws chat +messages-send --help` 被匹配；实际工具形态 `/bin/zsh -lc 'dws chat +messages-send --help'` 没有匹配。按单个 DWS 前缀写规则不能证明拦住 shell、别名、绝对路径或其他对外命令。[Codex 原生 execpolicy 规则说明](https://github.com/openai/codex/blob/main/codex-rs/execpolicy/README.md)
- 同一版 DWS 的底层 `chat message send` 和 `chat message send-by-bot` 标为 `effect=write`、`confirmation=not_required`；`+messages-send` 的确认提示不能代表全部发送入口。Schema 描述的是 CLI 契约，本身也不能证明服务端会拒绝写入。
- DWS 还提供宿主接管的 PAT 行为授权：设置 `DINGTALK_DWS_AGENTCODE` 时，缺少 PAT scope 的调用返回给宿主处理；`chat chmod` 称 chat 写 scope 的授权须经宿主 UI。但当前 Codex 子进程的环境白名单没有传入该标识或 `DWS_CONFIG_DIR`；合成环境探针证实两者均被剔除。v1.0.65 源码确认宿主接管模式**只**检查该环境变量；有原生 shell 的进程可以改变子命令环境，所以这个开关不能单独作为不可绕过的授权边界。它是否改变服务端写入授权尚未由真实请求证明。[DWS PAT 说明](https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli/blob/main/skills/multi/dingtalk-misc/references/pat.md)、[模式判定源码](https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli/blob/v1.0.65/internal/auth/channel.go)
- DWS 官方文档称应用只能调用已获授权的 API。这支持研究独立的只读应用授权，但不证明当前应用的授权范围，也不证明同一进程无法读取其他可写凭据。`dws auth status --readonly` 仅为**认证状态的本地只读快照**，不是业务命令只读模式。[DWS 官方说明](https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli#security-by-design)
- DWS 源码中，`dws api` 为原始 HTTP 请求单独取得**应用级令牌**，凭据依次来自命令参数、环境或应用配置（后者可取 Keychain）；它不走群消息 Shortcut 的确认入口，也不通过缺少 MCP PAT scope 时的重试分支。本机 v1.0.65 的 `dws api --help` 还明确说明：默认 MCP 登录取得的加密令牌**不能**用于原始 API；但只要同一进程能取得应用凭据，原始 API 仍是另一条路径。本机默认 DWS 目录存在应用配置文件（只检查存在性，未读取内容）。[原始 API 令牌路径](https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli/blob/v1.0.65/internal/app/api_command.go)、[PAT 错误处理](https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli/blob/v1.0.65/internal/app/pat_auth_retry.go)
- 本机执行 `codex sandbox -P ':read-only'` 的隔离探针，子进程仍成功 `open()` 当前用户的 `~/.dws/app.json`；探针立即关闭文件，未读取或输出内容。这只证明内置文件系统只读模式**不隔离已有 DWS 应用配置**，不能据此推断远端令牌有效或某项写入实际获准。要保留原生命令并做到业务只读，还需验证独立凭据和进程访问边界。
- 本机用 macOS `sandbox-exec` 做只拒绝 `~/.dws` 文件读取的探针：Python 直接运行、以及经 `/bin/zsh -lc` 运行时，打开同一配置文件均返回 `Operation not permitted`。探针没有读取配置内容或调用业务 API。这证明进程级路径规则能覆盖 shell 包装；它**没有**证明 Keychain、其他凭据路径、网络或 DWS 服务端写权限也被隔离。

## 候选方案与当前判断

- **仅用 Codex 内置 `:read-only` 或命令禁用规则：不满足条件。**前者仍可读取当前用户的 DWS 配置；后者的 DWS 前缀规则已被 shell 包装绕过。两者均不能作为“可读群、不可发消息”的权限证明。
- **同一系统用户下再套进程沙箱：只作为候选。**路径拒绝探针有效，且保留原生 `dws` 命令，但需要逐一证明它拦住当前用户可用的所有写凭据、Keychain 和替代外发通道，同时允许 DWS 只读身份、Codex 登录和群读取。现有探针不足以做出这个结论。
- **隔离的系统身份加只读 DWS 身份：优先验证。**让 Meeting 判断进程继续执行原生 Codex 和 `dws`，但使用独立的 OS 用户、该用户自己的 Codex home 与仅有群读取权限的 DWS 凭据；生产服务继续持有投递所需的凭据。独立用户须不能访问生产用户的 DWS 配置、Keychain 或其他写凭据。此方案也必须用真实服务端拒绝回执验证，不能仅从配置或权限名称推断安全性。切换运行身份、配置凭据及权限均属后续单独确认的实现，不在本文落地。

本机目前只发现一个未标识为测试环境的 DWS profile；没有隔离测试组织的写入拒绝回执。因此以上是将来实施强制只读时的验证顺序，不是当前提示约束的发布门槛，也不是已通过的只读实现。

## 研究路径：验证服务端权限与凭据隔离

1. 在受控测试组织核对群搜索、成员、消息和身份读取所需的实际权限。保留原生 `dws` CLI，分别验证 MCP 的 PAT 行为授权和原始 API 的应用权限；不能从 Schema 的 `effect` 标签推断远端授权。
2. 对 MCP 读取与写入，用隔离身份核对无写授权、已有授权和过期授权时的服务端结果，覆盖 `+` Shortcut、底层原子命令、`--yes`、shell 包装，以及子命令不带 `DINGTALK_DWS_AGENTCODE` 的情况。当前 Codex 环境未传入宿主 PAT 标识，若选择接入，属于后续独立权限变更；不能只凭 CLI 帮助声称需要 UI 就跳过实测。
3. 对原始 API，验证独立应用是否能只授予所需读取权限，并让 Meeting 进程只能取得该应用令牌。分别核对群读取、消息/邮件/OA 写入的服务端结果。DWS 的 MCP 用户 OAuth 与原始 API 应用令牌是不同调用路径，任何一个路径能写都不能称为只读。
4. 证明 Meeting Codex 进程无法访问现有写凭据、Keychain 条目或替代网络通道。只换 DWS profile、配置目录或普通文件系统 `read-only` 沙箱不构成此证明；当前进程与生产服务同属一个本机用户。候选实现可研究独立 OS 身份或可验证的原生进程沙箱，确认边界后再选。
5. Codex 命令规则可作辅助约束，但不能单独宣称“只读”。若全路径服务端拒绝或凭据隔离无法证实，带证据回到架构选择，不把此前被否决的新工具方案悄悄上线。

## 固定验收与发布门槛

先用虚构 Meeting 与替身 DWS 检查冷启动和既有的同 session 输出纠错轮：确实执行群搜索、完整成员与近期消息读取，选对业务承接群；群证据不足时不猜测，人员敏感内容不进入未授权受众。然后在受控测试组织核对真实 DWS 授权：上述读取成功；直接调用、shell 包装、别名、绝对路径、`--yes` 和原始 API 等写入尝试均得到可归因的拒绝，且无外部效果。至少覆盖常用 Shortcut、底层原子发送、消息、邮件、审批、已有授权和其他可用的外发入口；不能以单个 `chat send` 规则代表全局禁写。

强制安全权限若在后续实施，须按仓库 `AGENTS.md` 单独确认、单独提交和审查。PR #224 的 Skill 可见性修复与 PR #232 的 Meeting 提示此前已分开合并并部署；本轮只把外发禁令移入共享 Consumer 指令，Meeting 的 DWS 读群提示保留。此前匿名替身 DWS 案例证明了取证行为和该次未直接发送；尚无上线后的真实 Meeting trajectory 能证明后续每轮都如此。行为验证通过仍不等于 DWS 已被技术性禁止写入。
