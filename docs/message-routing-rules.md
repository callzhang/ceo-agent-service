# 钉钉消息路由规则

本文档列出 CEO 自动回复 worker 当前遇到的钉钉消息类型、应该如何路由，以及哪些规则已经由代码实现。

基本原则：代码规则只处理稳定的传输结构和卡片结构，不用宽泛关键词替代 agent 判断。只要消息含义依赖业务上下文，就应该交给 agent，并给 agent 正确的审阅要求。

## 路由结果

- `skip_before_agent`：不调用 agent，直接记录为 `no_reply/skipped`。
- `agent_review`：交给 agent，根据上下文判断是否回复、追问、跳过或交给本人。
- `oa_agent_review`：交给专用 OA agent，注入 `dingtalk-oa-approval` skill，并在既有 attempt 记录中保存 OA 审批动作、留言、URL 和执行结果。
- `handoff_or_material_check`：交给 agent，但回复前必须检查材料完整性，或交给本人处理。
- `candidate_rule`：候选规则，尚未实现；需要真实样本和测试后再落代码。

## 已由代码识别的规则

### 非文本钉钉消息类型

当前行为：`skip_before_agent`，但 AI 听记 / 静默会材料链接例外。

代码条件：

```python
message.message_type and message.message_type.lower() not in {"text"}
```

上游会从这些字段解析消息类型：

```regex
^(msgType|messageType|contentType|content_type|msg_type|type)$
```

例子：

```text
message_type=calendar, content=日程卡片
message_type=image, content=这是图片
message_type=file, content=这是文件
```

说明：

- 这类规则相对安全，因为钉钉已经把消息归类为非文本。
- 如果钉钉没有返回类型字段，则进入文本形态规则。

### 钉钉渲染出来的媒体占位符

当前行为：`skip_before_agent`。

当前代码前缀：

```regex
^\[(文件|图片|视频)\]
```

例子：

```text
[文件] product.md
[图片]
[视频]
```

说明：

- `[Ding]` 已经不在前置跳过列表里。
- 普通外部图片 URL 不属于这个规则。

### 钉钉渲染出来的日程占位符

当前行为：`agent_review` 或 `ask_clarifying_question`。

当前代码前缀：

```regex
^\[日程\]
```

处理规则：

- 先尝试从消息原始 payload 或链接中解析日程 ID。
- 如果消息没有日程 ID，查询消息时间附近到未来 14 天的日历，匹配同一发送人发起、本人待响应、状态 confirmed 的日程。
- 如果事件有 created/update 时间，必须接近消息时间。
- 如果 DWS 日历列表没有返回 created/update 时间，只有唯一候选时才匹配；多个候选不猜。
- 匹配成功后交给 agent 读取最近上下文事项、标题、时间、组织者、描述和冲突日程；如果最近事项和标题已经能判断 Derek 有必要参加，就接受。
- 如果仍判断不了，优先在日历里评论要求补充信息；当前 DWS 不支持日历评论时，fallback 到聊天文字追问。
- 匹配失败时，才追问对方补充会议标题、时间、描述、参会理由和需要 Derek 输入的内容。

### 消息开头就是钉钉内部链接

当前行为：`skip_before_agent`。

当前代码条件：

```regex
^\[dingtalk://
```

例子：

```text
[dingtalk://dingtalkclient/page/flash_minutes_detail?...](dingtalk://dingtalkclient/page/flash_minutes_detail?...)
```

说明：

- 这类主要覆盖钉钉渲染的内部卡片。
- AI 听记 / 静默会链接不走这个跳过规则，见下一节。
- 这个规则故意比“所有链接”窄。`https://example.com/a` 这类普通外链应该进入 agent。

### AI 听记 / 静默会材料链接

当前行为：`agent_review`。

识别范围：

```text
dingtalk://dingtalkclient/page/flash_minutes_detail?...minutesId=...
https://shanji.dingtalk.com/app/transcribes/...
```

处理规则：

