# dws oa Commands

OA 审批 / 同意 / 拒绝 / 撤销

Commands in this file: 18

## Command Index

| CLI | Canonical path | Risk |
| --- | --- | --- |
| [`dws oa approval approve`](#dws-oa-approval-approve) | `oa.approve_processInstance` | `mutating-review-first` |
| [`dws oa dingflow_comments`](#dws-oa-dingflowcomments) | `oa.dingflow_comments` | `mutating-review-first` |
| [`dws oa get_done_tasks`](#dws-oa-getdonetasks) | `oa.get_done_tasks` | `read-only` |
| [`dws oa get_noticed_instances`](#dws-oa-getnoticedinstances) | `oa.get_noticed_instances` | `read-only` |
| [`dws oa approval detail`](#dws-oa-approval-detail) | `oa.get_processInstance_detail` | `read-only` |
| [`dws oa approval records`](#dws-oa-approval-records) | `oa.get_processInstance_records` | `read-only` |
| [`dws oa get_processInstances`](#dws-oa-getprocessinstances) | `oa.get_processInstances` | `read-only` |
| [`dws oa get_submitted_instances`](#dws-oa-getsubmittedinstances) | `oa.get_submitted_instances` | `read-only` |
| [`dws oa get_todo_tasks`](#dws-oa-gettodotasks) | `oa.get_todo_tasks` | `read-only` |
| [`dws oa approval list-initiated`](#dws-oa-approval-list-initiated) | `oa.list_initiated_instances` | `read-only` |
| [`dws oa approval list-pending`](#dws-oa-approval-list-pending) | `oa.list_pending_approvals` | `read-only` |
| [`dws oa list_pending_approvals_for_me`](#dws-oa-listpendingapprovalsforme) | `oa.list_pending_approvals_for_me` | `read-only` |
| [`dws oa approval tasks`](#dws-oa-approval-tasks) | `oa.list_pending_tasks` | `read-only` |
| [`dws oa approval list-forms`](#dws-oa-approval-list-forms) | `oa.list_user_visible_process` | `read-only` |
| [`dws oa oa_cc_noticer`](#dws-oa-oaccnoticer) | `oa.oa_cc_noticer` | `unknown-review-before-use` |
| [`dws oa redirect_task`](#dws-oa-redirecttask) | `oa.redirect_task` | `unknown-review-before-use` |
| [`dws oa approval reject`](#dws-oa-approval-reject) | `oa.reject_processInstance` | `mutating-review-first` |
| [`dws oa approval revoke`](#dws-oa-approval-revoke) | `oa.revoke_processInstance` | `sensitive-mutating` |

## Group Summary

| Group | Commands |
| --- | ---: |
| `-` | 9 |
| `approval` | 9 |

## dws oa approval approve

- Canonical path: `oa.approve_processInstance`
- Product: `oa`
- Group: `approval`
- Subcommand: `approve`
- Title: approve_processInstance
- Description: 处理某个需要我处理的实例任务，拒绝审批实例任务，所需要的参数processInstanceId可以从list_pending_approvals工具获取。
- Required top-level parameters: `processInstanceId`, `taskId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `processInstanceId` | yes | string | 审批实例Id | 审批实例Id |
| `remark` |  | string | 审批意见 | 审批意见 |
| `taskId` | yes | number | 审批任务Id | 审批任务ID |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `processInstanceId` | `--instance-id` |  |  |  |  |
| `remark` | `--remark` |  |  |  |  |
| `taskId` | `--task-id` |  |  |  |  |

## dws oa dingflow_comments

- Canonical path: `oa.dingflow_comments`
- Product: `oa`
- Group: `-`
- Subcommand: `dingflow_comments`
- Title: dingflow_comments
- Description: 用户添加审批评论
- Required top-level parameters: `processInstanceId`, `text`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `commentStaffId` |  | string | commentStaffId | commentStaffId |
| `processInstanceId` | yes | string | processInstanceId | processInstanceId |
| `text` | yes | string | text | text |

### CLI flag overlay

- none

## dws oa get_done_tasks

- Canonical path: `oa.get_done_tasks`
- Product: `oa`
- Group: `-`
- Subcommand: `get_done_tasks`
- Title: get_done_tasks
- Description: 获取员工已处理任务列表
- Required top-level parameters: `pageSize`, `pageNumber`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `pageNumber` | yes | number | 页号 | 页号 |
| `pageSize` | yes | number | 页数 | 页数 |
| `query` |  | string | 查询字段 | 查询字段 |
| `userId` |  | string | 员工id | 员工id |

### CLI flag overlay

- none

## dws oa get_noticed_instances

- Canonical path: `oa.get_noticed_instances`
- Product: `oa`
- Group: `-`
- Subcommand: `get_noticed_instances`
- Title: get_noticed_instances
- Description: 获取抄送用户的列表
- Required top-level parameters: `pageSize`, `pageNumber`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `pageNumber` | yes | number | 页号 | 页号 |
| `pageSize` | yes | number | 页数 | 页数 |
| `query` |  | string | query | query |
| `userId` |  | string | 员工id | 员工id |

### CLI flag overlay

- none

## dws oa approval detail

- Canonical path: `oa.get_processInstance_detail`
- Product: `oa`
- Group: `approval`
- Subcommand: `detail`
- Title: get_processInstance_detail
- Description: 获取指定审批实例的详情信息
- Required top-level parameters: `processInstanceId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `processInstanceId` | yes | string | 审批实例Id | 审批实例Id |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `processInstanceId` | `--instance-id` |  |  |  |  |

## dws oa approval records

- Canonical path: `oa.get_processInstance_records`
- Product: `oa`
- Group: `approval`
- Subcommand: `records`
- Title: get_processInstance_records
- Description: 获取某个审批实例的审批操作记录信息，获取的是该审批实例有哪些人做了什么操作，以及操作结果是什么
- Required top-level parameters: `processInstanceId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `processInstanceId` | yes | string | 审批实例Id | 审批实例Id |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `processInstanceId` | `--instance-id` |  |  |  |  |

## dws oa get_processInstances

- Canonical path: `oa.get_processInstances`
- Product: `oa`
- Group: `-`
- Subcommand: `get_processInstances`
- Title: get_processInstances
- Description: 查询用户审批单
- Required top-level parameters: `processCode`, `processType`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `endTime` |  | number | 审批单创建时间的结束时间。毫秒时间戳格式，表示时间范围的上限 | 审批单创建时间的结束时间。毫秒时间戳格式，表示时间范围的上限。 |
| `pageNum` |  | number | 分页页码，从1开始 | 分页页码，从1开始 |
| `pageSize` |  | number | 每页大小，最大20 | 每页大小 |
| `processCode` | yes | string | processCode | 模版code |
| `processInstanceResult` |  | string | processInstanceResult | 审批结果（同意：agree, 拒绝：refuse） |
| `processInstanceStatus` |  | string | processInstanceStatus | 审批状态（如 RUNNING、COMPLETED） |
| `processType` | yes | string | processType | 流程类型（我提交的：SUBMITTED，待处理的：TODO，抄送我的：NOTIFIED，已处理的：PROCESSED） |
| `starTime` |  | number | 审批单创建时间的开始时间。毫秒时间戳格式，表示时间范围的下限 | 审批单创建时间的开始时间。毫秒时间戳格式，表示时间范围的下限 |
| `withProcessInfo` |  | boolean | withProcessInfo | 实例信息，设置为 true 会返回流程信息 |
| `withSummary` |  | boolean | withSummary | 表单摘要选项，设置为 true 会返回摘要信息 |

### CLI flag overlay

- none

## dws oa get_submitted_instances

- Canonical path: `oa.get_submitted_instances`
- Product: `oa`
- Group: `-`
- Subcommand: `get_submitted_instances`
- Title: get_submitted_instances
- Description: 获取已提交实例列表
- Required top-level parameters: `pageSize`, `pageNumber`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `pageNumber` | yes | number | 页号 | 页号 |
| `pageSize` | yes | number | 页数 | 页数 |
| `query` |  | string | 搜索字段 | 搜索字段 |
| `userId` |  | string | 员工id | 员工id |

### CLI flag overlay

- none

## dws oa get_todo_tasks

- Canonical path: `oa.get_todo_tasks`
- Product: `oa`
- Group: `-`
- Subcommand: `get_todo_tasks`
- Title: get_todo_tasks
- Description: 可查询企业内指定用户待处理的审批任务列表
- Required top-level parameters: `pageSize`, `pageNumber`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `createBefore` |  | string | 创建时间 | 创建时间 |
| `pageNumber` | yes | number | 页号 | 页号 |
| `pageSize` | yes | number | 页数 | 页数 |
| `userId` |  | string | 员工id | 员工id |

### CLI flag overlay

- none

## dws oa approval list-initiated

- Canonical path: `oa.list_initiated_instances`
- Product: `oa`
- Group: `approval`
- Subcommand: `list-initiated`
- Title: list_initiated_instances
- Description: 查询当前用户已发起的审批实例列表
- Required top-level parameters: `processCode`, `startTime`, `endTime`, `nextToken`, `maxResults`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `endTime` | yes | number | 审批实例开始时间，Unix时间戳，单位毫秒 | 审批实例开始时间，Unix时间戳，单位毫秒 |
| `maxResults` | yes | number | 分页查询的每页大小，最大值20 | 分页查询的每页大小，最大值20 |
| `nextToken` | yes | number | 分页查询的分页游标，如果是首次查询，该参数传0，非首次调用，传上次返回的nextToken | 分页查询的分页游标，如果是首次查询，该参数传0，非首次调用，传上次返回的nextToken |
| `processCode` | yes | string | 需要查询实例列表的表单processCode | 需要查询实例列表的表单processCode |
| `startTime` | yes | number | 审批实例开始时间，Unix时间戳，单位毫秒 | 审批实例开始时间，Unix时间戳，单位毫秒 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `endTime` | `--end` | iso8601_to_millis |  |  |  |
| `maxResults` | `--max-results` |  | 20 |  |  |
| `nextToken` | `--next-token` |  | 0 |  |  |
| `processCode` | `--process-code` |  |  |  |  |
| `startTime` | `--start` | iso8601_to_millis |  |  |  |

## dws oa approval list-pending

- Canonical path: `oa.list_pending_approvals`
- Product: `oa`
- Group: `approval`
- Subcommand: `list-pending`
- Title: list_pending_approvals
- Description: 查询当前用户待处理的审批单列表，返回每条审批单的名称、唯一编码（如审批实例 ID）、处理跳转链接（用于一键进入审批页面）等关键信息。结果仅包含用户作为审批人且尚未处理的审批事项，适用于工作台待办集成、审批提醒等场景。
- Required top-level parameters: `endTime`, `starTime`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `endTime` | yes | number | 审批单创建时间的结束时间。毫秒时间戳格式，表示时间范围的上限 | 审批单创建时间的结束时间。毫秒时间戳格式，表示时间范围的上限。 |
| `pageNum` |  | number | 分页页码，从1开始 | 分页页码，从1开始 |
| `pageSize` |  | number | 每页大小，最大20 | 每页大小 |
| `query` |  | string | query | query |
| `starTime` | yes | number | 审批单创建时间的开始时间。毫秒时间戳格式，表示时间范围的下限 | 审批单创建时间的开始时间。毫秒时间戳格式，表示时间范围的下限 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `endTime` | `--end` | iso8601_to_millis |  |  |  |
| `pageNum` | `--page` |  |  |  |  |
| `pageSize` | `--size` |  |  |  |  |
| `starTime` | `--start` | iso8601_to_millis |  |  |  |

## dws oa list_pending_approvals_for_me

- Canonical path: `oa.list_pending_approvals_for_me`
- Product: `oa`
- Group: `-`
- Subcommand: `list_pending_approvals_for_me`
- Title: list_pending_approvals_for_me
- Description: 查询当前用户待处理的审批单列表，返回每条审批单的名称、唯一编码（如审批实例 ID）、处理跳转链接（用于一键进入审批页面）等关键信息。结果仅包含用户作为审批人且尚未处理的审批事项，适用于工作台待办集成、审批提醒等场景。
- Required top-level parameters: `createTimeTo`, `createTimeFrom`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `createTimeFrom` | yes | string | createTimeFrom | 审批单创建时间的开始时间。毫秒时间戳格式，表示时间范围的下限 |
| `createTimeTo` | yes | string | createTimeTo | 审批单创建时间的结束时间。毫秒时间戳格式，表示时间范围的上限。 |
| `pageNum` |  | number | pageNum | 分页页码 |
| `pageSize` |  | number | pageSize | 每页大小。 |

### CLI flag overlay

- none

## dws oa approval tasks

- Canonical path: `oa.list_pending_tasks`
- Product: `oa`
- Group: `approval`
- Subcommand: `tasks`
- Title: query_pending_tasks
- Description: 查询待我审批的任务Id，获取任务Id之后，可以执行同意、拒绝审批单操作。
- Required top-level parameters: `processInstanceId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `processInstanceId` | yes | string | 待我处理的审批实例Id，可通过list_pending_approvals工具获取 | 待我处理的审批实例Id，可通过list_pending_approvals工具获取 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `processInstanceId` | `--instance-id` |  |  |  |  |

## dws oa approval list-forms

- Canonical path: `oa.list_user_visible_process`
- Product: `oa`
- Group: `approval`
- Subcommand: `list-forms`
- Title: list_user_visible_process
- Description: 获取当前用户可见的审批表单列表，可获取审批表单的processCode。
- Required top-level parameters: `cursor`, `pageSize`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `cursor` | yes | number | 分页游标，首次调用需要传0 | 分页游标，首次调用需要传0 |
| `pageSize` | yes | number | 每页大小，最大值100 | 每页大小，最大值100 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `cursor` | `--cursor` |  |  |  |  |
| `pageSize` | `--size` |  |  |  |  |

## dws oa oa_cc_noticer

- Canonical path: `oa.oa_cc_noticer`
- Product: `oa`
- Group: `-`
- Subcommand: `oa_cc_noticer`
- Title: oa_cc_noticer
- Description: 审批抄送通知
- Required top-level parameters: `processInstanceId`, `userList`
- Sensitive flag from schema: `false`
- Mutation risk: `unknown-review-before-use`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `operatorId` |  | string | operatorId | operatorId |
| `processInstanceId` | yes | string | processInstanceId | processInstanceId |
| `userList` | yes | array; items=string | userList | userList |
| `userList[]` |  | string |  |  |

### CLI flag overlay

- none

## dws oa redirect_task

- Canonical path: `oa.redirect_task`
- Product: `oa`
- Group: `-`
- Subcommand: `redirect_task`
- Title: redirect_task
- Description: 转交审批任务
- Required top-level parameters: `taskId`, `toActionerId`
- Sensitive flag from schema: `false`
- Mutation risk: `unknown-review-before-use`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `operateUserId` |  | string | operateUserId | 操作人的用户ID |
| `remark` |  | string | remark | 备注信息 |
| `taskId` | yes | string | taskId | 审批任务ID，必填 |
| `toActionerId` | yes | string | toActionerId | 转交目标用户的用户ID |

### CLI flag overlay

- none

## dws oa approval reject

- Canonical path: `oa.reject_processInstance`
- Product: `oa`
- Group: `approval`
- Subcommand: `reject`
- Title: reject_processInstance
- Description: 处理某个需要我处理的实例任务，拒绝审批实例任务，所需要的参数processInstanceId可以从list_pending_approvals工具获取。
- Required top-level parameters: `processInstanceId`, `taskId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `processInstanceId` | yes | string | 审批实例Id | 审批实例Id |
| `remark` |  | string | 审批意见 | 审批意见 |
| `taskId` | yes | number | 审批任务Id | 审批任务ID |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `processInstanceId` | `--instance-id` |  |  |  |  |
| `remark` | `--remark` |  |  |  |  |
| `taskId` | `--task-id` |  |  |  |  |

## dws oa approval revoke

- Canonical path: `oa.revoke_processInstance`
- Product: `oa`
- Group: `approval`
- Subcommand: `revoke`
- Title: revoke_processInstance
- Description: 撤销当前用户已经发起的审批实例，需要的参数processInstanceId可以从
- Required top-level parameters: `processInstanceId`
- Sensitive flag from schema: `true`
- Mutation risk: `sensitive-mutating`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `processInstanceId` | yes | string | 需要撤销的审批实例Id | 需要撤销的审批实例Id |
| `remark` |  | string | 撤销审批的说明 | 撤销审批的说明 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `processInstanceId` | `--instance-id` |  |  |  |  |
| `remark` | `--remark` |  |  |  |  |

