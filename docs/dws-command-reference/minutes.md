# dws minutes Commands

AI 听记（列表 / 详情 / 摘要 / 待办 / 文字稿 / 录音 / 思维导图 / 发言人 / 热词 / 上传）

Commands in this file: 27

## Command Index

| CLI | Canonical path | Risk |
| --- | --- | --- |
| [`dws minutes add_member_permission`](#dws-minutes-addmemberpermission) | `minutes.add_member_permission` | `unknown-review-before-use` |
| [`dws minutes hot-word add`](#dws-minutes-hot-word-add) | `minutes.add_personal_hot_word` | `unknown-review-before-use` |
| [`dws minutes get batch`](#dws-minutes-get-batch) | `minutes.batch_get_minutes_details` | `read-only` |
| [`dws minutes upload cancel`](#dws-minutes-upload-cancel) | `minutes.cancel_upload_session` | `mutating-review-first` |
| [`dws minutes upload complete`](#dws-minutes-upload-complete) | `minutes.complete_upload_session` | `mutating-review-first` |
| [`dws minutes mind-graph create`](#dws-minutes-mind-graph-create) | `minutes.create_mind_graph` | `mutating-review-first` |
| [`dws minutes create_speaker_summary`](#dws-minutes-createspeakersummary) | `minutes.create_speaker_summary` | `mutating-review-first` |
| [`dws minutes upload create`](#dws-minutes-upload-create) | `minutes.create_upload_session` | `mutating-review-first` |
| [`dws minutes get summary`](#dws-minutes-get-summary) | `minutes.get_minutes_ai_summary` | `read-only` |
| [`dws minutes get info`](#dws-minutes-get-info) | `minutes.get_minutes_basic_info` | `read-only` |
| [`dws minutes get keywords`](#dws-minutes-get-keywords) | `minutes.get_minutes_keywords` | `read-only` |
| [`dws minutes get transcription`](#dws-minutes-get-transcription) | `minutes.get_minutes_transcription` | `read-only` |
| [`dws minutes get_speaker_summary`](#dws-minutes-getspeakersummary) | `minutes.get_speaker_summary` | `read-only` |
| [`dws minutes list all`](#dws-minutes-list-all) | `minutes.list_by_keyword_and_time_range` | `read-only` |
| [`dws minutes list_by_keyword_range`](#dws-minutes-listbykeywordrange) | `minutes.list_by_keyword_range` | `read-only` |
| [`dws minutes get todos`](#dws-minutes-get-todos) | `minutes.list_minutes_todos` | `read-only` |
| [`dws minutes list mine`](#dws-minutes-list-mine) | `minutes.list_my_created_minutes` | `mutating-review-first` |
| [`dws minutes list_my_hotwords`](#dws-minutes-listmyhotwords) | `minutes.list_my_hotwords` | `read-only` |
| [`dws minutes list shared`](#dws-minutes-list-shared) | `minutes.list_shared_minutes` | `read-only` |
| [`dws minutes mind-graph status`](#dws-minutes-mind-graph-status) | `minutes.query_mind_graph_status` | `read-only` |
| [`dws minutes query_minutes_audio_url`](#dws-minutes-queryminutesaudiourl) | `minutes.query_minutes_audio_url` | `read-only` |
| [`dws minutes remove_member_permission`](#dws-minutes-removememberpermission) | `minutes.remove_member_permission` | `mutating-review-first` |
| [`dws minutes replace-text`](#dws-minutes-replace-text) | `minutes.replace_minutes_text` | `mutating-review-first` |
| [`dws minutes speaker replace`](#dws-minutes-speaker-replace) | `minutes.replace_speaker` | `mutating-review-first` |
| [`dws minutes update summary`](#dws-minutes-update-summary) | `minutes.update_minutes_summary` | `mutating-review-first` |
| [`dws minutes update title`](#dws-minutes-update-title) | `minutes.update_minutes_title` | `mutating-review-first` |
| [`dws minutes 执行听记指令-发起AI听记录音`](#dws-minutes-执行听记指令-发起ai听记录音) | `minutes.执行听记指令-发起AI听记录音` | `unknown-review-before-use` |

## Group Summary

| Group | Commands |
| --- | ---: |
| `-` | 9 |
| `get` | 6 |
| `hot-word` | 1 |
| `list` | 3 |
| `mind-graph` | 2 |
| `speaker` | 1 |
| `update` | 2 |
| `upload` | 3 |

## dws minutes add_member_permission

- Canonical path: `minutes.add_member_permission`
- Product: `minutes`
- Group: `-`
- Subcommand: `add_member_permission`
- Title: add_member_permission
- Description: 批量给多个听记增加成员，并且设置成员的权限。权限类型为：0:管理员;1:所有者；2:可编辑;3:可查看/下载;4:仅查看。
- Required top-level parameters: -
- Sensitive flag from schema: `false`
- Mutation risk: `unknown-review-before-use`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `coverPermission` |  | string | coverPermission | 是否覆盖已有权限，默认 false |
| `memberUids` |  | array; items=number | memberUids | 需要添加的成员的钉钉Uid列表，长整型 |
| `memberUids[]` |  | number |  |  |
| `policyId` |  | number | policyId | 策略id：MANAGER(0L, "管理员"),      OWNER(1L, "所有者"),      EDITOR(2L, "可编辑"),      READ_DOWNLOAD(3L, "可查看/下载"),      READ(4L, "仅查看"); |
| `roleSubResourceIds` |  | array; items=string | roleSubResourceIds | 权限子模块，OrigContent(原始内容) / Summary(纪要) / Analysis(分析) / Note(笔记) |
| `roleSubResourceIds[]` |  | string |  |  |
| `uuids` |  | array; items=string | uuids | 听记uuid列表，uuid为听记唯一标识 |
| `uuids[]` |  | string |  |  |

### CLI flag overlay

- none

## dws minutes hot-word add

- Canonical path: `minutes.add_personal_hot_word`
- Product: `minutes`
- Group: `hot-word`
- Subcommand: `add`
- Title: add_personal_hot_word
- Description: 添加听记个人热词 对于听记语音识别中，需要优化识别结果的专有名词、人名、以及其他用户需要添加热词的场景使用 热词长度不超过10个汉字或5个英文单词
- Required top-level parameters: `hotWordList`
- Sensitive flag from schema: `false`
- Mutation risk: `unknown-review-before-use`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `hotWordList` | yes | array; items=string | hotWordList | hotWordList |
| `hotWordList[]` |  | string |  |  |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `hotWordList` | `--words` | csv_to_array |  |  |  |

## dws minutes get batch

- Canonical path: `minutes.batch_get_minutes_details`
- Product: `minutes`
- Group: `get`
- Subcommand: `batch`
- Title: batch_get_minutes_details
- Description: 根据听记taskUuid列表批量查询听记详情。输入参数：taskUuid列表（string[]，必填，每个元素为听记的唯一标识符，最少传入1个）。返回对应听记的详情列表，每条记录包含：听记标题、时长、参与人列表、创建时间、听记唯一标识taskUuid、听记状态。适用于已知特定听记taskUuid需批量获取详情的列表展示场景。
- Required top-level parameters: `requestBody`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `requestBody` | yes | object | 字段名 | 请求的taskUuid列表 |
| `requestBody.taskUuids` | yes | array; items=string | taskUuids | 请求的taskUuid列表 |
| `requestBody.taskUuids[]` |  | string |  |  |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `requestBody.taskUuids` | `--ids` | csv_to_array |  |  |  |

## dws minutes upload cancel

- Canonical path: `minutes.cancel_upload_session`
- Product: `minutes`
- Group: `upload`
- Subcommand: `cancel`
- Title: cancel_upload_session
- Description: 取消文件上传会话 当需要取消create_upload_session创建的会话时使用该接口，传入要取消的会话ID
- Required top-level parameters: `sessionId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `sessionId` | yes | string | sessionId | 需要取消的会话sessionId |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `sessionId` | `--session-id` |  |  |  |  |

## dws minutes upload complete

- Canonical path: `minutes.complete_upload_session`
- Product: `minutes`
- Group: `upload`
- Subcommand: `complete`
- Title: complete_upload_session
- Description: 文件上传完成后，创建听记。 必须在create_upload_session之后、预签名URL上传`curl -X PUT "{presignedUrl}" -T "/path/to/file.mp4"`完成后调用。 调用流程参考create_upload_session介绍。 幂等：同一sessionId重复调用直接返回已有的任务，不会重复创建。
- Required top-level parameters: `sessionId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `sessionId` | yes | string | sessionId | 上传文件会话ID，create_upload_session返回的sessionId |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `sessionId` | `--session-id` |  |  |  |  |

## dws minutes mind-graph create

- Canonical path: `minutes.create_mind_graph`
- Product: `minutes`
- Group: `mind-graph`
- Subcommand: `create`
- Title: create_mind_graph
- Description: 触发创建听记思维导图任务。触发成功后，你需要知道听记思维导图任务状态，每2s调用一次query_mind_graph_status，如果状态是“进行中”需要保持轮询。
- Required top-level parameters: `taskUuid`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `question` |  | string | question | 用户问题 |
| `taskUuid` | yes | string | taskUuid | 听记唯一标识 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `taskUuid` | `--id` |  |  |  |  |

## dws minutes create_speaker_summary

- Canonical path: `minutes.create_speaker_summary`
- Product: `minutes`
- Group: `-`
- Subcommand: `create_speaker_summary`
- Title: create_speaker_summary
- Description: 触发创建发言人的段落总结任务，该任务将听记的发言人的所有撰写内容汇总总结，触发后需要继续调用查询接口去查询总结任务的结果
- Required top-level parameters: -
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `uuids` |  | array; items=string | uuids | 听记uuid列表 |
| `uuids[]` |  | string |  |  |

### CLI flag overlay

- none

## dws minutes upload create

- Canonical path: `minutes.create_upload_session`
- Product: `minutes`
- Group: `upload`
- Subcommand: `create`
- Title: create_upload_session
- Description: 创建文件上传会话，获取预签名上传URL 调用方拿到 URL 后，直接用HTTP PUT将文件上传到该URL 必须与complete_upload_session配合使用 1.调用create_upload_session获取预签名上传URL和上传ID 2.HTTP PUT预签名上传URL上传文件(不带HEADER) 3.调用complete_upload_session传入会话ID
- Required top-level parameters: `fileName`, `fileSize`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `fileName` | yes | string | fileName | 文件名（含后缀），用于校验文件类型。例如："meeting.mp4" |
| `fileSize` | yes | number | fileSize | 文件大小（单位：Byte），用于容量校验和上传链接过期时间计算 |
| `minutesOption` |  | object | minutesOption | （可空）听记生成可选项 |
| `minutesOption.enableMessageCard` |  | boolean | enableMessageCard | （可空）是否推送闪记卡片消息 |
| `minutesOption.inputLanguage` |  | string | inputLanguage | （可空）输入语言，用于指定 ASR 识别的源语言 |
| `minutesOption.templateId` |  | string | templateId | （可空）听记使用模板ID，用于指定纪要生成使用的模板 |
| `title` |  | string | title | 听记标题，不传时默认使用 fileName 去掉后缀 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `fileName` | `--file-name` |  |  |  |  |
| `fileSize` | `--file-size` |  |  |  |  |
| `minutesOption.enableMessageCard` | `--enable-message-card` |  |  |  |  |
| `minutesOption.inputLanguage` | `--input-language` |  |  |  |  |
| `minutesOption.templateId` | `--template-id` |  |  |  |  |
| `title` | `--title` |  |  |  |  |

## dws minutes get summary

- Canonical path: `minutes.get_minutes_ai_summary`
- Product: `minutes`
- Group: `get`
- Subcommand: `summary`
- Title: get_minutes_ai_summary
- Description: 根据听记唯一标识获取由AI生成的听记内容摘要。输入参数：目标听记的唯一标识符taskUuid。返回Markdown格式的摘要文本，内容由AI对听记转写原文进行结构化提炼生成，涵盖会议主题、核心结论、关键讨论点等。适用于快速了解听记核心内容、会议记录整理、摘要分享等场景。
- Required top-level parameters: `taskUuid`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `taskUuid` | yes | string | taskUuid | 听记uuid |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `taskUuid` | `--id` |  |  |  |  |

## dws minutes get info

- Canonical path: `minutes.get_minutes_basic_info`
- Product: `minutes`
- Group: `get`
- Subcommand: `info`
- Title: get_minutes_basic_info
- Description: 根据听记唯一标识获取该听记的基础元数据信息。输入参数：听记的唯一标识符taskUuid。返回字段包括：创建人、开始时间、截止时间、听记标题、听记访问链接URL。适用于展示听记详情页头部信息、跳转听记原始链接、信息摘要展示等场景。
- Required top-level parameters: `taskUuid`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `taskUuid` | yes | string | taskUuid | 听记的taskUuid |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `taskUuid` | `--id` |  |  |  |  |

## dws minutes get keywords

- Canonical path: `minutes.get_minutes_keywords`
- Product: `minutes`
- Group: `get`
- Subcommand: `keywords`
- Title: get_minutes_keywords
- Description: 根据听记唯一标识查询该听记的关键字列表。输入参数：目标听记的唯一标识符taskUuid。返回关键字列表，每个元素为一个从听记内容中提取的核心关键词。适用于快速了解听记主题、关键词检索、内容分类打标及听记摘要辅助展示等场景。
- Required top-level parameters: `taskUuid`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `taskUuid` | yes | string | taskUuid | 听记的taskUuid |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `taskUuid` | `--id` |  |  |  |  |

## dws minutes get transcription

- Canonical path: `minutes.get_minutes_transcription`
- Product: `minutes`
- Group: `get`
- Subcommand: `transcription`
- Title: get_minutes_transcription
- Description: 根据听记唯一标识查询该听记的完整语音转写原文。输入参数：听记的唯一标识符 taskUuid。返回转写内容列表，每条记录包含：发言人信息、语音转写文本、对应时间戳。适用于用户回顾会议发言内容、按发言人检索原文、生成会议记录等场景。
- Required top-level parameters: `taskUuid`, `direction`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `direction` | yes | string | direction | 听记转写按时间的正反序，0为正序，相对时间逐渐增大，1为反序，默认为0 |
| `nextToken` |  | string | nextToken | 游标，第一次查询可为空，之后每次带上一次的游标 |
| `taskUuid` | yes | string | taskUuid | 听记的taskUuid |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `direction` | `--direction` |  |  |  |  |
| `nextToken` | `--next-token` |  |  |  |  |
| `taskUuid` | `--id` |  |  |  |  |

## dws minutes get_speaker_summary

- Canonical path: `minutes.get_speaker_summary`
- Product: `minutes`
- Group: `-`
- Subcommand: `get_speaker_summary`
- Title: get_speaker_summary
- Description: 查询发言人的段落总结任务的结果，该任务将听记的发言人的所有撰写内容汇总总结返回，配合创建任务使用
- Required top-level parameters: -
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `uuids` |  | array; items=string | uuids | 听记uuid列表，uuid为听记唯一标识 |
| `uuids[]` |  | string |  |  |

### CLI flag overlay

- none

## dws minutes list all

- Canonical path: `minutes.list_by_keyword_and_time_range`
- Product: `minutes`
- Group: `list`
- Subcommand: `all`
- Title: list_by_keyword_and_time_range
- Description: 新版：查询我的听记列表，支持输入关键词或者时间范围查询，返回听记列表。代替list_by_keyword_range。
- Required top-level parameters: -
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `belongingConditionId` |  | string | 筛选类型 | 筛选类型，默认noLimit，我创建的 created，分享给我的 shared |
| `bizTypeList` |  | array; items=number | 业务类型 | 业务类型：0到9 |
| `bizTypeList[]` |  | number |  |  |
| `createTimeEnd` |  | number | 时间结束 | 时间结束值 |
| `createTimeStart` |  | number | 时间开始 | 时间开始值 |
| `keyword` |  | string | 搜索关键词 | 搜索关键词 |
| `maxResults` |  | number | 每页数量 | 每页数量 |
| `nextToken` |  | string | 偏移量 | 分页查询下一次查询起始位置 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `belongingConditionId` | `--scope` |  |  |  |  |
| `createTimeEnd` | `--end` | iso8601_to_millis |  |  |  |
| `createTimeStart` | `--start` | iso8601_to_millis |  |  |  |
| `keyword` | `--query` |  |  |  |  |
| `maxResults` | `--max` |  |  |  |  |
| `nextToken` | `--next-token` |  |  |  |  |

## dws minutes list_by_keyword_range

- Canonical path: `minutes.list_by_keyword_range`
- Product: `minutes`
- Group: `-`
- Subcommand: `list_by_keyword_range`
- Title: list_by_keyword_range
- Description: 查询我的听记列表，支持输入关键词或者时间范围查询，返回听记列表
- Required top-level parameters: -
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `belongingConditionId` |  | string | 筛选类型 | 筛选类型，默认noLimit，我创建的 created，分享给我的 shared |
| `bizTypeList` |  | array; items=number | 业务类型 | 业务类型：0到9 |
| `bizTypeList[]` |  | number |  |  |
| `createTimeEnd` |  | number | 时间结束 | 时间结束值 |
| `createTimeStart` |  | number | 时间开始 | 时间开始值 |
| `keyword` |  | string | 搜索关键词 | 搜索关键词 |
| `offset` |  | string | 偏移量 | 偏移量 |
| `pageSize` |  | number | 每页数量 | 每页数量 |

### CLI flag overlay

- none

## dws minutes get todos

- Canonical path: `minutes.list_minutes_todos`
- Product: `minutes`
- Group: `get`
- Subcommand: `todos`
- Title: list_minutes_todos
- Description: 根据听记唯一标识查询该听记中提取的待办事项列表。输入参数：目标听记的唯一标识符taskUuid。返回待办事项列表，每条记录包含：待办事项内容、待办唯一ID、参与人信息、待办时间。适用于会后跟进行动项、任务分配追踪、按人员或时间筛选待办等场景。
- Required top-level parameters: `taskUuid`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `taskUuid` | yes | string | taskUuid | 听记的taskUiUuid |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `taskUuid` | `--id` |  |  |  |  |

## dws minutes list mine

- Canonical path: `minutes.list_my_created_minutes`
- Product: `minutes`
- Group: `list`
- Subcommand: `mine`
- Title: list_my_created_minutes
- Description: 查询当前用户所创建的听记列表，返回听记详情列表。无需传入额外参数，系统自动识别当前用户身份。返回字段包括：听记标题、时长、参与人列表、创建时间、听记唯一标识taskUuid、听记状态。适用于用户查看个人创建的听记列表、列表展示及管理场景。
- Required top-level parameters: `maxResults`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `maxResults` | yes | number | maxResults | 每一页的数据条数 |
| `nextToken` |  | string | nextToken | 分页token,首次查询为空，后续为上一次查询时返回的nextToken |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `createTimeEnd` | `--end` | iso8601_to_millis |  |  |  |
| `createTimeStart` | `--start` | iso8601_to_millis |  |  |  |
| `keyword` | `--query` |  |  |  |  |
| `maxResults` | `--max` |  |  |  |  |
| `nextToken` | `--next-token` |  |  |  |  |

## dws minutes list_my_hotwords

- Canonical path: `minutes.list_my_hotwords`
- Product: `minutes`
- Group: `-`
- Subcommand: `list_my_hotwords`
- Title: list_my_hotwords
- Description: 查询我的热词列表，返回个人的所有听记配置过的热词列表
- Required top-level parameters: -
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

- none

### CLI flag overlay

- none

## dws minutes list shared

- Canonical path: `minutes.list_shared_minutes`
- Product: `minutes`
- Group: `list`
- Subcommand: `shared`
- Title: list_shared_minutes
- Description: 查询他人共享给当前用户的听记列表，系统自动识别当前用户身份，无需传入额外参数。返回听记详情列表，每条记录包含：听记标题、时长、参与人列表、创建时间、听记唯一标识taskUuid、听记状态。适用于用户查看他人共享给自己的听记、协作场景下的听记列表展示与管理。
- Required top-level parameters: `maxResults`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `maxResults` | yes | number | maxResults | 查询的听记篇数 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `createTimeEnd` | `--end` | iso8601_to_millis |  |  |  |
| `createTimeStart` | `--start` | iso8601_to_millis |  |  |  |
| `keyword` | `--query` |  |  |  |  |
| `maxResults` | `--max` |  |  |  |  |
| `nextToken` | `--next-token` |  |  |  |  |

## dws minutes mind-graph status

- Canonical path: `minutes.query_mind_graph_status`
- Product: `minutes`
- Group: `mind-graph`
- Subcommand: `status`
- Title: query_mind_graph_status
- Description: 查询思维导图任务状态，根据taskUuid查询对应思维导图状态。 返回的结果里，任务状态（taskStatus）如下： 0-进行中，1-成功，2-失败 如果没有返回任务状态，也视为成功
- Required top-level parameters: `taskUuid`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `taskUuid` | yes | string | taskUuid | 听记唯一标识 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `taskUuid` | `--id` |  |  |  |  |

## dws minutes query_minutes_audio_url

- Canonical path: `minutes.query_minutes_audio_url`
- Product: `minutes`
- Group: `-`
- Subcommand: `query_minutes_audio_url`
- Title: query_minutes_audio_url
- Description: 查询听记的音频/视频地址，入参为 taskUuid 和操作人 uid，操作人需拥有该听记的"读"权限及以上才会返回；支持所有类型的听记（线上闪记、线下闪记、A1 硬件听记、上传文件听记等）。在返回地址前会过滤以下场景：听记已被删除、A1 无痕模式听记、临存过期的听记（媒体未准备好或临时存储已过期）。
- Required top-level parameters: `taskUuid`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `taskUuid` | yes | string | 字段名 | 听记uuid |

### CLI flag overlay

- none

## dws minutes remove_member_permission

- Canonical path: `minutes.remove_member_permission`
- Product: `minutes`
- Group: `-`
- Subcommand: `remove_member_permission`
- Title: remove_member_permission
- Description: 批量移除多个听记的成员的权限。权限类型为：0:管理员;1:所有者；2:可编辑;3:可查看/下载;4:仅查看。
- Required top-level parameters: -
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `memberUids` |  | array; items=number | memberUids | 需要添加的成员的钉钉Uid列表，长整型 |
| `memberUids[]` |  | number |  |  |
| `uuids` |  | array; items=string | uuids | 听记uuid列表，uuid为听记唯一标识 |
| `uuids[]` |  | string |  |  |

### CLI flag overlay

- none

## dws minutes replace-text

- Canonical path: `minutes.replace_minutes_text`
- Product: `minutes`
- Group: `-`
- Subcommand: `replace-text`
- Title: replace_minutes_text
- Description: 把听记中所有出现的原文字替换为目标文字，包括转写段落和纪要摘要中出现的原文字都会被替换为目标文字
- Required top-level parameters: `taskUuid`, `originalText`, `replacedText`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `originalText` | yes | string | originalText | 被替换的原始文字，如 "张三"。区分大小写，精确匹配 |
| `replacedText` | yes | string | replacedText | 替换后的目标文字，如 "李四"，允许为空字符串（即删除原始文字） |
| `taskUuid` | yes | string | taskUuid | 听记的taskUuid |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `originalText` | `--search` |  |  |  |  |
| `replacedText` | `--replace` |  |  |  |  |
| `taskUuid` | `--id` |  |  |  |  |

## dws minutes speaker replace

- Canonical path: `minutes.replace_speaker`
- Product: `minutes`
- Group: `speaker`
- Subcommand: `replace`
- Title: replace_speaker
- Description: 批量替换听记转写中指定发言人，将源发言人（speakerNick）精确匹配的所有段落替换为目标发言人，支持同时替换 nickName 和 subSpeakerNickname 两种匹配方式，并自动更新纪要、待办中的发言人信息。
- Required top-level parameters: `speakerNick`, `targetNickName`, `taskUuid`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `speakerNick` | yes | string | speakerNick | 源发言人昵称，精确匹配转写段落中的 nickName 或 subSpeakerNickname 字段 |
| `targetNickName` | yes | string | targetNickName | 目标发言人昵称，替换后所有匹配段落的发言人将显示为此名称 |
| `targetUid` |  | string | targetUid | 目标发言人uid，如果为空则只修改发言人昵称 |
| `taskUuid` | yes | string | taskUuid | 听记的taskUuid |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `speakerNick` | `--from` |  |  |  |  |
| `targetNickName` | `--to` |  |  |  |  |
| `targetUid` | `--target-uid` |  |  |  |  |
| `taskUuid` | `--id` |  |  |  |  |

## dws minutes update summary

- Canonical path: `minutes.update_minutes_summary`
- Product: `minutes`
- Group: `update`
- Subcommand: `summary`
- Title: update_minutes_summary
- Description: 用传入的摘要文本全量覆盖听记的纪要内容，不触发 AI 重新生成。适用于用户手动编辑或 AI Agent 修改纪要的场景。
- Required top-level parameters: `summaryText`, `taskUuid`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `summaryText` | yes | string | summaryText | 更新纪要的全文内容 |
| `taskUuid` | yes | string | taskUuid | 听记的taskUuid |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `summaryText` | `--content` |  |  |  |  |
| `taskUuid` | `--id` |  |  |  |  |

## dws minutes update title

- Canonical path: `minutes.update_minutes_title`
- Product: `minutes`
- Group: `update`
- Subcommand: `title`
- Title: update_minutes_title
- Description: 修改指定听记的标题名称。输入参数：taskUuid、newTitle 新的听记标题。操作成功返回更新结果状态。适用于用户对已有听记进行重命名、整理归档等场景。
- Required top-level parameters: `title`, `taskUuid`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `taskUuid` | yes | string | taskUuid | 听记的taskUuid |
| `title` | yes | string | title | 要更新的听记标题 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `taskUuid` | `--id` |  |  |  |  |
| `title` | `--title` |  |  |  |  |

## dws minutes 执行听记指令-发起AI听记录音

- Canonical path: `minutes.执行听记指令-发起AI听记录音`
- Product: `minutes`
- Group: `-`
- Subcommand: `执行听记指令-发起AI听记录音`
- Title: minutes_cmd_start
- Description: 执行听记指令，包括发起录音，暂停录音，结束录音。或者称为发起听记，暂停听记，结束听记
- Required top-level parameters: `cmd`
- Sensitive flag from schema: `false`
- Mutation risk: `unknown-review-before-use`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `cmd` | yes | string | 指令 | 指令，取值create：创建听记或者发起录音；pause:暂停录音；resume：恢复录音；end：结束录音。 |
| `operatorUid` |  | string | 操作人uid | 操作人钉钉Uid |
| `sessionId` |  | string | AI助理会话id | 助理会话的会话id |
| `uid` |  | string | 归属人uid | 操作人钉钉Uid |
| `uuid` |  | string | 听记uuid，或者称为taskUuid | 听记taskUuid,听记唯一标识 |

### CLI flag overlay

- none

