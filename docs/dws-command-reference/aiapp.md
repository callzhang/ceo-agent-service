# dws aiapp Commands

AI 应用创建 / 查询 / 修改

Commands in this file: 3

## Command Index

| CLI | Canonical path | Risk |
| --- | --- | --- |
| [`dws aiapp create`](#dws-aiapp-create) | `aiapp.create_ai_app` | `mutating-review-first` |
| [`dws aiapp modify`](#dws-aiapp-modify) | `aiapp.modify_ai_app` | `mutating-review-first` |
| [`dws aiapp query`](#dws-aiapp-query) | `aiapp.query_ai_app` | `read-only` |

## Group Summary

| Group | Commands |
| --- | ---: |
| `-` | 3 |

## dws aiapp create

- Canonical path: `aiapp.create_ai_app`
- Product: `aiapp`
- Group: `-`
- Subcommand: `create`
- Title: create_ai_app
- Description: 创建 AI 应用
- Required top-level parameters: -
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `attachments` |  | array; items=object | attachments | 创建ai应用所使用的附件信息 |
| `attachments[].name` | yes | string | name | 附件名称 |
| `attachments[].size` | yes | number | size | 附件数据量 |
| `attachments[].type` | yes | string | type | 附件类型 |
| `attachments[].url` | yes | string | url | 附件链接地址 |
| `officialSkillUids` |  | array; items=string | officialSkillUids | 创建ai应用可以使用的技能id |
| `officialSkillUids[]` |  | string |  |  |
| `prompt` |  | string | prompt | 创建ai应用的prompt |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `attachments` | `--attachments` | json_parse |  |  |  |
| `officialSkillUids` | `--skills` | csv_to_array |  |  |  |
| `prompt` | `--prompt` |  |  |  |  |

## dws aiapp modify

- Canonical path: `aiapp.modify_ai_app`
- Product: `aiapp`
- Group: `-`
- Subcommand: `modify`
- Title: modify_ai_app
- Description: 修改 AI 应用
- Required top-level parameters: `prompt`, `threadId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `officialSkillUids` |  | array; items=string | officialSkillUids | officialSkillUids |
| `officialSkillUids[]` |  | string |  |  |
| `prompt` | yes | string | prompt | prompt |
| `threadId` | yes | string | threadId | threadId |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `officialSkillUids` | `--skills` | csv_to_array |  |  |  |
| `prompt` | `--prompt` |  |  |  |  |
| `threadId` | `--thread-id` |  |  |  |  |

## dws aiapp query

- Canonical path: `aiapp.query_ai_app`
- Product: `aiapp`
- Group: `-`
- Subcommand: `query`
- Title: 查询ai应用
- Description: 查询 AI 应用
- Required top-level parameters: `taskId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `taskId` | yes | string | taskId | 查询对应ai应用的taskId |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `taskId` | `--task-id` |  |  |  |  |

