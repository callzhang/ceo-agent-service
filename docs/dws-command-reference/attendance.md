# dws attendance Commands

考勤打卡 / 排班 / 统计

Commands in this file: 4

## Command Index

| CLI | Canonical path | Risk |
| --- | --- | --- |
| [`dws attendance shift list`](#dws-attendance-shift-list) | `attendance.batch_get_employee_shifts` | `read-only` |
| [`dws attendance summary`](#dws-attendance-summary) | `attendance.get_attendance_summary` | `read-only` |
| [`dws attendance record get`](#dws-attendance-record-get) | `attendance.get_user_attendance_record` | `read-only` |
| [`dws attendance rules`](#dws-attendance-rules) | `attendance.query_attendance_group_or_rules` | `read-only` |

## Group Summary

| Group | Commands |
| --- | ---: |
| `-` | 2 |
| `record` | 1 |
| `shift` | 1 |

## dws attendance shift list

- Canonical path: `attendance.batch_get_employee_shifts`
- Product: `attendance`
- Group: `shift`
- Subcommand: `list`
- Title: batch_get_employee_shifts
- Description: 批量查询多个员工在指定日期的考勤班次信息，返回每条记录包含：用户 ID（userId）、工作日期（workDate，毫秒时间戳）、打卡类型（checkType，如 OnDuty 表示上班）、计划打卡时间（planCheckTime，毫秒时间戳）以及是否为休息日（isRest，"Y"/"N"）。结果基于组织考勤配置生成，仅返回调用者有权限查看的员工数据，适用于排班核对、考勤预览等场景。
- Required top-level parameters: `userIds`, `fromDateTime`, `toDateTime`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `fromDateTime` | yes | number | fromDateTime | 起始日期，Unix时间戳，单位毫秒。 开始时间和结束时间的间隔不能超过7天。 查询时间限制距今180天内。 |
| `toDateTime` | yes | number | toDateTime | 结束日期，Unix时间戳，单位毫秒。开始时间和结束时间的间隔不能超过7天。 查询时间限制距今180天内。 |
| `userIds` | yes | array; items=string | userIds | 要查询的人员userId列表，多个userId用列表表示，一次最多可传50个。 |
| `userIds[]` |  | string |  |  |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `fromDateTime` | `--start` | iso8601_to_millis |  |  |  |
| `toDateTime` | `--end` | iso8601_to_millis |  |  |  |
| `userIds` | `--users` | csv_to_array |  |  |  |

## dws attendance summary

- Canonical path: `attendance.get_attendance_summary`
- Product: `attendance`
- Group: `-`
- Subcommand: `summary`
- Title: get_attendance_summary
- Description: 获取考勤统计摘要
- Required top-level parameters: -
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `QueryUserAttendVO` |  | object | QueryUserAttendVO | QueryUserAttendVO |
| `QueryUserAttendVO.corpId` |  | string | corpId | 企业ID |
| `QueryUserAttendVO.opUserId` |  | string | opUserId | 操作者的userid |
| `QueryUserAttendVO.queryDate` |  | string | queryDate | 查询日期 |
| `QueryUserAttendVO.statsType` |  | string | statsType | 统计类型，支持周统计(week)或者月统计(month) |
| `QueryUserAttendVO.tagName` |  | string | tagName | tagName |
| `QueryUserAttendVO.userId` |  | string | userId | 用户ID |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `QueryUserAttendVO.userId` | `--user` |  |  |  |  |
| `QueryUserAttendVO.workDate` | `--date` |  |  |  |  |

## dws attendance record get

- Canonical path: `attendance.get_user_attendance_record`
- Product: `attendance`
- Group: `record`
- Subcommand: `get`
- Title: 查询指定用户某一天的考勤详情
- Description: 查询指定用户在某一天的考勤详情，包括实际打卡记录（如上班/下班时间、是否正常打卡）、当日所排班次、所属考勤组信息、是否为休息日、出勤工时（如 "0Hours"）、加班时长等。返回数据受组织权限和隐私策略限制，仅当调用者有权限查看该用户考勤信息时才返回有效内容。适用于员工自助查询、HR 核对出勤或审批关联场景。
- Required top-level parameters: `userId`, `workDate`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `UserAttendDetailParam` |  | object | UserAttendDetailParam | UserAttendDetailParam |
| `UserAttendDetailParam.queryInvalidRecord` |  | boolean | queryInvalidRecord | 是否查询无效的打卡记录，默认为true |
| `userId` | yes | string | userId | 要查询的用户的userId |
| `workDate` | yes | number | workDate | 要查询的日期的Unix时间戳，仅保留日期信息，单位毫秒。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `userId` | `--user` |  |  |  |  |
| `workDate` | `--date` | iso8601_to_millis |  |  |  |

## dws attendance rules

- Canonical path: `attendance.query_attendance_group_or_rules`
- Product: `attendance`
- Group: `-`
- Subcommand: `rules`
- Title: query_attendance_group_or_rules
- Description: 查询考勤组/考勤规则："我属于哪个考勤组""我们的打卡范围是什么""弹性工时是怎么算的"
- Required top-level parameters: `date`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `date` | yes | string | 考勤日期 格式：yyyy-MM-dd HH:mm:ss | 考勤日期 格式：yyyy-MM-dd HH:mm:ss |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `date` | `--date` |  |  |  |  |

