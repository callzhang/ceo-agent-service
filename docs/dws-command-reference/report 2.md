# dws report Commands

日志 / 模版 / 统计

Commands in this file: 7

## Command Index

| CLI | Canonical path | Risk |
| --- | --- | --- |
| [`dws report create`](#dws-report-create) | `report.create_report` | `mutating-review-first` |
| [`dws report template list`](#dws-report-template-list) | `report.get_available_report_templates` | `read-only` |
| [`dws report list`](#dws-report-list) | `report.get_received_report_list` | `read-only` |
| [`dws report detail`](#dws-report-detail) | `report.get_report_entry_details` | `read-only` |
| [`dws report stats`](#dws-report-stats) | `report.get_report_statistics_by_id` | `read-only` |
| [`dws report sent`](#dws-report-sent) | `report.get_send_report_list` | `mutating-review-first` |
| [`dws report template detail`](#dws-report-template-detail) | `report.get_template_details_by_name` | `read-only` |

## Group Summary

| Group | Commands |
| --- | ---: |
| `-` | 5 |
| `template` | 2 |

## dws report create

- Canonical path: `report.create_report`
- Product: `report`
- Group: `-`
- Subcommand: `create`
- Title: create_report
- Description: 创建日志（按模版）。重要: contents[].key 必须精确等于模板 field_name，先用 report template detail --name <模板名> 查询。
- Required top-level parameters: `contents`, `ddFrom`, `toChat`, `templateId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `contents` | yes | array; items=object | 日志内容列表 | 日志内容列表 |
| `contents[].content` | yes | string | 控件的值 | 控件的值 |
| `contents[].contentType` | yes | string | 内容类型，文本类型的组件（type=1)：markdown,其它类型组件：origin | 内容类型，文本类型的组件（type=1)：markdown,其它类型组件：origin |
| `contents[].key` | yes | string | 控件的标题，可通过获取模板详情获取，对应返回的field_name | 控件的标题，可通过获取模板详情获取，对应返回的field_name |
| `contents[].sort` | yes | string | 控件的排序，可通过获取模板详情获取，对应返回的field_sort | 控件的排序，可通过获取模板详情获取，对应返回的field_sort |
| `contents[].type` | yes | string | 字段type，1：文本类型，2：数字类型，3：单选类型，5：日期类型，7：多选类型，可通过获取模板详情获取，对应返回的field_type | 字段type，1：文本类型，2：数字类型，3：单选类型，5：日期类型，7：多选类型，可通过获取模板详情获取，对应返回的field_type |
| `ddFrom` | yes | string | 创建日志的来源，自定义值 | 创建日志的来源，自定义值 |
| `templateId` | yes | string | 需要发送哪个日志模板的日志，可通过获取可见日志模板服务获取 | 需要发送哪个日志模板的日志，可通过获取可见日志模板服务获取 |
| `toChat` | yes | boolean | 是否发送到日志接收人的单聊 | 是否发送到日志接收人的单聊 |
| `toUserIds` |  | array; items=string | 该日志发送到的人员userId列表 | 该日志发送到的人员userId列表 |
| `toUserIds[]` |  | string |  |  |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `contents` | `--contents` | json_parse |  |  |  |
| `ddFrom` | `--dd-from` |  | dws |  |  |
| `templateId` | `--template-id` |  |  |  |  |
| `toChat` | `--to-chat` |  | false |  |  |
| `toUserIds` | `--to-user-ids` | csv_to_array |  |  |  |

## dws report template list

- Canonical path: `report.get_available_report_templates`
- Product: `report`
- Group: `template`
- Subcommand: `list`
- Title: get_available_report_templates
- Description: 获取当前员工可使用的日志模版信息，包含日志模板的名称、模板Id等
- Required top-level parameters: -
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

- none

### CLI flag overlay

- none

## dws report list

- Canonical path: `report.get_received_report_list`
- Product: `report`
- Group: `-`
- Subcommand: `list`
- Title: 查询当前人收到的日志列表
- Description: 查询当前人收到的日志列表
- Required top-level parameters: `startTime`, `endTime`, `cursor`, `size`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `cursor` | yes | number | 分页游标，首次调用传0 | 起始游标，从 0 开始，示例值：1734048000000，必填 |
| `endTime` | yes | number | 查询的结束时间 | 结束时间，毫秒时间戳，示例值：1734048000000，必填，与开始时间的间隔不能超过180天 |
| `senderUserIds` |  | array; items=string | 发送人的工号 | 发送人的staffId |
| `senderUserIds[]` |  | string |  |  |
| `size` | yes | number | 每页的数量，最大值20 | 每页条数，最大 20，示例值：1734048000000，必填 |
| `startTime` | yes | number | 查询的开始时间 | 开始时间，毫秒时间戳，示例值：1734048000000，必填，与结束时间的间隔不能超过180天 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `cursor` | `--cursor` |  |  |  |  |
| `endTime` | `--end` | iso8601_to_millis |  |  |  |
| `size` | `--size` |  |  |  |  |
| `startTime` | `--start` | iso8601_to_millis |  |  |  |

## dws report detail

- Canonical path: `report.get_report_entry_details`
- Product: `report`
- Group: `-`
- Subcommand: `detail`
- Title: get_report_entry_details
- Description: 获取指定一篇日志的详情信息
- Required top-level parameters: `report_id`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `report_id` | yes | string | 日志Id，可以从查询当前用户收到的日志列表获取 | 日志Id，可以从查询当前用户收到的日志列表获取 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `report_id` | `--report-id` |  |  |  |  |

## dws report stats

- Canonical path: `report.get_report_statistics_by_id`
- Product: `report`
- Group: `-`
- Subcommand: `stats`
- Title: 获取指定日志的统计数据
- Description: 获取日志统计数据，包括评论数量、点赞数量、已读数等
- Required top-level parameters: `report_id`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `report_id` | yes | string | 日志Id | 日志Id |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `report_id` | `--report-id` |  |  |  |  |

## dws report sent

- Canonical path: `report.get_send_report_list`
- Product: `report`
- Group: `-`
- Subcommand: `sent`
- Title: get_send_report_list
- Description: 查询当前人创建的日志详情列表，包含日志的内容、日志名称、创建时间等信息
- Required top-level parameters: `cursor`, `size`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `cursor` | yes | number | 分页游标，首次传0 | 分页游标，首次传0 |
| `endTime` |  | number | 日志创建的结束时间，毫秒级时间戳格式，示例：1734048000000；注意：开始时间和结束时间跨度不能超过180天 | 日志创建的结束时间，毫秒级时间戳格式，示例：1734048000000；注意：开始时间和结束时间跨度不能超过180天 |
| `modifiedEndTime` |  | number | 日志修改的结束时间，毫秒级时间戳格式，示例：1734048000000 | 日志修改的结束时间，毫秒级时间戳格式，示例：1734048000000 |
| `modifiedStartTime` |  | number | 日志修改的开始时间，毫秒级时间戳格式，示例：1734048000000 | 日志修改的开始时间，毫秒级时间戳格式，示例：1734048000000 |
| `report_template_name` |  | string | 日志模板名称，可不传，查询的是全部日志 | 日志模板名称，可不传，查询的是全部日志 |
| `size` | yes | number | 分页大小，最大20 | 分页大小，最大20 |
| `startTime` |  | number | 日志创建的开始时间，毫秒级时间戳格式，示例：1734048000000；注意：开始时间和结束时间跨度不能超过180天 | 日志创建的开始时间，毫秒级时间戳格式，示例：1734048000000；注意：开始时间和结束时间跨度不能超过180天 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `cursor` | `--cursor` |  |  |  |  |
| `endTime` | `--end` | iso8601_to_millis |  |  |  |
| `modifiedEndTime` | `--modified-end` | iso8601_to_millis |  |  |  |
| `modifiedStartTime` | `--modified-start` | iso8601_to_millis |  |  |  |
| `report_template_name` | `--template-name` |  |  |  |  |
| `size` | `--size` |  |  |  |  |
| `startTime` | `--start` | iso8601_to_millis |  |  |  |

## dws report template detail

- Canonical path: `report.get_template_details_by_name`
- Product: `report`
- Group: `template`
- Subcommand: `detail`
- Title: get_template_details_by_name
- Description: 获取当前员工可使用的日志模版详情信息，包括日志模板Id、日志模板内字段的名称、字段类型、字段排序等
- Required top-level parameters: `report_template_name`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `report_template_name` | yes | string | 日志模板名称 | 日志模板名称 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `report_template_name` | `--name` |  |  |  |  |
