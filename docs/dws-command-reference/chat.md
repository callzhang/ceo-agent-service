# dws chat Commands

IM 扩展命令（合并到 dws chat 命令树）

Commands in this file: 41

## Command Index

| CLI | Canonical path | Risk |
| --- | --- | --- |
| [`dws chat group.member-role add`](#dws-chat-groupmember-role-add) | `chat.add_custom_group_role` | `unknown-review-before-use` |
| [`dws chat message add-emoji`](#dws-chat-message-add-emoji) | `chat.add_emoji_reaction` | `unknown-review-before-use` |
| [`dws chat message add-text-emotion`](#dws-chat-message-add-text-emotion) | `chat.add_text_emotion` | `unknown-review-before-use` |
| [`dws chat message combine-forward`](#dws-chat-message-combine-forward) | `chat.combine_forward_messages` | `mutating-review-first` |
| [`dws chat message send-card`](#dws-chat-message-send-card) | `chat.create_and_send_card` | `mutating-review-first` |
| [`dws chat message create-text-emotion`](#dws-chat-message-create-text-emotion) | `chat.create_text_emotion` | `mutating-review-first` |
| [`dws chat group dismiss`](#dws-chat-group-dismiss) | `chat.dismiss_group` | `mutating-review-first` |
| [`dws chat message forward`](#dws-chat-message-forward) | `chat.forward_message` | `mutating-review-first` |
| [`dws chat group get-by-group-id`](#dws-chat-group-get-by-group-id) | `chat.get_conv_info_by_group_id` | `read-only` |
| [`dws chat group invite-url`](#dws-chat-group-invite-url) | `chat.get_group_invite_url` | `read-only` |
| [`dws chat get_resource_download_url`](#dws-chat-getresourcedownloadurl) | `chat.get_resource_download_url` | `read-with-local-output` |
| [`dws chat list-conversations`](#dws-chat-list-conversations) | `chat.list_conversations_by_category` | `read-only` |
| [`dws chat group.member-role list`](#dws-chat-groupmember-role-list) | `chat.list_custom_group_roles` | `read-only` |
| [`dws chat list_ding_messages`](#dws-chat-listdingmessages) | `chat.list_ding_messages` | `read-only` |
| [`dws chat list_ding_receiver_status`](#dws-chat-listdingreceiverstatus) | `chat.list_ding_receiver_status` | `read-only` |
| [`dws chat message list-by-ids`](#dws-chat-message-list-by-ids) | `chat.list_messages_by_ids` | `read-only` |
| [`dws chat list_owned_or_admin_groups`](#dws-chat-listownedoradmingroups) | `chat.list_owned_or_admin_groups` | `read-only` |
| [`dws chat list-categories`](#dws-chat-list-categories) | `chat.list_user_define_conv_categories` | `read-only` |
| [`dws chat group.member-role query-user`](#dws-chat-groupmember-role-query-user) | `chat.query_custom_user_roles` | `read-only` |
| [`dws chat message query-send-status`](#dws-chat-message-query-send-status) | `chat.query_message_send_status` | `mutating-review-first` |
| [`dws chat message query-read-status`](#dws-chat-message-query-read-status) | `chat.query_msg_read_status` | `read-only` |
| [`dws chat group quit`](#dws-chat-group-quit) | `chat.quit_group` | `unknown-review-before-use` |
| [`dws chat message recall`](#dws-chat-message-recall) | `chat.recall_message` | `mutating-review-first` |
| [`dws chat group.member-role remove`](#dws-chat-groupmember-role-remove) | `chat.remove_custom_group_role` | `mutating-review-first` |
| [`dws chat group.member-role remove-user`](#dws-chat-groupmember-role-remove-user) | `chat.remove_custom_user_roles` | `mutating-review-first` |
| [`dws chat message remove-emoji`](#dws-chat-message-remove-emoji) | `chat.remove_emoji_reaction` | `mutating-review-first` |
| [`dws chat message remove-text-emotion`](#dws-chat-message-remove-text-emotion) | `chat.remove_text_emotion` | `mutating-review-first` |
| [`dws chat search`](#dws-chat-search) | `chat.search_groups` | `read-only` |
| [`dws chat message search-advanced`](#dws-chat-message-search-advanced) | `chat.search_messages` | `read-only` |
| [`dws chat group.member-role set-user`](#dws-chat-groupmember-role-set-user) | `chat.set_custom_user_roles` | `unknown-review-before-use` |
| [`dws chat group-mute-member`](#dws-chat-group-mute-member) | `chat.set_group_member_mute_list` | `read-only` |
| [`dws chat group-mute`](#dws-chat-group-mute) | `chat.set_group_mute` | `unknown-review-before-use` |
| [`dws chat set-top`](#dws-chat-set-top) | `chat.set_top_conversation` | `unknown-review-before-use` |
| [`dws chat group transfer-owner`](#dws-chat-group-transfer-owner) | `chat.transfer_group_owner` | `unknown-review-before-use` |
| [`dws chat group set-admin`](#dws-chat-group-set-admin) | `chat.update_conv_member_roles` | `mutating-review-first` |
| [`dws chat group.member-role update`](#dws-chat-groupmember-role-update) | `chat.update_custom_group_role` | `mutating-review-first` |
| [`dws chat group update-icon`](#dws-chat-group-update-icon) | `chat.update_group_icon` | `mutating-review-first` |
| [`dws chat group update-settings`](#dws-chat-group-update-settings) | `chat.update_group_settings` | `mutating-review-first` |
| [`dws chat mute`](#dws-chat-mute) | `chat.update_notification_off` | `mutating-review-first` |
| [`dws chat group set-history`](#dws-chat-group-set-history) | `chat.update_show_history_msg_option` | `mutating-review-first` |
| [`dws chat message update-card`](#dws-chat-message-update-card) | `chat.update_streaming_card` | `mutating-review-first` |

## Group Summary

| Group | Commands |
| --- | ---: |
| `-` | 11 |
| `group` | 9 |
| `group.member-role` | 7 |
| `message` | 14 |

## dws chat group.member-role add

- Canonical path: `chat.add_custom_group_role`
- Product: `chat`
- Group: `group.member-role`
- Subcommand: `add`
- Title: 添加群身份
- Description: 新增群自定义角色
- Required top-level parameters: `openConversationId`, `name`
- Sensitive flag from schema: `false`
- Mutation risk: `unknown-review-before-use`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `name` | yes | string | name | name |
| `openConversationId` | yes | string | openConversationId | openConversationId |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `name` | `--name` |  |  |  |  |
| `openConversationId` | `--group` |  |  |  |  |

## dws chat message add-emoji

- Canonical path: `chat.add_emoji_reaction`
- Product: `chat`
- Group: `message`
- Subcommand: `add-emoji`
- Title: 消息添加 emoji 回应
- Description: 给消息添加表情回复
- Required top-level parameters: `emojiName`, `openConversationId`, `openMsgId`
- Sensitive flag from schema: `false`
- Mutation risk: `unknown-review-before-use`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `emojiName` | yes | string | emojiName | emojiName |
| `openConversationId` | yes | string | openConversationId | openConversationId |
| `openMsgId` | yes | string | openMsgId | openMsgId |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `emojiName` | `--emoji` |  |  |  |  |
| `openConversationId` | `--group` |  |  |  |  |
| `openMsgId` | `--msg-id` |  |  |  |  |

## dws chat message add-text-emotion

- Canonical path: `chat.add_text_emotion`
- Product: `chat`
- Group: `message`
- Subcommand: `add-text-emotion`
- Title: 添加文字表情
- Description: 添加文字表情
- Required top-level parameters: `emotionName`, `text`, `openConversationId`, `openMsgId`, `emotionId`, `backgroundId`
- Sensitive flag from schema: `false`
- Mutation risk: `unknown-review-before-use`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `backgroundId` | yes | string | backgroundId | backgroundId |
| `emotionId` | yes | string | emotionId | emotionId |
| `emotionName` | yes | string | emotionName | emotionName |
| `openConversationId` | yes | string | openConversationId | openConversationId |
| `openMsgId` | yes | string | openMsgId | openMsgId |
| `text` | yes | string | text | text |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `backgroundId` | `--background-id` |  |  |  |  |
| `emotionId` | `--emotion-id` |  |  |  |  |
| `emotionName` | `--emotion-name` |  |  |  |  |
| `openConversationId` | `--group` |  |  |  |  |
| `openMsgId` | `--msg-id` |  |  |  |  |
| `text` | `--text` |  |  |  |  |

## dws chat message combine-forward

- Canonical path: `chat.combine_forward_messages`
- Product: `chat`
- Group: `message`
- Subcommand: `combine-forward`
- Title: 合并转发消息
- Description: 将一组消息合并转发到目标会话（合并转发卡片）。
- Required top-level parameters: `srcOpenCid`, `srcOpenMessageIds`, `destOpenCid`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `destOpenCid` | yes | string | destOpenCid | destOpenCid |
| `srcOpenCid` | yes | string | srcOpenCid | srcOpenCid |
| `srcOpenMessageIds` | yes | array; items=string | srcOpenMessageIds | srcOpenMessageIds |
| `srcOpenMessageIds[]` |  | string |  |  |
| `uuid` |  | string | uuid | uuid |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `destOpenCid` | `--dest-conversation-id` |  |  |  |  |
| `srcOpenCid` | `--src-conversation-id` |  |  |  |  |
| `srcOpenMessageIds` | `--msg-ids` | csv_to_array |  |  |  |
| `uuid` | `--uuid` |  |  |  |  |

## dws chat message send-card

- Canonical path: `chat.create_and_send_card`
- Product: `chat`
- Group: `message`
- Subcommand: `send-card`
- Title: create_and_send_card
- Description: 创建并发送卡片
- Required top-level parameters: `msgContent`, `openConversationId`, `receiverOpenDingTalkId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `msgContent` | yes | string | msgContent | msgContent |
| `openConversationId` | yes | string | openConversationId | openConversationId |
| `receiverOpenDingTalkId` | yes | string | receiverOpenDingTalkId | receiverOpenDingTalkId |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `cardData` | `--card-data` |  |  |  |  |
| `cardTemplateId` | `--card-template-id` |  |  |  |  |
| `openConversationId` | `--group` |  |  |  |  |
| `openDingTalkId` | `--user` |  |  |  |  |

## dws chat message create-text-emotion

- Canonical path: `chat.create_text_emotion`
- Product: `chat`
- Group: `message`
- Subcommand: `create-text-emotion`
- Title: 创建文字表情
- Description: 创建文字表情
- Required top-level parameters: `emotionName`, `text`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `emotionName` | yes | string | emotionName | emotionName |
| `text` | yes | string | text | text |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `backgroundId` | `--background-id` |  |  |  |  |
| `emotionName` | `--emotion-name` |  |  |  |  |
| `text` | `--text` |  |  |  |  |

## dws chat group dismiss

- Canonical path: `chat.dismiss_group`
- Product: `chat`
- Group: `group`
- Subcommand: `dismiss`
- Title: 解散群聊
- Description: 解散群（不可恢复，仅群主可用）。
- Required top-level parameters: `openConversationId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `openConversationId` | yes | string | openConversationId | openConversationId |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `openConversationId` | `--group` |  |  |  |  |

## dws chat message forward

- Canonical path: `chat.forward_message`
- Product: `chat`
- Group: `message`
- Subcommand: `forward`
- Title: 单条消息转发
- Description: 转发消息
- Required top-level parameters: `srcOpenCid`, `srcOpenMessageId`, `destOpenCid`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `destOpenCid` | yes | string | destOpenCid | destOpenCid |
| `srcOpenCid` | yes | string | srcOpenCid | srcOpenCid |
| `srcOpenMessageId` | yes | string | srcOpenMessageId | srcOpenMessageId |
| `uuid` |  | string | uuid | uuid |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `destOpenCid` | `--dest-conversation-id` |  |  |  |  |
| `srcOpenCid` | `--src-conversation-id` |  |  |  |  |
| `srcOpenMessageId` | `--msg-id` |  |  |  |  |

## dws chat group get-by-group-id

- Canonical path: `chat.get_conv_info_by_group_id`
- Product: `chat`
- Group: `group`
- Subcommand: `get-by-group-id`
- Title: 根据群号获取群聊信息
- Description: 按内部 groupId 获取会话信息
- Required top-level parameters: `groupId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `groupId` | yes | number | groupId | groupId |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `groupId` | `--group-id` |  |  |  |  |

## dws chat group invite-url

- Canonical path: `chat.get_group_invite_url`
- Product: `chat`
- Group: `group`
- Subcommand: `invite-url`
- Title: 获取群邀请链接
- Description: 获取群邀请链接
- Required top-level parameters: `openConversationId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `expiresSeconds` |  | number | expiresSeconds | expiresSeconds |
| `openConversationId` | yes | string | openConversationId | openConversationId |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `expiresSeconds` | `--expires-seconds` |  |  |  |  |
| `openConversationId` | `--group` |  |  |  |  |

## dws chat get_resource_download_url

- Canonical path: `chat.get_resource_download_url`
- Product: `chat`
- Group: `-`
- Subcommand: `get_resource_download_url`
- Title: 获取资源下载URL
- Description: 获取资源下载URL
- Required top-level parameters: `openConversationId`, `openMessageId`, `resourceId`, `resourceType`
- Sensitive flag from schema: `false`
- Mutation risk: `read-with-local-output`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `openConversationId` | yes | string | openConversationId | openConversationId |
| `openMessageId` | yes | string | openMessageId | openMessageId |
| `resourceId` | yes | string | resourceId | resourceId |
| `resourceType` | yes | string | resourceType | resourceType |

### CLI flag overlay

- none

## dws chat list-conversations

- Canonical path: `chat.list_conversations_by_category`
- Product: `chat`
- Group: `-`
- Subcommand: `list-conversations`
- Title: list_conversations_by_category
- Description: 按分类拉取会话列表
- Required top-level parameters: `categoryId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `categoryId` | yes | string | categoryId | categoryId |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `categoryId` | `--category-id` |  |  |  |  |

## dws chat group.member-role list

- Canonical path: `chat.list_custom_group_roles`
- Product: `chat`
- Group: `group.member-role`
- Subcommand: `list`
- Title: 拉取会话的群身份
- Description: 列出群自定义角色
- Required top-level parameters: `openConversationId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `openConversationId` | yes | string | openConversationId | openConversationId |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `openConversationId` | `--group` |  |  |  |  |

## dws chat list_ding_messages

- Canonical path: `chat.list_ding_messages`
- Product: `chat`
- Group: `-`
- Subcommand: `list_ding_messages`
- Title: 查询历史DING消息
- Description: 查询历史DING消息
- Required top-level parameters: `type`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `cursor` |  | number | cursor | cursor |
| `type` | yes | string | type | type |

### CLI flag overlay

- none

## dws chat list_ding_receiver_status

- Canonical path: `chat.list_ding_receiver_status`
- Product: `chat`
- Group: `-`
- Subcommand: `list_ding_receiver_status`
- Title: 查看DING接收状态
- Description: 查看DING接收状态
- Required top-level parameters: `openDingId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `openDingId` | yes | string | openDingId | openDingId |

### CLI flag overlay

- none

## dws chat message list-by-ids

- Canonical path: `chat.list_messages_by_ids`
- Product: `chat`
- Group: `message`
- Subcommand: `list-by-ids`
- Title: 批量查消息
- Description: 按消息 ID 列表批量获取消息
- Required top-level parameters: `openMsgIds`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `openMsgIds` | yes | array; items=string | openMsgIds | openMsgIds |
| `openMsgIds[]` |  | string |  |  |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `openMsgIds` | `--msg-ids` | csv_to_array |  |  |  |

## dws chat list_owned_or_admin_groups

- Canonical path: `chat.list_owned_or_admin_groups`
- Product: `chat`
- Group: `-`
- Subcommand: `list_owned_or_admin_groups`
- Title: 拉取我创建/管理的群
- Description: 拉取我创建/管理的群
- Required top-level parameters: `roleFilter`, `limit`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `limit` | yes | number | limit | limit |
| `roleFilter` | yes | string | roleFilter | roleFilter |

### CLI flag overlay

- none

## dws chat list-categories

- Canonical path: `chat.list_user_define_conv_categories`
- Product: `chat`
- Group: `-`
- Subcommand: `list-categories`
- Title: 获取用户自定义会话分组
- Description: 列出用户自定义会话分类
- Required top-level parameters: -
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

- none

### CLI flag overlay

- none

## dws chat group.member-role query-user

- Canonical path: `chat.query_custom_user_roles`
- Product: `chat`
- Group: `group.member-role`
- Subcommand: `query-user`
- Title: 查询群成员的群身份
- Description: 查询成员自定义角色
- Required top-level parameters: `openConversationId`, `openDingTalkId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `openConversationId` | yes | string | openConversationId | openConversationId |
| `openDingTalkId` | yes | string | openDingTalkId | openDingTalkId |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `openConversationId` | `--group` |  |  |  |  |
| `openDingTalkId` | `--user` |  |  |  |  |

## dws chat message query-send-status

- Canonical path: `chat.query_message_send_status`
- Product: `chat`
- Group: `message`
- Subcommand: `query-send-status`
- Title: 查询以当前用户的身份发送的消息的发送状态
- Description: 查询消息发送状态（按 openTaskId）
- Required top-level parameters: `openTaskId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `openTaskId` | yes | string | openTaskId | openTaskId |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `openTaskId` | `--open-task-id` |  |  |  |  |

## dws chat message query-read-status

- Canonical path: `chat.query_msg_read_status`
- Product: `chat`
- Group: `message`
- Subcommand: `query-read-status`
- Title: 查询消息的已读/未读状态
- Description: 查询消息已读状态
- Required top-level parameters: `openConversationId`, `openMessageId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `openConversationId` | yes | string | openConversationId | openConversationId |
| `openMessageId` | yes | string | openMessageId | openMessageId |
| `targetOpenDingTalkIds` |  | array; items=string | targetOpenDingTalkIds | targetOpenDingTalkIds |
| `targetOpenDingTalkIds[]` |  | string |  |  |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `openConversationId` | `--group` |  |  |  |  |
| `openMessageId` | `--msg-id` |  |  |  |  |

## dws chat group quit

- Canonical path: `chat.quit_group`
- Product: `chat`
- Group: `group`
- Subcommand: `quit`
- Title: 用户退群
- Description: 退出群聊
- Required top-level parameters: `openConversationId`
- Sensitive flag from schema: `false`
- Mutation risk: `unknown-review-before-use`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `openConversationId` | yes | string | openConversationId | openConversationId |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `openConversationId` | `--group` |  |  |  |  |

## dws chat message recall

- Canonical path: `chat.recall_message`
- Product: `chat`
- Group: `message`
- Subcommand: `recall`
- Title: 撤回用户发送的消息
- Description: 撤回单条消息
- Required top-level parameters: `openConversationId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `openConversationId` | yes | string | openConversationId | openConversationId |
| `openMessageId` |  | string | openMessageId | openMessageId |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `openConversationId` | `--group` |  |  |  |  |
| `openMessageId` | `--msg-id` |  |  |  |  |

## dws chat group.member-role remove

- Canonical path: `chat.remove_custom_group_role`
- Product: `chat`
- Group: `group.member-role`
- Subcommand: `remove`
- Title: 删除群身份
- Description: 删除群自定义角色
- Required top-level parameters: `openConversationId`, `openRoleId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `openConversationId` | yes | string | openConversationId | openConversationId |
| `openRoleId` | yes | string | openRoleId | openRoleId |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `openConversationId` | `--group` |  |  |  |  |
| `openRoleId` | `--role-id` |  |  |  |  |

## dws chat group.member-role remove-user

- Canonical path: `chat.remove_custom_user_roles`
- Product: `chat`
- Group: `group.member-role`
- Subcommand: `remove-user`
- Title: 移除用户的群身份
- Description: 移除成员自定义角色
- Required top-level parameters: `openConversationId`, `openDingTalkId`, `openRoleIds`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `openConversationId` | yes | string | openConversationId | openConversationId |
| `openDingTalkId` | yes | string | openDingTalkId | openDingTalkId |
| `openRoleIds` | yes | array; items=string | openRoleIds | openRoleIds |
| `openRoleIds[]` |  | string |  |  |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `openConversationId` | `--group` |  |  |  |  |
| `openDingTalkId` | `--user` |  |  |  |  |
| `openRoleIds` | `--role-ids` | csv_to_array |  |  |  |

## dws chat message remove-emoji

- Canonical path: `chat.remove_emoji_reaction`
- Product: `chat`
- Group: `message`
- Subcommand: `remove-emoji`
- Title: 消息删除emoji回应
- Description: 移除消息表情回复
- Required top-level parameters: `emojiName`, `openConversationId`, `openMsgId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `emojiName` | yes | string | emojiName | emojiName |
| `openConversationId` | yes | string | openConversationId | openConversationId |
| `openMsgId` | yes | string | openMsgId | openMsgId |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `emojiName` | `--emoji` |  |  |  |  |
| `openConversationId` | `--group` |  |  |  |  |
| `openMsgId` | `--msg-id` |  |  |  |  |

## dws chat message remove-text-emotion

- Canonical path: `chat.remove_text_emotion`
- Product: `chat`
- Group: `message`
- Subcommand: `remove-text-emotion`
- Title: 删除文字表情
- Description: 移除文字表情
- Required top-level parameters: `emotionName`, `text`, `openConversationId`, `openMsgId`, `emotionId`, `backgroundId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `backgroundId` | yes | string | backgroundId | backgroundId |
| `emotionId` | yes | string | emotionId | emotionId |
| `emotionName` | yes | string | emotionName | emotionName |
| `openConversationId` | yes | string | openConversationId | openConversationId |
| `openMsgId` | yes | string | openMsgId | openMsgId |
| `text` | yes | string | text | text |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `backgroundId` | `--background-id` |  |  |  |  |
| `emotionId` | `--emotion-id` |  |  |  |  |
| `emotionName` | `--emotion-name` |  |  |  |  |
| `openConversationId` | `--group` |  |  |  |  |
| `openMsgId` | `--msg-id` |  |  |  |  |
| `text` | `--text` |  |  |  |  |

## dws chat search

- Canonical path: `chat.search_groups`
- Product: `chat`
- Group: `-`
- Subcommand: `search`
- Title: 搜索群聊
- Description: 根据关键词搜索群聊（im 新版）
- Required top-level parameters: `keyword`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `cursor` |  | string | cursor | cursor |
| `keyword` | yes | string | keyword | keyword |
| `limit` |  | number | limit | limit |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `cursor` | `--cursor` |  | 0 |  |  |
| `keyword` | `--keyword` |  |  |  |  |
| `limit` | `--limit` |  | 20 |  |  |

## dws chat message search-advanced

- Canonical path: `chat.search_messages`
- Product: `chat`
- Group: `message`
- Subcommand: `search-advanced`
- Title: 搜索消息
- Description: 多维度搜索消息
- Required top-level parameters: -
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `atMe` |  | boolean | atMe | atMe |
| `atOpenDingTakIds` |  | array; items=string | atOpenDingTakIds | atOpenDingTakIds |
| `atOpenDingTakIds[]` |  | string |  |  |
| `cursor` |  | string | cursor | cursor |
| `endTime` |  | number | endTime | endTime |
| `keyword` |  | string | keyword | keyword |
| `limit` |  | number | limit | limit |
| `openConversationIds` |  | array; items=string | openConversationIds | openConversationIds |
| `openConversationIds[]` |  | string |  |  |
| `senderOpenDingTakIds` |  | array; items=string | senderOpenDingTakIds | senderOpenDingTakIds |
| `senderOpenDingTakIds[]` |  | string |  |  |
| `startTime` |  | number | startTime | startTime |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `atMe` | `--at-me` |  |  |  |  |
| `atOpenDingTakIds` | `--at-ids` | csv_to_array |  |  |  |
| `cursor` | `--cursor` |  |  |  |  |
| `endTime` | `--end` | iso8601_to_millis |  |  |  |
| `keyword` | `--keyword` |  |  |  |  |
| `limit` | `--limit` |  |  |  |  |
| `openConversationIds` | `--conversation-ids` | csv_to_array |  |  |  |
| `senderOpenDingTakIds` | `--sender-ids` | csv_to_array |  |  |  |
| `startTime` | `--start` | iso8601_to_millis |  |  |  |

## dws chat group.member-role set-user

- Canonical path: `chat.set_custom_user_roles`
- Product: `chat`
- Group: `group.member-role`
- Subcommand: `set-user`
- Title: 设置用户的群身份
- Description: 给成员设置自定义角色
- Required top-level parameters: `openConversationId`, `openRoleIds`, `openDingTalkId`
- Sensitive flag from schema: `false`
- Mutation risk: `unknown-review-before-use`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `openConversationId` | yes | string | openConversationId | openConversationId |
| `openDingTalkId` | yes | string | openDingTalkId | openDingTalkId |
| `openRoleIds` | yes | array; items=string | openRoleIds | openRoleIds |
| `openRoleIds[]` |  | string |  |  |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `openConversationId` | `--group` |  |  |  |  |
| `openDingTalkId` | `--user` |  |  |  |  |
| `openRoleIds` | `--role-ids` | csv_to_array |  |  |  |

## dws chat group-mute-member

- Canonical path: `chat.set_group_member_mute_list`
- Product: `chat`
- Group: `-`
- Subcommand: `group-mute-member`
- Title: 管理群禁言成员
- Description: 群成员禁言
- Required top-level parameters: `openConversationId`, `openDingTalkIds`, `mute`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `mute` | yes | boolean | mute | mute |
| `muteTime` |  | number | muteTime | muteTime |
| `openConversationId` | yes | string | openConversationId | openConversationId |
| `openDingTalkIds` | yes | array; items=string | openDingTalkIds | openDingTalkIds |
| `openDingTalkIds[]` |  | string |  |  |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `mute` | `--off` | invert_bool | false |  |  |
| `muteTime` | `--mute-time` |  |  |  |  |
| `openConversationId` | `--group` |  |  |  |  |
| `openDingTalkIds` | `--users` | csv_to_array |  |  |  |

## dws chat group-mute

- Canonical path: `chat.set_group_mute`
- Product: `chat`
- Group: `-`
- Subcommand: `group-mute`
- Title: 设置/取消群全员禁言
- Description: 群全员禁言
- Required top-level parameters: `openConversationId`, `mute`
- Sensitive flag from schema: `false`
- Mutation risk: `unknown-review-before-use`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `mute` | yes | boolean | mute | mute |
| `openConversationId` | yes | string | openConversationId | openConversationId |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `mute` | `--off` | invert_bool | false |  |  |
| `openConversationId` | `--group` |  |  |  |  |

## dws chat set-top

- Canonical path: `chat.set_top_conversation`
- Product: `chat`
- Group: `-`
- Subcommand: `set-top`
- Title: 会话置顶/取消置顶
- Description: 会话置顶（开启/关闭）
- Required top-level parameters: `openConversationId`, `top`
- Sensitive flag from schema: `false`
- Mutation risk: `unknown-review-before-use`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `openConversationId` | yes | string | openConversationId | openConversationId |
| `top` | yes | boolean | top | top |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `openConversationId` | `--group` |  |  |  |  |
| `top` | `--off` | invert_bool | false |  |  |

## dws chat group transfer-owner

- Canonical path: `chat.transfer_group_owner`
- Product: `chat`
- Group: `group`
- Subcommand: `transfer-owner`
- Title: 群主转让
- Description: 转让群主
- Required top-level parameters: `openConversationId`, `newOwnerOpenDingTalkId`
- Sensitive flag from schema: `false`
- Mutation risk: `unknown-review-before-use`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `newOwnerOpenDingTalkId` | yes | string | newOwnerOpenDingTalkId | newOwnerOpenDingTalkId |
| `openConversationId` | yes | string | openConversationId | openConversationId |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `newOwnerOpenDingTalkId` | `--new-owner` |  |  |  |  |
| `openConversationId` | `--group` |  |  |  |  |

## dws chat group set-admin

- Canonical path: `chat.update_conv_member_roles`
- Product: `chat`
- Group: `group`
- Subcommand: `set-admin`
- Title: 设置/取消群成员为管理员
- Description: 设置/取消群管理员
- Required top-level parameters: `openConversationId`, `openDingTalkIds`, `admin`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `admin` | yes | boolean | admin | admin |
| `openConversationId` | yes | string | openConversationId | openConversationId |
| `openDingTalkIds` | yes | array; items=string | openDingTalkIds | openDingTalkIds |
| `openDingTalkIds[]` |  | string |  |  |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `admin` | `--off` | invert_bool | false |  |  |
| `openConversationId` | `--group` |  |  |  |  |
| `openDingTalkIds` | `--users` | csv_to_array |  |  |  |

## dws chat group.member-role update

- Canonical path: `chat.update_custom_group_role`
- Product: `chat`
- Group: `group.member-role`
- Subcommand: `update`
- Title: 更新群身份
- Description: 更新群自定义角色
- Required top-level parameters: `openConversationId`, `openRoleId`, `name`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `name` | yes | string | name | name |
| `openConversationId` | yes | string | openConversationId | openConversationId |
| `openRoleId` | yes | string | openRoleId | openRoleId |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `name` | `--name` |  |  |  |  |
| `openConversationId` | `--group` |  |  |  |  |
| `openRoleId` | `--role-id` |  |  |  |  |

## dws chat group update-icon

- Canonical path: `chat.update_group_icon`
- Product: `chat`
- Group: `group`
- Subcommand: `update-icon`
- Title: 更新群头像
- Description: 更新群头像
- Required top-level parameters: `openConversationId`, `iconMediaId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `iconMediaId` | yes | string | iconMediaId | iconMediaId |
| `openConversationId` | yes | string | openConversationId | openConversationId |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `iconMediaId` | `--icon-media-id` |  |  |  |  |
| `openConversationId` | `--group` |  |  |  |  |

## dws chat group update-settings

- Canonical path: `chat.update_group_settings`
- Product: `chat`
- Group: `group`
- Subcommand: `update-settings`
- Title: 更新群设置
- Description: 更新群设置
- Required top-level parameters: `openConversationId`, `settingKey`, `status`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `openConversationId` | yes | string | openConversationId | openConversationId |
| `settingKey` | yes | string | settingKey | settingKey |
| `status` | yes | number | status | status |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `openConversationId` | `--group` |  |  |  |  |
| `settingKey` | `--setting-key` |  |  |  |  |
| `status` | `--status` |  |  |  |  |

## dws chat mute

- Canonical path: `chat.update_notification_off`
- Product: `chat`
- Group: `-`
- Subcommand: `mute`
- Title: 群免打扰开关
- Description: 会话消息免打扰（开启/关闭）
- Required top-level parameters: `openConversationId`, `mute`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `mute` | yes | boolean | mute | mute |
| `openConversationId` | yes | string | openConversationId | openConversationId |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `mute` | `--off` | invert_bool | false |  |  |
| `openConversationId` | `--group` |  |  |  |  |

## dws chat group set-history

- Canonical path: `chat.update_show_history_msg_option`
- Product: `chat`
- Group: `group`
- Subcommand: `set-history`
- Title: 设置新成员入群可查看历史消息选项
- Description: 设置新成员入群可见历史消息策略。
- Required top-level parameters: `openConversationId`, `option`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `openConversationId` | yes | string | openConversationId | openConversationId |
| `option` | yes | string | option | 可选值: "FORBIDDEN"-禁止查看历史消息；"RECENT_100"-可查看最近100条消息；"ALL"-可查看全部历史消息 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `openConversationId` | `--group` |  |  |  |  |
| `option` | `--option` |  |  |  |  |

## dws chat message update-card

- Canonical path: `chat.update_streaming_card`
- Product: `chat`
- Group: `message`
- Subcommand: `update-card`
- Title: 流式更新卡片内容
- Description: 更新流式卡片
- Required top-level parameters: `msgContent`, `bizId`, `flowStatus`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `bizId` | yes | string | bizId | bizId |
| `flowStatus` | yes | string | flowStatus | flowStatus |
| `msgContent` | yes | string | msgContent | msgContent |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `bizId` | `--biz-id` |  |  |  |  |
| `flowStatus` | `--flow-status` |  |  |  |  |
| `msgContent` | `--content` |  |  |  |  |