- 不按普通钉钉内部链接跳过。
- 从链接中提取 `minutesId` / taskUuid。
- 读取听记基础信息、AI 摘要、处理事项和文字稿预览。
- 将材料注入 prompt 的“已获取的钉钉材料”。
- agent 必须像处理待办事项一样处理听记中的明确事项，给出结论、负责人、下一步或需要补充的材料；不能只总结会议。
- 如果 agent 生成 `send_reply` / `ask_clarifying_question`，服务优先把处理结果写入原 AI 听记 / 静默会的全文评论；当前 DWS 对 AI 听记评论不可用时，会 fallback 到原消息 reply。这类消息不因为“只是材料投递、聊天里没有额外问题”而自动 `no_reply`。

### 只有钉钉内部链接或媒体占位符的消息

当前行为：`skip_before_agent`。

当前代码要求同时满足以下条件。

首先，消息里有钉钉内部链接或钉钉渲染媒体占位符：

```regex
(
  dingtalk://
  | https?://[^\s)]*dingtalk\.com
  | \[(?:文件|图片|视频|日程)\]
)
```

其次，链接外没有问题符号：

```regex
[？?]
```

最后，去掉链接、媒体和 @ 人以后，剩余信息量不超过 2 个信息单元。

会跳过的例子：

```text
@明哥 [dingtalk://dingtalkclient/page/flash_minutes_detail?x=1](dingtalk://dingtalkclient/page/flash_minutes_detail?x=1)
[https://alidocs.dingtalk.com/i/u/dingdocSelectorV4/save?...](https://alidocs.dingtalk.com/i/u/dingdocSelectorV4/save?...)
![图片](@lQLPJwKtm28)
```

不会跳过的例子：

```text
这个链接里的方案怎么看？ https://example.com/a
@明哥 https://example.com/a
https://github.com/alchaincyf/darwin-skill @Alex Chen 这个达尔文 skill 挺有意思
```

说明：

- 普通外链进入 `agent_review`。
- 钉钉在线文档如果是支持的在线文档 URL，会在进入 agent 前尝试读取正文。

### 结构化钉钉链接卡片

当前行为：`skip_before_agent`，但审批/OA 链接例外。

当前代码要求消息里有钉钉内部链接或钉钉媒体占位符：

```regex
(
  dingtalk://
  | https?://[^\s)]*dingtalk\.com
  | \[(?:文件|图片|视频|日程)\]
)
```

并且链接外没有问题符号：

```regex
[？?]
```

并且有结构化字段行：

```regex
^\s*[^:：\n]{1,60}[:：]\s*\S+
```

数量条件：

```text
总行数 >= 4
字段行数量 >= 3
字段行数量 / 总行数 >= 0.45
```

会跳过的例子：

```text
表单标题
字段一: A
字段二: B
字段三: C
字段四: D
[dingtalk://dingtalkclient/action/open_platform_link?x=1](dingtalk://dingtalkclient/action/open_platform_link?x=1)
```

不会跳过的审批/OA 例子：

```text
闫成成提交的项目立项全流程（第一曲线）
项目经理: 闫成成
销售经理: 曹宇航
项目类型: 点云;图片;视频
总预估数据量: 2546573
[dingtalk://dingtalkclient/action/open_platform_link?pcLink=https%3A%2F%2Faflow.dingtalk.com%2F...%26swfrom%3Doa%26dinghash%3Dapproval](dingtalk://dingtalkclient/action/open_platform_link?x=1)
```

### 审批/OA 链接例外

当前行为：`oa_agent_review` when the OA runner is configured; otherwise `agent_review`.

当前代码用以下正则识别审批/OA 链接：

```regex
aflow\.dingtalk\.com|dinghash(?:=|%3D)approval|swfrom(?:=|%3D)oa
```

例子：

```text
[Ding]张静提醒您审批他的录用申请
[Ding]黄楚提醒您审批他的费用报销对外付款综合审批单
贾金鹏提交的项目立项全流程（第一曲线）
... swfrom%3Doa ... dinghash%3Dapproval ...
```

路由要求：

- 不在 agent 前跳过。
- 配置了 OA runner 时，交给专用 OA agent；该 agent 注入 `dingtalk-oa-approval` skill。
- OA agent 最终记录的审阅动作只能是 `通过`、`拒绝`、`退回`。其中 `通过` 和 `拒绝` 执行审批动作；`退回` 不静默映射成 `拒绝`，而是把 `oa_remark` 作为审批单评论提交，提醒申请人补充材料或修改后再处理。
- 服务在既有 `reply_attempts` 审计记录中保存审批 URL、审批动作、审批留言、执行结果、Codex session 和工具事件。
- 不新增 OA 页面；从既有 attempt detail 查看处理过程。
- DWS 详情不完整时，OA agent 可使用已授权的钉钉 OA API 补读详情，但不得记录 token、AppKey、AppSecret、cookie、OAuth code 或签名 URL。

