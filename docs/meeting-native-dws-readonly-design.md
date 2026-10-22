# Meeting Agent 原生 DWS 只读边界设计

状态：研究与验收设计，未实施权限变更。2026-10-21 的选择是保留 Meeting Agent 的原生 `dws` 命令，先研究可验证的只读方案。恢复 `dingtalk-chat` Skill 可见性的 [PR #224](https://github.com/callzhang/ceo-agent-service/pull/224) 保持草稿；本文不改变投递规则或授权。

## 问题和完成条件

最近检查的 12 次已完成 Meeting 运行都没有读取群。精简输入虽要求 Agent 用 DWS 核验群，`MeetingAlignmentCodexRunner` 却把原生 Skill 选择设为 `()`，隐藏了 `dingtalk-chat` 入口。PR #224 只修复此发现问题；匿名替身 DWS 的原生 Codex 运行已能搜索群、读取成员与近期消息。生产受众判断尚未因此验证。

Meeting 判断轮需要保留原生 DWS 的群搜索、完整成员、近期消息和必要身份读取；模型不得直接发消息、邮件、审批或执行其他对外写入。实际投递仍由现有 Meeting 服务流程处理，群身份、完整成员、讨论内容与披露授权的业务校验不能退化。只读边界必须由运行时或服务端执行，不能只靠 prompt、Skill 文案或模型自律。

## 已验证的现状

- 当前 Meeting Codex 命令保留 shell；保存的运行上下文为 `danger-full-access`，且一次运行能使用原生 `exec_command`。本机有 `dws` 可执行文件。这个观察说明能调用 DWS，并不证明写命令被禁止。
- 本机 `dws v1.0.65` 的 leaf Schema 将 `+chat-search`、`+chat-members-list`、`+chat-messages` 标为 `effect=read`，将 `+messages-send`、`+send-to-group`、`+dm` 标为 `effect=write`、`confirmation=user_required`。非交互写入缺少 `--yes` 可被确认门禁挡住，但 `--yes` 可由 Agent 添加；`--dry-run` 只预览请求。因此两者都不是不可绕过的只读权限。[DWS 官方 Skill 的确认语义](https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli/blob/main/skills/mono/SKILL.md)
- `codex execpolicy check` 使用临时 `forbidden` 规则时，直接的 `dws chat +messages-send --help` 被匹配；实际工具形态 `/bin/zsh -lc 'dws chat +messages-send --help'` 没有匹配。按单个 DWS 前缀写规则不能证明拦住 shell、别名、绝对路径或其他对外命令。[Codex 原生 execpolicy 规则说明](https://github.com/openai/codex/blob/main/codex-rs/execpolicy/README.md)
- DWS 官方文档称应用只能调用已获授权的 API。这支持研究独立的只读应用授权，但不证明当前应用的授权范围，也不证明同一进程无法读取其他可写凭据。`dws auth status --readonly` 仅为**认证状态的本地只读快照**，不是业务命令只读模式。[DWS 官方说明](https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli#security-by-design)

## 首选研究路径：原生命令加真正受限的身份

1. 在测试组织核对群搜索、成员、消息和身份读取各自所需的钉钉应用权限，并验证是否能创建只授予这些读取权限的独立应用。不要从 DWS 的 `effect=read` 标签推断远端授权结果。
2. 用该应用登录隔离的 DWS 凭据环境，保持 Agent 使用原生 `dws` CLI。验证所有必需读取成功，`dws chat` 的发送路径、邮件发送、OA 处理及原始 API 写入均被**钉钉服务端**拒绝，包括带 `--yes` 的调用。
3. 隔离 Meeting Codex 进程可见的凭据和网络通道。仅设置另一个 DWS profile 或配置目录不够：当前进程与生产服务同属一个本机用户，尚未证明它无法访问原有凭据或调用其他命令。候选实现可以是独立 OS 身份或经验证的原生进程沙箱；必须先证明写凭据、Keychain 条目和替代网络调用都不可达，再决定实现。不能把普通 `read-only` 文件系统沙箱等同于 HTTP 只读。
4. 保留 Codex 命令规则作为辅助提示或纵深约束；只有上述身份和隔离验证通过，才把运行称为“只读”。若 DWS 应用权限不能满足完整群读取，或隔离无法可靠实现，带证据回到架构选择，不把此前被否决的新工具方案悄悄上线。

## 固定验收与发布门槛

先用虚构 Meeting 与替身 DWS 检查冷启动和既有的同 session 输出纠错轮：确实执行群搜索、完整成员与近期消息读取，选对业务承接群；群证据不足时不猜测，人员敏感内容不进入未授权受众。然后在受控测试组织核对真实 DWS 授权：上述读取成功；直接调用、shell 包装、别名、绝对路径、`--yes` 和原始 API 等写入尝试均得到可归因的拒绝，且无外部效果。至少覆盖消息、邮件、审批以及其他可用的外发入口；不能以单个 `chat send` 规则代表全局禁写。

安全权限实现须按仓库 `AGENTS.md` 单独确认、单独提交和审查。PR #224 在这一边界验收前不合并、不部署。后续若实施，先跑对应本地测试和固定案例，再按正式流程部署并读回版本、PID、健康、队列、Attention、History 及完整 Meeting trajectory；进程成功或 JSON 有效都不单独构成业务通过。
