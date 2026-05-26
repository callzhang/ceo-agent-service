# dws drive Commands

云盘 / 文件 / 上传 / 下载

Commands in this file: 7

## Command Index

| CLI | Canonical path | Risk |
| --- | --- | --- |
| [`dws drive commit`](#dws-drive-commit) | `drive.commit_upload` | `mutating-review-first` |
| [`dws drive mkdir`](#dws-drive-mkdir) | `drive.create_folder` | `mutating-review-first` |
| [`dws drive download`](#dws-drive-download) | `drive.download_file` | `read-with-local-output` |
| [`dws drive info`](#dws-drive-info) | `drive.get_file_info` | `read-only` |
| [`dws drive upload-info`](#dws-drive-upload-info) | `drive.get_upload_info` | `mutating-review-first` |
| [`dws drive list`](#dws-drive-list) | `drive.list_files` | `mutating-review-first` |
| [`dws drive list-spaces`](#dws-drive-list-spaces) | `drive.list_spaces` | `read-only` |

## Group Summary

| Group | Commands |
| --- | ---: |
| `-` | 7 |

## dws drive commit

- Canonical path: `drive.commit_upload`
- Product: `drive`
- Group: `-`
- Subcommand: `commit`
- Title: commit_upload
- Description: 文件上传流程： Step 1：调用 get_upload_info 获取预签名的 OSS 上传 URL Step 2：使用 Step 1 返回的 URL 将文件 PUT 到 OSS Step 3：调用本接口提交文件，传入Step 1返回的 uploadId 与对应的 fileName、fileSize 完成文件上传
- Required top-level parameters: `fileName`, `uploadId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `conflictHandler` |  | string | 重名冲突策略 | 文件名称冲突策略，不传则默认为 AUTO_RENAME。 AUTO_RENAME：自动重命名，   OVERWRITE：覆盖   RETURN_DENTRY_IF_EXISTS：返回已存在文件  RETURN_ERROR_IF_EXISTS：文件已存在时报错 |
| `fileName` | yes | string | 文件名 | 文件名（含扩展名），须与 get_upload_info 时传入的 fileName 一致 |
| `fileSize` |  | number | 文件大小 | 文件大小（字节），须与 get_upload_info 时传入的 fileSize 一致，用于完整性校验 |
| `parentId` |  | string | 父节点ID | 父节点 ID（dentryUuid 格式）。不传则提交到空间根目录。须与 get_upload_info 时传入的 parentId 一致 |
| `spaceId` |  | string | 空间ID | 目标空间 ID。不传时后端自动使用当前用户「我的文件」对应的 spaceId |
| `uploadId` | yes | string | 上传id | 上传 ID，来自 get_upload_info 返回的 uploadId |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `conflictHandler` | `--conflict-handler` |  |  |  |  |
| `fileName` | `--file-name` |  |  |  |  |
| `fileSize` | `--file-size` |  |  |  |  |
| `parentId` | `--parent-id` |  |  |  |  |
| `spaceId` | `--space-id` |  |  |  |  |
| `uploadId` | `--upload-id` |  |  |  |  |

## dws drive mkdir

- Canonical path: `drive.create_folder`
- Product: `drive`
- Group: `-`
- Subcommand: `mkdir`
- Title: 创建文件夹
- Description: 在钉盘中创建一个新文件夹 最简用法：只传 name，文件夹会创建在当前用户「我的文件」的根目录下。 如果想创建在某个子文件夹里，传 parentId（从 list_files 或 create_folder 的返回值中获取）。 创建成功后返回 fileId，可直接用于后续操作（如在该文件夹下继续创建子文件夹或上传文件）。
- Required top-level parameters: `name`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `name` | yes | string | 文件夹名称 | 文件夹名称，最长 50 个字符 |
| `parentId` |  | string | 父节点ID | 父节点 ID（dentryUuid 格式）。不传则在空间根目录下创建 |
| `spaceId` |  | string | 空间ID | 空间 ID。绝大多数情况下不需要传，后端自动使用当前用户默认空间 spaceId |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `name` | `--name` |  |  |  |  |
| `parentId` | `--parent-id` |  |  |  |  |
| `spaceId` | `--space-id` |  |  |  |  |

## dws drive download

- Canonical path: `drive.download_file`
- Product: `drive`
- Group: `-`
- Subcommand: `download`
- Title: 下载文件
- Description: 获取钉盘普通文件的下载链接。调用钉盘文件下载接口，返回 OSS 预签名下载链接，链接有效期 15 分钟
- Required top-level parameters: `fileId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-with-local-output`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `fileId` | yes | string | 文件ID | 文件 ID（dentryUuid 格式） |
| `spaceId` |  | string | 空间ID | 文件所属空间 ID |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `fileId` | `--file-id` |  |  |  |  |
| `spaceId` | `--space-id` |  |  |  |  |

## dws drive info

- Canonical path: `drive.get_file_info`
- Product: `drive`
- Group: `-`
- Subcommand: `info`
- Title: 获取文件元数据信息
- Description: 获取钉盘中指定节点（文件或文件夹）的元数据信息，包括文件名、类型、状态、路径、创建时间、文件大小等
- Required top-level parameters: `fileId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `fileId` | yes | string | 文件ID | 节点 ID（dentryUuid 格式），统一标识文件和文件夹 |
| `spaceId` |  | string | 空间ID | 节点所属空间 ID。可不传，后端自动从 fileId（dentryUuid 格式）中解析出 spaceId |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `fileId` | `--file-id` |  |  |  |  |
| `spaceId` | `--space-id` |  |  |  |  |

## dws drive upload-info

- Canonical path: `drive.get_upload_info`
- Product: `drive`
- Group: `-`
- Subcommand: `upload-info`
- Title: get_upload_info
- Description: 文件上传流程： Step 1：调用本接口获取预签名的 OSS 上传 URL Step 2：使用 Step 1 返回的 URL 将文件 PUT 到 OSS Step 3：调用commit_upload 提交文件，传入本接口返回的 uploadId 与对应的 fileName、fileSize 完成文件上传
- Required top-level parameters: `fileName`, `fileSize`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `fileName` | yes | string | 文件名 | 文件名，须包含扩展名，如 报告.pdf。 |
| `fileSize` | yes | number | 文件大小 | 文件大小（字节），用于服务端生成上传凭证和后续完整性校验。 |
| `mimeType` |  | string | MIME 类型 | 文件 MIME 类型，如 application/pdf。不传时由服务端根据文件扩展名自动推断。 |
| `parentId` |  | string | 父节点 ID | 父节点 ID（dentryUuid 格式）。不传则上传到空间根目录。 |
| `spaceId` |  | string | 空间 ID | 目标空间 ID。不传时后端自动使用当前用户「我的文件」对应的 spaceId。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `fileName` | `--file-name` |  |  |  |  |
| `fileSize` | `--file-size` |  |  |  |  |
| `mimeType` | `--mime-type` |  |  |  |  |
| `parentId` | `--parent-id` |  |  |  |  |
| `spaceId` | `--space-id` |  |  |  |  |

## dws drive list

- Canonical path: `drive.list_files`
- Product: `drive`
- Group: `-`
- Subcommand: `list`
- Title: 获取文件/文件夹列表
- Description: 列出钉盘指定文件夹下的文件和文件夹，支持分页和排序 最简用法：不传任何参数，列出默认空间根目录下的内容。 如果想列出某个子文件夹的内容，传 parentId（从 create_folder 或 list_files 返回值中获取）。 使用 nextToken 游标翻页：首次不传，后续翻页传上一次返回的 nextToken；nextToken 为 null 表示没有更多数据。
- Required top-level parameters: `maxResults`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `maxResults` | yes | number | 每页返回数量 | 每页返回数量，默认 20，最大 50 |
| `nextToken` |  | string | 分页游标 | 分页游标。首次请求不传；翻页时传上一次响应返回的 nextToken |
| `order` |  | string | 排序方向 | 排序方向。asc（升序）/ desc（降序），默认 desc |
| `orderBy` |  | string | 排序字段 | 排序字段。可选值：createTime（创建时间）、modifyTime（修改时间）、name（名称） |
| `parentId` |  | string | 父节点ID | 父文件夹 ID（dentryUuid 格式）。不传则列出「我的文件」根目录下的内容 |
| `spaceId` |  | string | 空间ID | 空间 ID。绝大多数情况下不需要传，后端自动使用当前用户「我的文件」对应的 spaceId |
| `withThumbnail` |  | boolean | 是否返回缩略图 | 是否返回缩略图信息。默认 false |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `maxResults` | `--max` |  |  |  |  |
| `nextToken` | `--next-token` |  |  |  |  |
| `order` | `--order` |  |  |  |  |
| `orderBy` | `--order-by` |  |  |  |  |
| `parentId` | `--parent-id` |  |  |  |  |
| `spaceId` | `--space-id` |  |  |  |  |
| `withThumbnail` | `--thumbnail` |  |  |  |  |

## dws drive list-spaces

- Canonical path: `drive.list_spaces`
- Product: `drive`
- Group: `-`
- Subcommand: `list-spaces`
- Title: list_spaces
- Description: 获取当前用户可访问的空间列表。 最简用法：不传任何参数，返回所有企业空间。 空间类型筛选：通过 spaceType 参数可以筛选返回的空间类型： orgSpace（或 不传）：返回企业空间列表 mySpace：返回用户的"我的文件"个人空间 支持游标分页，使用 nextToken 进行翻页（仅企业空间支持分页）。 返回结果说明： 当 spaceType 为 orgSpace ，返回企业团队空间列表 当 spaceType 为 mySpace 时，返回单个"我的文件"空间 如果 nextToken 不为空，表示还有更多空间可以查询（仅企业空间）
- Required top-level parameters: -
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `maxResults` |  | number | 分页数量 | 每页返回数量，默认 20，最大 50。建议根据实际需求调整，较大的值可以减少请求次数，但会增加单次响应的数据量。仅 spaceType 为 orgSpace 时有效 |
| `nextToken` |  | string | 分页游标 | 分页游标。首次请求不传，后续请求传上一次返回的 nextToken。当 nextToken 为 null 时，表示已获取所有空间。仅 spaceType 为 orgSpace 时有效 |
| `spaceType` |  | string | 空间类型 | 空间类型。可选值：orgSpace（企业空间）、mySpace（我的文件）。不传时默认为 orgSpace |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `maxResults` | `--max` |  |  |  |  |
| `nextToken` | `--cursor` |  |  |  |  |
| `spaceType` | `--space-type` |  |  |  |  |

