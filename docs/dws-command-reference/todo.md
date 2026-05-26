# dws todo Commands

待办任务管理

Commands in this file: 17

## Command Index

| CLI | Canonical path | Risk |
| --- | --- | --- |
| [`dws todo add_task_executors`](#dws-todo-addtaskexecutors) | `todo.add_task_executors` | `unknown-review-before-use` |
| [`dws todo add_task_participants`](#dws-todo-addtaskparticipants) | `todo.add_task_participants` | `unknown-review-before-use` |
| [`dws todo add_todo_comment`](#dws-todo-addtodocomment) | `todo.add_todo_comment` | `mutating-review-first` |
| [`dws todo add_todo_reminder`](#dws-todo-addtodoreminder) | `todo.add_todo_reminder` | `unknown-review-before-use` |
| [`dws todo create_personal_sub_todo`](#dws-todo-createpersonalsubtodo) | `todo.create_personal_sub_todo` | `mutating-review-first` |
| [`dws todo task create`](#dws-todo-task-create) | `todo.create_personal_todo` | `mutating-review-first` |
| [`dws todo task delete`](#dws-todo-task-delete) | `todo.delete_todo` | `sensitive-mutating` |
| [`dws todo delete_todo_comment`](#dws-todo-deletetodocomment) | `todo.delete_todo_comment` | `mutating-review-first` |
| [`dws todo task get`](#dws-todo-task-get) | `todo.get_todo_detail` | `read-only` |
| [`dws todo task list`](#dws-todo-task-list) | `todo.get_user_todos_in_current_org` | `read-only` |
| [`dws todo list_todo_comment`](#dws-todo-listtodocomment) | `todo.list_todo_comment` | `mutating-review-first` |
| [`dws todo query_todo_detail`](#dws-todo-querytododetail) | `todo.query_todo_detail` | `read-only` |
| [`dws todo remove_task_executors`](#dws-todo-removetaskexecutors) | `todo.remove_task_executors` | `mutating-review-first` |
| [`dws todo remove_task_participants`](#dws-todo-removetaskparticipants) | `todo.remove_task_participants` | `mutating-review-first` |
| [`dws todo reset_todo_reminder`](#dws-todo-resettodoreminder) | `todo.reset_todo_reminder` | `mutating-review-first` |
| [`dws todo task done`](#dws-todo-task-done) | `todo.update_todo_done_status` | `mutating-review-first` |
| [`dws todo task update`](#dws-todo-task-update) | `todo.update_todo_task` | `mutating-review-first` |

## Group Summary

| Group | Commands |
| --- | ---: |
| `-` | 11 |
| `task` | 6 |

## dws todo add_task_executors

- Canonical path: `todo.add_task_executors`
- Product: `todo`
- Group: `-`
- Subcommand: `add_task_executors`
- Title: add_task_executors
- Description: 添加待办执行人
- Required top-level parameters: `todoExecutorsAddRequest`
- Sensitive flag from schema: `false`
- Mutation risk: `unknown-review-before-use`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `todoExecutorsAddRequest` | yes | object | request | request |
| `todoExecutorsAddRequest.executorIds` |  | array; items=string | executorIds | 待办执行人钉钉 UID 列表 |
| `todoExecutorsAddRequest.executorIds[]` |  | string |  |  |
| `todoExecutorsAddRequest.taskId` |  | number | taskId | 待办任务 taskId |

### CLI flag overlay

- none

## dws todo add_task_participants

- Canonical path: `todo.add_task_participants`
- Product: `todo`
- Group: `-`
- Subcommand: `add_task_participants`
- Title: add_task_participants
- Description: 添加待办参与人
- Required top-level parameters: `todoParticipantsAddRequest`
- Sensitive flag from schema: `false`
- Mutation risk: `unknown-review-before-use`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `todoParticipantsAddRequest` | yes | object | request | request |
| `todoParticipantsAddRequest.participantIds` | yes | array; items=string | participantIds | 待办参与人钉钉 UID 列表 |
| `todoParticipantsAddRequest.participantIds[]` |  | string |  |  |
| `todoParticipantsAddRequest.taskId` | yes | number | taskId | 待办任务 taskId |

### CLI flag overlay

- none

## dws todo add_todo_comment

- Canonical path: `todo.add_todo_comment`
- Product: `todo`
- Group: `-`
- Subcommand: `add_todo_comment`
- Title: add_todo_comment
- Description: 给待办添加评论
- Required top-level parameters: `taskId`, `content`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `content` | yes | string | content | 评论的内容 |
| `taskId` | yes | string | taskId | 待办的标识id |

### CLI flag overlay

- none

## dws todo add_todo_reminder

- Canonical path: `todo.add_todo_reminder`
- Product: `todo`
- Group: `-`
- Subcommand: `add_todo_reminder`
- Title: add_todo_reminder
- Description: 添加待办提醒
- Required top-level parameters: `todoReminderAddRequest`
- Sensitive flag from schema: `false`
- Mutation risk: `unknown-review-before-use`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `todoReminderAddRequest` | yes | object | request | request |
| `todoReminderAddRequest.baseTime` | yes | string | baseTime | baseTime |
| `todoReminderAddRequest.dueDateOffset` |  | number | dueDateOffset | dueDateOffset |
| `todoReminderAddRequest.reminderTimeStamp` |  | number | reminderTimeStamp | reminderTimeStamp |
| `todoReminderAddRequest.taskId` | yes | number | taskId | taskId |

### CLI flag overlay

- none

## dws todo create_personal_sub_todo

- Canonical path: `todo.create_personal_sub_todo`
- Product: `todo`
- Group: `-`
- Subcommand: `create_personal_sub_todo`
- Title: create_personal_sub_todo
- Description: 在当前企业组织内创建一条个人待办的子待办事项（要求父待办必须是操作者本人创建），支持设置标题、执行人列表、截止时间、优先级（如高/中/低）。待办将归属于当前用户，并对有权限的协作者可见。子待办不支持设置单独的循环规则（遵循父待办的循环规则）。
- Required top-level parameters: `PersonalTodoCreateVO`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `PersonalTodoCreateVO` | yes | object | PersonalTodoCreateVO | 待办信息 |
| `PersonalTodoCreateVO.dueTime` |  | number | dueTime | 待办截止时间，unix时间戳，精确到毫秒 |
| `PersonalTodoCreateVO.executorIds` | yes | array; items=string | executorIds | 待办执行者(UserId)，可传入多个 |
| `PersonalTodoCreateVO.executorIds[]` |  | string |  |  |
| `PersonalTodoCreateVO.parentId` | yes | number | parentId | 父待办的taskId。要求父待办也必须是当前操作者本人创建的。 |
| `PersonalTodoCreateVO.priority` |  | number | priority | 待办优先级: 10:低，20:普通，30:较高，40:紧急 |
| `PersonalTodoCreateVO.subject` | yes | string | subject | 待办的标题 |

### CLI flag overlay

- none

## dws todo task create

- Canonical path: `todo.create_personal_todo`
- Product: `todo`
- Group: `task`
- Subcommand: `create`
- Title: 创建个人待办
- Description: 在当前企业组织内创建一条个人待办事项，支持设置标题、执行人列表（UserId）、截止时间、优先级（如高/中/低）。待办将归属于当前用户，并对有权限的协作者可见。
- Required top-level parameters: `PersonalTodoCreateVO`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `PersonalTodoCreateVO` | yes | object | PersonalTodoCreateVO | 待办信息 |
| `PersonalTodoCreateVO.dueTime` |  | number | dueTime | 待办截止时间，unix时间戳，精确到毫秒 |
| `PersonalTodoCreateVO.executorIds` | yes | array; items=string | executorIds | 待办执行者。可传入多个 |
| `PersonalTodoCreateVO.executorIds[]` |  | string |  |  |
| `PersonalTodoCreateVO.priority` |  | number | priority | 待办优先级: 10:低，20:普通，30:较高，40:紧急 |
| `PersonalTodoCreateVO.recurrence` |  | string | recurrence | 循环待办的格式，只有当设置了截止时间时，才能设置循环待办的入参。每天循环的格式如下：DTSTART:20260320T100000Z\nRRULE:FREQ=DAILY;INTERVAL=1。其中 DTSTART表示第一次待办任务的截止时间。当前只支持按天循环。 |
| `PersonalTodoCreateVO.subject` | yes | string | subject | 待办的标题 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `PersonalTodoCreateVO.dueTime` | `--due` | iso8601_to_millis |  |  |  |
| `PersonalTodoCreateVO.executorIds` | `--executors` | csv_to_array |  |  |  |
| `PersonalTodoCreateVO.priority` | `--priority` |  |  |  |  |
| `PersonalTodoCreateVO.recurrence` | `--recurrence` |  |  |  |  |
| `PersonalTodoCreateVO.subject` | `--title` |  |  |  |  |

## dws todo task delete

- Canonical path: `todo.delete_todo`
- Product: `todo`
- Group: `task`
- Subcommand: `delete`
- Title: delete_todo
- Description: 删除待办（所有执行者都删除）
- Required top-level parameters: `taskId`
- Sensitive flag from schema: `true`
- Mutation risk: `sensitive-mutating`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `taskId` | yes | string | taskId | taskId |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `taskId` | `--task-id` |  |  |  |  |

## dws todo delete_todo_comment

- Canonical path: `todo.delete_todo_comment`
- Product: `todo`
- Group: `-`
- Subcommand: `delete_todo_comment`
- Title: delete_todo_comment
- Description: 删除待办的评论
- Required top-level parameters: `taskId`, `commentId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `commentId` | yes | string | commentId | 评论的标识id |
| `taskId` | yes | string | taskId | 待办的标识id |

### CLI flag overlay

- none

## dws todo task get

- Canonical path: `todo.get_todo_detail`
- Product: `todo`
- Group: `task`
- Subcommand: `get`
- Title: 带鉴权的查询待办详情
- Description: 查询待办详情，用户必须是待办的创建者或者执行者才有权限查看。
- Required top-level parameters: `taskId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `taskId` | yes | string | taskId | 待办的标识 id |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `taskId` | `--task-id` |  |  |  |  |

## dws todo task list

- Canonical path: `todo.get_user_todos_in_current_org`
- Product: `todo`
- Group: `task`
- Subcommand: `list`
- Title: 查询个人待办
- Description: 获取当前用户在所属组织中的个人待办事项列表，返回每项待办的标题、截止日期、优先级（如高/中/低）、完成状态。
- Required top-level parameters: `pageSize`, `pageNum`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `pageNum` | yes | string | pageNum | 当前页。从1开始 |
| `pageSize` | yes | string | pageSize | 分页大小。 |
| `todoStatus` |  | string | todoStatus | 待办完成状态。true：已完成；false：未完成。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `isDone` | `--status` |  |  |  |  |
| `pageNum` | `--page` |  |  |  |  |
| `pageSize` | `--size` |  |  |  |  |

## dws todo list_todo_comment

- Canonical path: `todo.list_todo_comment`
- Product: `todo`
- Group: `-`
- Subcommand: `list_todo_comment`
- Title: list_todo_comment
- Description: 获取待办的评论
- Required top-level parameters: `page`, `pageSize`, `taskId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `page` | yes | string | page | 页码 |
| `pageSize` | yes | string | pageSize | 每页显示多少条 |
| `taskId` | yes | string | taskId | 待办的标识 id |

### CLI flag overlay

- none

## dws todo query_todo_detail

- Canonical path: `todo.query_todo_detail`
- Product: `todo`
- Group: `-`
- Subcommand: `query_todo_detail`
- Title: query_todo_detail
- Description: 查询待办详情
- Required top-level parameters: `taskId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `taskId` | yes | string | taskId | taskId |

### CLI flag overlay

- none

## dws todo remove_task_executors

- Canonical path: `todo.remove_task_executors`
- Product: `todo`
- Group: `-`
- Subcommand: `remove_task_executors`
- Title: remove_task_executors
- Description: 移除待办执行人
- Required top-level parameters: `todoExecutorsRemoveRequest`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `todoExecutorsRemoveRequest` | yes | object | request | request |
| `todoExecutorsRemoveRequest.executorIds` | yes | array; items=string | executorIds | 待办执行人钉钉 userId 列表 |
| `todoExecutorsRemoveRequest.executorIds[]` |  | string |  |  |
| `todoExecutorsRemoveRequest.taskId` | yes | number | taskId | 待办任务 taskId |

### CLI flag overlay

- none

## dws todo remove_task_participants

- Canonical path: `todo.remove_task_participants`
- Product: `todo`
- Group: `-`
- Subcommand: `remove_task_participants`
- Title: remove_task_participants
- Description: 移除待办参与人
- Required top-level parameters: `todoParticipantsRemoveRequest`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `todoParticipantsRemoveRequest` | yes | object | request | request |
| `todoParticipantsRemoveRequest.participantIds` | yes | array; items=string | participantIds | 待办参与人钉钉 UID 列表 |
| `todoParticipantsRemoveRequest.participantIds[]` |  | string |  |  |
| `todoParticipantsRemoveRequest.taskId` | yes | number | taskId | 待办任务 taskId |

### CLI flag overlay

- none

## dws todo reset_todo_reminder

- Canonical path: `todo.reset_todo_reminder`
- Product: `todo`
- Group: `-`
- Subcommand: `reset_todo_reminder`
- Title: reset_todo_reminder
- Description: 重置待办提醒
- Required top-level parameters: `todoReminderUpdateRequest`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `todoReminderUpdateRequest` | yes | object | request | request |
| `todoReminderUpdateRequest.reminderRules` |  | array; items=object | reminderRules | reminderRules |
| `todoReminderUpdateRequest.reminderRules[].baseTime` | yes | string | baseTime | baseTime |
| `todoReminderUpdateRequest.reminderRules[].dueDateOffset` |  | number | dueDateOffset | dueDateOffset |
| `todoReminderUpdateRequest.reminderRules[].reminderTimeStamp` |  | number | reminderTimeStamp | reminderTimeStamp |
| `todoReminderUpdateRequest.taskId` | yes | number | taskId | taskId |

### CLI flag overlay

- none

## dws todo task done

- Canonical path: `todo.update_todo_done_status`
- Product: `todo`
- Group: `task`
- Subcommand: `done`
- Title: 修改执行者的待办完成状态
- Description: 修改执行者的待办完成状态
- Required top-level parameters: `taskId`, `isDone`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `isDone` | yes | string | isDone | 需要修改待办完成状态结果。true：已完成；false未完成。 |
| `taskId` | yes | string | taskId | 待办任务唯一标识id。指定需要修改哪个待办 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `isDone` | `--status` |  |  |  |  |
| `taskId` | `--task-id` |  |  |  |  |

## dws todo task update

- Canonical path: `todo.update_todo_task`
- Product: `todo`
- Group: `task`
- Subcommand: `update`
- Title: 修改整个待办任务
- Description: 修改整个待办任务
- Required top-level parameters: `TodoUpdateRequest`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `TodoUpdateRequest` | yes | object | TodoUpdateRequest | TodoUpdateRequest |
| `TodoUpdateRequest.dueTime` |  | number | dueTime | 待办截止时间，unix时间戳，精确到毫秒 |
| `TodoUpdateRequest.isDone` |  | boolean | isDone | 待办完成状态。true：已完成；alse：未完成 |
| `TodoUpdateRequest.priority` |  | number | priority | 待办优先级: 10:低，20:普通，30:较高，40:紧急 |
| `TodoUpdateRequest.subject` |  | string | subject | 待办标题 |
| `TodoUpdateRequest.taskId` | yes | string | taskId | 待办任务唯一标识id |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `TodoUpdateRequest.dueTime` | `--due` | iso8601_to_millis |  |  |  |
| `TodoUpdateRequest.isDone` | `--done` |  |  |  |  |
| `TodoUpdateRequest.priority` | `--priority` |  |  |  |  |
| `TodoUpdateRequest.subject` | `--title` |  |  |  |  |
| `TodoUpdateRequest.taskId` | `--task-id` |  |  |  |  |

