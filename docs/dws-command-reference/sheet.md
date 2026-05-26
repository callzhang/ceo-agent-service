# dws sheet Commands

钉钉表格管理

Commands in this file: 42

## Command Index

| CLI | Canonical path | Risk |
| --- | --- | --- |
| [`dws sheet add-dimension`](#dws-sheet-add-dimension) | `sheet.add_dimension` | `unknown-review-before-use` |
| [`dws sheet append`](#dws-sheet-append) | `sheet.append_rows` | `mutating-review-first` |
| [`dws sheet clear_filter_criteria`](#dws-sheet-clearfiltercriteria) | `sheet.clear_filter_criteria` | `mutating-review-first` |
| [`dws sheet filter-view delete-criteria`](#dws-sheet-filter-view-delete-criteria) | `sheet.clear_filter_view_criteria` | `mutating-review-first` |
| [`dws sheet copy_sheet`](#dws-sheet-copysheet) | `sheet.copy_sheet` | `unknown-review-before-use` |
| [`dws sheet create_filter`](#dws-sheet-createfilter) | `sheet.create_filter` | `mutating-review-first` |
| [`dws sheet filter-view create`](#dws-sheet-filter-view-create) | `sheet.create_filter_view` | `mutating-review-first` |
| [`dws sheet create_float_image`](#dws-sheet-createfloatimage) | `sheet.create_float_image` | `mutating-review-first` |
| [`dws sheet new`](#dws-sheet-new) | `sheet.create_sheet` | `mutating-review-first` |
| [`dws sheet create`](#dws-sheet-create) | `sheet.create_workspace_sheet` | `mutating-review-first` |
| [`dws sheet delete-dimension`](#dws-sheet-delete-dimension) | `sheet.delete_dimension` | `mutating-review-first` |
| [`dws sheet delete_dropdown_lists`](#dws-sheet-deletedropdownlists) | `sheet.delete_dropdown_lists` | `mutating-review-first` |
| [`dws sheet delete_filter`](#dws-sheet-deletefilter) | `sheet.delete_filter` | `mutating-review-first` |
| [`dws sheet filter-view delete`](#dws-sheet-filter-view-delete) | `sheet.delete_filter_view` | `mutating-review-first` |
| [`dws sheet delete_float_image`](#dws-sheet-deletefloatimage) | `sheet.delete_float_image` | `mutating-review-first` |
| [`dws sheet find`](#dws-sheet-find) | `sheet.find_cells` | `unknown-review-before-use` |
| [`dws sheet list`](#dws-sheet-list) | `sheet.get_all_sheets` | `read-only` |
| [`dws sheet get_dropdown_lists`](#dws-sheet-getdropdownlists) | `sheet.get_dropdown_lists` | `read-only` |
| [`dws sheet get_filter`](#dws-sheet-getfilter) | `sheet.get_filter` | `read-only` |
| [`dws sheet filter-view list`](#dws-sheet-filter-view-list) | `sheet.get_filter_views` | `read-only` |
| [`dws sheet get_float_image`](#dws-sheet-getfloatimage) | `sheet.get_float_image` | `read-only` |
| [`dws sheet range read`](#dws-sheet-range-read) | `sheet.get_range` | `read-only` |
| [`dws sheet info`](#dws-sheet-info) | `sheet.get_sheet` | `read-only` |
| [`dws sheet insert-dimension`](#dws-sheet-insert-dimension) | `sheet.insert_dimension` | `unknown-review-before-use` |
| [`dws sheet list_float_images`](#dws-sheet-listfloatimages) | `sheet.list_float_images` | `read-only` |
| [`dws sheet merge-cells`](#dws-sheet-merge-cells) | `sheet.merge_cells` | `unknown-review-before-use` |
| [`dws sheet move-dimension`](#dws-sheet-move-dimension) | `sheet.move_dimension` | `unknown-review-before-use` |
| [`dws sheet query_export_job`](#dws-sheet-queryexportjob) | `sheet.query_export_job` | `read-with-local-output` |
| [`dws sheet replace`](#dws-sheet-replace) | `sheet.replace_all` | `mutating-review-first` |
| [`dws sheet set_dropdown_lists`](#dws-sheet-setdropdownlists) | `sheet.set_dropdown_lists` | `read-only` |
| [`dws sheet set_filter_criteria`](#dws-sheet-setfiltercriteria) | `sheet.set_filter_criteria` | `mutating-review-first` |
| [`dws sheet filter-view update-criteria`](#dws-sheet-filter-view-update-criteria) | `sheet.set_filter_view_criteria` | `mutating-review-first` |
| [`dws sheet sort_filter`](#dws-sheet-sortfilter) | `sheet.sort_filter` | `mutating-review-first` |
| [`dws sheet submit_export_job`](#dws-sheet-submitexportjob) | `sheet.submit_export_job` | `read-with-local-output` |
| [`dws sheet unmerge-cells`](#dws-sheet-unmerge-cells) | `sheet.unmerge_range` | `unknown-review-before-use` |
| [`dws sheet update-dimension`](#dws-sheet-update-dimension) | `sheet.update_dimension` | `mutating-review-first` |
| [`dws sheet update_filter`](#dws-sheet-updatefilter) | `sheet.update_filter` | `mutating-review-first` |
| [`dws sheet filter-view update`](#dws-sheet-filter-view-update) | `sheet.update_filter_view` | `mutating-review-first` |
| [`dws sheet update_float_image`](#dws-sheet-updatefloatimage) | `sheet.update_float_image` | `mutating-review-first` |
| [`dws sheet range update`](#dws-sheet-range-update) | `sheet.update_range` | `mutating-review-first` |
| [`dws sheet update_sheet`](#dws-sheet-updatesheet) | `sheet.update_sheet` | `mutating-review-first` |
| [`dws sheet write-image`](#dws-sheet-write-image) | `sheet.write_image` | `mutating-review-first` |

## Group Summary

| Group | Commands |
| --- | ---: |
| `-` | 34 |
| `filter-view` | 6 |
| `range` | 2 |

## dws sheet add-dimension

- Canonical path: `sheet.add_dimension`
- Product: `sheet`
- Group: `-`
- Subcommand: `add-dimension`
- Title: 追加行列
- Description: 在工作表末尾追加空行或空列
- Required top-level parameters: `nodeId`, `sheetId`, `dimension`, `length`
- Sensitive flag from schema: `false`
- Mutation risk: `unknown-review-before-use`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `dimension` | yes | string | 维度类型 | 追加维度（必填）。ROWS 表示追加行，COLUMNS 表示追加列。 |
| `length` | yes | number | 追加数量 | 要追加的行/列数量（必填），必须为正整数（>= 1）。 |
| `nodeId` | yes | string | 文档标识 | 目标电子表格的标识（必填）。支持两种格式：1) 文档链接 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid}；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。 |
| `sheetId` | yes | string | 工作表标识 | 目标工作表的 ID 或名称（必填）。可通过 get_all_sheets 接口获取工作表的 id 或 name。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `dimension` | `--dimension` |  |  |  |  |
| `length` | `--length` |  |  |  |  |
| `nodeId` | `--node` |  |  |  |  |
| `sheetId` | `--sheet-id` |  |  |  |  |

## dws sheet append

- Canonical path: `sheet.append_rows`
- Product: `sheet`
- Group: `-`
- Subcommand: `append`
- Title: 追加行
- Description: 在工作表末尾追加若干行数据
- Required top-level parameters: `nodeId`, `sheetId`, `values`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `nodeId` | yes | string | 文档标识 | 目标电子表格的标识，支持两种格式：1) 文档链接 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid}；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。 |
| `sheetId` | yes | string | 工作表标识 | 目标工作表的标识，支持两种格式：1) 工作表 ID（sheetId）；2) 工作表名称。可通过 get_all_sheets 获取。 |
| `values` | yes | array; items=array; items=string | 要追加的数据 | 二维数组，外层数组的每个元素代表一行，内层数组的每个元素代表该行中的一个单元格值。追加的数据列数应与工作表已有数据的列数保持一致。 |
| `values[]` |  | array; items=string |  |  |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `nodeId` | `--node` |  |  |  |  |
| `sheetId` | `--sheet-id` |  |  |  |  |
| `values` | `--values` | json_parse |  |  |  |

## dws sheet clear_filter_criteria

- Canonical path: `sheet.clear_filter_criteria`
- Product: `sheet`
- Group: `-`
- Subcommand: `clear_filter_criteria`
- Title: 清除筛选条件
- Description: 清除钉钉电子表格中指定工作表筛选的某一列的筛选条件。  通过 nodeId 定位电子表格文档，通过 sheetId 定位工作表，通过 column 指定目标列。 nodeId 支持传入文档链接 URL 或文档 ID（dentryUuid），系统自动识别。 sheetId 支持传入工作表 ID 或工作表名称，可通过 get_all_sheets 获取。  清除指定列的筛选条件后，该列不再参与筛选计算，之前被该列条件隐藏的行将重新显示（前提是其他列的条件也不隐藏该行）。 此操作仅清除指定列的条件，不会删除整个筛选。如需删除整个筛选，请使用 delete_filter。  如果指定列没有设置筛选条件，调用此接口不会报错。  仅限用户对文档具备"可编辑"权限时可操作。不支持跨组织操作。
- Required top-level parameters: `nodeId`, `sheetId`, `column`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `column` | yes | number | 列偏移量 | 要清除筛选条件的列偏移量（必填），0-based，相对于筛选范围起始列。 |
| `nodeId` | yes | string | 文档标识 | 目标电子表格的标识（必填）。支持两种格式：1) 文档链接 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid}；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。 |
| `sheetId` | yes | string | 工作表标识 | 目标工作表的 ID 或名称（必填）。可通过 get_all_sheets 接口获取工作表的 id 或 name。 |

### CLI flag overlay

- none

## dws sheet filter-view delete-criteria

- Canonical path: `sheet.clear_filter_view_criteria`
- Product: `sheet`
- Group: `filter-view`
- Subcommand: `delete-criteria`
- Title: 清除筛选视图列条件
- Description: 清除筛选视图某列的筛选条件
- Required top-level parameters: `nodeId`, `sheetId`, `filterViewId`, `column`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `column` | yes | number | 列偏移量 | 要清除筛选条件的列偏移量（必填），0-based，相对于筛选视图范围起始列。 |
| `filterViewId` | yes | string | 筛选视图 ID | 目标筛选视图 ID（必填）。可通过 get_filter_views 接口获取。 |
| `nodeId` | yes | string | 文档标识 | 目标电子表格的标识（必填）。支持两种格式：1) 文档链接 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid}；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。 |
| `sheetId` | yes | string | 工作表标识 | 目标工作表的 ID 或名称（必填）。可通过 get_all_sheets 接口获取工作表的 id 或 name。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `column` | `--column` |  |  |  |  |
| `filterViewId` | `--filter-view-id` |  |  |  |  |
| `nodeId` | `--node` |  |  |  |  |
| `sheetId` | `--sheet-id` |  |  |  |  |

## dws sheet copy_sheet

- Canonical path: `sheet.copy_sheet`
- Product: `sheet`
- Group: `-`
- Subcommand: `copy_sheet`
- Title: 复制工作表
- Description: 复制钉钉电子表格中的指定工作表，在同一表格中创建一个副本，可同时指定副本的目标位置。
- Required top-level parameters: `nodeId`, `sheetId`
- Sensitive flag from schema: `false`
- Mutation risk: `unknown-review-before-use`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `index` |  | number | 副本位置索引 | 副本工作表的位置索引（可选，0-based）。不传时放在源工作表之后。 |
| `nodeId` | yes | string | 表格文件 ID | 表格文件 ID（必填）。支持两种格式：1) 钉钉文档链接 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid} 或 https://alidocs.dingtalk.com/spreadsheetv2/{dentryKey}/...；2) 文档 ID（dentryUuid），32 位字母数字字符串； |
| `sheetId` | yes | string | 源工作表标识 | 要复制的源工作表的 ID 或名称（必填）。可通过 get_all_sheets 接口获取工作表的 id 或 name。 |
| `title` |  | string | 副本名称 | 副本名称 |

### CLI flag overlay

- none

## dws sheet create_filter

- Canonical path: `sheet.create_filter`
- Product: `sheet`
- Group: `-`
- Subcommand: `create_filter`
- Title: 创建筛选
- Description: 在钉钉电子表格的指定工作表中创建筛选。 创建筛选后，工作表的指定范围将出现筛选下拉箭头，用户可通过 set_filter_criteria 设置具体的筛选条件。 每个工作表只能有一个筛选，如果已存在筛选则会报错。
- Required top-level parameters: `nodeId`, `sheetId`, `range`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `criteria` |  | array; items=object | 筛选条件 | 各列的筛选条件（可选）。数组格式，每个元素包含 column（列偏移量，0-based）和筛选条件字段。示例：[{"column":0,"filterType":"values","visibleValues":["A","B"]}] |
| `criteria[].backgroundColor` |  | string | 背景色 | 按背景色筛选时的颜色值（十六进制，如 #FF0000）。仅 filterType 为 color 时有效，与 fontColor 二选一 |
| `criteria[].column` | yes | number | 列偏移量 | 列偏移量（必填），0-based，相对于筛选范围起始列 |
| `criteria[].conditionOperator` |  | string | 条件逻辑关系 | 多条件之间的逻辑关系：and（且，默认）或 or（或）。仅 conditions 包含 2 个条件时有效 |
| `criteria[].conditions` |  | array; items=object | 筛选条件列表 | 按条件筛选时的条件列表，最多 2 个。仅 filterType 为 condition 时有效。每个条件包含 operator（操作符）和 value（条件值） |
| `criteria[].conditions[].operator` |  | string | 条件操作符 | 条件操作符：equal、not-equal、contains、not-contains、starts-with、not-starts-with、ends-with、not-ends-with、greater、greater-equal、less、less-equal |
| `criteria[].conditions[].value` |  | string | 条件值 | 条件值 |
| `criteria[].filterType` | yes | string | 筛选类型 | 筛选类型：values（按值筛选）、condition（按条件筛选）、color（按颜色筛选） |
| `criteria[].fontColor` |  | string | 字体色 | 按字体色筛选时的颜色值（十六进制，如 #0000FF）。仅 filterType 为 color 时有效，与 backgroundColor 二选一 |
| `criteria[].visibleValues` |  | array; items=string | 可见值列表 | 按值筛选时，允许显示的值列表。仅 filterType 为 values 时有效 |
| `criteria[].visibleValues[]` |  | string |  |  |
| `nodeId` | yes | string | 文档标识 | 目标电子表格的标识（必填）。支持两种格式：1) 钉钉文档链接 URL；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。 |
| `range` | yes | string | 筛选范围 | 筛选的单元格范围（必填），使用 A1 表示法，如 A1:E10。 |
| `sheetId` | yes | string | 工作表标识 | 目标工作表的 ID 或名称（必填）。可通过 get_all_sheets 接口获取工作表的 id 或 name。 |

### CLI flag overlay

- none

## dws sheet filter-view create

- Canonical path: `sheet.create_filter_view`
- Product: `sheet`
- Group: `filter-view`
- Subcommand: `create`
- Title: 创建筛选视图
- Description: 创建筛选视图
- Required top-level parameters: `nodeId`, `sheetId`, `name`, `range`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `criteria` |  | array; items=object | 筛选条件 | 各列的筛选条件（可选）。数组格式，每个元素包含 column（列偏移量，0-based）和筛选条件字段。示例：[{"column":0,"filterType":"values","visibleValues":["A","B"]}] |
| `criteria[].backgroundColor` |  | string | backgroundColor | 按背景色筛选时的颜色值（十六进制，如 #FF0000）。仅 filterType 为 color 时有效，与 fontColor 二选一 |
| `criteria[].column` | yes | number | column | 列偏移量（必填），0-based，相对于筛选范围起始列 |
| `criteria[].conditionOperator` |  | string | conditionOperator | 多条件之间的逻辑关系：and（且，默认）或 or（或）。仅 conditions 包含 2 个条件时有效 |
| `criteria[].conditions` |  | array; items=object | conditions | 按条件筛选时的条件列表，最多 2 个。仅 filterType 为 condition 时有效。每个条件包含 operator（操作符）和 value（条件值） |
| `criteria[].conditions[].operator` |  | string | operator | 条件操作符：equal、not-equal、contains、not-contains、starts-with、not-starts-with、ends-with、not-ends-with、greater、greater-equal、less、less-equal |
| `criteria[].conditions[].value` |  | string | value | 条件值 |
| `criteria[].filterType` | yes | string | filterType | 筛选类型：values（按值筛选）、condition（按条件筛选）、color（按颜色筛选） |
| `criteria[].fontColor` |  | string | fontColor | 按字体色筛选时的颜色值（十六进制，如 #0000FF）。仅 filterType 为 color 时有效，与 backgroundColor 二选一 |
| `criteria[].visibleValues` |  | array; items=string | visibleValues | 按值筛选时，允许显示的值列表。仅 filterType 为 values 时有效 |
| `criteria[].visibleValues[]` |  | string |  |  |
| `name` | yes | string | 筛选视图名称 | 筛选视图的名称（必填）。 |
| `nodeId` | yes | string | 文档标识 | 目标电子表格的标识（必填）。支持两种格式：1) 文档链接 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid}；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。 |
| `range` | yes | string | 筛选视图范围 | 筛选视图的单元格范围（必填），使用 A1 表示法，如 A1:E10。 |
| `sheetId` | yes | string | 工作表标识 | 目标工作表的 ID 或名称（必填）。可通过 get_all_sheets 接口获取工作表的 id 或 name。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `criteria` | `--criteria` | json_parse_strict |  |  |  |
| `name` | `--name` |  |  |  |  |
| `nodeId` | `--node` |  |  |  |  |
| `range` | `--range` |  |  |  |  |
| `sheetId` | `--sheet-id` |  |  |  |  |

## dws sheet create_float_image

- Canonical path: `sheet.create_float_image`
- Product: `sheet`
- Group: `-`
- Subcommand: `create_float_image`
- Title: 插入浮动图片
- Description: 在钉钉电子表格的指定工作表中插入一张浮动图片。
- Required top-level parameters: `nodeId`, `sheetId`, `src`, `range`, `width`, `height`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `height` | yes | number | 图片高度 | 图片高度（必填），单位像素，正整数。 |
| `nodeId` | yes | string | 文档标识 | 目标电子表格的标识（必填）。支持两种格式：1) 钉钉文档链接 URL；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。 |
| `offsetX` |  | number | 水平偏移 | 相对锚点单元格的水平偏移量（可选），单位像素，默认 0。 |
| `offsetY` |  | number | 垂直偏移 | 相对锚点单元格的垂直偏移量（可选），单位像素，默认 0。 |
| `range` | yes | string | 锚点单元格 | 浮动图片锚定的单元格位置（必填），使用 A1 表示法，如 "A1"、"B3"。必须是单个单元格。支持带工作表前缀（如 "Sheet1!A1"）。 |
| `sheetId` | yes | string | 工作表标识 | 目标工作表的 ID 或名称（必填）。可通过 get_all_sheets 接口获取工作表的 id 或 name。 |
| `src` | yes | string | 图片 URL | 图片的 URL 地址（必填）。 |
| `width` | yes | number | 图片宽度 | 图片宽度（必填），单位像素，正整数。 |

### CLI flag overlay

- none

## dws sheet new

- Canonical path: `sheet.create_sheet`
- Product: `sheet`
- Group: `-`
- Subcommand: `new`
- Title: 新建工作表
- Description: 新建工作表
- Required top-level parameters: `nodeId`, `name`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `name` | yes | string | 工作表名称 | 新工作表的名称。当指定名称与已有工作表重复时，系统会自动重命名为合法值。必填。 |
| `nodeId` | yes | string | 表格文件 ID | 表格文件 ID，知识库 API 返回的 nodeId(dentryUuid) 即是表格 nodeId，可通过获取节点或创建知识库文档接口获取。必填。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `name` | `--name` |  |  |  |  |
| `nodeId` | `--node` |  |  |  |  |

## dws sheet create

- Canonical path: `sheet.create_workspace_sheet`
- Product: `sheet`
- Group: `-`
- Subcommand: `create`
- Title: 新建钉钉表格
- Description: 创建钉钉表格文档
- Required top-level parameters: `name`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `folderId` |  | string | 文件夹 ID | 目标文件夹的节点 ID（dentryUuid），支持传入文件夹链接 URL 或 ID。不传时：如果提供了 workspaceId 则创建在该知识库根目录下，否则创建在用户'我的文档'根目录下。 |
| `name` | yes | string | 表格名称 | 新表格的标题，必填。 |
| `workspaceId` |  | string | 知识库 ID | 目标知识库的 ID。如果同时传了 folderId，则以 folderId 为准。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `folderId` | `--folder` |  |  |  |  |
| `name` | `--name` |  |  |  |  |
| `workspaceId` | `--workspace` |  |  |  |  |

## dws sheet delete-dimension

- Canonical path: `sheet.delete_dimension`
- Product: `sheet`
- Group: `-`
- Subcommand: `delete-dimension`
- Title: 删除指定位置的行或列
- Description: 删除指定位置起的若干行或列
- Required top-level parameters: `nodeId`, `sheetId`, `dimension`, `position`, `length`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `dimension` | yes | string | 维度类型 | 删除维度：ROWS 表示删除行，COLUMNS 表示删除列。必填。 |
| `length` | yes | number | 删除数量 | 要删除的行/列数量，必须为正整数（>= 1）。必填。 |
| `nodeId` | yes | string | 表格文件 ID | 表格文件 ID，知识库 API 返回的 nodeId(dentryUuid) 即是表格 nodeId，可通过获取节点或创建知识库文档接口获取。必填。 |
| `position` | yes | string | 删除起始位置 | 删除起始位置的 A1 表示法。dimension=ROWS 时为 1-based 行号字符串，如 "3" 表示从第 3 行开始删除；dimension=COLUMNS 时为列字母，如 "A" 表示从 A 列开始删除、"AB" 表示从 AB 列开始删除。从该位置开始连续删除 length 个行/列。允许携带工作表前缀，如 "Sheet1!3" / "Sheet1!A"，此时将忽略 sheetId 参数。必填。 |
| `sheetId` | yes | string | 工作表 ID | 工作表 ID 或名称。可通过获取所有工作表接口获取 id 或 name 参数值。必填。若 position 携带 Sheet 前缀（如 Sheet1!3），以前缀为准。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `dimension` | `--dimension` |  |  |  |  |
| `length` | `--length` |  |  |  |  |
| `nodeId` | `--node` |  |  |  |  |
| `position` | `--position` |  |  |  |  |
| `sheetId` | `--sheet-id` |  |  |  |  |

## dws sheet delete_dropdown_lists

- Canonical path: `sheet.delete_dropdown_lists`
- Product: `sheet`
- Group: `-`
- Subcommand: `delete_dropdown_lists`
- Title: 删除下拉列表
- Description: 删除钉钉表格指定单元格范围内的下拉列表配置。
- Required top-level parameters: `nodeId`, `sheetId`, `range`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `nodeId` | yes | string | 文档标识 | 目标电子表格的标识（必填）。支持两种格式：1) 钉钉文档链接 URL；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。 |
| `range` | yes | string | 单元格范围 | 要删除下拉列表的单元格范围（必填），A1 表示法，如 "A2:A100"。 |
| `sheetId` | yes | string | 工作表标识 | 目标工作表的 ID 或名称（必填）。可通过 get_all_sheets 获取。 |

### CLI flag overlay

- none

## dws sheet delete_filter

- Canonical path: `sheet.delete_filter`
- Product: `sheet`
- Group: `-`
- Subcommand: `delete_filter`
- Title: 删除筛选
- Description: 删除钉钉电子表格中指定工作表的筛选。  通过 nodeId 定位电子表格文档，通过 sheetId 定位工作表。 nodeId 支持传入文档链接 URL 或文档 ID（dentryUuid），系统自动识别。 sheetId 支持传入工作表 ID 或工作表名称，可通过 get_all_sheets 获取。  删除筛选后，工作表中的筛选下拉箭头和所有筛选条件将被移除，所有被隐藏的行将重新显示。 如果工作表没有筛选，调用此接口会报错。  仅限用户对文档具备"可编辑"权限时可删除。不支持跨组织操作。
- Required top-level parameters: `nodeId`, `sheetId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `nodeId` | yes | string | 文档标识 | 目标电子表格的标识（必填）。支持两种格式：1) 文档链接 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid}；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。 |
| `sheetId` | yes | string | 工作表标识 | 目标工作表的 ID 或名称（必填）。可通过 get_all_sheets 接口获取工作表的 id 或 name。 |

### CLI flag overlay

- none

## dws sheet filter-view delete

- Canonical path: `sheet.delete_filter_view`
- Product: `sheet`
- Group: `filter-view`
- Subcommand: `delete`
- Title: 删除筛选视图
- Description: 删除筛选视图
- Required top-level parameters: `nodeId`, `sheetId`, `filterViewId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `filterViewId` | yes | string | 筛选视图 ID | 要删除的筛选视图 ID（必填）。可通过 get_filter_views 接口获取。 |
| `nodeId` | yes | string | 文档标识 | 目标电子表格的标识（必填）。支持两种格式：1) 文档链接 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid}；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。 |
| `sheetId` | yes | string | 工作表标识 | 目标工作表的 ID 或名称（必填）。可通过 get_all_sheets 接口获取工作表的 id 或 name。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `filterViewId` | `--filter-view-id` |  |  |  |  |
| `nodeId` | `--node` |  |  |  |  |
| `sheetId` | `--sheet-id` |  |  |  |  |

## dws sheet delete_float_image

- Canonical path: `sheet.delete_float_image`
- Product: `sheet`
- Group: `-`
- Subcommand: `delete_float_image`
- Title: 删除浮动图片
- Description: 删除钉钉电子表格中指定的浮动图片。
- Required top-level parameters: `nodeId`, `sheetId`, `floatImageId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `floatImageId` | yes | string | 浮动图片 ID | 浮动图片 ID（必填）。可通过 list_float_images 接口获取。 |
| `nodeId` | yes | string | 文档标识 | 目标电子表格的标识（必填）。支持两种格式：1) 钉钉文档链接 URL；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。 |
| `sheetId` | yes | string | 工作表标识 | 目标工作表的 ID 或名称（必填）。可通过 get_all_sheets 接口获取工作表的 id 或 name。 |

### CLI flag overlay

- none

## dws sheet find

- Canonical path: `sheet.find_cells`
- Product: `sheet`
- Group: `-`
- Subcommand: `find`
- Title: 查找
- Description: 在工作表中搜索单元格内容（支持子串/正则/整格匹配/搜索公式文本/包含隐藏）
- Required top-level parameters: `nodeId`, `sheetId`, `text`
- Sensitive flag from schema: `false`
- Mutation risk: `unknown-review-before-use`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `includeHidden` |  | boolean | 包含隐藏单元格 | 是否在隐藏的行/列中也进行查找。默认为 false，即跳过隐藏单元格。 |
| `matchCase` |  | boolean | 区分大小写 | 是否区分大小写。默认为 true（区分大小写）。设为 false 时不区分大小写。 |
| `matchEntireCell` |  | boolean | 完整单元格匹配 | 是否要求单元格内容与查找文本完全一致。默认为 false（子字符串匹配）。设为 true 时仅匹配内容完全相同的单元格。 |
| `matchFormulaText` |  | boolean | 搜索公式文本 | 是否在公式文本中查找（而非公式计算结果）。默认为 false，即在单元格显示值中查找。 |
| `nodeId` | yes | string | 文档标识 | 目标电子表格的标识（必填）。支持两种格式：1) 文档链接 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid}；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。 |
| `range` |  | string | 查找范围 | 将查找限定在指定单元格范围内（可选），使用 A1 表示法，如 A1:D100、A:A（整列）、1:1（整行）。不传时搜索整个工作表。底层直接映射为 findOptions.scope 参数，无额外性能开销。 |
| `sheetId` | yes | string | 工作表标识 | 目标工作表的 ID 或名称（必填）。可通过 get_all_sheets 接口获取工作表的 id 或 name。 |
| `text` | yes | string | 查找内容 | 要查找的文本内容（必填）。当 useRegExp 为 true 时，作为正则表达式处理。不能为空字符串。 |
| `useRegExp` |  | boolean | 使用正则表达式 | 是否将 text 作为正则表达式进行匹配。默认为 false。启用时 text 必须是合法的正则表达式。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `includeHidden` | `--include-hidden` |  | false |  |  |
| `matchCase` | `--match-case` |  | true |  |  |
| `matchEntireCell` | `--match-entire-cell` |  | false |  |  |
| `matchFormulaText` | `--match-formula` |  | false |  |  |
| `nodeId` | `--node` |  |  |  |  |
| `range` | `--range` |  |  |  |  |
| `sheetId` | `--sheet-id` |  |  |  |  |
| `text` | `--find` |  |  |  |  |
| `useRegExp` | `--use-regexp` |  | false |  |  |

## dws sheet list

- Canonical path: `sheet.get_all_sheets`
- Product: `sheet`
- Group: `-`
- Subcommand: `list`
- Title: 获取所有工作表
- Description: 获取全部工作表列表
- Required top-level parameters: `nodeId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `nodeId` | yes | string | 文档标识 | 目标电子表格的标识，支持两种格式：1) 文档链接 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid}；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `nodeId` | `--node` |  |  |  |  |

## dws sheet get_dropdown_lists

- Canonical path: `sheet.get_dropdown_lists`
- Product: `sheet`
- Group: `-`
- Subcommand: `get_dropdown_lists`
- Title: 获取下拉列表
- Description: 查询钉钉表格指定范围内的下拉列表配置。
- Required top-level parameters: `nodeId`, `sheetId`, `range`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `nodeId` | yes | string | 文档标识 | 目标电子表格的标识（必填）。支持两种格式：1) 钉钉文档链接 URL；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。 |
| `range` | yes | string | 单元格范围 | 要查询下拉列表的单元格范围（必填），A1 表示法，如 "A1"、"A1:A100"、"B2:D10"。支持查询范围内所有单元格的下拉配置。 |
| `sheetId` | yes | string | 工作表标识 | 目标工作表的 ID 或名称（必填）。可通过 get_all_sheets 获取。 |

### CLI flag overlay

- none

## dws sheet get_filter

- Canonical path: `sheet.get_filter`
- Product: `sheet`
- Group: `-`
- Subcommand: `get_filter`
- Title: 获取筛选
- Description: 获取钉钉电子表格中指定工作表的筛选信息。  通过 nodeId 定位电子表格文档，通过 sheetId 定位工作表。 nodeId 支持传入文档链接 URL 或文档 ID（dentryUuid），系统自动识别。 sheetId 支持传入工作表 ID 或工作表名称，可通过 get_all_sheets 获取。  返回当前工作表的筛选范围和各列的筛选条件详情。 筛选条件类型包括： - 按值筛选（filterType: values）：指定允许显示的值列表 - 按条件筛选（filterType: condition）：指定条件运算符和比较值 - 按颜色筛选（filterType: color）：按单元格背景色或字体颜色筛选  如果工作表未设置筛选，返回结果中筛选信息为空。  仅限用户对文档具备"可阅读"权限时可获取。不支持跨组织查询。
- Required top-level parameters: `nodeId`, `sheetId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `nodeId` | yes | string | 文档标识 | 目标电子表格的标识（必填）。支持两种格式：1) 文档链接 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid}；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。 |
| `sheetId` | yes | string | 工作表标识 | 目标工作表的 ID 或名称（必填）。可通过 get_all_sheets 接口获取工作表的 id 或 name。 |

### CLI flag overlay

- none

## dws sheet filter-view list

- Canonical path: `sheet.get_filter_views`
- Product: `sheet`
- Group: `filter-view`
- Subcommand: `list`
- Title: 获取筛选视图
- Description: 查询工作表的筛选视图列表
- Required top-level parameters: `nodeId`, `sheetId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `nodeId` | yes | string | 文档标识 | 目标电子表格的标识（必填）。支持两种格式：1) 文档链接 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid}；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。 |
| `sheetId` | yes | string | 工作表标识 | 目标工作表的 ID 或名称（必填）。可通过 get_all_sheets 接口获取工作表的 id 或 name。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `nodeId` | `--node` |  |  |  |  |
| `sheetId` | `--sheet-id` |  |  |  |  |

## dws sheet get_float_image

- Canonical path: `sheet.get_float_image`
- Product: `sheet`
- Group: `-`
- Subcommand: `get_float_image`
- Title: 获取单张浮动图片
- Description: 获取钉钉电子表格中指定浮动图片的详细信息。
- Required top-level parameters: `nodeId`, `sheetId`, `floatImageId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `floatImageId` | yes | string | 浮动图片 ID | 浮动图片 ID（必填）。可通过 list_float_images 接口获取。 |
| `nodeId` | yes | string | 文档标识 | 目标电子表格的标识（必填）。支持两种格式：1) 钉钉文档链接 URL；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。 |
| `sheetId` | yes | string | 工作表标识 | 目标工作表的 ID 或名称（必填）。可通过 get_all_sheets 接口获取工作表的 id 或 name。 |

### CLI flag overlay

- none

## dws sheet range read

- Canonical path: `sheet.get_range`
- Product: `sheet`
- Group: `range`
- Subcommand: `read`
- Title: 读取工作表数据
- Description: 读取工作表数据（别名: get）
- Required top-level parameters: `nodeId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `nodeId` | yes | string | 文档标识 | 目标电子表格的标识（必填）。支持两种格式：1) 文档链接 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid}；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。仅支持钉钉在线电子表格类型的文档，传入其他类型文档（如钉钉文档）会报错。 |
| `range` |  | string | 读取范围 | 要读取的单元格范围（可选），使用 A1 表示法。支持两种格式：1) 仅范围，如 A1:D10（此时使用 sheetId 参数指定工作表）；2) 带工作表前缀，如 {sheetId}!A1:D10（此时忽略 sheetId 参数，从 range 中解析工作表标识）。不传时默认读取目标工作表的全部非空数据。系统会自动检测工作表的实际数据范围，避免返回大量空数据。 |
| `sheetId` |  | string | 工作表标识 | 目标工作表的 ID 或名称（可选）。可通过 get_all_sheets 接口获取工作表的 id 或 name。不传时默认读取第一个工作表。注意：当 range 参数包含工作表前缀（如 sheetId!A1:D10）时，range 中的前缀会覆盖此参数。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `nodeId` | `--node` |  |  |  |  |
| `range` | `--range` |  |  |  |  |
| `sheetId` | `--sheet-id` |  |  |  |  |

## dws sheet info

- Canonical path: `sheet.get_sheet`
- Product: `sheet`
- Group: `-`
- Subcommand: `info`
- Title: 获取工作表
- Description: 获取指定工作表详情
- Required top-level parameters: `nodeId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `nodeId` | yes | string | 文档标识 | 目标电子表格的标识，支持两种格式：1) 文档链接 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid}；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。 |
| `sheetId` |  | string | 工作表标识 | 目标工作表的 ID 或名称。可通过 get_all_sheets 接口获取工作表的 id 或 name 参数值。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `nodeId` | `--node` |  |  |  |  |
| `sheetId` | `--sheet-id` |  |  |  |  |

## dws sheet insert-dimension

- Canonical path: `sheet.insert_dimension`
- Product: `sheet`
- Group: `-`
- Subcommand: `insert-dimension`
- Title: 在指定位置插入行或列
- Description: 在指定位置插入空行或空列
- Required top-level parameters: `nodeId`, `sheetId`, `dimension`, `position`, `length`
- Sensitive flag from schema: `false`
- Mutation risk: `unknown-review-before-use`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `dimension` | yes | string | 维度类型 | 插入维度：ROWS 表示插入行，COLUMNS 表示插入列。必填。 |
| `length` | yes | number | 插入数量 | 要插入的行/列数量，必须为正整数（>= 1）。必填。 |
| `nodeId` | yes | string | 表格文件 ID | 表格文件 ID，知识库 API 返回的 nodeId(dentryUuid) 即是表格 nodeId，可通过获取节点或创建知识库文档接口获取。必填。 |
| `position` | yes | string | 插入位置 | 插入位置的 A1 表示法。dimension=ROWS 时为 1-based 行号字符串，如 "3" 表示在第 3 行之前插入；dimension=COLUMNS 时为列字母，如 "A" 表示在 A 列之前插入、"AB" 表示在 AB 列之前插入。允许携带工作表前缀，如 "Sheet1!3" / "Sheet1!A"，此时将忽略 sheetId 参数。必填。 |
| `sheetId` | yes | string | 工作表 ID | 工作表 ID 或名称。可通过获取所有工作表接口获取 id 或 name 参数值。必填。若 position 携带 Sheet 前缀（如 Sheet1!3），以前缀为准。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `dimension` | `--dimension` |  |  |  |  |
| `length` | `--length` |  |  |  |  |
| `nodeId` | `--node` |  |  |  |  |
| `position` | `--position` |  |  |  |  |
| `sheetId` | `--sheet-id` |  |  |  |  |

## dws sheet list_float_images

- Canonical path: `sheet.list_float_images`
- Product: `sheet`
- Group: `-`
- Subcommand: `list_float_images`
- Title: 获取所有浮动图片
- Description: 列出钉钉电子表格指定工作表中的所有浮动图片。
- Required top-level parameters: `nodeId`, `sheetId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `nodeId` | yes | string | 文档标识 | 目标电子表格的标识（必填）。支持两种格式：1) 钉钉文档链接 URL；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。 |
| `sheetId` | yes | string | 工作表标识 | 目标工作表的 ID 或名称（必填）。可通过 get_all_sheets 接口获取工作表的 id 或 name。 |

### CLI flag overlay

- none

## dws sheet merge-cells

- Canonical path: `sheet.merge_cells`
- Product: `sheet`
- Group: `-`
- Subcommand: `merge-cells`
- Title: 合并单元格
- Description: 合并指定范围的单元格（mergeAll/mergeRows/mergeColumns）
- Required top-level parameters: `nodeId`, `sheetId`, `rangeAddress`
- Sensitive flag from schema: `false`
- Mutation risk: `unknown-review-before-use`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `mergeType` |  | string | 合并方式 | 合并方式，可选值：mergeAll（合并所有，默认）、mergeRows（按行合并）、mergeColumns（按列合并）。不传时默认为 mergeAll。 |
| `nodeId` | yes | string | 表格文件 ID | 表格文件 ID，知识库 API 返回的 nodeId(dentryUuid) 即是表格 nodeId，可通过获取节点或创建知识库文档接口获取。必填。 |
| `rangeAddress` | yes | string | Range 地址 | 目标单元格区域地址，如 A1:B3。支持带工作表前缀的写法，如 Sheet1!A1:B3，此时将忽略 sheetId 参数。必填。 |
| `sheetId` | yes | string | 工作表 ID | 工作表 ID 或名称。可通过获取所有工作表接口获取 id 或 name 参数值。必填。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `mergeType` | `--merge-type` |  |  |  |  |
| `nodeId` | `--node` |  |  |  |  |
| `rangeAddress` | `--range` |  |  |  |  |
| `sheetId` | `--sheet-id` |  |  |  |  |

## dws sheet move-dimension

- Canonical path: `sheet.move_dimension`
- Product: `sheet`
- Group: `-`
- Subcommand: `move-dimension`
- Title: 移动行列
- Description: 移动行或列到指定位置（0-based 索引）
- Required top-level parameters: `nodeId`, `sheetId`, `dimension`, `startIndex`, `endIndex`, `destinationIndex`
- Sensitive flag from schema: `false`
- Mutation risk: `unknown-review-before-use`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `destinationIndex` | yes | number | 目标位置索引 | 移动后源行/列将从该索引开始（必填，0-based）。不能在 [startIndex, endIndex] 范围内。向下移动时 destinationIndex 应 > endIndex，向上移动时 destinationIndex 应 < startIndex。 |
| `dimension` | yes | string | 维度类型 | 移动维度（必填）。ROWS 表示移动行，COLUMNS 表示移动列。 |
| `endIndex` | yes | number | 源结束索引 | 要移动的行/列的结束索引（必填，0-based，包含）。例如移动第 2~4 行（1-based），则 startIndex=1, endIndex=3。 |
| `nodeId` | yes | string | 文档标识 | 目标电子表格的标识（必填）。支持两种格式：1) 文档链接 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid}；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。 |
| `sheetId` | yes | string | 工作表标识 | 目标工作表的 ID 或名称（必填）。可通过 get_all_sheets 接口获取工作表的 id 或 name。 |
| `startIndex` | yes | number | 源起始索引 | 要移动的行/列的起始索引（必填，0-based，包含）。例如移动第 2 行（1-based），则 startIndex=1。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `destinationIndex` | `--destination-index` |  |  |  |  |
| `dimension` | `--dimension` |  |  |  |  |
| `endIndex` | `--end-index` |  |  |  |  |
| `nodeId` | `--node` |  |  |  |  |
| `sheetId` | `--sheet-id` |  |  |  |  |
| `startIndex` | `--start-index` |  |  |  |  |

## dws sheet query_export_job

- Canonical path: `sheet.query_export_job`
- Product: `sheet`
- Group: `-`
- Subcommand: `query_export_job`
- Title: 查询表格导出任务状态
- Description: 用于查询通过 submit_export_job 提交的在线表格导出任务的执行状态，任务完成时返回 xlsx 文件下载链接。
- Required top-level parameters: `jobId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-with-local-output`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `jobId` | yes | string | 导出任务 ID | 由 submit_export_job 返回的导出任务 ID。必填。 |

### CLI flag overlay

- none

## dws sheet replace

- Canonical path: `sheet.replace_all`
- Product: `sheet`
- Group: `-`
- Subcommand: `replace`
- Title: 全局查找替换
- Description: 全局查找替换（支持正则/整格匹配/区分大小写/范围/包含隐藏）
- Required top-level parameters: `nodeId`, `sheetId`, `text`, `replaceText`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `includeHidden` |  | boolean | 包含隐藏行列 | 是否包含隐藏的行和列（可选，默认 false）。 |
| `matchCase` |  | boolean | 区分大小写 | 是否区分大小写（可选，默认 false）。 |
| `matchEntireCell` |  | boolean | 完整单元格匹配 | 是否要求文本完全匹配整个单元格内容（可选，默认 false）。 |
| `nodeId` | yes | string | 文档标识 | 目标电子表格的标识（必填）。支持两种格式：1) 文档链接 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid}；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。 |
| `range` |  | string | 替换范围 | 使用 A1 表示法限定替换范围（可选），如 A1:D100。不传时在整个工作表中替换。 |
| `replaceText` | yes | string | 替换文本 | 替换后的文本内容（必填）。可以为空字符串，表示删除匹配内容。 |
| `sheetId` | yes | string | 工作表标识 | 目标工作表的 ID 或名称（必填）。可通过 get_all_sheets 接口获取工作表的 id 或 name。 |
| `text` | yes | string | 查找文本 | 要查找的文本内容（必填），不能为空字符串。 |
| `useRegExp` |  | boolean | 正则表达式 | 是否使用正则表达式匹配（可选，默认 false）。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `includeHidden` | `--include-hidden` |  | false |  |  |
| `matchCase` | `--match-case` |  | false |  |  |
| `matchEntireCell` | `--match-entire-cell` |  | false |  |  |
| `nodeId` | `--node` |  |  |  |  |
| `range` | `--range` |  |  |  |  |
| `replaceText` | `--replacement` |  |  |  |  |
| `sheetId` | `--sheet-id` |  |  |  |  |
| `text` | `--find` |  |  |  |  |
| `useRegExp` | `--use-regexp` |  | false |  |  |

## dws sheet set_dropdown_lists

- Canonical path: `sheet.set_dropdown_lists`
- Product: `sheet`
- Group: `-`
- Subcommand: `set_dropdown_lists`
- Title: 设置下拉列表
- Description: 在钉钉表格的指定单元格范围内设置下拉列表。
- Required top-level parameters: `nodeId`, `sheetId`, `range`, `options`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `enableMultiSelect` |  | boolean | 是否允许多选 | 是否允许在单元格中选择多个选项（可选，默认 false）。 |
| `nodeId` | yes | string | 文档标识 | 目标电子表格的标识（必填）。支持两种格式：1) 钉钉文档链接 URL；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。 |
| `options` | yes | array; items=object | 下拉选项列表 | 下拉选项数组（必填），至少包含 1 个选项。每个选项包含 value（必填）和 color（可选）。选项值不能包含英文逗号。 |
| `options[].color` |  | string | 选项的背景色 | 选项的背景色（可选），如 "#ff0000" |
| `options[].value` |  | string | 选项值 | 选项值（必填） |
| `range` | yes | string | 单元格范围 | 要设置下拉列表的单元格范围（必填），A1 表示法，如 "A2:A100"。 |
| `sheetId` | yes | string | 工作表标识 | 目标工作表的 ID 或名称（必填）。可通过 get_all_sheets 获取。 |

### CLI flag overlay

- none

## dws sheet set_filter_criteria

- Canonical path: `sheet.set_filter_criteria`
- Product: `sheet`
- Group: `-`
- Subcommand: `set_filter_criteria`
- Title: 设置筛选条件
- Description: 设置钉钉电子表格中指定工作表筛选的某一列的筛选条件。  通过 nodeId 定位电子表格文档，通过 sheetId 定位工作表，通过 column 指定目标列。 nodeId 支持传入文档链接 URL 或文档 ID（dentryUuid），系统自动识别。 sheetId 支持传入工作表 ID 或工作表名称，可通过 get_all_sheets 获取。  支持三种筛选类型： - 按值筛选（filterType: values）：指定允许显示的值列表 - 按条件筛选（filterType: condition）：指定条件运算符和比较值 - 按颜色筛选（filterType: color）：按单元格背景色或字体颜色筛选  使用此接口前，工作表必须已创建筛选（通过 create_filter 创建）。 设置条件后会立即生效，不满足条件的行将被隐藏。  与 update_filter 的区别：此接口仅设置单列条件，update_filter 可同时设置多列条件。  仅限用户对文档具备"可编辑"权限时可操作。不支持跨组织操作。
- Required top-level parameters: `nodeId`, `sheetId`, `column`, `filterCriteria`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `column` | yes | number | 列偏移量 | 要设置筛选条件的列偏移量（必填），0-based，相对于筛选范围起始列。 |
| `filterCriteria` | yes | object | 筛选条件对象 | 筛选条件对象（必填），包含 filterType 及对应类型的条件字段。 |
| `filterCriteria.backgroundColor` |  | string | 背景色 | 按背景色筛选时的颜色值（十六进制，如 #FF0000）。仅 filterType 为 color 时有效，与 fontColor 二选一。 |
| `filterCriteria.conditionOperator` |  | string | 条件逻辑关系 | 多条件之间的逻辑关系：and（且，默认）或 or（或）。仅 conditions 包含 2 个条件时有效。 |
| `filterCriteria.conditions` |  | array; items=object | 筛选条件列表 | 按条件筛选时的条件列表，最多 2 个。仅 filterType 为 condition 时有效。 |
| `filterCriteria.conditions[].operator` |  | string | 条件操作符 | 条件操作符：equal、not-equal、contains、not-contains、starts-with、not-starts-with、ends-with、not-ends-with、greater、greater-equal、less、less-equal |
| `filterCriteria.conditions[].value` |  | string | 条件值 | 条件值 |
| `filterCriteria.filterType` | yes | string | 筛选类型 | 筛选类型（必填）：values（按值筛选）、condition（按条件筛选）、color（按颜色筛选） |
| `filterCriteria.fontColor` |  | string | 字体色 | 按字体色筛选时的颜色值（十六进制，如 #0000FF）。仅 filterType 为 color 时有效，与 backgroundColor 二选一。 |
| `filterCriteria.visibleValues` |  | array; items=string | 可见值列表 | 按值筛选时，允许显示的值列表。仅 filterType 为 values 时有效。 |
| `filterCriteria.visibleValues[]` |  | string |  |  |
| `nodeId` | yes | string | 文档标识 | 目标电子表格的标识（必填）。支持两种格式：1) 文档链接 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid}；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。 |
| `sheetId` | yes | string | 工作表标识 | 目标工作表的 ID 或名称（必填）。可通过 get_all_sheets 接口获取工作表的 id 或 name。 |

### CLI flag overlay

- none

## dws sheet filter-view update-criteria

- Canonical path: `sheet.set_filter_view_criteria`
- Product: `sheet`
- Group: `filter-view`
- Subcommand: `update-criteria`
- Title: 设置筛选视图列条件
- Description: 设置/更新筛选视图某列的筛选条件
- Required top-level parameters: `nodeId`, `sheetId`, `filterViewId`, `column`, `filterCriteria`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `column` | yes | number | 列偏移量 | 要设置筛选条件的列偏移量（必填），0-based，相对于筛选视图范围起始列。 |
| `filterCriteria` | yes | object | 筛选条件对象 | 筛选条件对象（必填），包含 filterType 及对应类型的条件字段。 |
| `filterCriteria.backgroundColor` |  | string | 背景色 | 按背景色筛选时的颜色值（十六进制，如 #FF0000）。仅 filterType 为 color 时有效，与 fontColor 二选一。 |
| `filterCriteria.conditionOperator` |  | string | 条件逻辑关系 | 多条件之间的逻辑关系：and（且，默认）或 or（或）。仅 conditions 包含 2 个条件时有效。 |
| `filterCriteria.conditions` |  | array; items=object | 筛选条件列表 | 按条件筛选时的条件列表，最多 2 个。仅 filterType 为 condition 时有效。 |
| `filterCriteria.conditions[].operator` |  | string | 条件操作符 | 条件操作符：equal、not-equal、contains、not-contains、starts-with、not-starts-with、ends-with、not-ends-with、greater、greater-equal、less、less-equal |
| `filterCriteria.conditions[].value` |  | string | 条件值 | 条件值 |
| `filterCriteria.filterType` | yes | string | 筛选类型 | 筛选类型（必填）：values（按值筛选）、condition（按条件筛选）、color（按颜色筛选） |
| `filterCriteria.fontColor` |  | string | 字体色 | 按字体色筛选时的颜色值（十六进制，如 #0000FF）。仅 filterType 为 color 时有效，与 backgroundColor 二选一。 |
| `filterCriteria.visibleValues` |  | array; items=string | 可见值列表 | 按值筛选时，允许显示的值列表。仅 filterType 为 values 时有效。 |
| `filterCriteria.visibleValues[]` |  | string |  |  |
| `filterViewId` | yes | string | 筛选视图 ID | 目标筛选视图 ID（必填）。可通过 get_filter_views 接口获取。 |
| `nodeId` | yes | string | 文档标识 | 目标电子表格的标识（必填）。支持两种格式：1) 文档链接 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid}；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。 |
| `sheetId` | yes | string | 工作表标识 | 目标工作表的 ID 或名称（必填）。可通过 get_all_sheets 接口获取工作表的 id 或 name。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `column` | `--column` |  |  |  |  |
| `filterCriteria` | `--filter-criteria` | json_parse |  |  |  |
| `filterViewId` | `--filter-view-id` |  |  |  |  |
| `nodeId` | `--node` |  |  |  |  |
| `sheetId` | `--sheet-id` |  |  |  |  |

## dws sheet sort_filter

- Canonical path: `sheet.sort_filter`
- Product: `sheet`
- Group: `-`
- Subcommand: `sort_filter`
- Title: 筛选排序
- Description: 对钉钉电子表格中指定工作表的筛选范围内的数据进行排序。  通过 nodeId 定位电子表格文档，通过 sheetId 定位工作表。 nodeId 支持传入文档链接 URL 或文档 ID（dentryUuid），系统自动识别。 sheetId 支持传入工作表 ID 或工作表名称，可通过 get_all_sheets 获取。  排序基于筛选范围内的指定列，支持升序和降序。 排序会实际改变工作表中数据行的物理顺序。  使用此接口前，工作表必须已创建筛选（通过 create_filter 创建）。  仅限用户对文档具备"可编辑"权限时可操作。不支持跨组织操作。
- Required top-level parameters: `nodeId`, `sheetId`, `field`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `field` | yes | object | 排序规则 | 排序规则（必填）。包含 column（列偏移量）和 ascending（是否升序）。例如：{"column": 0, "ascending": true} 表示按第一列升序，{"column": 1, "ascending": false} 表示按第二列降序。 |
| `field.ascending` |  | boolean | ascending | 是否升序排列（可选），默认为 true（升序）。设为 false 表示降序。 |
| `field.column` | yes | number | column | 列偏移量（必填），从 0 开始，相对于筛选范围首列 |
| `nodeId` | yes | string | 文档标识 | 目标电子表格的标识（必填）。支持两种格式：1) 文档链接 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid}；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。 |
| `sheetId` | yes | string | 工作表标识 | 目标工作表的 ID 或名称（必填）。可通过 get_all_sheets 接口获取工作表的 id 或 name。 |

### CLI flag overlay

- none

## dws sheet submit_export_job

- Canonical path: `sheet.submit_export_job`
- Product: `sheet`
- Group: `-`
- Subcommand: `submit_export_job`
- Title: 导出钉钉表格
- Description: 用于将钉钉在线表格导出为 xlsx。导出为异步操作，提交后需使用 query_export_job 轮询任务状态并获取下载链接。
- Required top-level parameters: `nodeId`, `exportFormat`
- Sensitive flag from schema: `false`
- Mutation risk: `read-with-local-output`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `exportFormat` | yes | string | 导出格式 | 导出的目标文件格式，必填。钉钉电子表格侧仅支持 'xlsx'，必须传入 'xlsx'。 |
| `nodeId` | yes | string | 表格标识 | 要导出的钉钉在线电子表格的标识，支持以下格式：1) 文档 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid}；2) dentryUuid（32 位字母数字字符串）。必填。仅支持钉钉在线电子表格（alxs），传入其他类型（如钉钉文字文档）会报错。 |

