# dws mail Commands

邮箱 / 邮件收发

Commands in this file: 22

## Command Index

| CLI | Canonical path | Risk |
| --- | --- | --- |
| [`dws mail batch_delete_message`](#dws-mail-batchdeletemessage) | `mail.batch_delete_message` | `mutating-review-first` |
| [`dws mail batch_move_message`](#dws-mail-batchmovemessage) | `mail.batch_move_message` | `unknown-review-before-use` |
| [`dws mail create_download_session`](#dws-mail-createdownloadsession) | `mail.create_download_session` | `mutating-review-first` |
| [`dws mail create_draft`](#dws-mail-createdraft) | `mail.create_draft` | `mutating-review-first` |
| [`dws mail create_forward_draft`](#dws-mail-createforwarddraft) | `mail.create_forward_draft` | `mutating-review-first` |
| [`dws mail create_reply_draft`](#dws-mail-createreplydraft) | `mail.create_reply_draft` | `mutating-review-first` |
| [`dws mail create_replyall_draft`](#dws-mail-createreplyalldraft) | `mail.create_replyall_draft` | `mutating-review-first` |
| [`dws mail create_upload_session`](#dws-mail-createuploadsession) | `mail.create_upload_session` | `mutating-review-first` |
| [`dws mail forward_message`](#dws-mail-forwardmessage) | `mail.forward_message` | `mutating-review-first` |
| [`dws mail message get`](#dws-mail-message-get) | `mail.get_email_by_message_id` | `read-only` |
| [`dws mail get_thread`](#dws-mail-getthread) | `mail.get_thread` | `read-only` |
| [`dws mail list_folders`](#dws-mail-listfolders) | `mail.list_folders` | `read-only` |
| [`dws mail list_mail_attachments`](#dws-mail-listmailattachments) | `mail.list_mail_attachments` | `read-only` |
| [`dws mail list_tags`](#dws-mail-listtags) | `mail.list_tags` | `read-only` |
| [`dws mail mailbox list`](#dws-mail-mailbox-list) | `mail.list_user_mailboxes` | `read-only` |
| [`dws mail reply_all`](#dws-mail-replyall) | `mail.reply_all` | `mutating-review-first` |
| [`dws mail reply_message`](#dws-mail-replymessage) | `mail.reply_message` | `mutating-review-first` |
| [`dws mail message search`](#dws-mail-message-search) | `mail.search_emails` | `read-only` |
| [`dws mail search_mail_users`](#dws-mail-searchmailusers) | `mail.search_mail_users` | `read-only` |
| [`dws mail send_draft`](#dws-mail-senddraft) | `mail.send_draft` | `mutating-review-first` |
| [`dws mail message send`](#dws-mail-message-send) | `mail.send_email` | `mutating-review-first` |
| [`dws mail update_draft`](#dws-mail-updatedraft) | `mail.update_draft` | `mutating-review-first` |

## Group Summary

| Group | Commands |
| --- | ---: |
| `-` | 18 |
| `mailbox` | 1 |
| `message` | 3 |

## dws mail batch_delete_message

- Canonical path: `mail.batch_delete_message`
- Product: `mail`
- Group: `-`
- Subcommand: `batch_delete_message`
- Title: 批量删除邮件
- Description: 批量删除邮件
- Required top-level parameters: `email`, `ids`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `deleteType` |  | number | deleteType | 删除类型：USER_DELETED(0) - 移动到已删除文件夹；ENTERPRISE_TRASH(1) - 移动到企业回收站；PERMANENT(2) - 永久删除 |
| `email` | yes | string | email | 邮箱地址 |
| `ids` | yes | array; items=string | ids | 邮件id列表 |
| `ids[]` |  | string |  |  |

### CLI flag overlay

- none

## dws mail batch_move_message

- Canonical path: `mail.batch_move_message`
- Product: `mail`
- Group: `-`
- Subcommand: `batch_move_message`
- Title: 批量移动邮件到指定文件夹
- Description: 批量移动邮件到指定文件夹
- Required top-level parameters: `email`, `ids`, `destinationFolderId`
- Sensitive flag from schema: `false`
- Mutation risk: `unknown-review-before-use`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `destinationFolderId` | yes | string | destinationFolderId | 目标文件夹 |
| `email` | yes | string | email | 邮箱地址 |
| `ids` | yes | array; items=string | ids | 邮件id列表 |
| `ids[]` |  | string |  |  |

### CLI flag overlay

- none

## dws mail create_download_session

- Canonical path: `mail.create_download_session`
- Product: `mail`
- Group: `-`
- Subcommand: `create_download_session`
- Title: 创建附件下载会话
- Description: 创建附件下载会话
- Required top-level parameters: `email`, `attachmentId`, `messageId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `attachmentId` | yes | string | attachmentId | 附件唯一标识 |
| `email` | yes | string | email | 用户的邮箱地址 |
| `messageId` | yes | string | messageId | 邮件的唯一标识id |

### CLI flag overlay

- none

## dws mail create_draft

- Canonical path: `mail.create_draft`
- Product: `mail`
- Group: `-`
- Subcommand: `create_draft`
- Title: 创建邮件草稿
- Description: 创建邮件草稿
- Required top-level parameters: `from`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `body` |  | string | body | markdown格式的邮件正文内容 |
| `ccRecipients` |  | array; items=string | ccRecipients | 抄送人Email地址列表 |
| `ccRecipients[]` |  | string |  |  |
| `from` | yes | string | from | 发信Email地址 |
| `subject` |  | string | subject | 邮件主题 |
| `toRecipients` |  | array; items=string | toRecipients | 收件人Email地址列表 |
| `toRecipients[]` |  | string |  |  |

### CLI flag overlay

- none

## dws mail create_forward_draft

- Canonical path: `mail.create_forward_draft`
- Product: `mail`
- Group: `-`
- Subcommand: `create_forward_draft`
- Title: 创建转发草稿
- Description: 创建转发草稿
- Required top-level parameters: `from`, `messageId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `body` |  | string | body | markdown格式的邮件正文内容 |
| `from` | yes | string | from | 发信Email地址 |
| `messageId` | yes | string | messageId | 转发的原始邮件id |
| `saveToSentItems` |  | boolean | saveToSentItems | 保存到发件箱 |
| `subject` |  | string | subject | 邮件主题 |
| `toRecipients` |  | array; items=string | toRecipients | 收件人Email地址列表 |
| `toRecipients[]` |  | string |  |  |

### CLI flag overlay

- none

## dws mail create_reply_draft

- Canonical path: `mail.create_reply_draft`
- Product: `mail`
- Group: `-`
- Subcommand: `create_reply_draft`
- Title: 创建回复草稿
- Description: 创建回复草稿
- Required top-level parameters: `from`, `messageId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `body` |  | string | body | markdown格式的邮件正文内容 |
| `from` | yes | string | from | 发信Email地址 |
| `messageId` | yes | string | messageId | 回复的原始邮件id |
| `saveToSentItems` |  | boolean | saveToSentItems | 保存到发件箱 |
| `subject` |  | string | subject | 邮件主题 |
| `to` |  | string | to | 收件人Email地址列表 |

### CLI flag overlay

- none

## dws mail create_replyall_draft

- Canonical path: `mail.create_replyall_draft`
- Product: `mail`
- Group: `-`
- Subcommand: `create_replyall_draft`
- Title: 创建回复全部草稿
- Description: 创建回复全部草稿
- Required top-level parameters: `from`, `messageId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `body` |  | string | body | markdown格式的邮件正文内容 |
| `from` | yes | string | from | 发信Email地址 |
| `messageId` | yes | string | messageId | 回复的原始邮件id |
| `saveToSentItems` |  | boolean | saveToSentItems | 保存到发件箱 |
| `subject` |  | string | subject | 邮件主题 |
| `toRecipients` |  | array; items=string | toRecipients | 收件人Email地址列表 |
| `toRecipients[]` |  | string |  |  |

### CLI flag overlay

- none

## dws mail create_upload_session

- Canonical path: `mail.create_upload_session`
- Product: `mail`
- Group: `-`
- Subcommand: `create_upload_session`
- Title: 创建附件上传会话
- Description: 为草稿邮件添加一个附件
- Required top-level parameters: `email`, `name`, `messageId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `contentId` |  | string | contentId | 附件cid |
| `email` | yes | string | email | 用户的邮箱地址 |
| `isInline` |  | boolean | isInline | 是否为内联附件 |
| `messageId` | yes | string | messageId | 邮件的唯一标识id |
| `name` | yes | string | name | 附件名称 |

### CLI flag overlay

- none

## dws mail forward_message

- Canonical path: `mail.forward_message`
- Product: `mail`
- Group: `-`
- Subcommand: `forward_message`
- Title: 转发邮件
- Description: 转发邮件
- Required top-level parameters: `from`, `toRecipients`, `subject`, `body`, `messageId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `body` | yes | string | body | markdown格式的邮件正文内容 |
| `from` | yes | string | from | 发件人email |
| `messageId` | yes | string | messageId | 要转发的原始邮件id |
| `saveToSentItems` |  | boolean | saveToSentItems | 保存到发件箱 |
| `subject` | yes | string | subject | 邮件主题 |
| `toRecipients` | yes | array; items=string | toRecipients | 收件人email列表 |
| `toRecipients[]` |  | string |  |  |

### CLI flag overlay

- none

## dws mail message get

- Canonical path: `mail.get_email_by_message_id`
- Product: `mail`
- Group: `message`
- Subcommand: `get`
- Title: 查询邮件的完整内容
- Description: 查看邮件完整内容
- Required top-level parameters: `email`, `messageId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `email` | yes | string | email | 邮件所属的邮箱地址 |
| `messageId` | yes | string | messageId | 邮件ID |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `email` | `--email` |  |  |  |  |
| `messageId` | `--id` |  |  |  |  |

## dws mail get_thread

- Canonical path: `mail.get_thread`
- Product: `mail`
- Group: `-`
- Subcommand: `get_thread`
- Title: 获取会话详情
- Description: 通过会话 ID 获取指定邮箱中的会话信息，默认仅返回会话基本信息，可通过 $select 参数指定需要额外返回的字段（如 $select=messages 返回会话内的邮件列表）
- Required top-level parameters: `email`, `conversationId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `conversationId` | yes | string | conversationId | 会话唯一标识 |
| `email` | yes | string | email | 会话所属的邮箱地址 |

### CLI flag overlay

- none

## dws mail list_folders

- Canonical path: `mail.list_folders`
- Product: `mail`
- Group: `-`
- Subcommand: `list_folders`
- Title: 列举邮件文件夹
- Description: 该工具支持列出指定邮箱的顶层文件夹或指定父文件夹下的所有子文件夹。 注意：该工具只会返回文件夹的 ID 和元信息（folderId为空则返回顶层文件夹，非空则返回指定父文件夹的子文件夹）
- Required top-level parameters: `email`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `email` | yes | string | email | 邮件所属的邮箱地址 |
| `folderId` |  | string | folderId | 文件夹唯一标识 |

### CLI flag overlay

- none

## dws mail list_mail_attachments

- Canonical path: `mail.list_mail_attachments`
- Product: `mail`
- Group: `-`
- Subcommand: `list_mail_attachments`
- Title: 列举邮件附件
- Description: 列举邮件附件
- Required top-level parameters: `messageId`, `email`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `email` | yes | string | email | 用户邮件地址 |
| `messageId` | yes | string | messageId | 邮件唯一标识 |

### CLI flag overlay

- none

## dws mail list_tags

- Canonical path: `mail.list_tags`
- Product: `mail`
- Group: `-`
- Subcommand: `list_tags`
- Title: 列举邮件标签
- Description: 该工具支持列出指定邮箱下的所有邮件标签。只会返回标签 的 ID 和元信息（名称、父标签、邮件数量、未读数量等。
- Required top-level parameters: `email`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `email` | yes | string | email | 用户的邮箱地址 |

### CLI flag overlay

- none

## dws mail mailbox list

- Canonical path: `mail.list_user_mailboxes`
- Product: `mail`
- Group: `mailbox`
- Subcommand: `list`
- Title: 查询当前用户可用邮箱
- Description: 查询可用邮箱地址
- Required top-level parameters: -
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

- none

### CLI flag overlay

- none

## dws mail reply_all

- Canonical path: `mail.reply_all`
- Product: `mail`
- Group: `-`
- Subcommand: `reply_all`
- Title: 回复全部
- Description: 回复全部
- Required top-level parameters: `from`, `toRecipients`, `subject`, `body`, `messageId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `body` | yes | string | body | markdown格式的邮件正文内容 |
| `from` | yes | string | from | 发件人email |
| `messageId` | yes | string | messageId | 要回复的原始邮件id |
| `saveToSentItems` |  | boolean | saveToSentItems | 保存到发件箱 |
| `subject` | yes | string | subject | 邮件主题 |
| `toRecipients` | yes | array; items=string | toRecipients | 收件人email列表 |
| `toRecipients[]` |  | string |  |  |

### CLI flag overlay

- none

## dws mail reply_message

- Canonical path: `mail.reply_message`
- Product: `mail`
- Group: `-`
- Subcommand: `reply_message`
- Title: 回复邮件
- Description: 回复邮件
- Required top-level parameters: `from`, `to`, `subject`, `body`, `messageId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `body` | yes | string | body | markdown格式的邮件正文内容 |
| `from` | yes | string | from | 发件人email |
| `messageId` | yes | string | messageId | 要回复的原始邮件id |
| `saveToSentItems` |  | boolean | saveToSentItems | 保存到发件箱 |
| `subject` | yes | string | subject | 邮件主题 |
| `to` | yes | string | to | 收件人email |

### CLI flag overlay

- none

## dws mail message search

- Canonical path: `mail.search_emails`
- Product: `mail`
- Group: `message`
- Subcommand: `search`
- Title: 使用类 KQL 查询表达式高效搜索邮件，支持灵活筛选、分页、排序与字段选择，仅返回邮件 ID 及元信息（不含正文）。
- Description: 搜索邮件 (KQL 语法)
- Required top-level parameters: `email`, `query`, `size`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `cursor` |  | string | cursor | 用于检索后续结果的分页游标。第一页时为空。 |
| `email` | yes | string | email | 搜索目标Email地址 |
| `query` | yes | string | query | 查询表达式用于筛选邮件，其语法类似于 KQL。  对于字符串类型的值，你可以选择以下两种方式之一： 使用双引号将字符串括起来（如果字符串本身包含双引号，则需使用两个连续的双引号进行转义）； 或者不使用双引号（此时字符串中不能包含空格）。  如果字符串本身已经用双引号括起，则必须再在外层加一层双引号，并对内部原有的双引号进行转义（即每个内部双引号替换为两个双引号）。  支持的字段包括： - date（日期） - size（大小） - tag（标签） - folderId（文件夹 ID）：常用文件夹 ID 如下： * 1：表示“已发送” * 2：表示“收件箱” * 3：表示“垃圾邮件” * 5：表示“草稿” * 6：表示“已删除” - isRead（是否已读） - hasAttachments（是否有附件） - subject（主题） - attachname（附件名称） - body（正文） - from（发件人） - to（收件人）  示例1： 查找 2025 年 1 月 1 日之后收到、且不在垃圾邮件或已删除文件夹中的邮件： date>2025-01-01T00:00:00Z AND (NOT folderId:3) AND (NOT folderId:6)  示例2： 查找发件人为 “alice” 的邮件，或者收件人为 “alice<a@b.com>” 且位于“已发送”文件夹中的邮件： (from:"alice") OR (to:"alice<a@b.com>" AND folderId:1)  示例3： 查找主题中包含 “test” 关键词的邮件，或者包含附件名含 “file” 关键词且发件人为 alice 的邮件： (subject:"test") OR (attachname:"file" AND from:alice<a@b.com>) |
| `size` | yes | string | size | 每次请求返回的最大结果数量（1-100）。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `cursor` | `--cursor` |  |  |  |  |
| `email` | `--email` |  |  |  |  |
| `query` | `--query` |  |  |  |  |
| `size` | `--size` |  | 20 |  |  |

## dws mail search_mail_users

- Canonical path: `mail.search_mail_users`
- Product: `mail`
- Group: `-`
- Subcommand: `search_mail_users`
- Title: 搜索账号
- Description: 搜索账号
- Required top-level parameters: `keyword`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `cursor` |  | string | cursor | cursor |
| `email` |  | string | email | email |
| `keyword` | yes | string | keyword | keyword |
| `size` |  | number | size | size |

### CLI flag overlay

- none

## dws mail send_draft

- Canonical path: `mail.send_draft`
- Product: `mail`
- Group: `-`
- Subcommand: `send_draft`
- Title: 发送邮件草稿
- Description: 发送草稿箱中的邮件
- Required top-level parameters: `messageId`, `email`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `email` | yes | string | email | 用户的邮箱地址 |
| `messageId` | yes | string | messageId | 邮件的唯一标识 |
| `saveToSentItems` |  | boolean | saveToSentItems | 是否将邮件保存到已发送文件夹中，默认为true |

### CLI flag overlay

- none

## dws mail message send

- Canonical path: `mail.send_email`
- Product: `mail`
- Group: `message`
- Subcommand: `send`
- Title: 发送邮件
- Description: 发送邮件
- Required top-level parameters: `from`, `toRecipients`, `subject`, `body`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `body` | yes | string | body | markdown格式的邮件正文内容 |
| `ccRecipients` |  | array; items=string | ccRecipients | 抄送人Email地址列表 |
| `ccRecipients[]` |  | string |  |  |
| `from` | yes | string | from | 发信Email地址 |
| `subject` | yes | string | subject | 邮件主题 |
| `toRecipients` | yes | array; items=string | toRecipients | 收件人Email地址列表 |
| `toRecipients[]` |  | string |  |  |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `body` | `--body` |  |  |  |  |
| `ccRecipients` | `--cc` | csv_to_array |  |  |  |
| `from` | `--from` |  |  |  |  |
| `subject` | `--subject` |  |  |  |  |
| `toRecipients` | `--to` | csv_to_array |  |  |  |

## dws mail update_draft

- Canonical path: `mail.update_draft`
- Product: `mail`
- Group: `-`
- Subcommand: `update_draft`
- Title: 更新邮件草稿
- Description: 更新邮件草稿
- Required top-level parameters: `from`, `id`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `body` |  | string | body | markdown格式的邮件正文内容 |
| `ccRecipients` |  | array; items=string | ccRecipients | 抄送人Email地址列表 |
| `ccRecipients[]` |  | string |  |  |
| `from` | yes | string | from | 发信Email地址 |
| `id` | yes | string | id | 待更新的邮件id |
| `subject` |  | string | subject | 邮件主题 |
| `toRecipients` |  | array; items=string | toRecipients | 收件人Email地址列表 |
| `toRecipients[]` |  | string |  |  |

### CLI flag overlay

- none

