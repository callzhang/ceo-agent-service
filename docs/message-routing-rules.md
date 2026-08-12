# 消息发现与 Skill 选择规则

本文档描述当前 Skill-first runtime。这里的“路由”只指稳定的传输结构过滤，不指根据业务关键词
选择处理代码。

## 核心原则

系统没有关键词业务 router。Producer 不通过“审批”“会议”“材料”、人名、项目名或正则命中来
决定业务结论，也不把业务正文交给不同的硬编码 handler。凡是需要理解语义的 trigger 都交给
Consumer A；A 根据完整上下文动态发现并读取适用 Skill。

Service 只负责：

- 识别消息来自群聊、私聊、机器人、日程/OA 等稳定平台结构；
- 应用群聊明确 @、私聊入口、系统自身回执过滤和 exact source revision 去重；
- 传递原始消息、上下文、平台 ID、链接、材料引用和精确读取命令；
- 校验 A/B 结果结构，保存队列和恢复状态；
- 从现有 tool events 生成 verified Skill receipts，并要求 B 重读；
- 对已确认的完全相同外部动作去重，对 unknown 结果先只读核对。

Service 不负责：

- 按关键词选择业务 Skill；
- 预读文档、文件夹、图片、表格、听记或 OA 正文后解释业务含义；
- 替 Agent 选择同名文件、猜测 OA 申请人/标题对应的 task，或用 fallback 拼装材料；
- 维护另一套 Skill 调用审计数据库。

## 传输结构过滤

### 群聊

群聊只有明确 @ 配置的管理者别名、配置的 Agent 名称或平台广播 alias 时才创建候选 trigger。
普通未 @ 的群消息不进入 Agent。连续消息可以作为同一 conversation turn 合并，但必须保留每条
source message ID 和顺序。

### 私聊与机器人私聊

私聊默认视为发给当前管理者，无需 @。机器人私聊使用配置的机器人身份读取和回复。平台生成的
自身发送回执、重复投递和已处理 source revision 在入队前过滤。

### 稳定平台对象

OA 卡片、日程邀请、会议材料、邮件卡片、文档卡片、图片和附件可以携带稳定 object type 与 ID。
Producer 可以保留这些结构字段，但不能据此替 Agent 作业务决定。只含链接或媒体的消息只要是有效
trigger，也应进入 A；A 使用对应 Skill 判断是否读取及如何处理。

### 系统噪声

只有可由平台类型或明确 sender identity 证明的纯系统同步、服务自身回执和重复消息可以在 Agent 前
终止。模糊的业务“状态通知”不能靠词表静默跳过，应由 `ceo-message-triage` 判断 `no_action`。

## Consumer A 的 Skill discovery

A 首先按任务语义选择业务 Skill，而不是由 service 注入结论：

| 任务 | 业务 Skill | 后续操作 Skill 示例 |
| --- | --- | --- |
| 普通消息、reaction、澄清 | `ceo-message-triage` | `dingtalk-chat` |
| 日程邀请 | `ceo-calendar-invite` | `dingtalk-calendar` |
| 文档、文件、图片、表格 | `ceo-document-review` | `dingtalk-doc`、`dingtalk-drive`、Lark 文档 Skill |
| 听记、会议材料、会后事项 | `ceo-meeting-work` | `dingtalk-minutes`、`dingtalk-chat` |
| 邮件线程 | `ceo-mail-review` | `dingtalk-mail` |
| 员工/候选人敏感沟通 | `ceo-personnel-communication` | 通讯录与专业面试 Skill |
| 项目、TODO、催办、关闭 | `ceo-work-tracking` | `dingtalk-todo`、`dingtalk-chat` |

一个 trigger 可以加载多个业务 Skill。例如带日程卡片的静默评审可以同时读取日历、文档和会议
Skill。Skill 描述承担动态发现职责；代码不得增加同义关键词列表来模仿 discovery。

OA、面试、OKR 委派给已有专业 Skill：`dingtalk-oa-approval`、当前安装的面试专业 Skill、
`dingtang-okr-review`。CEO 通用 Skill 不复制这些专业规则。

## 材料读取边界

Service 向 A 提供原始 ID、链接和精确命令提示，例如读取 OA detail、展开文档目录或下载附件。
A 决定是否调用、是否继续展开以及证据是否充分。A 读取失败时必须区分：

- 依赖/认证/网络不可用：返回明确可恢复错误，不伪装成“没有材料”；
- 参与者可以补充的事实：生成一个具体问题并发回来源会话；
- 只有 Derek 能做的管理判断：返回 `needs_human`；
- 已有上下文已确认：直接复用，不重复追问。

## Audit B 与执行

Service 只接受 completed `agent_cli.read_skill` events 形成的 receipt。B 必须按 receipt 重读 A 使用的
每个业务 Skill 和操作 Skill，然后核对实时事实、Audit Rules、目标和最小必要内容。合格候选由 B
执行并读回；需要改变业务含义时，B 通过反馈消息让 A 产生新 revision，不自行偷偷改写。

完全相同的 trigger/generation/revision 外部效果只执行一次。反馈后正文或参数变化形成新 revision，
可以继续执行。结果未知时，原 B session 只读 reconciliation；没有证据不得重放。

## Task extraction 与 follow-up

任务提取不是消息 router 的副产品，follow-up 也不是独立发送器。`ceo-work-tracking` 负责从证据中
识别事项、关联项目/TODO、确定 owner 和完成标准、到期追问、读取回复/外部状态并关闭。每条
follow-up 候选仍走 A/B 审阅与执行，使用同一 revision、receipt 和恢复语义。

## 结果

A 的有效结果是精确候选、`no_action`、`needs_human` 或 `failed`。可通过交流解决的证据不足默认
产生具体澄清候选，不向 Derek 展示“继续处理/先追问”的无意义选择。只有 B 已执行且外部读回确认
时才是完成；仅有诊断或文字总结不能冒充动作完成。