### CLI flag overlay

- none

## dws sheet unmerge-cells

- Canonical path: `sheet.unmerge_range`
- Product: `sheet`
- Group: `-`
- Subcommand: `unmerge-cells`
- Title: 取消合并单元格
- Description: 取消指定范围的合并单元格
- Required top-level parameters: `nodeId`, `sheetId`, `rangeAddress`
- Sensitive flag from schema: `false`
- Mutation risk: `unknown-review-before-use`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `nodeId` | yes | string | 文档标识 | 目标电子表格的标识（必填）。支持两种格式：1) 文档链接 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid}；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。 |
| `rangeAddress` | yes | string | 范围地址 | 要取消合并的范围（必填），使用 A1 表示法。例如 A1:D5 表示取消 A1 到 D5 区域内的所有合并。 |
| `sheetId` | yes | string | 工作表标识 | 目标工作表的 ID 或名称（必填）。可通过 get_all_sheets 接口获取工作表的 id 或 name。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `nodeId` | `--node` |  |  |  |  |
| `rangeAddress` | `--range` |  |  |  |  |
| `sheetId` | `--sheet-id` |  |  |  |  |

## dws sheet update-dimension

- Canonical path: `sheet.update_dimension`
- Product: `sheet`
- Group: `-`
- Subcommand: `update-dimension`
- Title: 更新行列属性
- Description: 更新指定范围行/列属性（显隐 hidden、行高/列宽 pixel-size，至少一项）
- Required top-level parameters: `nodeId`, `sheetId`, `dimension`, `startIndex`, `length`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `dimension` | yes | string | 维度类型 | 更新维度：ROWS 表示行（对应行高/行显隐），COLUMNS 表示列（对应列宽/列显隐）。必填。 |
| `hidden` |  | boolean | 是否隐藏 | 是否隐藏，true 表示隐藏、false 表示显示。与 pixelSize 至少填其一。可选。 |
| `length` | yes | number | 更新数量 | 要更新的连续行/列数量，必须为正整数（>= 1），行列均最多 5000。必填。 |
| `nodeId` | yes | string | 表格文件 ID | 表格文件 ID，知识库 API 返回的 nodeId(dentryUuid) 即是表格 nodeId，可通过获取节点或创建知识库文档接口获取。必填。 |
| `pixelSize` |  | number | 行高或列宽 | 行高或列宽，单位为像素（px），必须为非负整数。dimension=ROWS 时表示行高，dimension=COLUMNS 时表示列宽。与 hidden 至少填其一。可选。 |
| `sheetId` | yes | string | 工作表 ID | 工作表 ID 或名称。可通过获取所有工作表接口获取 id 或 name 参数值。必填。若 startIndex 携带 Sheet 前缀（如 Sheet1!3），以前缀为准。 |
| `startIndex` | yes | string | 起始位置 | 起始位置的 A1 表示法。dimension=ROWS 时为 1-based 行号字符串，如 "3" 表示从第 3 行开始；dimension=COLUMNS 时为列字母，如 "A" 表示从 A 列开始、"AB" 表示从 AB 列开始。从该位置起的连续 length 行/列将被更新。允许携带工作表前缀，如 "Sheet1!3" / "Sheet1!A"，此时将忽略 sheetId 参数。必填。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `dimension` | `--dimension` |  |  |  |  |
| `hidden` | `--hidden` |  | false |  |  |
| `length` | `--length` |  |  |  |  |
| `nodeId` | `--node` |  |  |  |  |
| `pixelSize` | `--pixel-size` |  |  |  |  |
| `sheetId` | `--sheet-id` |  |  |  |  |
| `startIndex` | `--start-index` |  |  |  |  |

