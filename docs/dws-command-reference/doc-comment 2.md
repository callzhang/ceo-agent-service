# dws doc-comment Commands

文档评论（子 server，由 doc 产品通过 toolOverrides.serverOverride 调用）。

Commands in this file: 4

## Command Index

| CLI | Canonical path | Risk |
| --- | --- | --- |
| [`dws doc-comment create_comment`](#dws-doc-comment-createcomment) | `doc-comment.create_comment` | `mutating-review-first` |
| [`dws doc-comment create_inline_comment`](#dws-doc-comment-createinlinecomment) | `doc-comment.create_inline_comment` | `mutating-review-first` |
| [`dws doc-comment list_comments`](#dws-doc-comment-listcomments) | `doc-comment.list_comments` | `mutating-review-first` |
| [`dws doc-comment reply_comment`](#dws-doc-comment-replycomment) | `doc-comment.reply_comment` | `mutating-review-first` |

## Group Summary

| Group | Commands |
| --- | ---: |
| `-` | 4 |

## dws doc-comment create_comment

- Canonical path: `doc-comment.create_comment`
- Product: `doc-comment`
- Group: `-`
- Subcommand: `create_comment`
- Title: 创建全文评论
- Description: 创建文档评论
- Required top-level parameters: `nodeId`, `content`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `content` | yes | string | 评论内容 | 评论的文字内容，纯文本格式。必填。 |
| `mentionedUserIds` |  | array; items=string | 被 @ 的用户 uid 列表 | 被 @ 的用户 uid 列表，填写后评论内容中会插入 @mention 节点并发送通知。可通过「钉钉通讯录」相关 MCP tool（如 `search_user_by_key_word`、`search_user_by_mobile`）检索用户 uid，或使用 `dingtalk-workspace-cli` 提供的相关 skill 来检索。 |
| `mentionedUserIds[]` |  | string |  |  |
| `nodeId` | yes | string | 文档标识 | 目标文档的标识，支持传入 URL 或 ID（dentryUuid）。必填。 |

### CLI flag overlay

- none

## dws doc-comment create_inline_comment

- Canonical path: `doc-comment.create_inline_comment`
- Product: `doc-comment`
- Group: `-`
- Subcommand: `create_inline_comment`
- Title: 创建划词评论
- Description: 创建文档行内评论
- Required top-level parameters: `nodeId`, `blockId`, `start`, `end`, `content`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `blockId` | yes | string | 块 ID | 评论标记所在的块 ID。必填。 |
| `content` | yes | string | 评论内容 | 评论的文字内容，纯文本格式。必填。 |
| `end` | yes | number | 结束位置 | 评论标记在块内文本中的结束字符偏移量，必须大于 start。必填。 |
| `mentionedUserIds` |  | array; items=string | 被 @ 的用户 uid 列表 | 填写后，评论内容中会插入 @mention 节点，并向对应用户发送通知。 |
| `mentionedUserIds[]` |  | string |  |  |
| `nodeId` | yes | string | 文档标识 | 目标文档的标识，支持传入 URL 或 ID（dentryUuid）。必填。 |
| `selectedText` |  | string | 划词选中的原文 | 可选填。填写后，评论列表中会展示「引用原文：xxx」。建议传入选中文本的完整内容或前100个字符。 |
| `start` | yes | number | 起始位置 | 评论标记在块内文本中的起始字符偏移量（从 0 开始）。必填。 |

### CLI flag overlay

- none

## dws doc-comment list_comments

- Canonical path: `doc-comment.list_comments`
- Product: `doc-comment`
- Group: `-`
- Subcommand: `list_comments`
- Title: 获取文档评论列表
- Description: 查询文档评论列表
- Required top-level parameters: `nodeId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `commentType` |  | string | 评论类型 | 按评论类型过滤。可选值：global（全文评论）、inline（划词评论）。不传返回所有评论。 |
| `nextToken` |  | string | 分页游标 | 分页游标，从上一次请求的返回结果中获取 nextToken。首次请求不传。 |
| `nodeId` | yes | string | 文档标识 | 目标文档的标识，支持传入 URL 或 ID。必填。 |
| `pageSize` |  | number | 每页数量 | 每页返回的评论数量，默认 50，最大 50。 |
| `resolveStatus` |  | string | 解决状态 | 按解决状态过滤。可选值：resolved（已解决）、unresolved（未解决）。不传返回所有评论。 |

### CLI flag overlay

- none

## dws doc-comment reply_comment

- Canonical path: `doc-comment.reply_comment`
- Product: `doc-comment`
- Group: `-`
- Subcommand: `reply_comment`
- Title: 回复评论
- Description: 回复文档评论
- Required top-level parameters: `nodeId`, `replyCommentKey`, `content`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `content` | yes | string | 回复内容 | 回复的文字内容，纯文本格式。表情回复时填写表情名称。必填。 |
| `emoji` |  | boolean | 是否为表情贴图回复 | 设为 true 时，本次回复将作为表情贴图回复，categoryFeature 会自动从被回复评论继承。默认 false。 |
| `mentionedUserIds` |  | array; items=string | 被 @ 的用户 uid 列表 | 填写后，评论内容中会插入 @mention 节点，并向对应用户发送通知。 |
| `mentionedUserIds[]` |  | string |  |  |
| `nodeId` | yes | string | 文档标识 | 目标文档的标识，支持传入 URL 或 ID（dentryUuid）。必填。 |
| `replyCommentKey` | yes | string | 被回复评论的 commentKey | 被回复评论的唯一标识，格式：{13位毫秒时间戳}{32位UUID}，共45位。可从 create_comment 或 list_comments 返回结果中获取。必填。 |

### CLI flag overlay

- none
