# dws contact Commands

通讯录 / 用户 / 部门

Commands in this file: 10

## Command Index

| CLI | Canonical path | Risk |
| --- | --- | --- |
| [`dws contact user get-self`](#dws-contact-user-get-self) | `contact.get_current_user_profile` | `read-only` |
| [`dws contact get_dept_info_by_dept_id`](#dws-contact-getdeptinfobydeptid) | `contact.get_dept_info_by_dept_id` | `read-only` |
| [`dws contact dept list-members`](#dws-contact-dept-list-members) | `contact.get_dept_members_by_deptId` | `read-only` |
| [`dws contact get_sub_depts_by_dept_id`](#dws-contact-getsubdeptsbydeptid) | `contact.get_sub_depts_by_dept_id` | `read-only` |
| [`dws contact user get`](#dws-contact-user-get) | `contact.get_user_info_by_user_ids` | `read-only` |
| [`dws contact list_my_followings`](#dws-contact-listmyfollowings) | `contact.list_my_followings` | `read-only` |
| [`dws contact user search`](#dws-contact-user-search) | `contact.search_contact_by_key_word` | `read-only` |
| [`dws contact dept search`](#dws-contact-dept-search) | `contact.search_dept_by_keyword` | `read-only` |
| [`dws contact search_user_by_key_word`](#dws-contact-searchuserbykeyword) | `contact.search_user_by_key_word` | `read-only` |
| [`dws contact user search-mobile`](#dws-contact-user-search-mobile) | `contact.search_user_by_mobile` | `read-only` |

## Group Summary

| Group | Commands |
| --- | ---: |
| `-` | 4 |
| `dept` | 2 |
| `user` | 4 |

## dws contact user get-self

- Canonical path: `contact.get_current_user_profile`
- Product: `contact`
- Group: `user`
- Subcommand: `get-self`
- Title: 获取当前用户详情
- Description: 获取当前用户信息
- Required top-level parameters: -
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

- none

### CLI flag overlay

- none

## dws contact get_dept_info_by_dept_id

- Canonical path: `contact.get_dept_info_by_dept_id`
- Product: `contact`
- Group: `-`
- Subcommand: `get_dept_info_by_dept_id`
- Title: 查询部门详情
- Description: 根据指定的部门 ID，获取部门详情。结果受组织架构可见性控制：仅返回调用者有权限查看的部门信息
- Required top-level parameters: `deptId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `deptId` | yes | number | 部门Id | 部门Id |

### CLI flag overlay

- none

## dws contact dept list-members

- Canonical path: `contact.get_dept_members_by_deptId`
- Product: `contact`
- Group: `dept`
- Subcommand: `list-members`
- Title: 获取部门下所有成员
- Description: 查看部门成员（逗号分隔 deptId）
- Required top-level parameters: `deptIds`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `deptIds` | yes | array; items=number | deptIds | 需要获取部门的ID |
| `deptIds[]` |  | number |  |  |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `deptIds` | `--ids` | csv_to_array |  |  |  |

## dws contact get_sub_depts_by_dept_id

- Canonical path: `contact.get_sub_depts_by_dept_id`
- Product: `contact`
- Group: `-`
- Subcommand: `get_sub_depts_by_dept_id`
- Title: 获取部门的子部门
- Description: 根据指定的部门 ID，获取其直接子部门列表，返回每个子部门的部门 ID、名称。结果受组织架构可见性控制：仅返回调用者有权限查看的子部门；若父部门不可见或无子部门，则返回空列表。
- Required top-level parameters: `deptId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `deptId` | yes | number | 部门Id | 部门Id |

### CLI flag overlay

- none

## dws contact user get

- Canonical path: `contact.get_user_info_by_user_ids`
- Product: `contact`
- Group: `user`
- Subcommand: `get`
- Title: 获取用户详情
- Description: 批量获取用户详情（逗号分隔 userId）
- Required top-level parameters: `user_id_list`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `user_id_list` | yes | array; items=string | user_id列表 | user_id的列表。可能也叫userId |
| `user_id_list[]` |  | string |  |  |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `user_id_list` | `--ids` | csv_to_array |  |  |  |

## dws contact list_my_followings

- Canonical path: `contact.list_my_followings`
- Product: `contact`
- Group: `-`
- Subcommand: `list_my_followings`
- Title: 获取我的特别关注列表
- Description: 获取我的特别关注列表
- Required top-level parameters: -
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

- none

### CLI flag overlay

- none

## dws contact user search

- Canonical path: `contact.search_contact_by_key_word`
- Product: `contact`
- Group: `user`
- Subcommand: `search`
- Title: 根据关键词搜索好友和同事
- Description: 按关键词搜索用户
- Required top-level parameters: `keyword`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `keyword` | yes | string | keyword | 搜索的关键词；按照关联性排序 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `keyword` | `--query` |  |  |  |  |

## dws contact dept search

- Canonical path: `contact.search_dept_by_keyword`
- Product: `contact`
- Group: `dept`
- Subcommand: `search`
- Title: 搜索部门
- Description: 按关键词搜索部门
- Required top-level parameters: `query`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `query` | yes | string | query | 搜索关键词 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `query` | `--query` |  |  |  |  |

## dws contact search_user_by_key_word

- Canonical path: `contact.search_user_by_key_word`
- Product: `contact`
- Group: `-`
- Subcommand: `search_user_by_key_word`
- Title: 搜索成员
- Description: 搜索组织内成员，并返回成员的userId。如果需要查询详情，需要调用另外一个工具
- Required top-level parameters: `keyWord`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `keyWord` | yes | string | 搜索关键词 | 搜索关键词 |

### CLI flag overlay

- none

## dws contact user search-mobile

- Canonical path: `contact.search_user_by_mobile`
- Product: `contact`
- Group: `user`
- Subcommand: `search-mobile`
- Title: 通过手机号获取成员userId和名称
- Description: 按手机号搜索用户
- Required top-level parameters: `mobile`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `mobile` | yes | string | mobile | 搜索的手机号 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `mobile` | `--mobile` |  |  |  |  |