## dws sheet update_filter

- Canonical path: `sheet.update_filter`
- Product: `sheet`
- Group: `-`
- Subcommand: `update_filter`
- Title: 更新筛选
- Description: 批量更新钉钉电子表格中指定工作表的筛选条件。  通过 nodeId 定位电子表格文档，通过 sheetId 定位工作表。 nodeId 支持传入文档链接 URL 或文档 ID（dentryUuid），系统自动识别。 sheetId 支持传入工作表 ID 或工作表名称，可通过 get_all_sheets 获取。  可同时更新多列的筛选条件，支持三种筛选类型： - 按值筛选（filterType: values）：指定允许显示的值列表，未在列表中的值将被隐藏 - 按条件筛选（filterType: condition）：指定条件运算符和比较值 - 按颜色筛选（filterType: color）：按单元格背景色或字体颜色筛选  使用此接口前，工作表必须已创建筛选（通过 create_filter 创建）。 此接口会替换指定列的现有筛选条件，未指定的列保持不变。  仅限用户对文档具备"可编辑"权限时可操作。不支持跨组织操作。
- Required top-level parameters: `nodeId`, `sheetId`, `criteria`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `criteria` | yes | array; items=object | 筛选条件 | 批量设置多列的筛选条件（必填）。数组格式，每个元素包含 column（列偏移量，0-based）和筛选条件字段。示例：[{"column":0,"filterType":"values","visibleValues":["A","B"]}] |
| `criteria[].backgroundColor` |  | string | 背景色 | 按背景色筛选时的颜色值（十六进制，如 #FF0000）。仅 filterType 为 color 时有效，与 fontColor 二选一 |
| `criteria[].column` | yes | number | 列偏移量 | 列偏移量（必填），0-based，相对于筛选范围起始列 |
| `criteria[].conditionOperator` |  | string | 条件逻辑关系 | 多条件之间的逻辑关系：and（且，默认）或 or（或）。仅 conditions 包含 2 个条件时有效 |
| `criteria[].conditions` |  | array; items=object | 筛选条件列表 | 按条件筛选时的条件列表，最多 2 个。仅 filterType 为 condition 时有效。每个条件包含 operator（操作符）和 value（条件值） |
| `criteria[].conditions[].operator` |  | string | 条件操作符 | 条件操作符：equal、not-equal、contains、not-contains、starts-with、not-starts-with、ends-with、not-ends-with、greater、greater-equal、less、less-equal |
| `criteria[].conditions[].value` |  | string | 条件值 | 条件值 |
| `criteria[].filterType` | yes | string | 筛选类型 | 筛选类型：values（按值筛选）、condition（按条件筛选）、color（按颜色筛选） |
| `criteria[].fontColor` |  | string | 字体色 | 按字体色筛选时的颜色值（十六进制，如 #0000FF）。仅 filterType 为 color 时有效，与 backgroundColor 二选一 |
| `criteria[].visibleValues` |  | array; items=string | 可见值列表 | 按值筛选时，允许显示的值列表。仅 filterType 为 values 时有效 |
| `criteria[].visibleValues[]` |  | string |  |  |
| `nodeId` | yes | string | 文档标识 | 目标电子表格的标识（必填）。支持两种格式：1) 钉钉文档链接 URL；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。 |
| `sheetId` | yes | string | 工作表标识 | 目标工作表的 ID 或名称（必填）。可通过 get_all_sheets 接口获取工作表的 id 或 name。 |

