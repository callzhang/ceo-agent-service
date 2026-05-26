# dws aitable Commands

AI 表格操作（Base / 数据表 / 字段 / 记录 / 视图 / 仪表盘 / 图表 / 导入导出 / 附件 / 模板）

Commands in this file: 48

## Command Index

| CLI | Canonical path | Risk |
| --- | --- | --- |
| [`dws aitable base copy`](#dws-aitable-base-copy) | `aitable.copy_base` | `unknown-review-before-use` |
| [`dws aitable base create`](#dws-aitable-base-create) | `aitable.create_base` | `mutating-review-first` |
| [`dws aitable chart create`](#dws-aitable-chart-create) | `aitable.create_chart` | `mutating-review-first` |
| [`dws aitable dashboard create`](#dws-aitable-dashboard-create) | `aitable.create_dashboard` | `mutating-review-first` |
| [`dws aitable field create`](#dws-aitable-field-create) | `aitable.create_fields` | `mutating-review-first` |
| [`dws aitable create_guide_document`](#dws-aitable-createguidedocument) | `aitable.create_guide_document` | `mutating-review-first` |
| [`dws aitable record create`](#dws-aitable-record-create) | `aitable.create_records` | `mutating-review-first` |
| [`dws aitable table create`](#dws-aitable-table-create) | `aitable.create_table` | `mutating-review-first` |
| [`dws aitable view create`](#dws-aitable-view-create) | `aitable.create_view` | `mutating-review-first` |
| [`dws aitable base delete`](#dws-aitable-base-delete) | `aitable.delete_base` | `sensitive-mutating` |
| [`dws aitable chart delete`](#dws-aitable-chart-delete) | `aitable.delete_chart` | `sensitive-mutating` |
| [`dws aitable dashboard delete`](#dws-aitable-dashboard-delete) | `aitable.delete_dashboard` | `sensitive-mutating` |
| [`dws aitable field delete`](#dws-aitable-field-delete) | `aitable.delete_field` | `sensitive-mutating` |
| [`dws aitable delete_guide_document`](#dws-aitable-deleteguidedocument) | `aitable.delete_guide_document` | `mutating-review-first` |
| [`dws aitable record delete`](#dws-aitable-record-delete) | `aitable.delete_records` | `sensitive-mutating` |
| [`dws aitable table delete`](#dws-aitable-table-delete) | `aitable.delete_table` | `sensitive-mutating` |
| [`dws aitable view delete`](#dws-aitable-view-delete) | `aitable.delete_view` | `sensitive-mutating` |
| [`dws aitable export data`](#dws-aitable-export-data) | `aitable.export_data` | `read-with-local-output` |
| [`dws aitable base get`](#dws-aitable-base-get) | `aitable.get_base` | `read-only` |
| [`dws aitable get_base_primary_doc_id`](#dws-aitable-getbaseprimarydocid) | `aitable.get_base_primary_doc_id` | `read-only` |
| [`dws aitable chart get`](#dws-aitable-chart-get) | `aitable.get_chart` | `read-only` |
| [`dws aitable chart.share get`](#dws-aitable-chartshare-get) | `aitable.get_chart_share` | `mutating-review-first` |
| [`dws aitable dashboard get`](#dws-aitable-dashboard-get) | `aitable.get_dashboard` | `read-only` |
| [`dws aitable dashboard config-example`](#dws-aitable-dashboard-config-example) | `aitable.get_dashboard_config_example` | `mutating-review-first` |
| [`dws aitable dashboard.share get`](#dws-aitable-dashboardshare-get) | `aitable.get_dashboard_share` | `mutating-review-first` |
| [`dws aitable chart widgets-example`](#dws-aitable-chart-widgets-example) | `aitable.get_dashboard_widgets_example` | `mutating-review-first` |
| [`dws aitable field get`](#dws-aitable-field-get) | `aitable.get_fields` | `mutating-review-first` |
| [`dws aitable table get`](#dws-aitable-table-get) | `aitable.get_tables` | `read-only` |
| [`dws aitable view get`](#dws-aitable-view-get) | `aitable.get_views` | `read-only` |
| [`dws aitable import data`](#dws-aitable-import-data) | `aitable.import_data` | `mutating-review-first` |
| [`dws aitable base list`](#dws-aitable-base-list) | `aitable.list_bases` | `read-only` |
| [`dws aitable attachment upload`](#dws-aitable-attachment-upload) | `aitable.prepare_attachment_upload` | `mutating-review-first` |
| [`dws aitable import upload`](#dws-aitable-import-upload) | `aitable.prepare_import_upload` | `mutating-review-first` |
| [`dws aitable record query`](#dws-aitable-record-query) | `aitable.query_records` | `read-only` |
| [`dws aitable run_ai_field`](#dws-aitable-runaifield) | `aitable.run_ai_field` | `unknown-review-before-use` |
| [`dws aitable run_datasource_sync`](#dws-aitable-rundatasourcesync) | `aitable.run_datasource_sync` | `read-only` |
| [`dws aitable base search`](#dws-aitable-base-search) | `aitable.search_bases` | `read-only` |
| [`dws aitable template search`](#dws-aitable-template-search) | `aitable.search_templates` | `mutating-review-first` |
| [`dws aitable base update`](#dws-aitable-base-update) | `aitable.update_base` | `mutating-review-first` |
| [`dws aitable chart update`](#dws-aitable-chart-update) | `aitable.update_chart` | `mutating-review-first` |
| [`dws aitable chart.share update`](#dws-aitable-chartshare-update) | `aitable.update_chart_share` | `mutating-review-first` |
| [`dws aitable dashboard update`](#dws-aitable-dashboard-update) | `aitable.update_dashboard` | `mutating-review-first` |
| [`dws aitable dashboard.share update`](#dws-aitable-dashboardshare-update) | `aitable.update_dashboard_share` | `mutating-review-first` |
| [`dws aitable field update`](#dws-aitable-field-update) | `aitable.update_field` | `mutating-review-first` |
| [`dws aitable update_guide_document`](#dws-aitable-updateguidedocument) | `aitable.update_guide_document` | `mutating-review-first` |
| [`dws aitable record update`](#dws-aitable-record-update) | `aitable.update_records` | `mutating-review-first` |
| [`dws aitable table update`](#dws-aitable-table-update) | `aitable.update_table` | `mutating-review-first` |
| [`dws aitable view update`](#dws-aitable-view-update) | `aitable.update_view` | `mutating-review-first` |

## Group Summary

| Group | Commands |
| --- | ---: |
| `-` | 6 |
| `attachment` | 1 |
| `base` | 7 |
| `chart` | 5 |
| `chart.share` | 2 |
| `dashboard` | 5 |
| `dashboard.share` | 2 |
| `export` | 1 |
| `field` | 4 |
| `import` | 2 |
| `record` | 4 |
| `table` | 4 |
| `template` | 1 |
| `view` | 4 |

## dws aitable base copy

- Canonical path: `aitable.copy_base`
- Product: `aitable`
- Group: `base`
- Subcommand: `copy`
- Title: copy_base
- Description: 复制 AI 表格到指定目录
- Required top-level parameters: `baseId`, `onlyCopyMeta`, `targetFolderId`
- Sensitive flag from schema: `false`
- Mutation risk: `unknown-review-before-use`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | 源 Base 标识（必填），支持以下格式：dentryUuid / baseId：32 位字母数字字符串文档/文件夹的 dentryUuid |
| `onlyCopyMeta` | yes | boolean | onlyCopyMeta | 是否仅复制基础元数据（必选，默认 false）当设置为true}时，仅复制 Base 的结构信息（如表、字段定义等），不复制实际记录数据；当设置为 false 或未提供时，将完整复制 Base 的全部内容与结构。 |
| `targetFolderId` | yes | string | targetFolderId | 目标父节点标识（必填），该字段最终生效值必须为 dentryUuid 类型（32 位字母数字字符串）若直接传入 dentryUuid 字符串，则直接使用该值 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |
| `onlyCopyMeta` | `--only-struct` |  |  |  |  |
| `targetFolderId` | `--target-folder-id` |  |  |  |  |

## dws aitable base create

- Canonical path: `aitable.create_base`
- Product: `aitable`
- Group: `base`
- Subcommand: `create`
- Title: 创建 AI 表格
- Description: 创建一个新的 AI 表格 Base。当前仅要求 baseName，服务端按默认模板创建并返回 baseId/baseName，如果需要创建的特定的文件夹路径下，则需要传递folderId，这个是知识库节点的ID，调用文档的相关服务可以获取
- Required top-level parameters: `baseName`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseName` | yes | string | baseName | Base 名称，1-50 字符；会去除首尾空格后校验 |
| `folderId` |  | string | folderId | 对外协议字段名固定为folderId，表示目标父节点的 dentryUuid。   层会进一步兼容同字段传入的标准节点 URL，并在创建前解析出实际生效的节点 ID。 |
| `templateId` |  | string | templateId | 创建 Base 模板 ID，默认创建一个空 Base。可通过 search_templates 获取模板。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseName` | `--name` |  |  |  |  |
| `templateId` | `--template-id` |  |  |  |  |

## dws aitable chart create

- Canonical path: `aitable.create_chart`
- Product: `aitable`
- Group: `chart`
- Subcommand: `create`
- Title: 更新图表
- Description: 在指定 dashboard 下创建 chart。调用前必须先调用 get_dashboard_widgets_example 了解 config 入参结构和要求。返回新创建的 chart 详情。
- Required top-level parameters: `baseId`, `dashboardId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | 必传参数，所属 Base 的唯一标识，可通过 list_bases / search_bases 获取 |
| `config` |  | object | config | 必传参数，图表配置对象，必须按 get_dashboard_widgets_example 返回的 JSONC 结构和注释构造符合要求的 JSON，仅需将占位值替换为真实值 |
| `dashboardId` | yes | string | dashboardId | 必传参数，所属 dashboard 的唯一标识，可通过 get_base 获取；若需进一步确认该 dashboard 下的 chart summary，可先调用 get_dashboard |
| `layout` |  | object | layout | 必传参数，图表在 dashboard 中的位置和大小。x/y 表示横纵坐标，w/h 表示宽度/高度，单位是列数或行数。仪表盘是网格布局共 12 列、行数无明确限制，设置布局时一定注意。添加图表时同一行的图表保持高度一致，每行的图表宽度相加需要正好将整行填满，以避免出现空白。总计类的图表排在上部，以方便用户快速查看，下方再放置更具体的图表。通过 get_dashboard 获取当前已有图表的布局信息，新增图表时请合理安排布局以避免与现有图表重叠，如有必要也可以通过 update_chart 调整已有图表的布局以适应新增图表。 |
| `layout.h` | yes | number | h | 布局高度（所占行数） |
| `layout.parentId` |  | string | parentId | 可选，父容器 ID |
| `layout.w` | yes | number | w | 布局宽度（所占列数） |
| `layout.x` | yes | number | x | 布局横坐标（列） |
| `layout.y` | yes | number | y | 布局纵坐标（行） |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |
| `config` | `--config` | json_parse |  |  |  |
| `dashboardId` | `--dashboard-id` |  |  |  |  |
| `layout` | `--layout` | json_parse |  |  |  |

## dws aitable dashboard create

- Canonical path: `aitable.create_dashboard`
- Product: `aitable`
- Group: `dashboard`
- Subcommand: `create`
- Title: 创建仪表盘
- Description: 在指定 Base 下创建 dashboard。调用前必须先调用 get_dashboard_config_example 了解 config 入参结构和要求。返回新创建的 dashboard 详情。
- Required top-level parameters: `baseId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | 必传参数，所属 Base 的唯一标识，可通过 list_bases / search_bases 获取 |
| `config` |  | object | config | 必传参数，Dashboard 配置对象，必须按 get_dashboard_config_example 返回的 JSONC 结构和注释构造符合要求的 JSON |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |
| `config` | `--config` | json_parse |  |  |  |

## dws aitable field create

- Canonical path: `aitable.create_fields`
- Product: `aitable`
- Group: `field`
- Subcommand: `create`
- Title: create_fields
- Description: 在已有表格中批量新增字段。适用于建表后补充一批字段，或一次性添加多个关联、流转等复杂类型字段。单次最多创建 15 个字段；若超过该数量，请拆分多次调用。允许部分成功，返回结果会逐项说明每个字段是否创建成功；失败项会返回 reason 说明失败原因。
- Required top-level parameters: `baseId`, `tableId`, `fields`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | Base ID（通过 list_bases 获取） |
| `fields` | yes | array; items=object | fields | 待新增字段列表，至少包含 1 个字段，单次最多 15 个。系统会按数组顺序依次创建，返回结果顺序与入参保持一致，并逐项标明成功/失败状态。 |
| `fields[].aiConfig` |  | object | aiConfig | AI 字段配置。当前内部固定按 ai-agent 处理，调用方无需传 type。 |
| `fields[].aiConfig.autoRecompute` |  | boolean | autoRecompute | 可选，是否在 prompt 引用的字段值发生变化后自动重新计算该 AI 字段。true 表示开启自动重算，引用字段更新后系统会自动刷新结果；false 表示关闭自动重算，仅在手动触发或其他业务动作要求重算时更新。 |
| `fields[].aiConfig.computeOnEmptyRef` |  | boolean | computeOnEmptyRef | 可选，引用字段为空时是否继续触发 AI 计算。默认 false（引用字段为空时跳过计算）；设为 true 时，即使 prompt 中引用的字段值为空，也会触发计算。 |
| `fields[].aiConfig.enableThinking` |  | boolean | enableThinking | 可选，是否启用深度思考。 |
| `fields[].aiConfig.enableWebSearch` |  | boolean | enableWebSearch | 可选，是否启用联网搜索。 |
| `fields[].aiConfig.imageConfig` |  | object | imageConfig | 可选，图片生成配置。仅 outputType=image 时有效。 |
| `fields[].aiConfig.imageConfig.aiGeneratedWatermark` | yes | boolean | aiGeneratedWatermark | 是否生成 AI 水印。 |
| `fields[].aiConfig.imageConfig.resolution` | yes | string | resolution | 图片分辨率。支持 1280*1280、1024*1024、800*1200、1200*800、960*1280、1280*960、720*1280、1280*720、1344*576。 |
| `fields[].aiConfig.outputType` | yes | string | outputType | 必填，输出字段类型。支持 text、select、multiSelect、number、currency、image、video。 |
| `fields[].aiConfig.prompt` | yes | array; items=object | prompt | 必填，用户编辑的 prompt 片段列表。每项 type=text 时传 value；type=fieldRef 时传 fieldId。 |
| `fields[].aiConfig.prompt[].fieldId` |  | string | fieldId | 当 type=fieldRef 时必填，被引用字段的 fieldId。 |
| `fields[].aiConfig.prompt[].type` | yes | string | type | Prompt 片段类型。支持 text、fieldRef。 |
| `fields[].aiConfig.prompt[].value` |  | string | value | 当 type=text 时必填，文本片段内容。 |
| `fields[].aiConfig.videoConfig` |  | object | videoConfig | 可选，视频生成配置。仅 outputType=video 时有效。 |
| `fields[].aiConfig.videoConfig.aspectRatio` | yes | string | aspectRatio | 视频宽高比。支持 832*480、480*832、624*624、1280*720、720*1280、960*960、1088*832、832*1088、1920*1080、1080*1920、1440*1440、1632*1248、1248*1632。 |
| `fields[].aiConfig.videoConfig.duration` |  | number | duration | 可选，视频时长。支持 5 或 10。 |
| `fields[].aiConfig.videoConfig.resolution` | yes | string | resolution | 视频分辨率。支持 480p、720p、1080p。 |
| `fields[].config` |  | object | config | "各 type 的 config 结构（无 config 的类型省略此字段）：\n\nformatter（number）:\n  INT\|FLOAT_1\|FLOAT_2\|FLOAT_3\|FLOAT_4\|THOUSAND\|THOUSAND_FLOAT\|\n  PERCENT\|PERCENT_FLOAT\n\nformatter（currency）: 可省略（默认 FLOAT_2）；若需指定小数位可用 INT\|FLOAT_1\|FLOAT_2\|FLOAT_3\|FLOAT_4\n\nformatter（date）:\n  YYYY-MM-DD \| YYYY-MM-DD HH:mm \| YYYY-MM-DD HH:mm:ss \|\n  YYYY/MM/DD \| YYYY/MM/DD HH:mm\n\nformatter（progress）: 固定填 "PERCENT"\n\ncurrencyType（currency）:\n  CNY \| HKD \| USD \| EUR \| GBP \| MOP \| VND \| JPY \| KRW \| AED \|\n  AUD \| BRL \| CAD \| CHF \| INR \| IDR \| MXN \| MYR \| PHP \| PLN \|\n  RUB \| SGD \| THB \| TRY \| TWD\n\noptions（singleSelect/multipleSelect）:\n  [{"name":"选项名"}, ...]  — id 由系统生成，创建时只需传 name\n  更新字段配置时，已有选项建议回传原 id；新增选项无需传 id\n  若更新请求携带的 id 在当前字段配置中不存在，系统会忽略该 id，并将该选项视为新增项\n\nmultiple（user/department/group/unidirectionalLink/bidirectionalLink）:\n  true（多选，默认）\| false（单选）\n\nprogress: {"formatter":"PERCENT"}  — 使用系统默认范围\nprogress（自定义范围）: {"formatter":"PERCENT","min":0,"max":1,"customizeRange":true}  — customizeRange 必须为 true\n\nrating: {"min":1,"max":5,"icon":"star"}  — max 范围 1~10\n\nformula（formula）: {"formula":"[单价] * [数量]"}  — 使用 AI 表格公式字符串格式，方括号内填写表内字段名\n\nfilterUp（filterUp）: {"targetSheet":"<目标表ID>","filters":[{"fieldId":"<目标表字段ID>","operator":"equal\|not_equal\|contain\|not_contain...","value":"常量值","currentSheetFieldId":"<当前表字段ID>","link":"AND\|OR"}],"valuesField":"<目标表字段ID>","aggregator":"SUM\|AVERAGE\|COUNT\|MAX\|MIN\|CONCATENATE"}  — 创建新表时 filters 只能使用 value（字段对常量）；在已有表中添加字段时可使用 currentSheetFieldId（字段对字段）；filters 的 link 必须统一（全部 AND 或全部 OR）\n\nlookup（lookup）: {"associateField":"<关联字段ID>","valuesField":"<目标字段ID>","aggregator":"SUM\|AVERAGE\|COUNT\|MAX\|MIN\|CONCATENATE"}  — associateField 为双向/单向链接字段的 fieldId；aggregator 为汇总方式\n\nunidirectionalLink: {"linkedTableId":"<tableId>","multiple":true}\n\nbidirectionalLink: {"linkedTableId":"<tableId>","multiple":true}\n  — 反向关联端由系统自动创建，MCP 对外协议无需额外参数" |
| `fields[].config.options` |  | array; items=object | options | 选项列表。只有当字段类型为 singleSelect 或 multipleSelect 时 才需要提供 |
| `fields[].config.options[].name` | yes | string | name | 选项名称 |
| `fields[].fieldName` | yes | string | fieldName | 字段名称，最大 100 字，不支持换行 |
| `fields[].type` | yes | string | type | "字段类型。AI 字段不新增独立 type，仍使用其结果落库的基础字段类型，并在 aiConfig 中声明 AI 配置。括号内为 config 键，* 表示必填，无括号表示无需 config。\n\n- text: 文本\n- number: 数字 (formatter)\n- singleSelect: 单选 (options*)\n- multipleSelect: 多选 (options*)\n- date: 日期 (formatter)\n- currency: 货币 (currencyType, formatter)\n- user: 人员 (multiple)\n- department: 部门 (multiple)\n- group: 群组 (multiple)\n- progress: 进度 (formatter, customizeRange, min, max)\n- rating: 评分 (min, max, icon)\n- checkbox: 勾选\n- attachment: 附件\n- url: 链接\n- richText: 富文本\n- telephone: 电话\n- email: 邮件\n- idCard: 身份证\n- barcode: 条码\n- geolocation: 地理位置\n- address: 行政区域\n- primaryDoc: 文档 (仅限第一列)\n- formula: 公式\n- filterUp: 查找引用 (targetSheet*, filters*, valuesField*, aggregator*)，只读字段，不能通过 create_records/update_records 写入值；创建新表时 filters 只能使用 value（字段对常量），在已有表中添加字段时可使用 currentSheetFieldId（字段对字段）；filters 的 link 必须统一（全部 AND 或全部 OR）\n- lookup: 关联引用 (associateField*, valuesField*, aggregator*)，只读字段，不能通过 create_records/update_records 写入值\n- unidirectionalLink: 单向关联 (linkedTableId*, multiple)\n- bidirectionalLink: 双向关联 (linkedTableId*, multiple)\n- creator: 创建人 (系统字段)\n- lastModifier: 最后编辑人 (系统字段)\n- createdTime: 创建时间 (系统字段)\n- lastModifiedTime: 最后编辑时间 (系统字段)\n\n创建 AI 字段时，字段 type 须与 aiConfig.outputType 对应：outputType=text → type="text"；outputType=select → type="singleSelect"；outputType=multiSelect → type="multipleSelect"；outputType=number → type="number"；outputType=currency → type="currency"；outputType=image 或 video → type="attachment"。" |
| `tableId` | yes | string | tableId | Table ID（通过 get_base 获取） |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |
| `fields` | `--fields` | json_parse |  |  |  |
| `tableId` | `--table-id` |  |  |  |  |

## dws aitable create_guide_document

- Canonical path: `aitable.create_guide_document`
- Product: `aitable`
- Group: `-`
- Subcommand: `create_guide_document`
- Title: create_guide_document
- Description: 在指定 Base 中创建一个说明文档。说明文档是 Base 导航栏中的文档节点，用于记录 Base 的使用说明、数据字典等信息。 每个 Base 最多支持 5 个说明文档。需要管理员权限。 返回新创建的说明文档 ID 和名称。后续可通过钉钉文档 MCP 对该说明文档进行内容读写操作，返回的 documentId 即为钉钉文档 MCP 所需的 nodeId。
- Required top-level parameters: `baseId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | Base ID，可通过 list_bases 或 search_bases 获取 |
| `name` |  | string | name | 可选，说明文档名称；不传时系统自动生成默认名称 |

### CLI flag overlay

- none

## dws aitable record create

- Canonical path: `aitable.create_records`
- Product: `aitable`
- Group: `record`
- Subcommand: `create`
- Title: 新增记录
- Description: 在指定表格中批量新增记录
- Required top-level parameters: `baseId`, `tableId`, `records`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | Base ID，可通过 list_bases 或 search_bases 获取 |
| `records` | yes | array; items=object | records | 待创建的记录列表，单次最多 100 条 |
| `records[].cells` | yes | object | cells | 字段值映射，key 为 fieldId（通常先通过 get_tables 获取字段目录；若需某些字段完整配置再调用 get_fields），value 为写入值。 各类型写入格式：  text → "文本内容" number → 123 或 123.45（也接受字符串 "123"）；读出通常为字符串 singleSelect → "选项名称"，或 {"id":"opt_xxx"} / {"id":"opt_xxx","name":"进行中"}；对象写入时 id 为准，服务端会校验该 id 是否存在，并转换为当前 option name 后再写入；若直接传 option id 字符串会返回显式错误；若传入的新名称不在当前字段配置中，系统会先自动把该选项补进字段 options，再写入记录；读出为 {"id":"...","name":"..."} multipleSelect → ["选项名1","选项名2"]，或 [{"id":"opt_a","name":"标签A"},{"id":"opt_b","name":"标签B"}]；对象写入时每项都必须带 id，服务端会逐项校验并转换为当前 option name；若其中某些名称不在当前字段配置中，系统会先自动补齐缺失选项，再写入记录；若直接传 option id 字符串数组会返回显式错误；读出为 [{"id":"...","name":"..."}, ...] date → 日期字符串（如 "2026-03-15"、"2026-03-15 09:00"）或含时区的 RFC3339 字符串（如 "2026-03-15T09:00+08:00"）；亦支持毫秒时间戳（服务端自动转换为 UTC 日期字符串）；读出为 RFC3339 字符串 user → 通常为 [{"userId":"staff_001","corpId":"dingxxxxxxxx"}]，仅当目标用户不在当前请求组织、无法反查 userId 时回退为 [{"userRef":"ur_0AaZ19"}] department → [{"deptId":"52528700"}] group → [{"cid":"74577067501"}]；注意 key 是 cid，不是 openConversationId url → {"text":"显示文字","link":"https://..."}；也兼容直接传 "https://..."，服务端会按 {"text":"原字符串","link":"原字符串"} 自动补齐 richText → {"markdown":"**加粗**\n普通文字\n"}（读出有损，颜色/@人等信息丢失） checkbox → true \| false attachment → 可传 [{"fileToken":"ft_xxx"}]、[{"url":"https://..."}]                或 [{"filename":"a.xlsx","size":92250,"type":"xls"\|"image","resourceId":"<id>","resourceUrl":"<resourceUrl>"}]                （推荐先通过 prepare_attachment_upload 申请 uploadUrl 并完成上传，再把返回的 fileToken 写入 attachment 字段；也支持直接传 {"url":"https://..."} 让服务端代拉外链并转成内部附件。URL 转存是 best-effort 异步链路，create_records 返回成功仅表示已受理转存与写入。读出 url 常为有时效下载链接） idCard → "520402196001067498"（必须是后端认可的合法身份证号） geolocation → {"address":"浙江省杭州市思凯路与爱橙街交叉口东南200米","name":"阿里中心·未科D1幢","location":["120.007852","30.271194"]}（对象格式；location 按 [经度, 纬度] 传字符串数组；读写均使用该结构） unidirectionalLink/bidirectionalLink → {"linkedRecordIds":["recXXX","recYYY"]} creator / lastModifier → 系统自动回填，不建议手动写入 createdTime / lastModifiedTime → 系统自动回填，不建议手动写入  示例： {   "fldTextId": "这是一段文本",   "fldNumId": 123.45,   "fldSelectId": {"id":"opt_status_doing","name":"进行中"},   "fldMultiId": [{"id":"opt_tag_a","name":"标签A"},{"id":"opt_tag_b","name":"标签B"}],   "fldDateId": "2026-03-15",   "fldUserId": [{"userId":"staff_001","corpId":"dingxxxxxxxx"}],   "fldUrlId": {"text":"钉钉官网","link":"https://dingtalk.com"},   "fldRichTextId": {"markdown":"**加粗**\n普通文字\n"},   "fldGroupId": [{"cid":"74577067501"}],   "fldLinkId": {"linkedRecordIds":["recA","recB"]} } |
| `tableId` | yes | string | tableId | Table ID，可通过 get_base 获取 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |
| `records` | `--records` | json_parse |  |  |  |
| `tableId` | `--table-id` |  |  |  |  |

## dws aitable table create

- Canonical path: `aitable.create_table`
- Product: `aitable`
- Group: `table`
- Subcommand: `create`
- Title: 创建数据表
- Description: 在指定 Base 中新建表格，并可在创建时附带初始化一批基础字段。 建表时单次最多附带 15 个字段；若 fields 为空，服务会自动补一个名为“标题”的 primaryDoc 首列。 若 tableName 与当前 Base 下已有表重名，服务会自动续号为“原名 1 / 原名 2 ...”，并在 summary 中返回当前表名。 如需添加更多字段，或在已有表中增加字段，请使用 create_fields。
- Required top-level parameters: `baseId`, `tableName`, `fields`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | 目标 Base ID（通过 list_bases 获取） |
| `fields` | yes | array; items=object | fields | 建表时随附创建的初始字段列表，至少包含 1 个字段，单次最多 15 个。若传空数组，系统会自动补一个名为“标题”的 primaryDoc 首列。 建议在此处定义结构清晰的基础字段（如文本、数字、日期、单选等）； 复杂字段（关联、流转等）建议建表完成后通过 create_fields 单独添加。  每个字段对象包含：   fieldName（必填）: 字段名称   type（必填）: 字段类型，可选值与 config 结构详见本工具说明末尾的字段参考   config（可选）: 字段配置，结构因 type 而异，详见字段参考  示例： [   {"fieldName":"任务名称","type":"text"},   {"fieldName":"优先级","type":"singleSelect","config":{"options":[{"name":"高"},{"name":"中"},{"name":"低"}]}},   {"fieldName":"截止日期","type":"date","config":{"formatter":"YYYY-MM-DD"}},   {"fieldName":"负责人","type":"user","config":{"multiple":false}} ] |
| `fields[].config` |  | object | config | 各 type 的 config 结构（无 config 的类型省略此字段）：  formatter（number）:   INT\|FLOAT_1\|FLOAT_2\|FLOAT_3\|FLOAT_4\|THOUSAND\|THOUSAND_FLOAT\|   PERCENT\|PERCENT_FLOAT  formatter（currency）: 可省略（默认 FLOAT_2）；若需指定小数位可用 INT\|FLOAT_1\|FLOAT_2\|FLOAT_3\|FLOAT_4  formatter（date）:   YYYY-MM-DD \| YYYY-MM-DD HH:mm \| YYYY-MM-DD HH:mm:ss \|   YYYY/MM/DD \| YYYY/MM/DD HH:mm  formatter（progress）: 固定填 "PERCENT"  currencyType（currency）:   CNY \| HKD \| USD \| EUR \| GBP \| MOP \| VND \| JPY \| KRW \| AED \|   AUD \| BRL \| CAD \| CHF \| INR \| IDR \| MXN \| MYR \| PHP \| PLN \|   RUB \| SGD \| THB \| TRY \| TWD  options（singleSelect/multipleSelect）:   [{"name":"选项名"}, ...]  — id 由系统生成，创建时只需传 name   更新字段配置时，已有选项建议回传原 id；新增选项无需传 id   若更新请求携带的 id 在当前字段配置中不存在，系统会忽略该 id，并将该选项视为新增项  multiple（user/department/group/unidirectionalLink/bidirectionalLink）:   true（多选，默认）\| false（单选）  progress: {"formatter":"PERCENT"}  — 使用系统默认范围 progress（自定义范围）: {"formatter":"PERCENT","min":0,"max":1,"customizeRange":true}  — customizeRange 必须为 true  rating: {"min":1,"max":5,"icon":"star"}  — max 范围 1~10  formula（formula）: {"formula":"[单价] * [数量]"}  — 使用 AI 表格公式字符串格式，方括号内填写表内字段名  unidirectionalLink: {"linkedTableId":"<tableId>","multiple":true}  bidirectionalLink: {"linkedTableId":"<tableId>","multiple":true}   — 反向关联端由系统自动创建，MCP 对外协议无需额外参数 |
| `fields[].fieldName` | yes | string | fieldName | 字段名称，最大 100 字，不支持换行 |
| `fields[].type` | yes | string | type | 字段类型。括号内为 config 键，* 表示必填，无括号表示无需 config。  - text: 文本 - number: 数字 (formatter) - singleSelect: 单选 (options*) - multipleSelect: 多选 (options*) - date: 日期 (formatter) - currency: 货币 (currencyType, formatter) - user: 人员 (multiple) - department: 部门 (multiple) - group: 群组 (multiple) - progress: 进度 (formatter, customizeRange, min, max) - rating: 评分 (min, max, icon) - checkbox: 勾选 - attachment: 附件 - url: 链接 - richText: 富文本 - telephone: 电话 - email: 邮件 - idCard: 身份证 - barcode: 条码 - geolocation: 地理位置 - primaryDoc: 文档 (仅限第一列) - formula: 公式 - unidirectionalLink: 单向关联 (linkedTableId*, multiple) - bidirectionalLink: 双向关联 (linkedTableId*, multiple) - creator: 创建人 (系统字段) - lastModifier: 最后编辑人 (系统字段) - createdTime: 创建时间 (系统字段) - lastModifiedTime: 最后编辑时间 (系统字段) |
| `tableName` | yes | string | tableName | 表格名称，1~100 个字符；不能包含 / \ ? * [ ] : 等字符。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |
| `fields` | `--fields` | json_parse |  |  |  |
| `tableName` | `--name` |  |  |  |  |

## dws aitable view create

- Canonical path: `aitable.create_view`
- Product: `aitable`
- Group: `view`
- Subcommand: `create`
- Title: 创建视图
- Description: 在指定数据表（Table）下创建一个新视图（View）。 当前稳定支持的 viewType：Grid、FormDesigner、Gantt、Calendar、Kanban、Gallery。 若未传 viewName，则会按视图类型自动生成不重名名称。 首列字段是每条数据的索引，不支持删除、移动或隐藏。
- Required top-level parameters: `baseId`, `tableId`, `viewType`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | 所属 Base 的唯一标识。 |
| `config` |  | object | config | 可选，创建视图时附带的配置。 |
| `config.filter` |  | array; items=object | filter | 可选，视图筛选规则列表。 |
| `config.group` |  | array; items=object | group | 可选，视图分组规则列表。 |
| `config.sort` |  | array; items=object | sort | 可选，视图排序规则列表。 |
| `config.visibleFieldIds` |  | array; items=string | visibleFieldIds | 可选，创建后视图的可见字段列，以及顺序（fieldId 列表）。首列字段是每条数据的索引，必须保留在数组第一个位置，不能删除、移动或隐藏。 |
| `config.visibleFieldIds[]` |  | string |  |  |
| `tableId` | yes | string | tableId | 所属数据表（Table）的唯一标识。 |
| `viewDescription` |  | object | viewDescription | 可选，视图描述，结构与前端 ViewDTO.description 保持一致。 |
| `viewName` |  | string | viewName | 可选，新视图名称；未传时自动生成。 |
| `viewSubType` |  | string | viewSubType | 可选，视图子类型。 |
| `viewType` | yes | string | viewType | 新视图类型。当前支持：Grid、FormDesigner、Gantt、Calendar、Kanban、Gallery。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |
| `config` | `--config` | json_parse |  |  |  |
| `tableId` | `--table-id` |  |  |  |  |
| `viewName` | `--name` |  |  |  |  |
| `viewType` | `--view-type` |  |  |  |  |

## dws aitable base delete

- Canonical path: `aitable.delete_base`
- Product: `aitable`
- Group: `base`
- Subcommand: `delete`
- Title: 删除 AI 表格
- Description: 删除指定 Base（高风险、不可逆）。成功后应无法通过 get_base/search_bases 读取到该 Base
- Required top-level parameters: `baseId`
- Sensitive flag from schema: `true`
- Mutation risk: `sensitive-mutating`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | 待删除 Base ID。建议先通过 get_base 确认目标 |
| `reason` |  | string | reason | 一句话描述删除的原因 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |
| `reason` | `--reason` |  |  |  |  |

## dws aitable chart delete

- Canonical path: `aitable.delete_chart`
- Product: `aitable`
- Group: `chart`
- Subcommand: `delete`
- Title: 删除图表
- Description: 删除指定 chart，并同步删除其在 dashboard 中对应的布局项。删除操作不可逆。
- Required top-level parameters: `baseId`, `dashboardId`, `chartId`
- Sensitive flag from schema: `true`
- Mutation risk: `sensitive-mutating`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | 所属 Base 的唯一标识，可通过 list_bases / search_bases 获取 |
| `chartId` | yes | string | chartId | 目标 chart 的唯一标识，可通过 get_dashboard 获取 |
| `dashboardId` | yes | string | dashboardId | 所属 dashboard 的唯一标识，可通过 get_base 获取 |
| `reason` |  | string | reason | 可先参数，删除原因 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |
| `chartId` | `--chart-id` |  |  |  |  |
| `dashboardId` | `--dashboard-id` |  |  |  |  |
| `reason` | `--reason` |  |  |  |  |

## dws aitable dashboard delete

- Canonical path: `aitable.delete_dashboard`
- Product: `aitable`
- Group: `dashboard`
- Subcommand: `delete`
- Title: 删除仪表盘
- Description: 删除指定 dashboard，会级联删除该 dashboard 下的所有 chart；删除操作不可逆。
- Required top-level parameters: `baseId`, `dashboardId`
- Sensitive flag from schema: `true`
- Mutation risk: `sensitive-mutating`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | 所属 Base 的唯一标识，可通过 list_bases / search_bases 获取 |
| `dashboardId` | yes | string | dashboardId | 目标 dashboard 的唯一标识，可通过 get_base 获取 |
| `reason` |  | string | reason | 可选，删除原因 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |
| `dashboardId` | `--dashboard-id` |  |  |  |  |
| `reason` | `--reason` |  |  |  |  |

## dws aitable field delete

- Canonical path: `aitable.delete_field`
- Product: `aitable`
- Group: `field`
- Subcommand: `delete`
- Title: 删除字段
- Description: 删除指定 Table 中的一个字段（Field），删除操作不可逆。禁止删除主字段，且禁止删除最后一个字段  此操作不可逆，会永久删除字段及其所有数据。 必须提供准确的 baseId、tableId 和 fieldId，不得使用名称代替 ID。 若字段不存在或无权限，将返回错误。
- Required top-level parameters: `baseId`, `tableId`, `fieldId`
- Sensitive flag from schema: `true`
- Mutation risk: `sensitive-mutating`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | Base ID（通过 list_bases 获取） |
| `fieldId` | yes | string | fieldId | 待删除字段 ID（通过 get_tables 获取） |
| `tableId` | yes | string | tableId | Table ID（通过 get_base 获取） |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |
| `fieldId` | `--field-id` |  |  |  |  |
| `tableId` | `--table-id` |  |  |  |  |

## dws aitable delete_guide_document

- Canonical path: `aitable.delete_guide_document`
- Product: `aitable`
- Group: `-`
- Subcommand: `delete_guide_document`
- Title: delete_guide_document
- Description: 删除指定 Base 中的说明文档（不可逆，文档内容将永久丢失）。需要管理员权限。 调用前请先通过 get_base 确认目标说明文档 ID 与名称，避免误删。
- Required top-level parameters: `baseId`, `documentId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | Base ID，可通过 list_bases 或 search_bases 获取 |
| `documentId` | yes | string | documentId | 说明文档 ID，可通过 get_base 返回的 documents 列表获取 |
| `reason` |  | string | reason | 可选，一句话描述删除原因，用于审计 |

### CLI flag overlay

- none

## dws aitable record delete

- Canonical path: `aitable.delete_records`
- Product: `aitable`
- Group: `record`
- Subcommand: `delete`
- Title: 删除行记录
- Description: 在指定 Table 中批量删除记录（不可逆，数据将永久丢失）。 单次最多删除 100 条；超出请拆分多次调用。 调用前建议先通过 query_records 确认目标记录 ID 与内容，避免误删。
- Required top-level parameters: `baseId`, `tableId`, `recordIds`
- Sensitive flag from schema: `true`
- Mutation risk: `sensitive-mutating`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | Base ID，可通过 list_bases 或 search_bases 获取 |
| `recordIds` | yes | array; items=string | recordIds | 待删除的记录 ID 列表，最多 100 条 |
| `recordIds[]` |  | string |  |  |
| `tableId` | yes | string | tableId | Table ID，可通过 get_base 获取 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |
| `recordIds` | `--record-ids` | csv_to_array |  |  |  |
| `tableId` | `--table-id` |  |  |  |  |

## dws aitable table delete

- Canonical path: `aitable.delete_table`
- Product: `aitable`
- Group: `table`
- Subcommand: `delete`
- Title: 删除数据表
- Description: 删除指定 tableId 的数据表（不可逆，数据将永久丢失），该操作为高风险写入。 调用前请先通过 get_base / get_tables 确认目标表 ID 与名称。
- Required top-level parameters: `baseId`, `tableId`
- Sensitive flag from schema: `true`
- Mutation risk: `sensitive-mutating`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | 目标 Base ID（通过 list_bases 获取） |
| `reason` |  | string | reason | 一句话描述一下删除该数据表的原因，用于审计 |
| `tableId` | yes | string | tableId | 将被删除的 Table ID（通过 get_base / get_tables 获取） |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |
| `reason` | `--reason` |  |  |  |  |
| `tableId` | `--table-id` |  |  |  |  |

## dws aitable view delete

- Canonical path: `aitable.delete_view`
- Product: `aitable`
- Group: `view`
- Subcommand: `delete`
- Title: 删除视图
- Description: 删除指定视图（View）。该操作不可逆。 已知保护：禁止删除数据表中的最后一个视图；锁定视图不允许删除。
- Required top-level parameters: `baseId`, `tableId`, `viewId`
- Sensitive flag from schema: `true`
- Mutation risk: `sensitive-mutating`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | 所属 Base 的唯一标识。 |
| `tableId` | yes | string | tableId | 所属数据表（Table）的唯一标识。 |
| `viewId` | yes | string | viewId | 要删除的视图（View）的唯一标识。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |
| `tableId` | `--table-id` |  |  |  |  |
| `viewId` | `--view-id` |  |  |  |  |

## dws aitable export data

- Canonical path: `aitable.export_data`
- Product: `aitable`
- Group: `export`
- Subcommand: `data`
- Title: 导出数据
- Description: 导出 AI 表格数据的统一入口。 不传 taskId 时，会根据 scope / format 创建一个新的导出任务，并在 timeoutMs 时间内同步等待结果；若在等待窗口内完成，则直接返回 downloadUrl 和 fileName。 传入 taskId 时，不会重新创建任务，而是继续等待该任务；若仍未完成，则继续返回同一个 taskId，供下一次调用继续等待。 当前稳定支持的 scope：all、table、view；暂不开放按字段导出。 当前稳定支持的 format：excel、attachment、excel_and_attachment、excel_with_inline_images。
- Required top-level parameters: `baseId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-with-local-output`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | Base ID，可通过 list_bases 或 search_bases 获取 |
| `format` |  | string | format | 可选，导出格式。创建新任务时必填。 支持值：excel、attachment、excel_and_attachment、excel_with_inline_images。 |
| `scope` |  | string | scope | 可选，导出范围。创建新任务时必填。 支持值：all（整个 Base）、table（指定数据表）、view（指定视图）。 scope=table 时必须传 tableId；scope=view 时必须传 tableId 和 viewId。 |
| `tableId` |  | string | tableId | 可选，Table ID。scope=table 或 scope=view 时必填；可通过 get_base 获取。 |
| `taskId` |  | string | taskId | 可选，已有导出任务 ID。传入后表示继续等待该任务；此时不要再传 scope、format、tableId、viewId。 |
| `timeoutMs` |  | number | timeoutMs | 可选，单次等待超时时间（毫秒）。默认 30000，最小 200，最大 30000。超时后会返回 taskId，供下一次继续等待。 |
| `viewId` |  | string | viewId | 可选，View ID。scope=view 时必填；可通过 list_views 或 get_views 获取。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |
| `format` | `--format` |  |  |  |  |
| `scope` | `--scope` |  |  |  |  |
| `tableId` | `--table-id` |  |  |  |  |
| `taskId` | `--task-id` |  |  |  |  |
| `timeoutMs` | `--timeout-ms` |  |  |  |  |
| `viewId` | `--view-id` |  |  |  |  |

## dws aitable base get

- Canonical path: `aitable.get_base`
- Product: `aitable`
- Group: `base`
- Subcommand: `get`
- Title: 获取 AI 表格信息
- Description: 获取指定 Base 的资源目录级信息，返回 baseName、tables、dashboards 的 summary 信息（不含字段与记录详情）。 这是当前 Base 级目录入口：后续如需 tableId 或 dashboardId，优先从这里读取；table 详情再调用 get_tables，dashboard 详情再调用 get_dashboard
- Required top-level parameters: `baseId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | Base 唯一标识。优先使用 search_bases/list_bases 返回值 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |

## dws aitable get_base_primary_doc_id

- Canonical path: `aitable.get_base_primary_doc_id`
- Product: `aitable`
- Group: `-`
- Subcommand: `get_base_primary_doc_id`
- Title: 获取AI表格中主键即文档字段的文档ID
- Description: 根据 baseId、tableId 和 recordId 获取主键文档对应的文档信息，会返回对应的文档的dentryuuid，然后利用该uuid去获取文档的内容以及做其他操作
- Required top-level parameters: `recordId`, `tableId`, `baseId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | Base ID，可通过 list_bases 或 search_bases 获取 |
| `recordId` | yes | string | recordId | 记录Id |
| `tableId` | yes | string | tableId | Table ID，可通过 list_tables 或 get_base 获取 |

### CLI flag overlay

- none

## dws aitable chart get

- Canonical path: `aitable.get_chart`
- Product: `aitable`
- Group: `chart`
- Subcommand: `get`
- Title: 获取图表信息
- Description: 获取指定 chart 的详细信息。返回所属 dashboardId、chartName、chartType、widget.config 以及布局项。返回的 config 中 sheet 为该图表引用的数据表 tableId，view 为视图 viewId。
- Required top-level parameters: `baseId`, `dashboardId`, `chartId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | 所属 Base 的唯一标识，可通过 list_bases / search_bases 获取 |
| `chartId` | yes | string | chartId | 目标 chart 的唯一标识，可通过 get_dashboard 获取 |
| `dashboardId` | yes | string | dashboardId | 所属 dashboard 的唯一标识，可通过 get_base 获取 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |
| `chartId` | `--chart-id` |  |  |  |  |
| `dashboardId` | `--dashboard-id` |  |  |  |  |

## dws aitable chart.share get

- Canonical path: `aitable.get_chart_share`
- Product: `aitable`
- Group: `chart.share`
- Subcommand: `get`
- Title: 获取图表的分享配置
- Description: 查询指定 chart 的当前分享配置。返回分享是否已开启（enabled）、分享类型（shareType：PUBLIC / ORG）、分享链接（shareUrl）等信息。若分享尚未开启，enabled 为 false，shareUrl 为 null。
- Required top-level parameters: `baseId`, `dashboardId`, `chartId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | 必填，所属 Base 的唯一标识，可通过 list_bases / search_bases 获取 |
| `chartId` | yes | string | chartId | 必填，目标 chart 的唯一标识，可通过 get_dashboard 获取 |
| `dashboardId` | yes | string | dashboardId | 必填，所属 dashboard 的唯一标识，可通过 get_base 获取 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |
| `chartId` | `--chart-id` |  |  |  |  |
| `dashboardId` | `--dashboard-id` |  |  |  |  |

## dws aitable dashboard get

- Canonical path: `aitable.get_dashboard`
- Product: `aitable`
- Group: `dashboard`
- Subcommand: `get`
- Title: 获取仪表盘信息
- Description: 获取指定 dashboard 的详细信息。\r\n返回 dashboardName、filters、layout，以及该 dashboard 下的 charts summary（chartId、chartName、chartType）。
- Required top-level parameters: `baseId`, `dashboardId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | 字段baseId | 所属 Base 的唯一标识，可通过 list_bases / search_bases / get_base 获取 |
| `dashboardId` | yes | string | dashboardId | 目标 dashboard 的唯一标识，可通过 get_base 获取 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |
| `dashboardId` | `--dashboard-id` |  |  |  |  |

## dws aitable dashboard config-example

- Canonical path: `aitable.get_dashboard_config_example`
- Product: `aitable`
- Group: `dashboard`
- Subcommand: `config-example`
- Title: 获取仪表盘配置示例
- Description: 返回 dashboard config 的完整结构示例（JSONC 格式，含注释说明每个字段的含义和约束，请直接阅读理解）。可作为 create_dashboard / update_dashboard 的 config 参数结构参考。
- Required top-level parameters: -
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

- none

### CLI flag overlay

- none

## dws aitable dashboard.share get

- Canonical path: `aitable.get_dashboard_share`
- Product: `aitable`
- Group: `dashboard.share`
- Subcommand: `get`
- Title: 查询仪表盘分享配置
- Description: 查询指定 dashboard 的当前分享配置。返回分享是否已开启（enabled）、分享类型（shareType：PUBLIC / ORG）、分享链接（shareUrl）等信息。若分享尚未开启，enabled 为 false，shareUrl 为 null。
- Required top-level parameters: `baseId`, `dashboardId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | 所属 Base 的唯一标识，可通过 list_bases / search_bases / get_base 获取 |
| `dashboardId` | yes | string | dashboardId | 目标 dashboard 的唯一标识，可通过 get_base 获取 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |
| `dashboardId` | `--dashboard-id` |  |  |  |  |

## dws aitable chart widgets-example

- Canonical path: `aitable.get_dashboard_widgets_example`
- Product: `aitable`
- Group: `chart`
- Subcommand: `widgets-example`
- Title: 获取图表配置示例
- Description: 返回所有图表类型的 widget config 示例（JSONC 格式，含注释说明每个字段的含义和约束，请直接阅读理解）。可作为 create_chart / update_chart 的 config 参数结构参考，根据目标图表类型选取对应示例。
- Required top-level parameters: -
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

- none

### CLI flag overlay

- none

## dws aitable field get

- Canonical path: `aitable.get_fields`
- Product: `aitable`
- Group: `field`
- Subcommand: `get`
- Title: 获取字段详情
- Description: 批量获取指定字段的详细信息，包括 fieldId、名称、类型、description 以及类型相关完整配置（如格式化、选项、AI 配置等）。 传 fieldIds 时单次最多获取 10 个字段；若需更多字段，请拆分多次调用。 适用于在 get_tables 拿到字段目录后，按需展开少量字段的完整配置，避免大 options 字段放大 get_tables 返回值。 AI 字段的返回结果中，config 仅包含字段物理配置，aiConfig 作为同级字段单独返回，结构与 create_fields 写入参数一致。
- Required top-level parameters: `baseId`, `tableId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | Base ID（可通过 list_bases 获取） |
| `fieldIds` |  | array; items=string | fieldIds | 待获取详情的字段 ID 列表，可通过 get_tables 获取；建议只传真正需要展开完整配置的字段，单次最多 10 个；不传则默认返回当前表下全部字段。建议优先显式传入，以控制返回体大小，避免上下文突增 |
| `fieldIds[]` |  | string |  |  |
| `tableId` | yes | string | tableId | Table ID（可通过 get_base 获取） |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |
| `fieldIds` | `--field-ids` | csv_to_array |  |  |  |
| `tableId` | `--table-id` |  |  |  |  |

## dws aitable table get

- Canonical path: `aitable.get_tables`
- Product: `aitable`
- Group: `table`
- Subcommand: `get`
- Title: 获取数据表
- Description: 批量获取指定 Tables（数据表）的表级信息、字段目录与视图目录。 会返回 tables 列表；每个 table 直接包含 tableId、tableName、description、fields、views；字段列表仅包含 fieldId、fieldName、type、description；views 仅包含 viewId、viewName、type。 若需读取字段的完整配置，请再调用 get_fields。
- Required top-level parameters: `baseId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | 所属 Base ID（通过 list_bases / search_bases 获取） |
| `tableIds` |  | array; items=string | tableIds | 待获取详情的 Table ID 列表（通过 get_base 获取），单次最多 10 个；不传则默认返回当前 Base 下全部表。建议优先显式传入，以控制返回体大小，避免上下文突增 |
| `tableIds[]` |  | string |  |  |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |
| `tableIds` | `--table-ids` | csv_to_array |  |  |  |

## dws aitable view get

- Canonical path: `aitable.get_views`
- Product: `aitable`
- Group: `view`
- Subcommand: `get`
- Title: 获取视图详情
- Description: 获取指定数据表（Table）中的视图（View）完整信息，包括列顺序、筛选、排序、分组、条件格式、自定义配置等。 支持两种模式： - 显式选择：传入 viewIds，按入参顺序返回这些视图；单次最多 10 个。 - 默认全量：省略 viewIds，返回当前表下全部视图，顺序与当前表视图目录一致。
- Required top-level parameters: `baseId`, `tableId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | 所属 Base 的唯一标识。 |
| `tableId` | yes | string | tableId | 所属数据表（Table）的唯一标识。 |
| `viewIds` |  | array; items=string | viewIds | 可选，待获取详情的视图（View）ID 列表。显式传入时单次最多 10 个；省略时默认返回当前表下全部视图。 |
| `viewIds[]` |  | string |  |  |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |
| `tableId` | `--table-id` |  |  |  |  |
| `viewIds` | `--view-ids` | csv_to_array |  |  |  |

## dws aitable import data

- Canonical path: `aitable.import_data`
- Product: `aitable`
- Group: `import`
- Subcommand: `data`
- Title: 导入数据
- Description: 将已通过 prepare_import_upload 上传完成的文件导入 AI 表格，每个 Sheet 会新建为独立的数据表（不支持追加到已有表格）。 工具内部会等待导入完成，大多数情况下一次调用即可拿到最终结果。若在 timeout 内未完成，再次传入相同 importId 继续等待，无需重新提交任务，也不要重新上传同一文件。
- Required top-level parameters: `importId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `fieldMapping` |  | object | fieldMapping | 可选，字段映射关系。key 为目标表的字段名，value 为源文件中的列名。仅追加导入模式生效。不传则按列名自动匹配 |
| `headerRow` |  | number | headerRow | 可选，表头所在行号（从 1 开始，1 表示第一行）。数据将从 headerRow 的下一行开始读取。不传则自动识别表头行 |
| `importId` | yes | string | importId | prepare_import_upload 返回的 importId |
| `srcSheetName` |  | string | srcSheetName | 可选，源文件中的 Sheet 名称。当文件包含多个 Sheet 时，用于指定从哪个 Sheet 导入数据。不传则默认使用第一个 Sheet |
| `tableId` |  | string | tableId | 可选，目标数据表 ID。传入后数据将作为新行追加到该已有表中，而非新建表。不传则默认新建表导入 |
| `timeout` |  | number | timeout | 可选，本次调用的最长等待时间（秒），默认且推荐使用最大值 30。最小 5，最大 30。超时后若任务仍未完成，再次传入相同 importId 继续等待 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `importId` | `--import-id` |  |  |  |  |
| `timeout` | `--timeout` |  |  |  |  |

## dws aitable base list

- Canonical path: `aitable.list_bases`
- Product: `aitable`
- Group: `base`
- Subcommand: `list`
- Title: 获取 AI 表格列表
- Description: 列出当前用户可访问的 AI 表格 Base。默认返回最近访问结果，支持分页游标续取。返回 baseId 与 baseName，后续可直接用于 get_base。 AI 表格访问地址可按 baseId 拼接为：https://docs.dingtalk.com/i/nodes/{baseId}
- Required top-level parameters: -
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `cursor` |  | string | cursor | 首次不传；传入上次返回的游标继续获取下一页 |
| `limit` |  | number | limit | 每页数量，默认 10，最大 10 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `cursor` | `--cursor` |  |  |  |  |
| `limit` | `--limit` |  |  |  |  |

## dws aitable attachment upload

- Canonical path: `aitable.prepare_attachment_upload`
- Product: `aitable`
- Group: `attachment`
- Subcommand: `upload`
- Title: 准备附件上传
- Description: 为单个 attachment 字段文件申请带容量校验的 OSS 直传地址。 该工具仅适用于“需要先上传本地文件，再将其写入 attachment 字段”的场景，不是通用文件上传入口，也不适用于后续导入类任务上传。 如果已经有可直接下载的在线文件 URL，不要先下载文件再调用本工具；可直接在 create_records / update_records 的 attachment 字段中传入 [{"url":"https://..."}]，由服务端自动代拉外链并转存为内部附件。 该工具只负责准备上传，不直接接收文件二进制内容；实际文件字节流应由客户端在 MCP 外上传到返回的 uploadUrl。 上传文件时，向 uploadUrl 发起的 PUT 请求必须携带 Content-Type header，且其值必须是该文件的具体 MIME type。 上传成功后，请在 create_records / update_records 的 attachment 字段中写入 [{"fileToken":"..."}]。
- Required top-level parameters: `baseId`, `fileName`, `size`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | Base ID，可通过 list_bases 或 search_bases 获取 |
| `fileName` | yes | string | fileName | 待写入 attachment 字段的文件名，必须包含扩展名（如 report.xlsx、photo.png）。服务端会基于扩展名和 mimeType 推断资源类型。 |
| `mimeType` |  | string | mimeType | 可选，文件 MIME type（如 application/pdf、image/png）。不传时服务端会根据 fileName 扩展名推断。若传入该值，则上传文件到 uploadUrl 时，PUT 请求必须携带 Content-Type header，且其值必须与这里完全一致。该字段只影响附件资源识别，不会把该工具升级为通用上传接口。 |
| `size` | yes | number | size | 文件大小（字节），必须大于 0。prepare 阶段会用它向下游申请带容量校验的 attachment 上传地址。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |
| `fileName` | `--file-name` |  |  |  |  |
| `mimeType` | `--mime-type` |  |  |  |  |
| `size` | `--size` |  |  |  |  |

## dws aitable import upload

- Canonical path: `aitable.prepare_import_upload`
- Product: `aitable`
- Group: `import`
- Subcommand: `upload`
- Title: 准备导入文件上传
- Description: 为导入任务申请 OSS 直传地址。返回 uploadUrl 和 importId。 客户端应通过 HTTP PUT 将原始文件字节流上传至 uploadUrl；除非 uploadUrl 对应的存储服务明确要求，否则不要额外附带 Content-Type 等自定义请求头。上传完成后将 importId 传入 import_data 即可触发导入，无需再传其他参数。
- Required top-level parameters: `baseId`, `fileName`, `fileSize`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | Base ID，可通过 list_bases 或 search_bases 获取 |
| `fileName` | yes | string | fileName | 文件名，须带扩展名，例如 data.xlsx。扩展名将作为导入格式依据 |
| `fileSize` | yes | number | fileSize | 文件大小（字节数） |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |
| `fileName` | `--file-name` |  |  |  |  |
| `fileSize` | `--file-size` |  |  |  |  |

## dws aitable record query

- Canonical path: `aitable.query_records`
- Product: `aitable`
- Group: `record`
- Subcommand: `query`
- Title: query_records
- Description: 查询指定表格中的记录，支持两种模式： - 按 ID 取：传入 recordIds（单次最多 100 个），直接获取指定记录。 - 条件查：通过 filters 过滤、sort 排序、cursor 分页遍历全表。 两种模式均可通过 fieldIds（单次最多 100 个）限制返回字段以节省 token。 如果需要获取计算、查找引用等字段则必须传fieldIds的列ID或者列名称才会返回
- Required top-level parameters: `baseId`, `tableId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | Base ID（通过 list_bases / search_bases 获取） |
| `cursor` |  | string | cursor | 可选。分页游标，首次查询不传。当返回结果包含 cursor 字段时，将其传入下一次请求以获取后续数据； cursor 为空表示已取完全部记录。 |
| `fieldIds` |  | array; items=string | fieldIds | 可选。指定要返回的字段 ID 列表。省略则返回所有字段（不包括计算字段、查找引用字段、其他引用关联字段）。建议在字段较多时按需传入，可显著减少响应体积；单次最多 100 个，如果当前表里有公式、查找引用、关联引用等计算字段，则必须传具体的字段 ID，否则默认不传获取所有字段的时候不会获取计算相关字段。 |
| `fieldIds[]` |  | string |  |  |
| `filters` |  | object | filters | 结构化过滤条件，不传则返回全部记录（受 limit 限制） |
| `filters.operands` | yes | array; items=object | operands | 过滤条件列表，每项为一个条件对象 |
| `filters.operands[].operands` | yes | array; items=string | operands | 操作数列表：第一个元素为 fieldId，第二个元素为比较值（exist/un_exist 无需第二个元素）。 ⚠️ 注意：singleSelect / multipleSelect 字段的过滤值推荐传 option ID。 option ID 可先通过 get_fields 返回的完整字段配置获取。 |
| `filters.operands[].operands[]` |  | string |  |  |
| `filters.operands[].operator` | yes | string | operator | 比较操作符，详见下方操作符列表  \| 操作符 \| 适用类型 \| 含义 \| \|--------\|----------\|------\| \| `eq` \| 通用 \| 等于 \| \| `ne` \| 通用 \| 不等于 \| \| `exist` \| 通用 \| 有值 \| \| `un_exist` \| 通用 \| 为空 \| \| `lt` \| 数值 \| 小于 \| \| `gt` \| 数值 \| 大于 \| \| `lte` \| 数值 \| 小于等于 \| \| `gte` \| 数值 \| 大于等于 \| \| `contain` \| 文本 \| 包含 \| \| `exclusive` \| 文本 \| 不包含 \| \| `all_of` \| 多选 \| 全包含 \| \| `any_of` \| 多选 \| 包含任一 \| \| `none_of` \| 多选 \| 不包含任一 \| \| `date_eq` \| 日期 \| 日期等于 \| \| `before` \| 日期 \| 早于 \| \| `after` \| 日期 \| 晚于 \| \| `not_before` \| 日期 \| 不早于 \| \| `not_after` \| 日期 \| 不晚于 \| \| `from_now` \| 日期 \| 未来 N 天内（值为天数） \| \| `date_between` \| 日期 \| 区间（值为 [start, end] 时间戳数组） \| |
| `filters.operator` | yes | string | operator | 多条件间的逻辑关系：`and` 或 `or`，默认 `and` |
| `keyword` |  | string | keyword | 全文关键词。将对整表内容做文本匹配搜索，并返回符合条件的记录。 |
| `limit` |  | number | limit | 可选。单次返回的最大记录数，默认 100，最大 100。 |
| `recordIds` |  | array; items=string | recordIds | 可选。指定要获取的记录 ID 列表，单次最多 100 个。传入时直接按 ID 返回，忽略 filters 和 sort。 适用于已知 recordId（如关联字段中的 linkedRecordIds）时的精准取数。 |
| `recordIds[]` |  | string |  |  |
| `sort` |  | array; items=object | sort | 可选。排序条件列表，按数组顺序依次生效。  每个元素：{"fieldId": "<fieldId>", "direction": "asc" \| "desc"}  示例（先按优先级升序，再按截止日期降序）： [   {"fieldId": "fldPriorityId", "direction": "asc"},   {"fieldId": "fldDueDateId",  "direction": "desc"} ] |
| `sort[].direction` |  | string | direction | 排序方向，默认 asc |
| `sort[].fieldId` | yes | string | fieldId | 排序字段 ID |
| `tableId` | yes | string | tableId | Table ID（通过 get_base 获取） |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |
| `cursor` | `--cursor` |  |  |  |  |
| `fieldIds` | `--field-ids` | csv_to_array |  |  |  |
| `filters` | `--filters` | json_parse |  |  |  |
| `keyword` | `--query` |  |  |  |  |
| `limit` | `--limit` |  |  |  |  |
| `recordIds` | `--record-ids` | csv_to_array |  |  |  |
| `sort` | `--sort` | json_parse |  |  |  |
| `tableId` | `--table-id` |  |  |  |  |

## dws aitable run_ai_field

- Canonical path: `aitable.run_ai_field`
- Product: `aitable`
- Group: `-`
- Subcommand: `run_ai_field`
- Title: 运行ai字段
- Description: 触发指定 AI 字段的运行任务。支持同时运行多个 AI 字段（最多 10 个），每个字段独立提交任务。 不传 recordIds 时整列运行（刷新所有记录）；传入 recordIds 时仅运行指定记录（单次最多 500 条）。 该工具仅提交任务即返回，不会等待 AI 字段运行完成。返回结果包含文档链接，用户可打开文档查看运行进度和结果。 部分字段正在运行中（幂等冲突）不影响其他字段的提交，整体仍返回 success。
- Required top-level parameters: `tableId`, `baseId`, `fieldIds`
- Sensitive flag from schema: `false`
- Mutation risk: `unknown-review-before-use`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | 目标 Base ID（通过 list_bases / search_bases 获取） |
| `fieldIds` | yes | array; items=string | fieldIds | 待运行的 AI 字段 ID 列表。每个字段必须是 AI 类型字段。单次最多 10 个 |
| `fieldIds[]` |  | string |  |  |
| `recordIds` |  | array; items=string | recordIds | 指定运行的记录 ID 列表。不传时整列运行（刷新所有记录）；传入时仅运行指定记录。单次最多 500 条 |
| `recordIds[]` |  | string |  |  |
| `tableId` | yes | string | tableId | 包含 AI 字段的 Table ID（通过 get_base / get_tables 获取） |

### CLI flag overlay

- none

## dws aitable run_datasource_sync

- Canonical path: `aitable.run_datasource_sync`
- Product: `aitable`
- Group: `-`
- Subcommand: `run_datasource_sync`
- Title: run_datasource_sync
- Description: 对指定 Base 中的若干数据源表（即由外部数据源接入而来、可在 get_base / get_tables / get_fields 返回结果中通过 sync=true 识别的表）触发一次手动同步。\n单次最多 5 张表，超出请拆分多次调用。每张表独立提交同步任务，单表失败不影响其他表的提交，整体仍返回 success；调用方需要遍历 tasks 数组按 status 判断每张表的真实结果。\n该工具仅触发任务即返回，不会等待同步完成。返回结果包含文档链接，用户可打开文档查看同步进度与最终数据；同步运行中（errorCode=4014）属于幂等冲突，会被标记为 failed 并允许调用方稍后重试。\n非数据源表（sync=false 或未携带 sync 标识的普通表）不能用此工具触发同步，会以参数错误返回。
- Required top-level parameters: `baseId`, `tableIds`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | 目标 Base ID（通过 list_bases / search_bases 获取） |
| `tableIds` | yes | array; items=string | tableIds | 待触发同步的数据源表 ID 列表（通过 get_base / get_tables 获取，仅允许传入 sync=true 的表）。单次最多 5 张，超过请拆分多次调用 |
| `tableIds[]` |  | string |  |  |

### CLI flag overlay

- none

## dws aitable base search

- Canonical path: `aitable.search_bases`
- Product: `aitable`
- Group: `base`
- Subcommand: `search`
- Title: 搜索 AI 表格
- Description: 按名称关键词搜索 AI 表格 Base。返回 baseId/baseName，结果按相关性排序。返回的 baseId 可直接用于 get_base 等后续工具。 AI 表格访问地址可按 baseId 拼接为：https://docs.dingtalk.com/i/nodes/{baseId}
- Required top-level parameters: `query`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `cursor` |  | string | cursor | 分页游标，首次不传 |
| `query` | yes | string | query | Base 名称关键词，建议至少 2 个字符 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `cursor` | `--cursor` |  |  |  |  |
| `query` | `--query` |  |  |  |  |

## dws aitable template search

- Canonical path: `aitable.search_templates`
- Product: `aitable`
- Group: `template`
- Subcommand: `search`
- Title: 搜索模板
- Description: 按名称关键词搜索 AI 表格模板，支持分页。 返回每个模板的 templateId、name、description，以及分页信息 hasMore / nextCursor。 返回的 templateId 可直接用于 create_base。 模板预览链接可通过 https://docs.dingtalk.com/table/template/{templateId} 拼接得到
- Required top-level parameters: `query`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `cursor` |  | string | cursor | 分页游标。首次请求不传；后续请原样传入上次返回的 nextCursor |
| `limit` |  | number | limit | 每页返回数量。默认 10，最大 30 |
| `query` | yes | string | query | 模板名称关键词 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `cursor` | `--cursor` |  |  |  |  |
| `limit` | `--limit` |  |  |  |  |
| `query` | `--query` |  |  |  |  |

## dws aitable base update

- Canonical path: `aitable.update_base`
- Product: `aitable`
- Group: `base`
- Subcommand: `update`
- Title: 更新 AI 表格
- Description: 更新 Base 名称（可选备注）。当前不支持修改主题、封面等扩展属性
- Required top-level parameters: `baseId`, `newBaseName`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | 目标 Base ID |
| `description` |  | string | description | 备注文本 |
| `newBaseName` | yes | string | newBaseName | 新名称，1-50 字符 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |
| `description` | `--desc` |  |  |  |  |
| `newBaseName` | `--name` |  |  |  |  |

## dws aitable chart update

- Canonical path: `aitable.update_chart`
- Product: `aitable`
- Group: `chart`
- Subcommand: `update`
- Title: 更新图表
- Description: 更新指定 chart 的配置或布局。调用前必须先调用 get_dashboard_widgets_example 了解 config 入参结构和要求。返回更新后的 chart 详情。
- Required top-level parameters: `baseId`, `dashboardId`, `chartId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | 所属 Base 的唯一标识，可通过 list_bases / search_bases 获取 |
| `chartId` | yes | string | chartId | 目标 chart 的唯一标识，可通过 get_dashboard 获取 |
| `config` |  | object | config | 必传参数，图表配置对象，必须按 get_dashboard_widgets_example 返回的 JSONC 结构和注释构造符合要求的 JSON，仅需将占位值替换为真实值 |
| `dashboardId` | yes | string | dashboardId | 所属 dashboard 的唯一标识，可通过 get_base 获取 |
| `layout` |  | object | layout | 可选，不提供此参数代表不更改布局，图表在 dashboard 中的位置和大小。x/y 表示横纵坐标，w/h 表示宽度/高度，单位是列数或行数。仪表盘是网格布局共 12 列、行数无明确限制，更新布局时一定注意。同一行的图表保持高度一致，每行的图表宽度相加需要正好将整行填满，以避免出现空白。总计类的图表排在上部，以方便用户快速查看，下方再放置更具体的图表。通过 get_dashboard 获取当前已有图表的布局信息，更新图表布局时请合理安排布局以避免与现有图表重叠，如有必要也可以通过 update_chart 调整已有图表的布局以让新布局更合适。 |
| `layout.h` | yes | string | h | 布局高度（所占行数） |
| `layout.parentId` |  | string | parentId | 可选，父容器 ID |
| `layout.w` | yes | string | w | 布局宽度（所占列数） |
| `layout.x` | yes | string | x | 布局横坐标（列） |
| `layout.y` | yes | string | y | 布局纵坐标（行） |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |
| `chartId` | `--chart-id` |  |  |  |  |
| `config` | `--config` | json_parse |  |  |  |
| `dashboardId` | `--dashboard-id` |  |  |  |  |
| `layout` | `--layout` | json_parse |  |  |  |

## dws aitable chart.share update

- Canonical path: `aitable.update_chart_share`
- Product: `aitable`
- Group: `chart.share`
- Subcommand: `update`
- Title: 更新图表分享配置（开启或关闭或更新配置）
- Description: 用于开启或关闭指定 chart 的分享，并设置分享类型。\nenabled=true 时开启分享；enabled=false 时关闭分享（shareType 此时无意义）。\nshareType 支持两种：PUBLIC（任何人均可访问）、ORG（仅限当前组织成员访问）。\n返回更新后的分享配置，包括 shareUrl（可直接发送给他人）。
- Required top-level parameters: `baseId`, `dashboardId`, `chartId`, `enabled`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `allowBackToDoc` |  | boolean | allowBackToDoc | 可选，是否允许查看者通过分享页返回源 AI 表格文档。不传时保持原有配置 |
| `baseId` | yes | string | baseId | 必填，所属 Base 的唯一标识，可通过 list_bases / search_bases 获取 |
| `chartId` | yes | string | chartId | 必填，目标 chart 的唯一标识，可通过 get_dashboard 获取 |
| `dashboardId` | yes | string | dashboardId | 必填，所属 dashboard 的唯一标识，可通过 get_base 获取 |
| `enabled` | yes | boolean | enabled | 必填，分享开关。true 表示开启分享，false 表示关闭分享 |
| `shareType` |  | string | shareType | 可选，分享类型，仅在 enabled=true 时有意义。支持值：PUBLIC（任何人均可通过分享链接访问，无需鉴权）、ORG（仅限当前组织成员访问）。默认为 PUBLIC。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `allowBackToDoc` | `--allow-back-to-doc` |  |  |  |  |
| `baseId` | `--base-id` |  |  |  |  |
| `chartId` | `--chart-id` |  |  |  |  |
| `dashboardId` | `--dashboard-id` |  |  |  |  |
| `enabled` | `--enabled` |  |  |  |  |
| `shareType` | `--share-type` |  |  |  |  |

## dws aitable dashboard update

- Canonical path: `aitable.update_dashboard`
- Product: `aitable`
- Group: `dashboard`
- Subcommand: `update`
- Title: 更新仪表盘
- Description: 更新指定 dashboard 的配置。调用前必须先调用 get_dashboard_config_example 了解 config 入参结构和要求。返回更新后的 dashboard 详情。
- Required top-level parameters: `baseId`, `dashboardId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | 必传参数，所属 Base 的唯一标识，可通过 list_bases / search_bases 获取 |
| `config` |  | object | config | 必传参数，Dashboard 配置对象，必须按 get_dashboard_config_example 返回的 JSONC 结构和注释构造符合要求的 JSON。传入需要更新的字段，未传入的字段保持原值 |
| `dashboardId` | yes | string | dashboardId | 必传参数，目标 dashboard 的唯一标识，可通过 get_base 获取 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |
| `config` | `--config` | json_parse |  |  |  |
| `dashboardId` | `--dashboard-id` |  |  |  |  |

## dws aitable dashboard.share update

- Canonical path: `aitable.update_dashboard_share`
- Product: `aitable`
- Group: `dashboard.share`
- Subcommand: `update`
- Title: 设置仪表盘分享配置（开启或关闭或更新配置）
- Description: 用于开启或关闭指定 dashboard 的分享，并设置分享类型。enabled=true 时开启分享；enabled=false 时关闭分享（shareType 此时无意义）。shareType 支持两种：PUBLIC（任何人均可访问）、ORG（仅限当前组织成员访问）。返回更新后的分享配置，包括 shareUrl（可直接发送给他人）。
- Required top-level parameters: `baseId`, `dashboardId`, `enabled`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `allowBackToDoc` |  | boolean | allowBackToDoc | 可选，是否允许查看者通过分享页返回源 AI 表格文档。不传时保持原有配置 |
| `baseId` | yes | string | baseId | 必填，所属 Base 的唯一标识，可通过 list_bases / search_bases / get_base 获取 |
| `dashboardId` | yes | string | dashboardId | 必填，目标 dashboard 的唯一标识，可通过 get_base 获取 |
| `enabled` | yes | boolean | enabled | 必填，分享开关。true 表示开启分享，false 表示关闭分享 |
| `shareType` |  | string | shareType | 可选，分享类型，仅在 enabled=true 时有意义。支持值：PUBLIC（任何人均可通过分享链接访问，无需鉴权）、ORG（仅限当前组织成员访问）。默认为 PUBLIC。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `allowBackToDoc` | `--allow-back-to-doc` |  |  |  |  |
| `baseId` | `--base-id` |  |  |  |  |
| `dashboardId` | `--dashboard-id` |  |  |  |  |
| `enabled` | `--enabled` |  |  |  |  |
| `shareType` | `--share-type` |  |  |  |  |

## dws aitable field update

- Canonical path: `aitable.update_field`
- Product: `aitable`
- Group: `field`
- Subcommand: `update`
- Title: 更新字段
- Description: 更新指定字段的名称或配置。不可变更字段类型（type 不可修改）。 newFieldName、config、aiConfig 至少传入一项
- Required top-level parameters: `baseId`, `tableId`, `fieldId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `aiConfig` |  | object | aiConfig | 更新后的 AI 配置。传入时为整体替换，outputType 与 prompt 均为必填。不修改 AI 配置时省略。 |
| `aiConfig.autoRecompute` |  | boolean | autoRecompute | 可选，是否在 prompt 引用的字段值发生变化后自动重新计算该 AI 字段。true 表示开启自动重算，引用字段更新后系统会自动刷新结果；false 表示关闭自动重算，仅在手动触发或其他业务动作要求重算时更新。 |
| `aiConfig.computeOnEmptyRef` |  | boolean | computeOnEmptyRef | 可选，引用字段为空时是否继续触发 AI 计算。默认 false（引用字段为空时跳过计算）；设为 true 时，即使 prompt 中引用的字段值为空，也会触发计算。 |
| `aiConfig.enableThinking` |  | boolean | enableThinking | 可选，是否启用深度思考。 |
| `aiConfig.enableWebSearch` |  | boolean | enableWebSearch | 可选，是否启用联网搜索。 |
| `aiConfig.imageConfig` |  | object | imageConfig | 可选，图片生成配置。仅 outputType=image 时有效。 |
| `aiConfig.imageConfig.aiGeneratedWatermark` | yes | boolean | aiGeneratedWatermark | 是否生成 AI 水印。 |
| `aiConfig.imageConfig.resolution` | yes | string | resolution | 图片分辨率。支持 1280*1280、1024*1024、800*1200、1200*800、960*1280、1280*960、720*1280、1280*720、1344*576。 |
| `aiConfig.outputType` | yes | string | outputType | 必填，输出字段类型。支持 text、select、multiSelect、number、currency、image、video。 |
| `aiConfig.prompt` | yes | array; items=object | prompt | 必填，用户编辑的 prompt 片段列表。每项 type=text 时传 value；type=fieldRef 时传 fieldId。 |
| `aiConfig.prompt[].fieldId` |  | string | fieldId | 当 type=fieldRef 时必填，被引用字段的 fieldId。 |
| `aiConfig.prompt[].type` | yes | string | type | Prompt 片段类型。支持 text、fieldRef。 |
| `aiConfig.prompt[].value` |  | string | value | 当 type=text 时必填，文本片段内容。 |
| `aiConfig.videoConfig` |  | object | videoConfig | 可选，视频生成配置。仅 outputType=video 时有效。 |
| `aiConfig.videoConfig.aspectRatio` | yes | string | aspectRatio | 视频宽高比。支持 832*480、480*832、624*624、1280*720、720*1280、960*960、1088*832、832*1088、1920*1080、1080*1920、1440*1440、1632*1248、1248*1632。 |
| `aiConfig.videoConfig.duration` |  | number | duration | 可选，视频时长。支持 5 或 10。 |
| `aiConfig.videoConfig.resolution` | yes | string | resolution | 视频分辨率。支持 480p、720p、1080p。 |
| `baseId` | yes | string | baseId | Base ID（可通过 list_bases 获取） |
| `config` |  | object | config | 更新后的字段物理配置，结构与 create_fields.fields[].config 完全一致。 不修改配置时省略。 注意：更新 singleSelect/multipleSelect 的 options 时，需传入完整列表（含已有选项），系统以新列表整体覆盖，不是追加。 注意：为避免已有单元格因 option id 变化而丢数据，更新时已有选项应尽量回传原 id；新增选项无需传 id。 注意：如果请求中传入的 option id 在当前字段配置中不存在，系统会丢弃该 id，并按新增选项处理；若 id 合法但 name 改了，属于正常更新，会保留该 id。 |
| `config.options` |  | array; items=object | options | 选项列表。只有当字段类型为 singleSelect 或 multipleSelect 时 才需要提供。更新时需传入完整列表（含已有选项），系统以新列表整体覆盖；为避免已有单元格因 option id 变化而丢数据，已有选项应尽量回传原 id。 |
| `config.options[].id` |  | string | id | 可选，已有选项的 ID。更新 singleSelect 或 multipleSelect 时，已有选项建议回传原 id；新增选项无需传 id。若传入的 id 在当前字段配置中不存在，系统会忽略该 id，并按新增选项处理。 |
| `config.options[].name` | yes | string | name | 选项名称。若 id 合法，即使 name 与当前配置中的旧名称不同，也会保留该 id，并将名称更新为新值。 |
| `fieldId` | yes | string | fieldId | Field ID（可通过 get_tables 获取） |
| `newFieldName` |  | string | newFieldName | 更新后的字段名称，最大100字。不修改名称时省略 |
| `tableId` | yes | string | tableId | Table ID（可通过 get_base 获取） |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `aiConfig` | `--ai-config` | json_parse |  |  |  |
| `baseId` | `--base-id` |  |  |  |  |
| `config` | `--config` | json_parse |  |  |  |
| `fieldId` | `--field-id` |  |  |  |  |
| `newFieldName` | `--name` |  |  |  |  |
| `tableId` | `--table-id` |  |  |  |  |

## dws aitable update_guide_document

- Canonical path: `aitable.update_guide_document`
- Product: `aitable`
- Group: `-`
- Subcommand: `update_guide_document`
- Title: update_guide_document
- Description: 更新指定 Base 中的说明文档（重命名）。需要管理员权限。 返回更新后的说明文档 ID 和名称。
- Required top-level parameters: `baseId`, `documentId`, `newDocumentName`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | Base ID，可通过 list_bases 或 search_bases 获取 |
| `documentId` | yes | string | documentId | 说明文档 ID，可通过 get_base 返回的 documents 列表获取 |
| `newDocumentName` | yes | string | newDocumentName | 新的说明文档名称 |

### CLI flag overlay

- none

## dws aitable record update

- Canonical path: `aitable.update_records`
- Product: `aitable`
- Group: `record`
- Subcommand: `update`
- Title: 更新记录
- Description: 批量更新指定记录的字段值，只需传入需修改的字段，未传入的字段保持原值
- Required top-level parameters: `baseId`, `tableId`, `records`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | Base ID，可通过 list_bases 或 search_bases 获取 |
| `records` | yes | array; items=object | records | 待更新的记录内容列表，单次最多 100 条 |
| `records[].cells` | yes | object | cells | 字段值映射：key 为 fieldId，value 为字段新值。未传入的字段保持原值。  各类型写入格式： text → "文本内容" number/currency/progress → 123 或 123.45（数字）；progress 值范围 0~1（表示 0%~100%） rating → 4（数字，须在字段 min~max 范围内） singleSelect → "选项名称"，或 {"id":"opt_xxx"} / {"id":"opt_xxx","name":"进行中"}；对象写入时 id 为准，服务端会校验该 id 是否存在，并转换为当前 option name 后再写入；若直接传 option id 字符串会返回显式错误 multipleSelect → ["选项名1","选项名2"]，或 [{"id":"opt_a","name":"标签A"},{"id":"opt_b","name":"标签B"}]；对象写入时每项都必须带 id，服务端会逐项校验并转换为当前 option name；若直接传 option id 字符串数组会返回显式错误 date → 日期字符串（如 "2026-03-15"、"2026-03-15 09:00"）或含时区的 RFC3339 字符串（如 "2026-03-15T09:00+08:00"）；亦支持毫秒时间戳 checkbox → true \| false user → 通常为 [{"userId":"staff_001","corpId":"dingxxxxxxxx"}]，仅当目标用户不在当前请求组织、无法反查 userId 时回退为 [{"userRef":"ur_0AaZ19"}] department → [{"deptId":"52528700"}] group → [{"cid":"74577067501"}]  — 注意 key 是 cid，不是 openConversationId url → {"text":"显示文字","link":"https://..."}；也兼容直接传 "https://..."，服务端会按 {"text":"原字符串","link":"原字符串"} 自动补齐 richText → {"markdown":"**加粗**\n普通文字\n"} telephone/email/barcode/idCard → 字符串 attachment → 可传 [{"fileToken":"ft_xxx"}]、[{"url":"https://..."}] 或完整对象数组（会整体覆盖）。服务端会在写入前把 fileToken 展开为完整 attachment 对象；也支持把 {"url":"https://..."} 转成内部附件后再写入。URL 转存是 best-effort 异步链路，update_records 返回成功仅表示已受理转存与写入 geolocation → {"address":"浙江省杭州市思凯路与爱橙街交叉口东南200米","name":"阿里中心·未科D1幢","location":["120.007852","30.271194"]}（对象格式；location 按 [经度, 纬度] 传字符串数组；读写均使用该结构） unidirectionalLink/bidirectionalLink → {"linkedRecordIds":["recXXX","recYYY"]} creator/lastModifier/createdTime/lastModifiedTime → 系统只读字段，禁止写入  示例： [{"recordId":"recXXX","cells":{"fldStatusId":{"id":"opt_done","name":"已完成"},"fldNumId":99,"fldUserId":[{"userId":"staff_001","corpId":"dingxxxxxxxx"}]}}] |
| `records[].recordId` | yes | string | recordId | Record ID |
| `tableId` | yes | string | tableId | Table ID，可通过 get_base 获取 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |
| `records` | `--records` | json_parse |  |  |  |
| `tableId` | `--table-id` |  |  |  |  |

## dws aitable table update

- Canonical path: `aitable.update_table`
- Product: `aitable`
- Group: `table`
- Subcommand: `update`
- Title: 更新数据表
- Description: 重命名指定 Table（数据表）。若新名称不符合命名要求、与同一 Base 下其他表重名或无权限，将返回错误。
- Required top-level parameters: `baseId`, `tableId`, `newTableName`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | 所属 Base ID（用于定位目标表）。 |
| `newTableName` | yes | string | newTableName | 新表名。需非空；不能包含 / \ ? * [ ] : 等特殊字符。 |
| `tableId` | yes | string | tableId | 目标 Table ID（通过 get_base 获取）。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |
| `newTableName` | `--name` |  |  |  |  |
| `tableId` | `--table-id` |  |  |  |  |

## dws aitable view update

- Canonical path: `aitable.update_view`
- Product: `aitable`
- Group: `view`
- Subcommand: `update`
- Title: 更新视图
- Description: 更新指定视图（View）的名称、描述或配置。 当前稳定支持更新：newViewName、viewDescription、visibleFieldIds、filter、sort、group；fieldWidths 仅支持 Grid 视图。 首列字段是每条数据的索引，不支持删除、移动或隐藏。
- Required top-level parameters: `baseId`, `tableId`, `viewId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `baseId` | yes | string | baseId | 所属 Base 的唯一标识。 |
| `config` |  | object | config | 可选，视图配置更新项。 |
| `config.fieldWidths` |  | object | fieldWidths | 可选，列宽映射（key 为 fieldId，value 为宽度，单位像素，默认为 200 像素，合法范围由下游校验）。仅支持 Grid 视图；若目标视图不是 Grid，请不要传该字段。 |
| `config.filter` |  | array; items=object | filter | 可选，新的筛选规则列表，会全量覆盖当前 filter。 |
| `config.group` |  | array; items=object | group | 可选，新的分组规则列表，会全量覆盖当前 group。 |
| `config.sort` |  | array; items=object | sort | 可选，新的排序规则列表，会全量覆盖当前 sort。 |
| `config.visibleFieldIds` |  | array; items=string | visibleFieldIds | 可选，新的视图可见字段列，以及顺序（fieldId 列表）。需要传全量，不传则不修改。首列字段是每条数据的索引，必须保留在数组第一个位置，不能删除、移动或隐藏。 |
| `config.visibleFieldIds[]` |  | string |  |  |
| `newViewName` |  | string | newViewName | 可选，新的视图名称。 |
| `tableId` | yes | string | tableId | 所属数据表（Table）的唯一标识。 |
| `viewDescription` |  | object | viewDescription | 可选，新的视图描述。若不传则不修改；如需清空，可传 {"content": []}。 |
| `viewId` | yes | string | viewId | 目标视图（View）的唯一标识。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `baseId` | `--base-id` |  |  |  |  |
| `config` | `--config` | json_parse |  |  |  |
| `newViewName` | `--name` |  |  |  |  |
| `tableId` | `--table-id` |  |  |  |  |
| `viewId` | `--view-id` |  |  |  |  |