### 自动同步通知

当前行为：`skip_before_agent`。

当前代码识别的整条消息模板：

```regex
^(AI\s*)?自动同步(完成|成功|失败)([:：]\S.*)?$
|^已同步到(知识库|文档|项目)([:：]\S.*)$
```

例子：

```text
自动同步完成：xxx
AI 自动同步成功：xxx
已同步到知识库：xxx
```

保护条件：

- 不包含审批/OA 链接特征。
- 不包含普通外部链接。
- 链接外没有 `?` 或 `？`。

### 文件状态通知

当前行为：`skip_before_agent`。

当前代码识别的整条消息模板：

```regex
^(文件|文档)[^\n，,。；;？?]{0,40}(已上传|已更新|上传完成|更新完成)([:：]\S.*)?$
|^已更新文档([:：]\S.*)?$
```

例子：

```text
文件已上传：xxx.pdf
文件已更新：xxx
文档已更新：xxx
已更新文档：xxx
```

不会跳过的例子：

```text
文件已更新，麻烦明哥看一下
已更新文档，帮忙给 comments
```

说明：

- 代码只匹配整条纯通知。
- 如果状态后面又接了逗号、句号、分号和后续请求，就不会命中。

### 通用项目或流程状态通知

当前行为：`skip_before_agent`。

当前代码识别的整条消息模板：

```regex
^(项目立项|流程|审批)[^\n，,。；;？?]{0,40}(已提交|已通过|被退回|已退回|已撤回|已流转)([:：]\S.*)?$
```

例子：

```text
项目立项已提交
流程已流转
审批已通过
审批被退回
```

保护条件：

- 如果包含审批/OA 链接特征，不跳过，进入 agent。
- 如果包含普通外部链接，不跳过，进入 agent。
- 如果带问题符号，不跳过，进入 agent。

## 由 Agent 处理的消息类型

下面这些类型故意不由代码硬跳过。

### 群聊 @Alex 候选顺序

当前行为：`agent_review`。

群聊里有多条 @Alex 候选消息时，worker 按消息创建时间排序后处理最新一条，而不是依赖钉钉接口返回顺序。

说明：

- 钉钉近期消息接口可能按倒序返回。
- 未读尾部可能只包含后续文件、图片或状态消息。
- 如果同一轮里既有较早的 @Alex 问题，又有后续的 @Alex 审阅请求，应该优先处理时间上最新、仍未被 Alex 正式回复的请求。

### 自动处理回执

当前行为：由消费者在读取并固定本轮上下文后发送；不作为候选，不进入 agent 上下文。

系统检测到需要回复时会先发：

```text
稍等，我看看。
```

说明：

- 这只是处理回执，不代表 Alex 已经回复了业务问题。
- producer 只负责发现消息并入队，不发送这条回执。
- consumer 先读取上下文并构造 agent 输入，再发送这条回执，然后调用 agent。
- 候选过滤时不能把它当成 Alex 的最新人工回复。
- 构造 agent 上下文时也应过滤掉它，避免 agent 误判“已处理完”。

### 消费者中断后的任务恢复

当前行为：超过 30 分钟仍停留在 `processing` 的任务会自动退回 `pending`，由消费者重新领取。

说明：

- 消费者被重启或子进程异常退出时，任务可能已经领取但没有写入最终结果。
- 这类任务不应该永久停在处理中，否则会造成 @Alex 消息漏处理。
- 30 分钟阈值用于避免把仍在正常长时间处理的任务重复领取。

### 普通外部链接

当前行为：`agent_review`。

例子：

```text
@明哥 https://example.com/a
https://github.com/alchaincyf/darwin-skill @Alex Chen 这个达尔文 skill 挺有意思
这个链接里的方案怎么看？ https://example.com/a
```

原因：

- 普通外链可能是需要审阅的材料，也可能只是分享。它是否需要回复取决于文字和上下文。