### CLI flag overlay

- none

## dws sheet filter-view update

- Canonical path: `sheet.update_filter_view`
- Product: `sheet`
- Group: `filter-view`
- Subcommand: `update`
- Title: 更新筛选视图
- Description: 更新筛选视图（名称/范围/条件）
- Required top-level parameters: `nodeId`, `sheetId`, `filterViewId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `criteria` |  | array; items=object | 筛选条件 | 需要更新的列筛选条件（可选）。数组格式，每个元素包含 column（列偏移量，0-based）和筛选条件字段。示例：[{"column":0,"filterType":"values","visibleValues":["A","B"]}] |
| `criteria[].backgroundColor` |  | string | 背景色 | 按背景色筛选时的颜色值（十六进制，如 #FF0000）。仅 filterType 为 color 时有效，与 fontColor 二选一 |
| `criteria[].column` | yes | number | 列偏移量 | 列偏移量（必填），0-based，相对于筛选范围起始列 |
| `criteria[].conditionOperator` |  | string | 条件逻辑关系 | 多条件之间的逻辑关系：and（且，默认）或 or（或）。仅 conditions 包含 2 个条件时有效 |
| `criteria[].conditions` |  | array; items=object | 筛选条件列表 | 按条件筛选时的条件列表，最多 2 个。仅 filterType 为 condition 时有效。每个条件包含 operator（操作符）和 value（条件值） |
| `criteria[].conditions[].operator` |  | string | 条件操作符 | 条件操作符：equal、not-equal、contains、not-contains、starts-with、not-starts-with、ends-with、not-ends-with、greater、greater-equal、less、less-equal |
| `criteria[].conditions[].value` |  | string | 条件值 | 条件值 |
| `criteria[].filterType` | yes | string | 筛选类型 | 筛选类型：values（按值筛选）、condition（按条件筛选）、color（按颜色筛选） |
| `criteria[].fontColor` |  | string | 字体色 | 按字体色筛选时的颜色值（十六进制，如 #0000FF）。仅 filterType 为 color 时有效，与 backgroundColor 二选一 |
| `criteria[].visibleValues` |  | array; items=string | 可见值列表 | 按值筛选时，允许显示的值列表。仅 filterType 为 values 时有效 |
| `criteria[].visibleValues[]` |  | string |  |  |
| `filterViewId` | yes | string | 筛选视图 ID | 要更新的筛选视图 ID（必填）。可通过 get_filter_views 接口获取。 |
| `name` |  | string | 筛选视图名称 | 新的筛选视图名称（可选，name、range、criteria 至少传一个）。 |
| `nodeId` | yes | string | 文档标识 | 目标电子表格的标识（必填）。支持两种格式：1) 文档链接 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid}；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。 |
| `range` |  | string | 筛选视图范围 | 新的筛选视图范围（可选，name、range、criteria 至少传一个），使用 A1 表示法。 |
| `sheetId` | yes | string | 工作表标识 | 目标工作表的 ID 或名称（必填）。可通过 get_all_sheets 接口获取工作表的 id 或 name。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `criteria` | `--criteria` | json_parse_strict |  |  |  |
| `filterViewId` | `--filter-view-id` |  |  |  |  |
| `name` | `--name` |  |  |  |  |
| `nodeId` | `--node` |  |  |  |  |
| `range` | `--range` |  |  |  |  |
| `sheetId` | `--sheet-id` |  |  |  |  |

## dws sheet update_float_image

- Canonical path: `sheet.update_float_image`
- Product: `sheet`
- Group: `-`
- Subcommand: `update_float_image`
- Title: 更新浮动图片
- Description: 更新钉钉电子表格中指定浮动图片的属性。
- Required top-level parameters: `nodeId`, `sheetId`, `floatImageId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `floatImageId` | yes | string | 浮动图片 ID | 浮动图片 ID（必填）。可通过 list_float_images 接口获取。 |
| `height` |  | number | 图片高度 | 新的图片高度（可选），单位像素，正整数。 |
| `nodeId` | yes | string | 文档标识 | 目标电子表格的标识（必填）。支持两种格式：1) 钉钉文档链接 URL；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。 |
| `offsetX` |  | number | 水平偏移 | 新的水平偏移量（可选），单位像素。 |
| `offsetY` |  | number | 垂直偏移 | 新的垂直偏移量（可选），单位像素。 |
| `range` |  | string | 锚点单元格 | 新的锚点单元格位置（可选），使用 A1 表示法，如 "A1"、"B3"。 |
| `sheetId` | yes | string | 工作表标识 | 目标工作表的 ID 或名称（必填）。可通过 get_all_sheets 接口获取工作表的 id 或 name。 |
| `src` |  | string | 图片 URL | 新的图片 URL 地址（可选）。 |
| `width` |  | number | 图片宽度 | 新的图片宽度（可选），单位像素，正整数。 |

