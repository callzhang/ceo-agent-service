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
- 校验 A/B 结果结构，保存队列和失败重试状态；
- 对已确认的完全相同外部动作去重，记录 provider 返回的 operation、target 和稳定 result identifier。

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

### 当前 pre-Agent 结构过滤清单

`app.worker.DingTalkAutoReplyWorker._is_system_or_notification_message()` 当前执行以下窄范围过滤。
这些规则只判断钉钉传输/渲染结构或固定通知形态；它们不选择业务 Skill，也不产出业务结论。

| 当前输入形态 | pre-Agent 行为 | 仍进入 Agent 的例外 |
| --- | --- | --- |
| `message_type` 不是 `text` | 跳过 | 可解析的 AI 听记权限请求、已识别日历消息，或能补出 calendar context 的消息 |
| 只有有效 AI 听记链接，没有链接外信息单元或问号 | 跳过 | 静默会文档选择器、带具体文字/问题的听记消息，以及上述权限/日历例外 |
| 以钉钉渲染占位符 `[文件]`、`[图片]`、`[视频]`、`[日程]` 开头 | 跳过 | 已识别的听记权限请求或日历消息/context |
| 内容以 `[dingtalk://` 开头 | 跳过 | 已识别的听记权限请求或日历消息/context |
| 仅有钉钉内部链接/渲染媒体 caption，去掉链接与 @ 后最多两个信息单元 | 跳过 | Alidocs 文档、有效听记、OA 链接、链接外有问题的消息 |
| 至少四行、其中至少三行且不低于 45% 为 `字段: 值` 的内部结构化链接卡片 | 跳过 | Alidocs 文档、有效听记、OA 链接、链接外有问题的消息 |
| 完整匹配固定同步/文件状态/流程状态通知形态 | 跳过 | OA 链接、普通外链、链接外有问题的消息 |

OA 链接在上述内容过滤前直接保留。普通外链，包括裸外链，仍进入 Agent。状态通知规则只匹配完整
的固定通知句式；例如“文件已更新，帮忙看一下”包含额外请求，因此进入 Agent。过滤后还会再次
检查 AI 听记权限请求和日历结构，防止钉钉将有效业务对象渲染成非文本占位符时被误跳过。

这份清单是允许保留的结构过滤边界。禁止的是进一步增加人名、项目名、业务主题或同义词列表，
借此选择回复、审批、会议、文档或任务处理器。模糊业务状态与是否需要动作仍由
`ceo-message-triage` 或其他动态加载的业务 Skill 判断。

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

B 根据候选和 Audit Rules 独立判断，必要时读取 Skill 和实时事实；service 不要求 Skill receipt，
也不审核命令、工具或读取模式。合格候选由 B 执行；需要改变业务含义时，B 通过反馈消息让 A 产生新
revision，不自行偷偷改写。

完全相同的 trigger/generation/revision 外部效果只执行一次。反馈后正文或参数变化形成新 revision，
可以继续执行。若执行中断，下一次 Agent turn 按当前业务 Skill 读取目标状态后决定是否继续；服务不
启动专门的 unknown/reconciliation 流程。provider 返回的 operation、target 和稳定 result identifier
（若有）随原任务保存，仅用于关联和去重。

## Task extraction 与 follow-up

任务提取不是消息 router 的副产品，follow-up 也不是独立发送器。`ceo-work-tracking` 负责从证据中
识别事项、关联项目/TODO、确定 owner 和完成标准、到期追问、读取回复/外部状态并关闭。每条
follow-up 候选仍走 A/B 审阅与执行，使用同一 revision 和 provider 去重事实。

## 结果

A 的有效结果是精确候选、`no_action`、`needs_human` 或 `failed`。可通过交流解决的证据不足默认
产生具体澄清候选，不向 Derek 展示“继续处理/先追问”的无意义选择。只有 B 已执行且外部读回确认
时才是完成；仅有诊断或文字总结不能冒充动作完成。
