# 钉钉消息路由规则

本文档列出 CEO 自动回复 worker 当前遇到的钉钉消息类型、应该如何路由，以及哪些规则已经由代码实现。

基本原则：代码规则只处理稳定的传输结构和卡片结构，不用宽泛关键词替代 agent 判断。只要消息含义依赖业务上下文，就应该交给 agent，并给 agent 正确的审阅要求。

## 路由结果

- `skip_before_agent`：不调用 agent，直接记录为 `no_reply/skipped`。
- `agent_review`：交给 agent，根据上下文判断是否回复、追问、跳过或交给本人。
- `handoff_or_material_check`：交给 agent，但回复前必须检查材料完整性，或交给本人处理。
- `candidate_rule`：候选规则，尚未实现；需要真实样本和测试后再落代码。

## 已由代码识别的规则

### 非文本钉钉消息类型

当前行为：`skip_before_agent`。

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

### 钉钉渲染出来的媒体或日程占位符

当前行为：`skip_before_agent`。

当前代码前缀：

```regex
^\[(文件|图片|视频|日程)\]
```

例子：

```text
[文件] product.md
[图片]
[视频]
[日程]
```

说明：

- `[Ding]` 已经不在前置跳过列表里。
- 普通外部图片 URL 不属于这个规则。

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

- 这类主要覆盖钉钉渲染的内部卡片，例如 AI 听记/闪记链接。
- 这个规则故意比“所有链接”窄。`https://example.com/a` 这类普通外链应该进入 agent。

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
@磊哥 [dingtalk://dingtalkclient/page/flash_minutes_detail?x=1](dingtalk://dingtalkclient/page/flash_minutes_detail?x=1)
[https://alidocs.dingtalk.com/i/u/dingdocSelectorV4/save?...](https://alidocs.dingtalk.com/i/u/dingdocSelectorV4/save?...)
![图片](@lQLPJwKtm28)
```

不会跳过的例子：

```text
这个链接里的方案怎么看？ https://example.com/a
@磊哥 https://example.com/a
https://github.com/alchaincyf/darwin-skill @Derek Zen 这个达尔文 skill 挺有意思
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

当前行为：`agent_review`，并要求按 OA 审阅原则处理。

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
- agent 必须阅读 `management/OA/钉钉审批审阅原则.md`。
- agent 不能替 Derek 执行审批、承诺审批，也不能在材料不全时给批准、退回或拒绝结论。
- 如果审批材料缺失，正确结果应该是说明材料缺口、要求补材料，或交给本人处理。

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
文件已更新，麻烦磊哥看一下
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

### 群聊 @Derek 候选顺序

当前行为：`agent_review`。

群聊里有多条 @Derek 候选消息时，worker 按消息创建时间排序后处理最新一条，而不是依赖钉钉接口返回顺序。

说明：

- 钉钉近期消息接口可能按倒序返回。
- 未读尾部可能只包含后续文件、图片或状态消息。
- 如果同一轮里既有较早的 @Derek 问题，又有后续的 @Derek 审阅请求，应该优先处理时间上最新、仍未被 Derek 正式回复的请求。

### 自动处理回执

当前行为：由消费者在读取并固定本轮上下文后发送；不作为候选，不进入 agent 上下文。

系统检测到需要回复时会先发：

```text
稍等，我看看。
```

说明：

- 这只是处理回执，不代表 Derek 已经回复了业务问题。
- producer 只负责发现消息并入队，不发送这条回执。
- consumer 先读取上下文并构造 agent 输入，再发送这条回执，然后调用 agent。
- 候选过滤时不能把它当成 Derek 的最新人工回复。
- 构造 agent 上下文时也应过滤掉它，避免 agent 误判“已处理完”。

### 消费者中断后的任务恢复

当前行为：超过 30 分钟仍停留在 `processing` 的任务会自动退回 `pending`，由消费者重新领取。

说明：

- 消费者被重启或子进程异常退出时，任务可能已经领取但没有写入最终结果。
- 这类任务不应该永久停在处理中，否则会造成 @Derek 消息漏处理。
- 30 分钟阈值用于避免把仍在正常长时间处理的任务重复领取。

### 普通外部链接

当前行为：`agent_review`。

例子：

```text
@磊哥 https://example.com/a
https://github.com/alchaincyf/darwin-skill @Derek Zen 这个达尔文 skill 挺有意思
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

当前行为：`agent_review`。

例子：

```text
[Ding]张静提醒您审批他的录用申请
[Ding]于海龙提醒您审批他的云资源费用&服务器使用审批
[Ding]磊哥 房租已经过期，求审批
```

候选结构正则：

```regex
^\[Ding\].{0,80}(审批|求审批|请.*审批|催办)
```

建议用法：

- 路由到 agent，并套用 OA 审阅原则。
- 不在 agent 前跳过。

### 没有钉钉链接的审批/OA 文本

当前行为：`agent_review`。

例子：

```text
请磊哥审批
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
辛苦明姐去磊哥家里取一下身份证，拿到后我尽快去银行办理 @Derek Zen
候选人在外出差，需要临时找个面试的位置，预计晚5分钟，我稍后呼叫两位
磊哥你进下会议
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
@磊哥 链接已放
[dingtalk://dingtalkclient/action/open_mini_app?...]
```

候选正则：

```regex
(@[^\s]+.*)?(链接|材料|附件).{0,8}(已放|已发|已上传|补上).*
```

建议路由：

- 如果这是回复 Derek 之前索要材料，可以跳过或交给 agent 根据上下文判断。
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