可用于“标记含外链”的候选正则：

```regex
https?://(?![^\s)]*dingtalk\.com)\S+
```

建议用法：

- 只用于标记消息含普通外链。
- 不用于跳过 agent。

### DING 审批提醒

当前行为：`oa_agent_review` when the OA runner is configured for `[Ding]...提醒您审批...`; otherwise `agent_review`。

例子：

```text
[Ding]张静提醒您审批他的录用申请
[Ding]于海龙提醒您审批他的云资源费用&服务器使用审批
[Ding]明哥 房租已经过期，求审批
```

候选结构正则：

```regex
^\[Ding\].{0,80}(审批|求审批|请.*审批|催办)
```

当前代码只硬路由稳定的 `提醒您审批` 系统文案；更宽泛的 `求审批`、`催办` 仍应保留为 agent 判断或后续候选规则。

建议用法：

- 配置了 OA runner 时，稳定的审批提醒交给专用 OA agent。
- 不在 agent 前跳过。

### 没有钉钉链接的审批/OA 文本

当前行为：`agent_review`。

例子：

```text
请明哥审批
这个录用申请麻烦看一下
费用报销对外付款综合审批单需要处理
```

只建议用于检测的候选正则：

```regex
(审批|催办|提交).{0,30}(申请|单|流程|全流程|报销|合同|录用|晋升|立项)
```

建议用法：

- 路由到 agent，并套用 OA 审阅原则。
- 不作为 `skip_before_agent` 规则，因为这是业务含义，不是稳定传输结构。

### 现实动作请求

当前行为：`agent_review`，多数情况下应该 `handoff_to_human`。

例子：

```text
辛苦明姐去明哥家里取一下身份证，拿到后我尽快去银行办理 @Alex Chen
候选人在外出差，需要临时找个面试的位置，预计晚5分钟，我稍后呼叫两位
明哥你进下会议
```

只建议用于高风险检测的候选正则：

```regex
(进会|参加会议|接电话|呼叫|到现场|取.*身份证|身份证原件|开户|放款|审批通过后办理)
```

建议用法：

- 不自动发送状态回复。
- 保持在 prompt/agent 语义判断中，或作为强制 `handoff_to_human` 的高风险检测。

## 尚未实现的候选规则

下面这些类型需要先积累 2-3 条真实样本，再决定是否写代码规则。

### 链接或附件已补充

已见过的例子：

```text
@明哥 链接已放
[dingtalk://dingtalkclient/action/open_mini_app?...]
```

候选正则：

```regex
(@[^\s]+.*)?(链接|材料|附件).{0,8}(已放|已发|已上传|补上).*
```

建议路由：

- 如果这是回复 Alex 之前索要材料，可以跳过或交给 agent 根据上下文判断。
- 如果这是新的审阅请求，agent 必须读取链接或材料。
- 不适合无条件代码跳过。

## 汇总表

| 消息类型 | 当前路由 | 是否已有代码规则 | 建议 |
| --- | --- | --- | --- |
| 非文本消息类型 | agent 前跳过 | 是 | 保留 |
| `[文件]`、`[图片]`、`[视频]`、`[日程]` | agent 前跳过 | 是 | 保留 |
| `[Ding]` 审批提醒 | 进入 agent | 是，通过移出跳过前缀实现 | 保留 |
| 只有钉钉内部链接 | agent 前跳过 | 是 | 保留，除非用户明确要求查看链接 |
| 普通外部链接 | 进入 agent | 是，通过排除普通外链实现 | 保留 |
| 结构化钉钉卡片 | agent 前跳过 | 是 | 保留 |
| 结构化审批/OA 卡片 | 进入 agent | 是 | 保留 |
| 没有链接的 OA/审批文本 | 进入 agent | 没有硬路由，只有 prompt 规则 | 可以考虑 detector，但不要跳过 |
| 自动同步通知 | agent 前跳过 | 是 | 保留窄模板 |
| 文件状态通知 | agent 前跳过 | 是 | 保留窄模板 |
| 项目/流程状态通知 | agent 前跳过 | 是 | 保留窄模板 |
| 现实动作请求 | agent 判断 / handoff | 只有 prompt 规则 | 保持语义判断 |
