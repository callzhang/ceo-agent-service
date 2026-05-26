# dws ding Commands

DING 消息 / 发送 / 撤回

Commands in this file: 3

## Command Index

| CLI | Canonical path | Risk |
| --- | --- | --- |
| [`dws ding message recall`](#dws-ding-message-recall) | `ding.recall_ding_message` | `mutating-review-first` |
| [`dws ding search_my_robots`](#dws-ding-searchmyrobots) | `ding.search_my_robots` | `read-only` |
| [`dws ding message send`](#dws-ding-message-send) | `ding.send_ding_message` | `mutating-review-first` |

## Group Summary

| Group | Commands |
| --- | ---: |
| `-` | 1 |
| `message` | 2 |

## dws ding message recall

- Canonical path: `ding.recall_ding_message`
- Product: `ding`
- Group: `message`
- Subcommand: `recall`
- Title: 撤回已发送的DING消息
- Description: 撤回已发送的DING消息
- Required top-level parameters: `robotCode`, `openDingId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `openDingId` | yes | string | 钉钉消息ID | 要撤回的钉钉消息ID，可通过发送DING消息接口获取 |
| `robotCode` | yes | string | 机器人Code | 发送钉钉消息的机器人ID，必须与发送消息的机器人为同一个 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `openDingId` | `--id` |  |  |  |  |
| `robotCode` | `--robot-code` |  |  | DINGTALK_DING_ROBOT_CODE |  |

## dws ding search_my_robots

- Canonical path: `ding.search_my_robots`
- Product: `ding`
- Group: `-`
- Subcommand: `search_my_robots`
- Title: 搜索我创建的机器人
- Description: 搜索我创建的机器人
- Required top-level parameters: `currentPage`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `currentPage` | yes | number | 页码 | 页码，从1开始 |
| `pageSize` |  | number | 每页条数 | 每页条数，默认50 |
| `robotName` |  | string | 要搜索的名称 | 要搜索的名称 |

### CLI flag overlay

- none

## dws ding message send

- Canonical path: `ding.send_ding_message`
- Product: `ding`
- Group: `message`
- Subcommand: `send`
- Title: 发送DING消息
- Description: 使用企业内机器人发送DING消息，可发送应用内DING、短信DING、电话DING。
- Required top-level parameters: `remindType`, `receiverUserIdList`, `content`, `robotCode`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `callVoice` |  | string; enum=Standard_Female_Voice/Cantonese_Female_Voice/Gentine_Female_Voice/Overbearing_Female_Voice/Lovely_Girl_Voice/Standard_Male_Voice | 电话语音内容 | 电话语音内容 - Standard_Female_Voice - Cantonese_Female_Voice - Gentine_Female_Voice - Overbearing_Female_Voice - Lovely_Girl_Voice - Standard_Male_Voice |
| `content` | yes | string | 消息内容 | 消息内容 |
| `receiverUserIdList` | yes | array; items=string | 接收者用户ID列表 | 接收者用户ID列表 |
| `receiverUserIdList[]` |  | string |  |  |
| `remindType` | yes | number | 提醒类型 | 提醒类型，1：应用内钉钉，2：短信，3：电话 |
| `robotCode` | yes | string | 机器人Code | 机器人Code |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `content` | `--content` |  |  |  |  |
| `receiverUserIdList` | `--users` | csv_to_array |  |  |  |
| `remindType` | `--type` | enum_map |  |  | {"_default":1,"app":1,"call":3,"sms":2} |
| `robotCode` | `--robot-code` |  |  | DINGTALK_DING_ROBOT_CODE |  |