### CLI flag overlay

- none

## dws sheet range update

- Canonical path: `sheet.update_range`
- Product: `sheet`
- Group: `range`
- Subcommand: `update`
- Title: 更新表格指定区域内容
- Description: 更新工作表指定区域内容（--values 与 --hyperlinks 至少传一项）
- Required top-level parameters: `nodeId`, `sheetId`, `rangeAddress`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `backgroundColors` |  | array; items=array; items=string | 单元格背景色 | 单元格背景色，二维数组格式。取值为 #RRGGBB 形式的十六进制颜色字符串，单个值长度 ≤ 128。行列维度需与 rangeAddress 范围一致。示例（A1:B2）：[["#FFF2CC","#DDEBF7"],["#E2EFDA","#FCE4D6"]]。外层数组最大长度 1000。 |
| `backgroundColors[]` |  | array; items=string |  |  |
| `fontColors` |  | array; items=array; items=string | 字体颜色 | 字体颜色，二维数组格式。取值为 #RRGGBB 形式的十六进制颜色字符串，单个值长度 ≤ 128。行列维度需与 rangeAddress 范围一致。示例：[["#333333","#FF0000"]]。外层数组最大长度 1000。 |
| `fontColors[]` |  | array; items=string |  |  |
| `fontSizes` |  | array; items=array; items=number | 单元格字号 | 单元格字号，二维数组格式，元素为正整数。行列维度需与 rangeAddress 范围一致。示例（A1:B2）：[[12,14],[10,10]]。外层数组最大长度 1000。 |
| `fontSizes[]` |  | array; items=number |  |  |
| `fontWeights` |  | array; items=array; items=string | 字体粗细 | 字体粗细，二维数组格式，元素取值枚举：bold / normal。行列维度需与 rangeAddress 范围一致。示例：[["bold","normal"]]。外层数组最大长度 1000。 |
| `fontWeights[]` |  | array; items=string |  |  |
| `horizontalAlignments` |  | array; items=array; items=string | 水平对齐 | 单元格水平对齐方式，二维数组格式，元素取值枚举：left / center / right / general。行列维度需与 rangeAddress 范围一致。示例：[["center","left"]]。外层数组最大长度 1000。 |
| `horizontalAlignments[]` |  | array; items=string |  |  |
| `hyperlinks` |  | array; items=array; items=object | 超链接 | 超链接，二维数组格式。可与 values 共存，会进行合并（对于同一个单元格，hyperlinks 的优先级更高）。每个元素为对象或者 null（但不支持全部单元格都填充 null），null 代表清除单元格数据。对象包含 type、link、text 三个字段。type 可选值：path（外部链接）、sheet（工作表链接）、range（单元格链接）。示例：1) 外部链接：{"type":"path","link":"https://www.dingtalk.com","text":"DingTalk"}；2) 工作表链接：{"type":"sheet","link":"sheet2","text":"跳转到sheet2"} 或 {"type":"range","link":"sheet2!A1","text":"跳转到sheet2的A1"}；3) 单元格链接：{"type":"range","link":"sheet2!A1:B2","text":"跳转到sheet2的A1:B2"}。 |
| `hyperlinks[]` |  | array; items=object |  |  |
| `nodeId` | yes | string | 表格文件 ID | 表格文件 ID（必填）。支持三种格式：1) 钉钉文档链接 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid} 或 https://alidocs.dingtalk.com/spreadsheetv2/{dentryKey}/...；2) 文档 ID（dentryUuid），32 位字母数字字符串；3) 其他钉钉文档域名下的链接。系统自动识别格式。 |
| `numberFormat` |  | string | 数字格式 | 数字格式。常用值：General（常规）、@（文本）、#,##0（数字）、#,##0.00（带小数）、0%（百分数）、yyyy/m/d（日期）、hh:mm:ss（时间）、¥#,##0（人民币）、$#,##0（美元）等。 |
| `rangeAddress` | yes | string | Range 地址 | 目标单元格区域地址，如 A1:B3。必填。 |
| `sheetId` | yes | string | 工作表 ID | 工作表 ID 或名称。可通过获取所有工作表接口获取 id 或 name 参数值。必填。 |
| `values` |  | array; items=array; items=string | 单元格值 | 单元格的值，二维数组格式。行列维度需与 rangeAddress 范围一致。示例（A1:B3）：[["1","2"],["3","4"],["5","6"]]。可与 hyperlinks 共存，会进行合并（对于同一个单元格，hyperlinks 的优先级更高）。可插入的值类型包括：1) 字符，如 "123"；2) 公式，如 "=A3+B3"、"=SUM(B2:B4)"；3) null，代表清除该单元格内容（但不支持全部单元格都填充 null）。 |
| `values[]` |  | array; items=string |  |  |
| `verticalAlignments` |  | array; items=array; items=string | 垂直对齐 | 单元格垂直对齐方式，二维数组格式，元素取值枚举：top / middle / bottom。行列维度需与 rangeAddress 范围一致。示例：[["middle","top"]]。外层数组最大长度 1000。 |
| `verticalAlignments[]` |  | array; items=string |  |  |
| `wordWrap` |  | string | 换行方式 | 单元格换行方式，单值字符串，整个 range 共用（非二维数组）。取值枚举：overflow / clip / autoWrap（驼峰，非下划线）。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `hyperlinks` | `--hyperlinks` | json_parse |  |  |  |
| `nodeId` | `--node` |  |  |  |  |
| `numberFormat` | `--number-format` |  |  |  |  |
| `rangeAddress` | `--range` |  |  |  |  |
| `sheetId` | `--sheet-id` |  |  |  |  |
| `values` | `--values` | json_parse |  |  |  |

