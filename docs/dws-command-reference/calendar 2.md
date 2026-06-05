# dws calendar Commands

日历日程 / 会议室 / 闲忙

Commands in this file: 17

## Command Index

| CLI | Canonical path | Risk |
| --- | --- | --- |
| [`dws calendar add_attachments`](#dws-calendar-addattachments) | `calendar.add_attachments` | `unknown-review-before-use` |
| [`dws calendar participant add`](#dws-calendar-participant-add) | `calendar.add_calendar_participant` | `unknown-review-before-use` |
| [`dws calendar room add`](#dws-calendar-room-add) | `calendar.add_meeting_room` | `unknown-review-before-use` |
| [`dws calendar event create`](#dws-calendar-event-create) | `calendar.create_calendar_event` | `mutating-review-first` |
| [`dws calendar event delete`](#dws-calendar-event-delete) | `calendar.delete_calendar_event` | `sensitive-mutating` |
| [`dws calendar room delete`](#dws-calendar-room-delete) | `calendar.delete_meeting_room` | `sensitive-mutating` |
| [`dws calendar event get`](#dws-calendar-event-get) | `calendar.get_calendar_detail` | `read-only` |
| [`dws calendar participant list`](#dws-calendar-participant-list) | `calendar.get_calendar_participants` | `read-only` |
| [`dws calendar event list`](#dws-calendar-event-list) | `calendar.list_calendar_events` | `read-only` |
| [`dws calendar list_calendars`](#dws-calendar-listcalendars) | `calendar.list_calendars` | `read-only` |
| [`dws calendar room list-groups`](#dws-calendar-room-list-groups) | `calendar.list_meeting_room_groups` | `read-only` |
| [`dws calendar event suggest`](#dws-calendar-event-suggest) | `calendar.list_suggested_event_times` | `read-only` |
| [`dws calendar room search`](#dws-calendar-room-search) | `calendar.query_available_meeting_room` | `read-only` |
| [`dws calendar busy search`](#dws-calendar-busy-search) | `calendar.query_busy_status` | `read-only` |
| [`dws calendar participant delete`](#dws-calendar-participant-delete) | `calendar.remove_calendar_participant` | `sensitive-mutating` |
| [`dws calendar respond`](#dws-calendar-respond) | `calendar.respond` | `unknown-review-before-use` |
| [`dws calendar event update`](#dws-calendar-event-update) | `calendar.update_calendar_event` | `mutating-review-first` |

## Group Summary

| Group | Commands |
| --- | ---: |
| `-` | 3 |
| `busy` | 1 |
| `event` | 6 |
| `participant` | 3 |
| `room` | 4 |

## dws calendar add_attachments

- Canonical path: `calendar.add_attachments`
- Product: `calendar`
- Group: `-`
- Subcommand: `add_attachments`
- Title: 添加日程附件
- Description: 为日程添加附件。请先使用钉盘功能上传附件，得到文件id
- Required top-level parameters: `eventId`, `attachments`
- Sensitive flag from schema: `false`
- Mutation risk: `unknown-review-before-use`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `attachments` | yes | array; items=object | attachments | attachments |
| `attachments[].id` | yes | string | id | 文件id。上传到钉盘的文件fileId |
| `attachments[].name` | yes | string | name | 附件名 |
| `eventId` | yes | string | eventId | 日程id |

### CLI flag overlay

- none

## dws calendar participant add

- Canonical path: `calendar.add_calendar_participant`
- Product: `calendar`
- Group: `participant`
- Subcommand: `add`
- Title: 新增日程参与人
- Description: 向已存在的指定日程添加参与者，支持批量添加多人，可设置参与者类型和通知方式
- Required top-level parameters: `eventId`, `attendeesToAdd`
- Sensitive flag from schema: `false`
- Mutation risk: `unknown-review-before-use`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `attendeesToAdd` | yes | array; items=string | attendeesToAdd | 需要添加的参与人uid列表。 |
| `attendeesToAdd[]` |  | string |  |  |
| `eventId` | yes | string | eventId | 日程ID，可调用创建日程接口或查询日程列表接口获取id参数值 |
| `optional` |  | boolean | optional | 参会人可选。true - 可选参会人，false - 必选参会人。 默认值：false |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `attendeesToAdd` | `--users` | csv_to_array |  |  |  |
| `eventId` | `--event` |  |  |  |  |
| `optional` | `--optional` |  |  |  |  |

## dws calendar room add

- Canonical path: `calendar.add_meeting_room`
- Product: `calendar`
- Group: `room`
- Subcommand: `add`
- Title: 添加会议室
- Description: 添加会议室
- Required top-level parameters: `eventId`, `roomIds`
- Sensitive flag from schema: `false`
- Mutation risk: `unknown-review-before-use`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `eventId` | yes | string | eventId | 日程ID，调用查询日程列表接口获取id参数值。 |
| `roomIds` | yes | array; items=string | roomIds | 需要预定的会议室roomId列表，可调用查询空闲会议室接口获取，一个日程最多添加5个会议室。 |
| `roomIds[]` |  | string |  |  |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `eventId` | `--event` |  |  |  |  |
| `roomIds` | `--rooms` | csv_to_array |  |  |  |

## dws calendar event create

- Canonical path: `calendar.create_calendar_event`
- Product: `calendar`
- Group: `event`
- Subcommand: `create`
- Title: 创建日程
- Description: 创建新的日程，支持设置时间、参与者、提醒等完整功能
- Required top-level parameters: `summary`, `startDateTime`, `endDateTime`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `attendees` |  | array; items=string | attendees | 日程参与人userId列表，最多支持500个参与人。 |
| `attendees[]` |  | string |  |  |
| `description` |  | string | description | 日程描述，纯文本类型，最大不超过5000个字符。 |
| `endDateTime` | yes | string | endDateTime | 日程结束时间，格式为ISO-8601的带时区的date-time格式，例如2025-11-14T10:00:00+08:00。 |
| `freeBusy` |  | string | freeBusy | 此日程的忙碌状态，默认值为busy。 busy - 在忙闲视图中，此日程时间段为忙碌。 free - 此日程不占用忙闲。 |
| `location` |  | string | location | 地点信息。 |
| `openDingTalkIds` |  | array; items=string | openDingTalkIds | openDingTalkId 和attendees至少传一个 |
| `openDingTalkIds[]` |  | string |  |  |
| `recurrence` |  | object | recurrence | 日程循环规则，支持按天、周、年循环发生 |
| `recurrence.pattern` |  | object | pattern | 循环规则 |
| `recurrence.pattern.dayOfMonth` |  | number | dayOfMonth | 用于指定是每个月的第几天。当type为absoluteYearly、absoluteMonthly时，此值必填。 |
| `recurrence.pattern.daysOfWeek` |  | string | daysOfWeek | 日程发生的一周中的天数的集合。可能的值为：sunday, monday, tuesday, wednesday, thursday, friday, saturday。如果有多个值，使用英文逗号分割。 当类型为weekly、relativeMonthly时，此值必填。 |
| `recurrence.pattern.firstDayOfWeek` |  | string | firstDayOfWeek | 一周起始日，可取值包括：sunday、monday、tuesday、wednesday、thursday、friday、saturday。 默认值为sunday。 |
| `recurrence.pattern.index` |  | string | index | 用于指定每月第几周，可取值：first：第一周  second：第二周  third：第三周  fourth：第四周  last：最后一周。当type值为relativeMonthly时，此值必填。 |
| `recurrence.pattern.interval` |  | number | interval | 循环间隔，根据type不同单位不同。 例如： 当type取值为daily时表示间隔N天。 当type取值为absoluteYearly则表示间隔N年。 |
| `recurrence.pattern.type` |  | string | type | 循环规则类型。  daily：每interval天重复  weekly：每interval周的第daysOfWeek天重复  absoluteMonthly：每interval月的第dayOfMonth天重复  relativeMonthly：每interval月的第index周的第daysOfWeek天重复  absoluteYearly：每interval年重复 |
| `recurrence.range` |  | object | range | 循环范围，设定循环的截止时间。 |
| `recurrence.range.endDate` |  | number | endDate | 循环结束时间，时间戳（毫秒级）。当type为endDate时，此值必填。 |
| `recurrence.range.numberOfOccurrences` |  | number | numberOfOccurrences | 循环次数。当type为numbered时，此值必填。 |
| `recurrence.range.type` |  | string | type | 循环范围类型。  noEnd：永不结束  endDate：循环至指定日期结束  numbered：循环指定次数后结束 |
| `richTextDescription` |  | string | richTextDescription | html格式的富文本类型日程描述，用于复杂内容的展示。 |
| `roomIds` |  | array; items=string | roomIds | 需要预定的会议室roomId列表，可调用查询空闲会议室接口获取，先确认会议室在时间段内为空闲状态 |
| `roomIds[]` |  | string |  |  |
| `startDateTime` | yes | string | startDateTime | 日程开始时间，格式为ISO-8601的带时区的date-time格式，例如2025-11-14T10:00:00+08:00。 |
| `summary` | yes | string | summary | 日程标题，最大不超过2048个字符。 |
| `timeZone` |  | string | timeZone | IANA Time Zone Database name. 指定日程时区，比如：Asia/Shanghai. 默认值为Asia/Shanghai |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `attendees` | `--attendees` | csv_to_array |  |  |  |
| `description` | `--desc` |  |  |  |  |
| `endDateTime` | `--end` |  |  |  |  |
| `openDingTalkIds` | `--open-dingtalk-ids` | csv_to_array |  |  |  |
| `startDateTime` | `--start` |  |  |  |  |
| `summary` | `--title` |  |  |  |  |
| `timeZone` | `--timezone` |  |  |  |  |

## dws calendar event delete

- Canonical path: `calendar.delete_calendar_event`
- Product: `calendar`
- Group: `event`
- Subcommand: `delete`
- Title: 删除指定日程
- Description: 删除指定日程，组织者删除将通知所有参与者，参与者删除仅从自己日历移除
- Required top-level parameters: `eventId`
- Sensitive flag from schema: `true`
- Mutation risk: `sensitive-mutating`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `eventId` | yes | string | eventId | 日程ID，可调用创建日程接口或查询日程列表接口获取id参数值。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `eventId` | `--id` |  |  |  |  |

## dws calendar room delete

- Canonical path: `calendar.delete_meeting_room`
- Product: `calendar`
- Group: `room`
- Subcommand: `delete`
- Title: 删除会议室
- Description: 移除日程中预约的会议室
- Required top-level parameters: `eventId`, `roomIds`
- Sensitive flag from schema: `true`
- Mutation risk: `sensitive-mutating`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `eventId` | yes | string | eventId | 日程ID，调用查询日程列表接口获取id参数值。 |
| `roomIds` | yes | array; items=string | roomIds | 需要删除的会议室roomId列表 |
| `roomIds[]` |  | string |  |  |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `eventId` | `--event` |  |  |  |  |
| `roomIds` | `--rooms` | csv_to_array |  |  |  |

## dws calendar event get

- Canonical path: `calendar.get_calendar_detail`
- Product: `calendar`
- Group: `event`
- Subcommand: `get`
- Title: 查询日程详情
- Description: 获取我的日历指定日程的详细信息
- Required top-level parameters: `eventId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `calendarId` |  | string | calendarId | 日历id。查询指定日历下的日程信息。默认值为primary (主日历)；大部分场景无需传此值，仅当用户要求查询其他日历本时。calendarId可通过"查询用户日历列表"获取。 |
| `eventId` | yes | string | eventId | 日程ID，可调用创建日程接口或查询日程列表接口获取id参数值 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `eventId` | `--id` |  |  |  |  |

## dws calendar participant list

- Canonical path: `calendar.get_calendar_participants`
- Product: `calendar`
- Group: `participant`
- Subcommand: `list`
- Title: 获取日程参与者及状态
- Description: 获取指定日程的所有参与者列表及其状态信息
- Required top-level parameters: `eventId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `eventId` | yes | string | eventId | 日程ID，可调用创建日程接口或查询日程列表接口获取id参数值。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `eventId` | `--event` |  |  |  |  |

## dws calendar event list

- Canonical path: `calendar.list_calendar_events`
- Product: `calendar`
- Group: `event`
- Subcommand: `list`
- Title: 查询日程列表
- Description: 仅允许查询当前用户指定时间范围内的日程列表，最多返回100条。
- Required top-level parameters: -
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `calendarId` |  | string | calendarId | 日历id。查询指定日历下的日程信息。默认值为primary (主日历)；大部分场景无需传此值，仅当用户要求查询其他日历本时。calendarId可通过"查询用户日历列表"获取。 |
| `endTime` |  | number | endTime | 日程结束时间，时间戳（毫秒级） |
| `startTime` |  | number | startTime | 日程开始时间，时间戳（毫秒级） |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `endTime` | `--end` | iso8601_to_millis |  |  |  |
| `startTime` | `--start` | iso8601_to_millis |  |  |  |

## dws calendar list_calendars

- Canonical path: `calendar.list_calendars`
- Product: `calendar`
- Group: `-`
- Subcommand: `list_calendars`
- Title: 查询用户日历列表
- Description: 查询用户的所有日历，其中id = "primary"为主日历（name = "我的日历"），为固定值。
- Required top-level parameters: -
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

- none

### CLI flag overlay

- none

## dws calendar room list-groups

- Canonical path: `calendar.list_meeting_room_groups`
- Product: `calendar`
- Group: `room`
- Subcommand: `list-groups`
- Title: 查询会议室分组列表
- Description: 分页查询当前企业下的会议室分组列表，返回每个分组的名称（groupName）、唯一 ID（groupId）及其父分组 ID（parentId，0 表示根分组）。结果按组织架构权限过滤，仅包含调用者有权限查看的分组。
- Required top-level parameters: -
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `pageIndex` |  | string | pageIndex | 分页开始位置 ，不填默认 0 |
| `pageSize` |  | string | pageSize | 页大小，不填默认 100。超过100的，按照100来处理 |

### CLI flag overlay

- none

## dws calendar event suggest

- Canonical path: `calendar.list_suggested_event_times`
- Product: `calendar`
- Group: `event`
- Subcommand: `suggest`
- Title: 建议日程时间
- Description: 可根据参会人员的信息，推荐日程时间。用于日程时间未确定，解决会议时间协调问题。
- Required top-level parameters: -
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `attendeeUserIds` |  | array; items=string | attendeeUserIds | attendeeUserIds |
| `attendeeUserIds[]` |  | string |  |  |
| `durationMinutes` |  | string | durationMinutes | 日程持续时间，单位：分钟。默认为30分钟 |
| `end` |  | string | end | 推荐时间范围：结束时间点 ISO8601 格式，如：2025-12-30T18:00:00+08:00 默认为次日18点 |
| `start` |  | string | start | 推荐时间范围：开始时间点。 ISO8601 格式，如：2025-12-30T12:00:00+08:00。 默认为当前时间。 |
| `timeZone` |  | string | timeZone | 时区，IANA时区格式。默认为系统时区，即Asia/Shanghai。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `attendeeUserIds` | `--users` |  |  |  |  |
| `durationMinutes` | `--duration` |  |  |  |  |
| `end` | `--end` |  |  |  |  |
| `start` | `--start` |  |  |  |  |
| `timeZone` | `--timezone` |  |  |  |  |

## dws calendar room search

- Canonical path: `calendar.query_available_meeting_room`
- Product: `calendar`
- Group: `room`
- Subcommand: `search`
- Title: 查询空闲会议室
- Description: 根据时间筛选出符合闲忙条件的会议室列表。
- Required top-level parameters: `startTime`, `endTime`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `endTime` | yes | string | endTime | 结束时间，时间戳（毫秒级） |
| `groupId` |  | string | groupId | 会议室分组ID。可选字段。若不填写，则默认查询根目录下的空闲会议室。 建议使用方式：首次查询时请留空此字段；若因当前企业会议室数量超过100条而返回错误，请先调用分页查询当前企业下的会议室分组列表，再根据具体的分组ID分别查询各分组下的会议室数据。 |
| `roomName` |  | string | roomName | 会议室名 |
| `startTime` | yes | string | startTime | 开始时间，时间戳（毫秒级） |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `endTime` | `--end` | iso8601_to_millis |  |  |  |
| `groupId` | `--group-id` |  |  |  |  |
| `needAvailable` | `--available` |  |  |  |  |
| `startTime` | `--start` | iso8601_to_millis |  |  |  |

## dws calendar busy search

- Canonical path: `calendar.query_busy_status`
- Product: `calendar`
- Group: `busy`
- Subcommand: `search`
- Title: 获取用户闲忙信息
- Description: 可查询指定用户或者会议室在给定时间范围内的闲忙状态，返回已占用时间段的信息（注意只有时间信息）。结果受组织可见性策略控制：仅当调用者有权限查看该用户日历时方可获取有效数据。适用于安排会议前快速确认他人可用时间，或者查看指定会议室的预订状态。
- Required top-level parameters: `startTime`, `endTime`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `endTime` | yes | number | endTime | 查询的结束时间，时间戳（毫秒级）格式。 |
| `roomIds` |  | array; items=string | roomIds | 待查询的会议室id列表 |
| `roomIds[]` |  | string |  |  |
| `startTime` | yes | number | startTime | 查询的开始时间，时间戳（毫秒级）格式。 |
| `userIds` |  | array; items=string | userIds | 用户uid列表最大长度 20。 |
| `userIds[]` |  | string |  |  |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `endTime` | `--end` | iso8601_to_millis |  |  |  |
| `startTime` | `--start` | iso8601_to_millis |  |  |  |
| `userIds` | `--users` | csv_to_array |  |  |  |

## dws calendar participant delete

- Canonical path: `calendar.remove_calendar_participant`
- Product: `calendar`
- Group: `participant`
- Subcommand: `delete`
- Title: 删除日程参与人
- Description: 从已存在的指定日程中移除参与者，支持批量移除多人
- Required top-level parameters: `eventId`, `attendeesToRemove`
- Sensitive flag from schema: `true`
- Mutation risk: `sensitive-mutating`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `attendeesToRemove` | yes | array; items=string | attendeesToRemove | 需要被删除的日程参与者userId列表。 |
| `attendeesToRemove[]` |  | string |  |  |
| `eventId` | yes | string | eventId | 日程ID，可调用创建日程接口或查询日程列表接口获取id参数值。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `attendeesToRemove` | `--users` | csv_to_array |  |  |  |
| `eventId` | `--event` |  |  |  |  |

## dws calendar respond

- Canonical path: `calendar.respond`
- Product: `calendar`
- Group: `-`
- Subcommand: `respond`
- Title: 响应日程
- Description: 作为日程参会人，设置自己的响应状态（接受、拒绝、暂定）。
- Required top-level parameters: `eventId`, `responseStatus`
- Sensitive flag from schema: `false`
- Mutation risk: `unknown-review-before-use`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `eventId` | yes | string | eventId | 日程id |
| `responseStatus` | yes | string | responseStatus | 设置响应状态，可取值和对应说明如下： 1. needsAction：未操作（默认值） 2. accepted：接受 3. declined：拒绝 4. tentative：暂定 |

### CLI flag overlay

- none

## dws calendar event update

- Canonical path: `calendar.update_calendar_event`
- Product: `calendar`
- Group: `event`
- Subcommand: `update`
- Title: 修改日程
- Description: 修改现有日程的信息，支持更新标题、时间、地点等任意字段，需要组织者权限。（修改参与人需要使用给日程添加参与人或给日程删除参与人工具）
- Required top-level parameters: `eventId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `description` |  | string | description | 日程描述，最大不超过5000个字符。 |
| `endDateTime` |  | string | endDateTime | 日程结束时间，格式为ISO-8601的带时区的date-time格式，例如2025-11-14T10:00:00+08:00。 |
| `eventId` | yes | string | eventId | 日程ID，可调用创建日程接口或查询日程列表接口获取id参数值 |
| `freeBusy` |  | string | freeBusy | 修改此日程的忙碌状态。busy - 在忙闲视图中，此日程时间段为忙碌。free - 此日程时间段不占用忙闲。 |
| `location` |  | string | location | 地点信息 |
| `recurrence` |  | object | recurrence | 日程循环规则，支持按天、周、年循环发生。 原日程为普通日程，设置该值后，将改为周期日程。 原日程为周期日程，设置该值后，将修改周期规则。 |
| `recurrence.pattern` |  | object | pattern | 循环规则 |
| `recurrence.pattern.dayOfMonth` |  | number | dayOfMonth | 用于指定是每个月的第几天。当type为absoluteYearly、absoluteMonthly时，此值必填。 |
| `recurrence.pattern.daysOfWeek` |  | string | daysOfWeek | 日程发生的一周中的天数的集合。可能的值为：sunday, monday, tuesday, wednesday, thursday, friday, saturday。如果有多个值，使用英文逗号分割。 当类型为weekly、relativeMonthly时，此值必填。 |
| `recurrence.pattern.firstDayOfWeek` |  | string | firstDayOfWeek | 一周起始日，可取值包括：sunday、monday、tuesday、wednesday、thursday、friday、saturday。 默认值为sunday。 |
| `recurrence.pattern.index` |  | string | index | 用于指定每月第几周，可取值：first：第一周  second：第二周  third：第三周  fourth：第四周  last：最后一周。当type值为relativeMonthly时，此值必填。 |
| `recurrence.pattern.interval` |  | number | interval | 循环间隔，根据type不同单位不同。 例如： 当type取值为daily时表示间隔N天。 当type取值为absoluteYearly则表示间隔N年。 |
| `recurrence.pattern.type` |  | string | type | 循环规则类型。  daily：每interval天重复  weekly：每interval周的第daysOfWeek天重复  absoluteMonthly：每interval月的第dayOfMonth天重复  relativeMonthly：每interval月的第index周的第daysOfWeek天重复  absoluteYearly：每interval年重复 |
| `recurrence.range` |  | object | range | 循环范围，设定循环的截止时间。 |
| `recurrence.range.endDate` |  | number | endDate | 循环结束时间，时间戳（毫秒级）。当type为endDate时，此值必填。 |
| `recurrence.range.numberOfOccurrences` |  | number | numberOfOccurrences | 循环次数。当type为numbered时，此值必填。 |
| `recurrence.range.type` |  | string | type | 循环范围类型。  noEnd：永不结束  endDate：循环至指定日期结束  numbered：循环指定次数后结束 |
| `richTextDescription` |  | string | richTextDescription | html格式的富文本类型日程描述，用于复杂内容的展示。 |
| `startDateTime` |  | string | startDateTime | 日程开始时间，格式为ISO-8601的带时区的date-time格式，例如2025-11-14T10:00:00+08:00。 |
| `summary` |  | string | summary | 日程标题，最大不超过2048个字符。 |
| `timeZone` |  | string | timeZone | IANA Time Zone Database name. 指定日程时区，比如：Asia/Shanghai. 默认值为Asia/Shanghai |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `description` | `--desc` |  |  |  |  |
| `endDateTime` | `--end` |  |  |  |  |
| `eventId` | `--id` |  |  |  |  |
| `startDateTime` | `--start` |  |  |  |  |
| `summary` | `--title` |  |  |  |  |
| `timeZone` | `--timezone` |  |  |  |  |
