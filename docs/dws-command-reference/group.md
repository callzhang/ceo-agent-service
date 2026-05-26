# dws group Commands

机器人群组成员

Commands in this file: 12

## Command Index

| CLI | Canonical path | Risk |
| --- | --- | --- |
| [`dws group members add-bot`](#dws-group-members-add-bot) | `group.add_robot_to_group` | `unknown-review-before-use` |
| [`dws group batch_recall_robot_users_msg`](#dws-group-batchrecallrobotusersmsg) | `group.batch_recall_robot_users_msg` | `mutating-review-first` |
| [`dws group batch_send_robot_msg_to_users`](#dws-group-batchsendrobotmsgtousers) | `group.batch_send_robot_msg_to_users` | `mutating-review-first` |
| [`dws group create_robot`](#dws-group-createrobot) | `group.create_robot` | `mutating-review-first` |
| [`dws group bots`](#dws-group-bots) | `group.list_group_bots` | `read-only` |
| [`dws group recall_robot_group_message`](#dws-group-recallrobotgroupmessage) | `group.recall_robot_group_message` | `mutating-review-first` |
| [`dws group members remove-bot`](#dws-group-members-remove-bot) | `group.remove_robot_in_group` | `mutating-review-first` |
| [`dws group search_bots`](#dws-group-searchbots) | `group.search_bots` | `read-only` |
| [`dws group search_groups_by_keyword`](#dws-group-searchgroupsbykeyword) | `group.search_groups_by_keyword` | `read-only` |
| [`dws group search_my_robots`](#dws-group-searchmyrobots) | `group.search_my_robots` | `read-only` |
| [`dws group send_message_by_custom_robot`](#dws-group-sendmessagebycustomrobot) | `group.send_message_by_custom_robot` | `mutating-review-first` |
| [`dws group send_robot_group_message`](#dws-group-sendrobotgroupmessage) | `group.send_robot_group_message` | `mutating-review-first` |

## Group Summary

| Group | Commands |
| --- | ---: |
| `-` | 10 |
| `members` | 2 |

## dws group members add-bot

- Canonical path: `group.add_robot_to_group`
- Product: `group`
- Group: `members`
- Subcommand: `add-bot`
- Title: 将企业机器人添加我有权限的群中
- Description: 添加机器人到群
- Required top-level parameters: `robotCode`, `openConversationId`
- Sensitive flag from schema: `false`
- Mutation risk: `unknown-review-before-use`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `openConversationId` | yes | string | 群会话Id，可通过关键词搜索群列表服务获取 | 群会话Id，可通过关键词搜索群列表服务获取 |
| `robotCode` | yes | string | 机器人code，可在开发者后台查看，或者调用创建机器人服务获取 | 机器人code，可在开发者后台查看，或者调用创建机器人服务获取 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `openConversationId` | `--id` |  |  |  |  |
| `robotCode` | `--robot-code` |  |  |  |  |

## dws group batch_recall_robot_users_msg

- Canonical path: `group.batch_recall_robot_users_msg`
- Product: `group`
- Group: `-`
- Subcommand: `batch_recall_robot_users_msg`
- Title: 批量撤回企业机器人发送的单聊消息
- Description: 批量撤回机器人发送的单聊消息。
- Required top-level parameters: `processQueryKeys`, `robotCode`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `processQueryKeys` | yes | array; items=string | 消息Id列表，机器人发送单聊消息时返回的值 | 消息Id列表，机器人发送单聊消息时返回的值 |
| `processQueryKeys[]` |  | string |  |  |
| `robotCode` | yes | string | 机器人robotCode | 机器人robotCode |

### CLI flag overlay

- none

## dws group batch_send_robot_msg_to_users

- Canonical path: `group.batch_send_robot_msg_to_users`
- Product: `group`
- Group: `-`
- Subcommand: `batch_send_robot_msg_to_users`
- Title: 企业机器人批量发送单聊消息
- Description: 机器人批量发送单聊消息，在该机器人可使用范围内的员工，可接收到单聊消息。
- Required top-level parameters: `userIds`, `title`, `markdown`, `robotCode`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `markdown` | yes | string | 消息内容，显示在对话消息正文，Markdown格式。图片使用'![]()'，文件使用'[]()' | 消息内容，Markdown格式 |
| `robotCode` | yes | string | 机器人Code，在开发者后台创建的机器人信息中可查看，或者调用创建机器人服务获取 | 机器人Code |
| `title` | yes | string | 消息标题，显示在IM消息列表 | 消息标题 |
| `userIds` | yes | array; items=string | 接收用户UserID列表，最多支持20个 | 接收用户UserID列表，最多支持20个 |
| `userIds[]` |  | string |  |  |

### CLI flag overlay

- none

## dws group create_robot

- Canonical path: `group.create_robot`
- Product: `group`
- Group: `-`
- Subcommand: `create_robot`
- Title: 创建企业机器人
- Description: 创建企业机器人，调用本服务会在当前组织创建一个企业内部应用并自动开启stream功能的机器人，该应用被创建时自动完成发布，默认可见范围是当前用户。
- Required top-level parameters: `robot_name`, `desc`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `desc` | yes | string | 机器人描述 | 机器人描述 |
| `robot_name` | yes | string | 机器人名称 | 机器人名称 |

### CLI flag overlay

- none

## dws group bots

- Canonical path: `group.list_group_bots`
- Product: `group`
- Group: `-`
- Subcommand: `bots`
- Title: list_group_bots
- Description: 列出群内的机器人。
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

## dws group recall_robot_group_message

- Canonical path: `group.recall_robot_group_message`
- Product: `group`
- Group: `-`
- Subcommand: `recall_robot_group_message`
- Title: 企业机器人撤回内部群消息
- Description: 可批量撤回企业机器人在群内发送的消息。
- Required top-level parameters: `openConversationId`, `processQueryKeys`, `robotCode`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `openConversationId` | yes | string | 群会话Id | 群ID，可通过客户端调用chooseChat接口获取 |
| `processQueryKeys` | yes | array; items=string | 消息Id列表，机器人发送消息服务返回的值 | 消息Id列表，机器人发送消息服务返回的值 |
| `processQueryKeys[]` |  | string |  |  |
| `robotCode` | yes | string | 机器人robotCode | 机器人Code |

### CLI flag overlay

- none

## dws group members remove-bot

- Canonical path: `group.remove_robot_in_group`
- Product: `group`
- Group: `members`
- Subcommand: `remove-bot`
- Title: remove_robot_in_group
- Description: 把机器人移出群。
- Required top-level parameters: `openBotId`, `openConversationId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `openBotId` | yes | string | openBotId | openBotId |
| `openConversationId` | yes | string | openConversationId | openConversationId |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `openBotId` | `--bot-id` |  |  |  |  |
| `openConversationId` | `--id` |  |  |  |  |

## dws group search_bots

- Canonical path: `group.search_bots`
- Product: `group`
- Group: `-`
- Subcommand: `search_bots`
- Title: search_bots
- Description: 搜索机器人
- Required top-level parameters: `keyword`, `limit`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `cursor` |  | string | cursor | cursor |
| `keyword` | yes | string | keyword | keyword |
| `limit` | yes | number | limit | limit |

### CLI flag overlay

- none

## dws group search_groups_by_keyword

- Canonical path: `group.search_groups_by_keyword`
- Product: `group`
- Group: `-`
- Subcommand: `search_groups_by_keyword`
- Title: 根据关键词搜索会话openconversationId
- Description: 根据关键词搜索我的群会话信息，包含群openconversationId、群名称等信息
- Required top-level parameters: `keyword`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `cursor` |  | string | 分页游标，从0开始 | 分页游标，从0开始 |
| `keyword` | yes | string | 搜索关键词 | 搜索关键词 |

### CLI flag overlay

- none

## dws group search_my_robots

- Canonical path: `group.search_my_robots`
- Product: `group`
- Group: `-`
- Subcommand: `search_my_robots`
- Title: 搜索我创建的企业机器人
- Description: 搜索我创建的机器人，可获取机器人robotCode等信息。
- Required top-level parameters: `currentPage`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `currentPage` | yes | number | 页码，从1开始 | 页码，从1开始 |
| `pageSize` |  | number | 每页条数，默认50 | 每页条数，默认50 |
| `robotName` |  | string | 要搜索的名称，该参数不传，搜索的是所有 | 要搜索的名称 |

### CLI flag overlay

- none

## dws group send_message_by_custom_robot

- Canonical path: `group.send_message_by_custom_robot`
- Product: `group`
- Group: `-`
- Subcommand: `send_message_by_custom_robot`
- Title: 自定义机器人发送群消息
- Description: 使用自定义机器人发送群消息，请注意自定义机器人与企业机器人的区别。
- Required top-level parameters: `text`, `title`, `robotToken`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `atMobiles` |  | array; items=string | 被@的群成员手机号 | 被@的群成员手机号 |
| `atMobiles[]` |  | string |  |  |
| `atUserIds` |  | array; items=string | 被@的群成员userId | 被@的群成员userId |
| `atUserIds[]` |  | string |  |  |
| `isAtAll` |  | boolean | 是否@所有人 | 是否@所有人 |
| `robotToken` | yes | string | 自定义机器人Token，在创建自定义机器人时得到的webhook地址中的accessToken值 | 自定义机器人Token，在创建自定义机器人时得到的webhook地址中的accessToken值 |
| `text` | yes | string | 消息内容 | 消息内容,Markdown格式 |
| `title` | yes | string | 消息标题 | 消息标题 |

### CLI flag overlay

- none

## dws group send_robot_group_message

- Canonical path: `group.send_robot_group_message`
- Product: `group`
- Group: `-`
- Subcommand: `send_robot_group_message`
- Title: 企业机器人发送群聊消息
- Description: 机器人发送群聊消息，该机器人必须已存在对应的群内。
- Required top-level parameters: `title`, `markdown`, `robotCode`, `openConversationId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `atOpendingtalkIds` |  | array; items=string | 消息@人atOpendingtalkIds | 消息@人atOpendingtalkIds |
| `atOpendingtalkIds[]` |  | string |  |  |
| `atUserIds` |  | array; items=string | 消息@人atUserIds | 消息@人atUserIds |
| `atUserIds[]` |  | string |  |  |
| `isAtAll` |  | string | 是否@所有人 | 是否@所有人 |
| `markdown` | yes | string | 消息内容，显示在对话框内，Markdown格式，图片使用'![]()'，文件使用'[]()' | 消息内容，Markdown格式 |
| `openConversationId` | yes | string | 群聊会话Id，可调用通过关键词搜索会话群获取 | 群聊会话ID |
| `robotCode` | yes | string | 机器人Code，可在开发者后台查看机器人robotCode，或者调用搜索机器人获取 | 机器人Code |
| `title` | yes | string | 消息标题，显示在IM对话列表 | 消息标题 |

### CLI flag overlay

- none