## dws sheet update_sheet

- Canonical path: `sheet.update_sheet`
- Product: `sheet`
- Group: `-`
- Subcommand: `update_sheet`
- Title: 更新工作表属性
- Description: 更新钉钉电子表格中指定工作表的属性，包括名称、位置、可见性、冻结行列等。
- Required top-level parameters: `nodeId`, `sheetId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `frozenColumnCount` |  | number | 冻结列数 | 冻结的列数（可选）。0 表示取消冻结。 |
| `frozenRowCount` |  | number | 冻结行数 | 冻结的行数（可选）。0 表示取消冻结。 |
| `hidden` |  | boolean | 是否隐藏 | 是否隐藏工作表（可选）。true 隐藏，false 显示。 |
| `index` |  | number | 新位置索引 | 工作表的新位置索引（可选，0-based）。0 表示移到最前面。 |
| `nodeId` | yes | string | 表格文件 ID | 表格文件 ID（必填）。支持两种格式：1) 钉钉文档链接 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid} 或 https://alidocs.dingtalk.com/spreadsheetv2/{dentryKey}/...；2) 文档 ID（dentryUuid），32 位字母数字字符串； |
| `sheetId` | yes | string | 工作表标识 | 目标工作表的 ID 或名称（必填）。可通过 get_all_sheets 接口获取工作表的 id 或 name。 |
| `title` |  | string | 新名称 | 工作表的新名称（可选）。最长 100 字符。如果新名称与已有工作表重名，系统会自动在名称后追加后缀（如 "-1"）以避免冲突。 |

### CLI flag overlay

- none

## dws sheet write-image

- Canonical path: `sheet.write_image`
- Product: `sheet`
- Group: `-`
- Subcommand: `write-image`
- Title: 单元格写入图片
- Description: 将已上传图片资源写入指定单元格
- Required top-level parameters: `nodeId`, `sheetId`, `rangeAddress`, `resourceId`, `resourceUrl`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `height` |  | number | 图片高度 | 显示的图片高度 |
| `nodeId` | yes | string | 表格文件 ID | 表格文件 ID，知识库 API 返回的 nodeId(dentryUuid) 即是表格 nodeId，可通过获取节点或创建知识库文档接口获取。必填。 |
| `rangeAddress` | yes | string | Range 地址 | 目标单元格区域地址，如 A1:B3。必填。 |
| `resourceId` | yes | string | 资源 ID | 获取上传资源信息返回的资源 ID |
| `resourceUrl` | yes | string | 资源链接 | 获取上传资源信息返回的资源链接 |
| `sheetId` | yes | string | 工作表 ID | 工作表 ID 或名称。可通过获取所有工作表接口获取 id 或 name 参数值。必填。 |
| `width` |  | number | 图片宽度 | 显示的图片宽度 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `height` | `--height` |  |  |  |  |
| `nodeId` | `--node` |  |  |  |  |
| `rangeAddress` | `--range` |  |  |  |  |
| `resourceId` | `--resource-id` |  |  |  |  |
| `resourceUrl` | `--resource-url` |  |  |  |  |
| `sheetId` | `--sheet-id` |  |  |  |  |
| `width` | `--width` |  |  |  |  |

