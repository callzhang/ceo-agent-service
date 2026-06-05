# dws doc Commands

钉钉文档（搜索 / 浏览 / 读写 / 上传下载 / 文件 / 文件夹 / 块级编辑 / 评论）

Commands in this file: 26

## Command Index

| CLI | Canonical path | Risk |
| --- | --- | --- |
| [`dws doc add_permission`](#dws-doc-addpermission) | `doc.add_permission` | `read-only` |
| [`dws doc commit_uploaded_file`](#dws-doc-commituploadedfile) | `doc.commit_uploaded_file` | `mutating-review-first` |
| [`dws doc copy`](#dws-doc-copy) | `doc.copy_document` | `read-only` |
| [`dws doc create`](#dws-doc-create) | `doc.create_document` | `mutating-review-first` |
| [`dws doc file create`](#dws-doc-file-create) | `doc.create_file` | `mutating-review-first` |
| [`dws doc folder create`](#dws-doc-folder-create) | `doc.create_folder` | `mutating-review-first` |
| [`dws doc delete_document`](#dws-doc-deletedocument) | `doc.delete_document` | `mutating-review-first` |
| [`dws doc block delete`](#dws-doc-block-delete) | `doc.delete_document_block` | `sensitive-mutating` |
| [`dws doc download_doc_attachment`](#dws-doc-downloaddocattachment) | `doc.download_doc_attachment` | `read-with-local-output` |
| [`dws doc download`](#dws-doc-download) | `doc.download_file` | `read-with-local-output` |
| [`dws doc get_doc_attachment_upload_info`](#dws-doc-getdocattachmentuploadinfo) | `doc.get_doc_attachment_upload_info` | `mutating-review-first` |
| [`dws doc read`](#dws-doc-read) | `doc.get_document_content` | `read-only` |
| [`dws doc info`](#dws-doc-info) | `doc.get_document_info` | `read-with-local-output` |
| [`dws doc upload`](#dws-doc-upload) | `doc.get_file_upload_info` | `mutating-review-first` |
| [`dws doc block insert`](#dws-doc-block-insert) | `doc.insert_document_block` | `mutating-review-first` |
| [`dws doc block list`](#dws-doc-block-list) | `doc.list_document_blocks` | `read-only` |
| [`dws doc list`](#dws-doc-list) | `doc.list_nodes` | `mutating-review-first` |
| [`dws doc list_permission`](#dws-doc-listpermission) | `doc.list_permission` | `read-only` |
| [`dws doc move`](#dws-doc-move) | `doc.move_document` | `read-only` |
| [`dws doc query_export_job`](#dws-doc-queryexportjob) | `doc.query_export_job` | `read-with-local-output` |
| [`dws doc rename`](#dws-doc-rename) | `doc.rename_document` | `mutating-review-first` |
| [`dws doc search`](#dws-doc-search) | `doc.search_documents` | `mutating-review-first` |
| [`dws doc submit_export_job`](#dws-doc-submitexportjob) | `doc.submit_export_job` | `read-with-local-output` |
| [`dws doc update`](#dws-doc-update) | `doc.update_document` | `mutating-review-first` |
| [`dws doc block update`](#dws-doc-block-update) | `doc.update_document_block` | `mutating-review-first` |
| [`dws doc update_permission`](#dws-doc-updatepermission) | `doc.update_permission` | `mutating-review-first` |

## Group Summary

| Group | Commands |
| --- | ---: |
| `-` | 20 |
| `block` | 4 |
| `file` | 1 |
| `folder` | 1 |

## dws doc add_permission

- Canonical path: `doc.add_permission`
- Product: `doc`
- Group: `-`
- Subcommand: `add_permission`
- Title: 添加节点成员
- Description: 为知识库下的节点（文档、文件夹、文件）添加指定角色的企业用户成员（仅支持 USER 类型）。  通过传入 userIds 列表批量授予企业用户对节点的访问权限，添加成功后即可按指定角色访问该节点及其子节点（若子节点继承权限）。  注意事项： - OWNER 角色不可通过此接口添加。 - **权限要求（业务规则）**：操作者必须在该节点上具备「可编辑（EDITOR）」及以上角色（OWNER / MANAGER / EDITOR）。 - 单次请求 `userIds` 列表最多传 30 个，超出需分批调用。 - 入参直接采用扁平的 `userIds: List<String>`，每个元素为钉钉 staffId（外部 userId 格式，由钉钉开放平台体系下颁发）。如果你只有 unionId，请先调用钉钉开放平台「根据 unionId 获取 userId」接口换取。
- Required top-level parameters: `nodeId`, `roleId`, `userIds`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `nodeId` | yes | string | 节点标识 | 目标节点的 nodeId（文档、文件夹或文件），可通过 list_nodes 工具获取。 |
| `roleId` | yes | string | 授予的角色 | 授予成员的角色，取值：MANAGER、EDITOR、DOWNLOADER、READER。 |
| `userIds` | yes | array; items=string | 用户 userId 列表 | 被授权的用户 userId 列表（钉钉 staffId / 外部 userId 格式，由钉钉开放平台体系下颁发；通常为数字字符串），至少包含一个 userId，单次最多 30 个。如果你只有 unionId，请先调用钉钉开放平台「根据 unionId 获取 userId」接口换取 userId。 |
| `userIds[]` |  | string |  |  |
| `workspaceId` |  | string | 知识库标识 | 目标知识库的标识，选填。仅用于辅助构造返回的 docUrl，业务实际依赖 nodeId 定位节点；支持两种格式：1) 知识库 ID（纯字符串）；2) 知识库 URL，如 https://alidocs.dingtalk.com/i/spaces/{workspaceId}/overview，系统自动提取其中的 workspaceId。 |

### CLI flag overlay

- none

## dws doc commit_uploaded_file

- Canonical path: `doc.commit_uploaded_file`
- Product: `doc`
- Group: `-`
- Subcommand: `commit_uploaded_file`
- Title: 提交已上传的文件
- Description: 提交已上传的文件，完成文件入库。本工具是上传本地文件到钉钉文档或钉钉知识库的第三步（最后一步）。  前置条件：   - 必须已调用 get_file_upload_info（第一步），并获取到 uploadKey。   - 必须已通过 HTTP PUT 成功将文件二进制内容上传到 OSS（第二步，响应 HTTP 200）。   - 如果第二步 HTTP PUT 失败，不得调用本工具。
- Required top-level parameters: `uploadKey`, `name`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `convertToOnlineDoc` |  | boolean | 是否转换为在线文档 | 是否将上传的 Office 文件（如 .xlsx、.docx）转换为钉钉在线文档。默认为 false。 |
| `fileSize` |  | number | 文件大小 | 文件大小，单位字节。填写后服务端会校验与实际上传内容是否一致。 |
| `folderId` |  | string | 目标文件夹 ID | 目标文件夹的节点 ID（dentryUuid），支持传入文件夹链接 URL 或 ID。必须与调用 get_file_upload_info 时传入的一致。不传时：如果提供了 workspaceId 则提交到该知识库根目录下，否则提交到用户'我的文档'根目录下。folderId 优先级高于 workspaceId。 |
| `name` | yes | string | 文件名称 | 文件最终展示名称，含后缀（如 Q1 Report.xlsx）。必填。命名规则：头尾不能有空格；不能含制表符、*、"、<、>、\|；不能以 "." 结尾。 |
| `uploadKey` | yes | string | 上传唯一标识 | get_file_upload_info 返回的 uploadKey，用于关联本次 OSS 上传记录。必填。 |
| `workspaceId` |  | string | 知识库 ID | 目标知识库的标识，支持知识库 ID 或知识库 URL。如果同时传了 folderId，则以 folderId 为准。 |

### CLI flag overlay

- none

## dws doc copy

- Canonical path: `doc.copy_document`
- Product: `doc`
- Group: `-`
- Subcommand: `copy`
- Title: 将指定节点复制到目标文件夹
- Description: 将指定节点复制到目标文件夹。  支持的节点类型：知识库节点（文档、文件夹）、钉盘文件/文件夹。 nodeId 支持文档 URL 或 dentryUuid（32 位字母数字字符串）。 targetFolderId 为目标文件夹的 dentryUuid；workspaceId 为目标知识库标识，不传 targetFolderId 时复制到该知识库根目录，如果不传 targetFolderId 和 workspaceId，默认到当前用户所在组织的「我的文档」下  权限要求： - 对源节点有可查看下载权限 - 对目标文件夹有写入权限  注意：复制操作底层可能异步执行，异步时操作已提交但新节点 ID 无法立即返回，请稍后查看目标文件夹确认结果。
- Required top-level parameters: `nodeId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `nodeId` | yes | string | 源节点标识 | 要复制的节点标识，支持以下格式：\n1. 文档/文件夹 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid}\n2. dentryUuid（32 位字母数字字符串） |
| `targetFolderId` |  | string | 目标文件夹 ID | 目标文件夹的 dentryUuid（32 位字母数字字符串），复制后的节点将放置在此文件夹下。 |
| `workspaceId` |  | string | 知识库 ID | 目标知识库的标识，支持两种格式：1) 知识库 ID（纯字符串）；2) 知识库 URL，如 https://alidocs.dingtalk.com/i/spaces/{workspaceId}/overview，系统自动提取其中的 workspaceId。当 targetFolderId 不传时，复制到该知识库的根目录下。与 targetFolderId 至少传一个。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `nodeId` | `--node` |  |  |  |  |
| `targetFolderId` | `--folder` |  |  |  |  |
| `workspaceId` | `--workspace` |  |  |  |  |

## dws doc create

- Canonical path: `doc.create_document`
- Product: `doc`
- Group: `-`
- Subcommand: `create`
- Title: 创建钉钉文档
- Description: 创建一篇文字类型的钉钉在线文档（扩展名=adoc）。 支持创建空文档或带有初始 Markdown 内容的文档。（如需创建钉钉AI表格、钉钉表格、钉钉脑图等其他类型的文件，使用 create_file 工具） 支持三种创建位置： 1. 指定 folderId：在该文件夹下创建 2. 指定 workspaceId（知识库 ID）但不传 folderId：在该知识库的根目录下创建 3. 都不传：在用户"我的文档"根目录下创建 操作受权限控制，仅当调用者对目标位置有写入权限时可成功创建。
- Required top-level parameters: `name`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `folderId` |  | string | 文件夹 ID | 目标文件夹的节点 ID（dentryUuid），支持传入文件夹链接 URL 或 ID。不传时：如果提供了 workspaceId 则创建在该知识库根目录下，否则创建在用户'我的文档'根目录下。 |
| `markdown` |  | string | 文档初始内容 | 文档的初始 Markdown 内容。不传则创建空文档。注意：markdown 参数中的换行必须使用真实换行符（即实际的换行字符，Unicode U+000A），而不是字面量字符串 \n（反斜杠加字母 n）。在通过程序或大模型构造此参数时，请确保字符串在发送前已正确反转义。如果传入的是两个字符的字面量 \n，所有内容将渲染在同一行，导致标题、段落和表格格式全部错乱。 |
| `name` | yes | string | 文档名称 | 新文档的标题，必填。 |
| `workspaceId` |  | string | 知识库 ID | 目标知识库的 ID。如果同时传了 folderId，则以 folderId 为准。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `folderId` | `--folder` |  |  |  |  |
| `markdown` | `--markdown` |  |  |  |  |
| `name` | `--name` |  |  |  |  |
| `workspaceId` | `--workspace` |  |  |  |  |

## dws doc file create

- Canonical path: `doc.create_file`
- Product: `doc`
- Group: `file`
- Subcommand: `create`
- Title: 创建节点
- Description: 在指定位置创建一个新的文件（文档、表格、演示、白板、脑图、多维表或文件夹）。  支持三种创建位置（按优先级）： 1. 指定 folderId：在该文件夹下创建 2. 仅指定 workspaceId（不传 folderId）：在该知识库的根目录下创建 3. 都不传：在用户"我的文档"根目录下创建 4.  type  要创建的文件类型，必填。支持以下值：adoc（钉钉在线文档）、axls（钉钉表格）、appt（钉钉演示）、adraw（钉钉白板）、amind（钉钉脑图）、able（钉钉多维表）、folder（文件夹） 操作受权限控制，仅当调用者对目标位置有写入权限时可成功创建。
- Required top-level parameters: `name`, `type`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `folderId` |  | string | 父文件夹 ID | 目标文件夹的节点 ID（dentryUuid），支持传入文件夹链接 URL 或 ID。不传时：如果提供了 workspaceId 则创建在该知识库根目录下，否则创建在用户'我的文档'根目录下。 |
| `name` | yes | string | 文件名称 | 新文件的名称，必填。 |
| `type` | yes | string | 文件类型 | 要创建的文件类型，必填。支持：adoc（钉钉在线文档）、axls（钉钉表格）、appt（钉钉演示）、adraw（钉钉白板）、amind（钉钉脑图）、able（钉钉多维表）、folder（文件夹）。同时兼容旧版 accessType 数字字符串："0"=adoc、"1"=axls、"2"=appt、"3"=adraw、"6"=amind、"7"=able、"13"=folder。 |
| `workspaceId` |  | string | 知识库 ID | 目标知识库的标识，支持知识库 ID 或知识库 URL。如果同时传了 folderId，则以 folderId 为准。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `folderId` | `--folder` |  |  |  |  |
| `name` | `--name` |  |  |  |  |
| `type` | `--type` |  |  |  |  |
| `workspaceId` | `--workspace` |  |  |  |  |

## dws doc folder create

- Canonical path: `doc.create_folder`
- Product: `doc`
- Group: `folder`
- Subcommand: `create`
- Title: 创建文件夹
- Description: 在指定位置创建一个新的文件夹。  支持在文件夹下或知识库下创建子文件夹。 创建后返回新文件夹的节点 ID，可用于后续在该文件夹下创建文档或子文件夹。 操作受权限控制，仅当调用者对父节点有写入权限时可成功创建。
- Required top-level parameters: `name`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `folderId` |  | string | 父文件夹 ID | 父文件夹的节点 ID，支持传入 URL 或 ID。不传时：如果提供了 workspaceId 则创建在该知识库根目录下，否则创建在用户'我的文档'根目录下。 |
| `name` | yes | string | 文件夹名称 | 新文件夹的名称，必填。 |
| `workspaceId` |  | string | 知识库 ID | 目标知识库的 ID。如果同时传了 folderId，则以 folderId 为准。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `folderId` | `--folder` |  |  |  |  |
| `name` | `--name` |  |  |  |  |
| `workspaceId` | `--workspace` |  |  |  |  |

## dws doc delete_document

- Canonical path: `doc.delete_document`
- Product: `doc`
- Group: `-`
- Subcommand: `delete_document`
- Title: 删除节点
- Description: 将指定节点移入回收站，30 天内可从回收站恢复，超过 30 天将被永久删除。  支持范围： - 知识库（workspace）下的文档节点、文件夹节点 - 钉盘「我的文件」或「团队空间（space）」下的文件、文件夹节点（但钉盘操作仍建议走钉盘MCP服务）  不支持的操作： - 不支持直接删除知识库本身（workspace） - 不支持直接删除团队空间（space）  权限要求： - 对目标节点有管理权限（owner 或被授予管理权限的成员）  注意事项： - 删除操作不可立即撤销，请在调用前向用户确认 - 删除文件夹时，文件夹内的所有子节点也会一并移入回收站
- Required top-level parameters: `nodeId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `nodeId` | yes | string | 文档标识 | 要删除的节点标识，支持以下格式：1) 文档/文件夹 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid}；2) dentryUuid（32 位字母数字字符串）。 |

### CLI flag overlay

- none

## dws doc block delete

- Canonical path: `doc.delete_document_block`
- Product: `doc`
- Group: `block`
- Subcommand: `delete`
- Title: 删除块元素
- Description: 在指定文档中删除指定块元素，需要提供文档ID(dentryUuid)与块元素ID(blockId)。操作受文档权限控制，仅当调用者拥有编辑权限时生效。
- Required top-level parameters: `nodeId`, `blockId`
- Sensitive flag from schema: `true`
- Mutation risk: `sensitive-mutating`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `blockId` | yes | string | 目标块 ID | 待删除块元素的唯一标识。可通过 list_document_blocks 工具查询获取（返回结果中的 blockId 字段）。目前仅支持根目录下的第一级块元素的 blockId。必填。 |
| `nodeId` | yes | string | 文档标识 | 目标文档的标识，支持传入文档链接 URL 或文档 ID（dentryUuid），系统自动识别。必填。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `blockId` | `--block-id` |  |  |  |  |
| `nodeId` | `--node` |  |  |  |  |

## dws doc download_doc_attachment

- Canonical path: `doc.download_doc_attachment`
- Product: `doc`
- Group: `-`
- Subcommand: `download_doc_attachment`
- Title: 下载指定钉钉文档中的指定附件
- Description: 获取钉钉文档中指定附件的临时下载 URL。 传入文档标识 nodeId 和附件资源标识 resourceId，返回附件的 OSS 临时下载链接 downloadUrl。 前置依赖：需先调用 list_document_blocks 获取文档块列表，从中找到 blockType 为 attachment 的块元素，提取其 resourceId 作为本工具的入参。
- Required top-level parameters: `nodeId`, `resourceId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-with-local-output`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `nodeId` | yes | string | 文档nodeId | 文档nodeId，支持传入文档链接 URL 或文档 ID（dentryUuid），系统自动识别。必填。 |
| `resourceId` | yes | string | 资源ID | 附件的资源ID |

### CLI flag overlay

- none

## dws doc download

- Canonical path: `doc.download_file`
- Product: `doc`
- Group: `-`
- Subcommand: `download`
- Title: 获取文件下载凭证
- Description: 获取文件下载凭证，供 AI Agent 自行发起 HTTP GET 请求下载文件内容。本工具是两步下载流程的第一步（也是唯一需要调用的 MCP Tool）。  完整下载流程：   Step 1：调用本工具，传入文件节点 ID（nodeId），获取下载 URL（resourceUrl）和签名请求头（headers）。   Step 2：AI Agent 自行发起 HTTP GET 请求：     - URL：使用 resourceUrl 中的第一个 URL（优先级最高）。     - Headers：将 headers 中所有键值对作为请求头携带。     - 期望响应：HTTP 200，Body 为文件二进制内容。     - 注意：下载凭证有过期时间（expirationSeconds），请在过期前完成下载；若已过期，需重新调用本工具获取新凭证。
- Required top-level parameters: `nodeId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-with-local-output`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `nodeId` | yes | string | 文件节点 ID | 支持两种格式：1) 文档链接 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid}；2) dentryUuid（32 位字母数字字符串）。必须指向文件节点（非文件夹）。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `nodeId` | `--node` |  |  |  |  |

## dws doc get_doc_attachment_upload_info

- Canonical path: `doc.get_doc_attachment_upload_info`
- Product: `doc`
- Group: `-`
- Subcommand: `get_doc_attachment_upload_info`
- Title: 获取向指定钉钉文档上传附件所需的 OSS 上传凭证信息
- Description: 获取向指定钉钉文档上传附件所需的 OSS 上传凭证信息。 使用流程： 1. 调用本 Tool，传入文档标识（nodeId）、文件名、文件大小和 MIME 类型，获取上传凭证。 2. 使用返回的 uploadUrl 通过 HTTP PUT 请求将文件内容上传至 OSS，需设置以下请求头：    - `Content-Type`：与入参 mimeType 保持一致，例如 `application/pdf`    - `Content-Length`：与入参 fileSize 保持一致，单位为字节 3. 上传成功后，使用返回的 resourceId 在文档中插入类型为附件的块元素。  注意事项： - uploadUrl 具有时效性，请在获取后尽快完成上传，避免凭证过期。 - fileSize 必须与实际文件大小一致，否则上传可能失败。 - mimeType 应与实际文件类型匹配，例如 PDF 文件传 application/pdf。
- Required top-level parameters: `fileName`, `fileSize`, `mimeType`, `nodeId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `fileName` | yes | string | 文件名 | 附件的文件名，包含扩展名，最大 300 个字符。必填。示例：report.pdf |
| `fileSize` | yes | number | 文件大小 | 附件的文件大小，单位为字节（Byte），必须大于 0。必填。示例：1048576（即 1MB） |
| `mimeType` | yes | string | MIME 类型 | 附件的 MIME 类型，必须是合法的 MIME 格式。必填。示例：application/pdf、image/png、text/plain |
| `nodeId` | yes | string | 文档nodeId | 文档nodeId，支持传入文档链接 URL 或文档 ID（dentryUuid），系统自动识别。必填。 |

### CLI flag overlay

- none

## dws doc read

- Canonical path: `doc.get_document_content`
- Product: `doc`
- Group: `-`
- Subcommand: `read`
- Title: 获取钉钉文档内容
- Description: 获取钉钉文档的内容，以 Markdown 格式返回。  通过 nodeId 定位文档，支持传入文档链接 URL 或文档 ID（dentryUuid），系统自动识别。  仅限用户对文档具备"可阅读"权限时可获取。不支持跨组织查询。  支持的文档类型：钉钉在线文档。 文档链接格式：https://alidocs.dingtalk.com/i/nodes/{dentryUuid} dentryUuid 为 32 位字母数字字符串。
- Required top-level parameters: `nodeId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `nodeId` | yes | string | 文档标识 | 目标文档的标识，支持两种格式：1) 文档链接 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid}；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `nodeId` | `--node` |  |  |  |  |

## dws doc info

- Canonical path: `doc.get_document_info`
- Product: `doc`
- Group: `-`
- Subcommand: `info`
- Title: 获取节点基本信息
- Description: 获取存储在钉钉文档（含知识库）、钉盘相关文件的元信息，包括文档标题、类型、创建者、创建时间等。 返回字段中的 contentType 和 extension 是后续工具路由的关键依据。  ⚠️ 重要：当用户需要获取任何钉钉文件的内容时，必须先调用本工具， 再根据返回的 contentType 和 extension 字段按以下规则选择对应工具：  - contentType=ALIDOC, extension=adoc  → 调用 get_document_content(nodeId)，返回 Markdown 内容 - contentType=ALIDOC, extension=axls  → 调用钉钉表格 MCP: get_all_sheets(nodeId) 获取工作表列表，再调用 get_range(nodeId, sheetId, range) 读取数据 - contentType=ALIDOC, extension=able  → 调用钉钉AI表格 MCP: get_tables(nodeId) 获取数据表列表（nodeId 即 baseId），再调用 query_records(nodeId, tableId) 查询记录 - contentType≠ALIDOC 且 nodeType=file → 调用 download_file(nodeId)，返回文件下载链接
- Required top-level parameters: `nodeId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-with-local-output`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `nodeId` | yes | string | 文档标识 | 目标文档的标识，支持传入 URL 或 ID。必填。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `nodeId` | `--node` |  |  |  |  |

## dws doc upload

- Canonical path: `doc.get_file_upload_info`
- Product: `doc`
- Group: `-`
- Subcommand: `upload`
- Title: 获取文件上传凭证
- Description: 获取将文件上传到钉钉文档或钉钉知识库所需的上传凭证。 本工具是三步上传流程的第一步：   第一步（本工具）：调用 get_file_upload_info，获取 OSS 上传地址和签名 headers。   第二步（HTTP PUT）：使用第一步返回的 resourceUrl 作为目标地址，发起 HTTP PUT 请求上传文件二进制内容。PUT 请求头需包含返回的 headers 中所有键值对，且 Content-Type 必须设置为空字符串（""）。PUT 请求必须返回 HTTP 200 才能继续。   第三步（下一个工具）：调用 commit_uploaded_file，传入本工具返回的 uploadKey，完成文件入库。  注意：必须在第二步 HTTP PUT 成功后，才能调用 commit_uploaded_file，不可跳过。
- Required top-level parameters: -
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `folderId` |  | string | 目标文件夹 ID | 目标文件夹的节点 ID（dentryUuid），支持传入文件夹链接 URL 或 ID。不传时：如果提供了 workspaceId 则上传到该知识库根目录下，否则上传到用户'我的文档'根目录下。folderId 优先级高于 workspaceId。 |
| `workspaceId` |  | string | 知识库 ID | 目标知识库的标识，支持知识库 ID 或知识库 URL。如果同时传了 folderId，则以 folderId 为准。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `folderId` | `--folder` |  |  |  |  |
| `workspaceId` | `--workspace` |  |  |  |  |

## dws doc block insert

- Canonical path: `doc.insert_document_block`
- Product: `doc`
- Group: `block`
- Subcommand: `insert`
- Title: 插入块元素
- Description: 将一个新的块元素插入指定文档，需要提供文档ID(dentryUuid)与块元素内容(必须包含 blockType 及对应类型的属性对象)。可以支持在指定块元素(传入blockId或index)的头部或尾部插入块元素。操作受文档权限控制，仅当调用者拥有编辑权限时生效。  【附件插入说明】 若 blockType 为 "attachment"： 1. 必须先调用 get_doc_attachment_upload_info 工具获取该附件的属性（resourceId）。 2. 将获取到的信息整合进 element 的 attachment 对象中，然后再调用此工具。  【get_doc_attachment_upload_info 接口使用说明】 获取向指定钉钉文档上传附件所需的 OSS 上传凭证信息。 使用流程： 1. 调用 get_doc_attachment_upload_info 接口，传入文档标识（nodeId）、文件名、文件大小和 MIME 类型，获取上传凭证。 2. 使用返回的 uploadUrl 通过 HTTP PUT 请求将文件内容上传至 OSS，需设置以下请求头：      Content-Type：与入参 mimeType 保持一致，例如 `application/pdf`      Content-Length：与入参 fileSize 保持一致，单位为字节 3. 上传成功后，使用返回的 resourceId 在文档中插入类型为附件的块元素。  注意事项： 1. uploadUrl 具有时效性，请在获取后尽快完成上传，避免凭证过期。 2. fileSize 必须与实际文件大小一致，否则上传可能失败。 3. mimeType 应与实际文件类型匹配，例如 PDF 文件传 application/pdf。
- Required top-level parameters: `nodeId`, `element`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `element` | yes | object | 块元素 | 待插入的块元素，必须包含 blockType 及对应类型的属性对象，详见块元素数据结构附录。最精简示例：{"blockType": "paragraph", "paragraph": {}}。 |
| `element.attachment` |  | object | attachment 元素内容 | 附件元素包含的属性，blockType 为 attachment 时必填。暂不支持 children。 |
| `element.attachment.name` |  | string | 资源名称 | 带文件后缀的资源名称 |
| `element.attachment.resourceId` |  | string | 资源 ID | 资源 ID |
| `element.attachment.size` |  | number | 文件大小 | 文件大小，单位是Byte |
| `element.attachment.type` |  | string | 资源类型 | 资源类型，String 类型的 MIME Type |
| `element.attachment.viewType` |  | string | 展示形式 | 附件的展示形式，可以选填 preview 或 summary |
| `element.blockType` | yes | string | 块元素类型 | 待插入的块元素类型：paragraph/heading/blockquote/callout/columns/orderedList/unorderedList/table/tableRow/tableCell/sheet/attachment/slot |
| `element.blockquote` |  | object | blockquote 元素内容 | 引用元素包含的属性，blockType 为 blockquote 时必填。 |
| `element.blockquote.indent` |  | object | 缩进 | 缩进值 |
| `element.blockquote.indent.left` |  | number | 左缩进 | 缩进值，必须是大于 0 的整数 |
| `element.blockquote.text` |  | string | 引用文本内容 | 引用里的文字 |
| `element.callout` |  | object | callout 元素内容 | 高亮块元素包含的属性，blockType 为 callout 时必填。children 为 BlockElement 数组。 |
| `element.callout.bgcolor` |  | string | 背景色 | 背景色 |
| `element.callout.border` |  | string | 边框颜色 | 边框颜色 |
| `element.callout.color` |  | string | 字色 | 字色 |
| `element.callout.showstk` |  | boolean | 是否显示表情 | 是否显示表情 |
| `element.callout.sticker` |  | string | 表情编码 | 表情编码，详见附录 D Emoji 枚举值 |
| `element.children` |  | array; items=object | 子元素 | 子元素 |
| `element.children[].elementType` |  | string | 行内元素类型 | 行内元素类型 |
| `element.children[].properties` |  | object | 行内元素属性 | 行内元素属性 |
| `element.children[].properties.src` |  | string | 图片元素 Url | 图片元素 Url |
| `element.columns` |  | object | columns 元素内容 | 分栏元素包含的属性，blockType 为 columns 时必填。children 为 BlockElement 数组。 |
| `element.columns.noFill` |  | boolean | 是否自动填充背景色 | 是否自动填充背景色 |
| `element.columns.size` |  | number | 分栏数量 | 分栏数量 |
| `element.heading` |  | object | heading 元素内容 | 标题元素包含的属性，blockType 为 heading 时必填。 |
| `element.heading.level` |  | number | 标题级别 | 标题级别，取值 1～6，1 表示一级标题 |
| `element.heading.text` |  | string | 标题文本内容 | 标题的文本内容 |
| `element.orderedList` |  | object | orderedList 元素内容 | 有序列表元素包含的属性，blockType 为 orderedList 时必填。 |
| `element.orderedList.indent` |  | object | 缩进 | 缩进值 |
| `element.orderedList.indent.left` |  | number | 左缩进 | 缩进值，必须是大于 0 的整数 |
| `element.orderedList.list` |  | object | 列表属性 | 有序列表的具体属性，详见附录 E ListObject |
| `element.orderedList.list.level` |  | number | 列表层级 | 列表层级（从 0 开始） |
| `element.orderedList.list.listId` |  | string | 列表 ID | 列表 ID；同组多级列表需保持 listId 一致 |
| `element.orderedList.list.listStyle` |  | object | 列表样式 | 列表样式，含 format、text、align |
| `element.orderedList.list.listStyle.align` |  | string | 对齐方式 | 对齐方式：left/center/right |
| `element.orderedList.list.listStyle.format` |  | string | 项目符号格式 | 项目符号格式 |
| `element.orderedList.list.listStyle.text` |  | string | 文本 | 文本 |
| `element.orderedList.list.listStyleType` |  | string | 列表样式类型 | 列表样式类型 |
| `element.orderedList.list.symbolStyle` |  | object | 列表符样式 | 列表符样式，含 sz/shd/fonts/color/bold/strike/italic |
| `element.orderedList.list.symbolStyle.bold` |  | boolean | 是否加粗 | 是否加粗 |
| `element.orderedList.list.symbolStyle.color` |  | string | 字体颜色 | 字体颜色 |
| `element.orderedList.list.symbolStyle.fonts` |  | string | 字体格式 | 字体格式 |
| `element.orderedList.list.symbolStyle.italic` |  | boolean | 是否斜体 | 是否斜体 |
| `element.orderedList.list.symbolStyle.shd` |  | string | 背景色 | 背景色 |
| `element.orderedList.list.symbolStyle.strike` |  | boolean | 是否删除线 | 是否删除线 |
| `element.orderedList.list.symbolStyle.sz` |  | number | 字体大小 | 字体大小 |
| `element.paragraph` |  | object | paragraph 元素内容 | 段落元素包含的属性，blockType 为 paragraph 时必填，内容为空时须传 {}。 |
| `element.paragraph.folded` |  | boolean | 是否折叠 | 是否折叠段落（折叠 indent 值比当前段落大的块元素） |
| `element.paragraph.indent` |  | object | 缩进 | 缩进值，left 必须是大于 0 的整数 |
| `element.paragraph.indent.left` |  | number | 左缩进 | 缩进值，必须是大于 0 的整数 |
| `element.paragraph.text` |  | string | 段落文本内容 | 段落的文本内容 |
| `element.table` |  | object | table 元素内容 | 表格元素包含的属性，blockType 为 table 时必填。暂不支持 children。 |
| `element.table.cells` |  | array; items=string | 单元格内容 | 单元格文本内容，二维 String 数组 |
| `element.table.cells[]` |  | string |  |  |
| `element.table.colSize` |  | number | 列数 | 列数 |
| `element.table.rolSize` |  | number | 行数 | 行数 |
| `element.unorderedList` |  | object | unorderedList 元素内容 | 无序列表元素包含的属性，blockType 为 unorderedList 时必填。 |
| `element.unorderedList.indent` |  | object | 缩进 | 缩进值 |
| `element.unorderedList.indent.left` |  | number | 左缩进 | 缩进值，必须是大于 0 的整数 |
| `element.unorderedList.list` |  | object | 列表属性 | 无序列表的具体属性，详见附录 E ListObject |
| `element.unorderedList.list.level` |  | number | 列表层级 | 列表层级（从 0 开始） |
| `element.unorderedList.list.listId` |  | string | 列表 ID | 列表 ID；同组多级列表需保持 listId 一致 |
| `element.unorderedList.list.listStyle` |  | object | 列表样式 | 列表样式，含 format、text、align |
| `element.unorderedList.list.listStyle.align` |  | string | 对齐方式 | 对齐方式：left/center/right |
| `element.unorderedList.list.listStyle.format` |  | string | 项目符号格式 | 项目符号格式 |
| `element.unorderedList.list.listStyle.text` |  | string | 文本 | 文本 |
| `element.unorderedList.list.listStyleType` |  | string | 列表样式类型 | 列表样式类型 |
| `element.unorderedList.list.symbolStyle` |  | object | 列表符样式 | 列表符样式，含 sz/shd/fonts/color/bold/strike/italic |
| `element.unorderedList.list.symbolStyle.bold` |  | boolean | 是否加粗 | 是否加粗 |
| `element.unorderedList.list.symbolStyle.color` |  | string | 字体颜色 | 字体颜色 |
| `element.unorderedList.list.symbolStyle.fonts` |  | string | 字体格式 | 字体格式 |
| `element.unorderedList.list.symbolStyle.italic` |  | boolean | 是否斜体 | 是否斜体 |
| `element.unorderedList.list.symbolStyle.shd` |  | string | 背景色 | 背景色 |
| `element.unorderedList.list.symbolStyle.strike` |  | boolean | 是否删除线 | 是否删除线 |
| `element.unorderedList.list.symbolStyle.sz` |  | number | 字体大小 | 字体大小 |
| `index` |  | number | 参照位置索引 | 当 referenceBlockId 未指定时，使用 index 查找文档第 index 个一级块元素作为参照位置（从 0 开始）。两者均未指定时，默认插入到文档末尾。 |
| `nodeId` | yes | string | 文档标识 | 目标文档的标识，支持传入文档链接 URL 或文档 ID（dentryUuid），系统自动识别。必填。 |
| `referenceBlockId` |  | string | 参照块 ID | 参照块的唯一标识（由 list_document_blocks 返回的 blockId）。指定后以该块为插入参照位置，优先级高于 index。 |
| `where` |  | string | 插入方向 | 插入方向：before（参照位置之前）或 after（参照位置之后）。默认为 after。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `element` | `--element` | json_parse |  |  |  |
| `index` | `--index` |  |  |  |  |
| `nodeId` | `--node` |  |  |  |  |
| `referenceBlockId` | `--ref-block` |  |  |  |  |
| `where` | `--where` |  |  |  |  |

## dws doc block list

- Canonical path: `doc.list_document_blocks`
- Product: `doc`
- Group: `block`
- Subcommand: `list`
- Title: 查询指定钉钉文档下的一级块元素列表
- Description: 查询指定钉钉文档下的一级块元素列表，需要提供dentryUuid。支持按按起始位置、终止位置范围及块类型过滤。操作受文档权限控制，当调用者拥有编辑或查看或下载权限时生效。
- Required top-level parameters: `nodeId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `blockType` |  | string | 块类型过滤 | 按块类型过滤。不传时返回所有类型。 |
| `endIndex` |  | number | 终止位置 | 终止位置（≥ 0 的整数）。表示查询到根节点 children 的第 endIndex 个块为止（含）。不传时默认查询到末尾。 |
| `nodeId` | yes | string | 文档标识 | 目标文档的标识，支持两种格式：1) 文档链接 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid}；2) 文档 ID（dentryUuid），32 位字母数字字符串。系统自动识别格式。 |
| `startIndex` |  | number | 起始位置 | 起始位置（≥ 0 的整数）。表示从根节点 children 的第 startIndex 个块开始查询。不传时默认从头开始。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `blockType` | `--block-type` |  |  |  |  |
| `endIndex` | `--end-index` |  |  |  |  |
| `nodeId` | `--node` |  |  |  |  |
| `startIndex` | `--start-index` |  |  |  |  |

## dws doc list

- Canonical path: `doc.list_nodes`
- Product: `doc`
- Group: `-`
- Subcommand: `list`
- Title: 列出指定文件夹或知识库下的直接子节点列表
- Description: 列出指定文件夹或知识库下的直接子节点列表（文件夹/文档/文件等），支持分页。  返回结果基于当前 MCP 会话用户的可访问权限进行过滤。 返回的节点列表包含多种类型：在线文档、文件夹、PDF、docx、表格、脑图、白板等。  定位方式（按优先级）： 1. 传 folderId：列出该文件夹下的直接子节点 2. 仅传 workspaceId（不传 folderId）：列出该知识库根目录下的直接子节点 3. 都不传：列出用户"我的文档"根目录下的直接子节点  返回的 nodeId 可直接用于其他工具（如 get_document_content、update_document、create_document 等）。 若 nodeType=folder 且 hasChildren=true，可将该 nodeId 作为 folderId 再次调用本工具进行递归遍历。 若 contentType=alidoc，可调用 get_document_content(nodeId=nodeId) 获取文档内容。
- Required top-level parameters: -
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `folderId` |  | string | 文件夹 ID | 要遍历的文件夹的节点 ID（dentryUuid），支持传入文件夹链接 URL 或 ID。不传时：如果提供了 workspaceId 则列出该知识库根目录，否则列出用户'我的文档'根目录。 |
| `pageSize` |  | number | 每页数量 | 每页返回的节点数量，默认 50，最大 50。 |
| `pageToken` |  | string | 分页标记 | 分页游标，从上一次请求的返回结果中获取 nextPageToken。首次请求不传。 |
| `workspaceId` |  | string | 知识库 ID | 知识库 ID。当需要遍历某个知识库时使用。如果同时传了 folderId，则以 folderId 为准。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `folderId` | `--folder` |  |  |  |  |
| `pageSize` | `--page-size` |  |  |  |  |
| `pageToken` | `--page-token` |  |  |  |  |
| `workspaceId` | `--workspace` |  |  |  |  |

## dws doc list_permission

- Canonical path: `doc.list_permission`
- Product: `doc`
- Group: `-`
- Subcommand: `list_permission`
- Title: 查询节点成员列表
- Description: 查询知识库节点当前的成员权限列表。
- Required top-level parameters: `nodeId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `filterRoleIds` |  | array; items=string | 角色过滤 | 按角色过滤成员列表，选填。取值：OWNER、MANAGER、EDITOR、DOWNLOADER、READER。不传时返回所有角色的成员。 |
| `filterRoleIds[]` |  | string |  |  |
| `maxResults` |  | number | 返回成员数上限 | 期望返回的最大成员条数，默认 30，最大 200。底层一次性返回全量成员后在内存中按本字段截断；本接口不支持游标翻页，不存在 nextToken。若发生截断（出参 truncated=true），可通过出参 totalCount 感知全量成员数，并通过 filterRoleIds 收窄查询范围。 |
| `nodeId` | yes | string | 节点标识 | 目标节点的 nodeId。 |
| `workspaceId` |  | string | 知识库标识 | 目标知识库的标识，选填。仅用于辅助构造返回的 docUrl，业务实际依赖 nodeId 定位节点；支持两种格式：1) 知识库 ID（纯字符串）；2) 知识库 URL，如 https://alidocs.dingtalk.com/i/spaces/{workspaceId}/overview，系统自动提取其中的 workspaceId。 |

### CLI flag overlay

- none

## dws doc move

- Canonical path: `doc.move_document`
- Product: `doc`
- Group: `-`
- Subcommand: `move`
- Title: 移动节点
- Description: 将指定节点移动到目标文件夹。  支持的节点类型：知识库节点（文档、文件夹）、钉盘文件/文件夹。 nodeId 支持文档 URL 或 dentryUuid（32 位字母数字字符串）。 targetFolderId 为目标文件夹的 dentryUuid；workspaceId 为目标知识库标识，不传 targetFolderId 时移动到该知识库根目录，如果不传 targetFolderId 和 workspaceId，默认到当前用户所在组织的「我的文档」下  权限要求： - 对源节点有编辑权限 - 对目标文件夹有写入权限  注意：移动操作底层可能异步执行，异步时操作已提交但无法立即确认完成，请稍后查看目标文件夹确认结果。
- Required top-level parameters: `nodeId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `nodeId` | yes | string | 源节点标识 | 要移动的节点标识，支持以下格式：\n1. 文档/文件夹 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid}\n2. dentryUuid（32 位字母数字字符串） |
| `targetFolderId` |  | string | 目标文件夹 ID | 目标文件夹的 dentryUuid（32 位字母数字字符串），节点将被移动到此文件夹下。 |
| `workspaceId` |  | string | 知识库 ID | 目标知识库的标识，支持两种格式：1) 知识库 ID（纯字符串）；2) 知识库 URL，如 https://alidocs.dingtalk.com/i/spaces/{workspaceId}/overview，系统自动提取其中的 workspaceId。当 targetFolderId 不传时，移动到该知识库的根目录下。如果同时传了 targetFolderId，则以 targetFolderId 为准。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `nodeId` | `--node` |  |  |  |  |
| `targetFolderId` | `--folder` |  |  |  |  |
| `workspaceId` | `--workspace` |  |  |  |  |

## dws doc query_export_job

- Canonical path: `doc.query_export_job`
- Product: `doc`
- Group: `-`
- Subcommand: `query_export_job`
- Title: 查询文档导出任务状态
- Description: 用于查询通过 submit_export_job 提交的文字文档导出任务的执行状态，任务完成时返回文件下载链接。
- Required top-level parameters: `jobId`
- Sensitive flag from schema: `false`
- Mutation risk: `read-with-local-output`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `jobId` | yes | string | 导出任务 ID | 由 submit_export_job 返回的导出任务 ID。必填。 |

### CLI flag overlay

- none

## dws doc rename

- Canonical path: `doc.rename_document`
- Product: `doc`
- Group: `-`
- Subcommand: `rename`
- Title: 对指定节点进行重命名
- Description: 对指定节点进行重命名。  支持范围： - 知识库（workspace）下的文档节点、文件夹节点  权限要求： - 对目标节点有编辑权限
- Required top-level parameters: `newName`, `nodeId`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `newName` | yes | string | 文档新名称 | 重命名后的新名称，不能为空，长度不超过 255 个字符。 |
| `nodeId` | yes | string | 文档标识 | 要重命名的节点标识，支持以下格式：1) 文档/文件夹 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid}；2) dentryUuid（32 位字母数字字符串）。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `newName` | `--name` |  |  |  |  |
| `nodeId` | `--node` |  |  |  |  |

## dws doc search

- Canonical path: `doc.search_documents`
- Product: `doc`
- Group: `-`
- Subcommand: `search`
- Title: 搜索文档
- Description: 根据关键词和多种过滤条件搜索当前用户有权限访问的文档列表。  支持的过滤条件： - keyword：关键词匹配文档标题和内容 - extensions：按文件扩展名精确过滤（pdf/docx/png 等） - createdTimeFrom / createdTimeTo：按创建时间范围过滤 - visitedTimeFrom / visitedTimeTo：按访问时间范围过滤 - creatorUserIds：按创建者用户 ID 过滤     - editorUserIds：按编辑者用户 ID 过滤 - mentionedUserIds：按文档中 @提及的用户 ID 过滤 - workspaceIds：按知识库范围过滤（支持知识库 URL）  支持分页获取结果。搜索范围为用户在当前组织内有权限访问的所有文档数据。 所有过滤条件均为可选，不传则不过滤。多个条件同时传入时取交集
- Required top-level parameters: -
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `createdTimeFrom` |  | number | 创建时间起始 | 创建时间范围的起始时间（毫秒时间戳，含） |
| `createdTimeTo` |  | number | 创建时间截止 | 创建时间范围的截止时间（毫秒时间戳，含） |
| `creatorUserIds` |  | array; items=string | 创建者用户 ID 列表 | 按创建者过滤，传入用户 ID 列表 |
| `creatorUserIds[]` |  | string |  |  |
| `editorUserIds` |  | array; items=string | 编辑者用户 ID 列表 | 按编辑者过滤，传入用户 ID 列表 |
| `editorUserIds[]` |  | string |  |  |
| `extensions` |  | array; items=string | 文件扩展名过滤 | 按文件扩展名精确过滤，不含点号。例如 ["pdf", "docx", "png"]。与 fileTypes 可同时使用。 |
| `extensions[]` |  | string |  |  |
| `keyword` |  | string | 搜索关键词 | 搜索关键词，匹配文档标题和内容。不传则返回最近访问的文档列表。 |
| `mentionedUserIds` |  | array; items=string | @提及的用户 ID 列表 | 按文档中 @提及的用户过滤 |
| `mentionedUserIds[]` |  | string |  |  |
| `pageSize` |  | number | 每页数量 | 每页返回的文档数量，默认 10，最大 30 |
| `pageToken` |  | string | 分页标记 | 分页游标，从上一次请求的返回结果中获取 nextPageToken |
| `visitedTimeFrom` |  | number | 访问时间起始 | 访问时间范围的起始时间（毫秒时间戳，含） |
| `visitedTimeTo` |  | number | 访问时间截止 | 访问时间范围的截止时间（毫秒时间戳，含） |
| `workspaceIds` |  | array; items=string | 知识库 ID 列表 | 按知识库范围过滤，支持知识库 URL |
| `workspaceIds[]` |  | string |  |  |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `createdTimeFrom` | `--created-from` |  |  |  |  |
| `createdTimeTo` | `--created-to` |  |  |  |  |
| `creatorUserIds` | `--creator-uids` | csv_to_array |  |  |  |
| `editorUserIds` | `--editor-uids` | csv_to_array |  |  |  |
| `extensions` | `--extensions` | csv_to_array |  |  |  |
| `keyword` | `--query` |  |  |  |  |
| `mentionedUserIds` | `--mentioned-uids` | csv_to_array |  |  |  |
| `pageSize` | `--page-size` |  |  |  |  |
| `pageToken` | `--page-token` |  |  |  |  |
| `visitedTimeFrom` | `--visited-from` |  |  |  |  |
| `visitedTimeTo` | `--visited-to` |  |  |  |  |
| `workspaceIds` | `--workspace-ids` | csv_to_array |  |  |  |

## dws doc submit_export_job

- Canonical path: `doc.submit_export_job`
- Product: `doc`
- Group: `-`
- Subcommand: `submit_export_job`
- Title: 导出在线文档
- Description: 提交钉钉在线文档导出任务，将文档导出为 Office docx 文件（目前仅支持导出 docx 格式，未来会扩展）。
- Required top-level parameters: `nodeId`, `exportFormat`
- Sensitive flag from schema: `false`
- Mutation risk: `read-with-local-output`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `exportFormat` | yes | string | 导出格式 | 导出的目标文件格式（必填）。文档侧仅支持 'docx'（导出为 Office docx）；不传将返回 invalidRequest.argument.illegal 错误，传入其他值将返回 invalidRequest.export.unsupportedFormat 错误。 |
| `nodeId` | yes | string | 文档标识 | 要导出的文档标识（必填）。支持两种格式：1) 文档链接 URL，如 https://alidocs.dingtalk.com/i/nodes/{dentryUuid}；2) dentryUuid（32 位字母数字字符串）。仅支持钉钉在线文档类型。 |

### CLI flag overlay

- none

## dws doc update

- Canonical path: `doc.update_document`
- Product: `doc`
- Group: `-`
- Subcommand: `update`
- Title: 更新钉钉文档的内容
- Description: 更新钉钉文档的内容，扩展名=adoc，其他类型在线文档不能使用此mcp服务，支持两种更新模式。  ## 更新模式  1. **overwrite**（覆盖，默认）：清空文档全部内容后重新写入。适用于需要完整替换文档内容的场景。⚠️ 会丢失原有内容，请谨慎使用。 2. **append**（追加）：在文档末尾追加新内容。最安全的模式，不影响现有内容。    支持通过 index 参数指定插入位置（从 0 开始）；不传 index 时追加到末尾。    block 的 index 可通过 list_document_blocks 工具获取。    插入成功后，该位置及之后的所有 block 的 index 会依次 +1。  ## 注意事项 - 默认模式为 overwrite，不传 mode 时使用覆盖模式 - overwrite 模式会清空所有内容，包括图片、评论等，请谨慎使用
- Required top-level parameters: `nodeId`, `markdown`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `index` |  | number | 插入位置 | 插入位置（从 0 开始），仅在 mode=append 时生效。指定将内容插入到文档第几个 block 之前。不传时追加到末尾。block 的 index 可通过 list_document_blocks 工具获取。插入成功后，该位置及之后的所有 block 的 index 会依次 +1。 |
| `markdown` | yes | string | Markdown 内容 | 要写入的 Markdown 内容，必填。overwrite 模式下为完整的新文档内容，append 模式下为追加的内容。 |
| `mode` |  | string | 更新模式 | 更新模式，默认 overwrite。可选值：overwrite（覆盖全文）、append（追加到末尾）。 |
| `nodeId` | yes | string | 文档标识 | 目标文档的标识，支持传入文档链接 URL 或文档 ID（dentryUuid），系统自动识别。必填。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `content` | `--content` |  |  |  |  |
| `contentFile` | `--content-file` | file_read |  |  |  |
| `mode` | `--mode` |  |  |  |  |
| `nodeId` | `--node` |  |  |  |  |

## dws doc block update

- Canonical path: `doc.update_document_block`
- Product: `doc`
- Group: `block`
- Subcommand: `update`
- Title: 更新块元素
- Description: 更新指定文档的指定块元素内容或样式，需要提供文档ID(dentryUuid)、块元素ID(blockId)与块元素内容(必须包含 blockType 及对应类型的属性对象)。操作受文档权限控制，仅当调用者拥有编辑权限时生效。
- Required top-level parameters: `nodeId`, `blockId`, `element`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `blockId` | yes | string | 目标块 ID | 待删除块元素的唯一标识。可通过 list_document_blocks 工具查询获取（返回结果中的 blockId 字段）。目前仅支持根目录下的第一级块元素的 blockId。必填。 |
| `element` | yes | object | 块元素 | 待插入的块元素，必须包含 blockType 及对应类型的属性对象，详见块元素数据结构附录。最精简示例：{"blockType": "paragraph", "paragraph": {}}。 |
| `element.attachment` |  | object | attachment 元素内容 | 附件元素包含的属性，blockType 为 attachment 时必填。不支持 children。 |
| `element.attachment.name` |  | string | 文件名称 | 带后缀的文件名称 |
| `element.attachment.resourceId` |  | string | 资源 ID | 资源 ID |
| `element.attachment.size` |  | number | 文件大小 | 文件大小（字节） |
| `element.attachment.type` |  | string | 文件 MIME Type | 文件 MIME Type |
| `element.attachment.viewType` |  | string | 展现形式 | 展现形式：preview（预览卡片）或 summary（摘要） |
| `element.blockType` | yes | string | 块元素类型 | 待插入的块元素类型：paragraph/heading/blockquote/callout/columns/orderedList/unorderedList/table/tableRow/tableCell/sheet/attachment/slot |
| `element.blockquote` |  | object | blockquote 元素内容 | 引用元素包含的属性，blockType 为 blockquote 时必填。 |
| `element.blockquote.indent` |  | object | 缩进 | 缩进值 |
| `element.blockquote.indent.left` |  | number | 左缩进 | 缩进值，必须是大于 0 的整数 |
| `element.blockquote.text` |  | string | 引用文本内容 | 引用里的文字 |
| `element.callout` |  | object | callout 元素内容 | 高亮块元素包含的属性，blockType 为 callout 时必填。children 为 BlockElement 数组。 |
| `element.callout.bgcolor` |  | string | 背景色 | 背景色 |
| `element.callout.border` |  | string | 边框颜色 | 边框颜色 |
| `element.callout.color` |  | string | 字色 | 字色 |
| `element.callout.showstk` |  | boolean | 是否显示表情 | 是否显示表情 |
| `element.callout.sticker` |  | string | 表情编码 | 表情编码，详见附录 D Emoji 枚举值 |
| `element.children` |  | array; items=object | 子元素列表 | 子元素列表，选填 |
| `element.children[].text` |  | string | 子元素文本内容 | 子元素文本内容 |
| `element.columns` |  | object | columns 元素内容 | 分栏元素包含的属性，blockType 为 columns 时必填。children 为 BlockElement 数组。 |
| `element.columns.noFill` |  | boolean | 是否自动填充背景色 | 是否自动填充背景色 |
| `element.columns.size` |  | number | 分栏数量 | 分栏数量 |
| `element.heading` |  | object | heading 元素内容 | 标题元素包含的属性，blockType 为 heading 时必填。 |
| `element.heading.level` |  | number | 标题级别 | 标题级别，取值 1～6，1 表示一级标题 |
| `element.heading.text` |  | string | 标题文本内容 | 标题的文本内容 |
| `element.orderedList` |  | object | orderedList 元素内容 | 有序列表元素包含的属性，blockType 为 orderedList 时必填。 |
| `element.orderedList.indent` |  | object | 缩进 | 缩进值 |
| `element.orderedList.indent.left` |  | number | 左缩进 | 缩进值，必须是大于 0 的整数 |
| `element.orderedList.list` |  | object | 列表属性 | 有序列表的具体属性，详见附录 E ListObject |
| `element.orderedList.list.level` |  | number | 列表层级 | 列表层级（从 0 开始） |
| `element.orderedList.list.listId` |  | string | 列表 ID | 列表 ID；同组多级列表需保持 listId 一致 |
| `element.orderedList.list.listStyle` |  | object | 列表样式 | 列表样式，含 format、text、align |
| `element.orderedList.list.listStyle.align` |  | string | 对齐方式 | 对齐方式：left/center/right |
| `element.orderedList.list.listStyle.format` |  | string | 项目符号格式 | 项目符号格式 |
| `element.orderedList.list.listStyle.text` |  | string | 文本 | 文本 |
| `element.orderedList.list.listStyleType` |  | string | 列表样式类型 | 列表样式类型 |
| `element.orderedList.list.symbolStyle` |  | object | 列表符样式 | 列表符样式，含 sz/shd/fonts/color/bold/strike/italic |
| `element.orderedList.list.symbolStyle.bold` |  | boolean | 是否加粗 | 是否加粗 |
| `element.orderedList.list.symbolStyle.color` |  | string | 字体颜色 | 字体颜色 |
| `element.orderedList.list.symbolStyle.fonts` |  | string | 字体格式 | 字体格式 |
| `element.orderedList.list.symbolStyle.italic` |  | boolean | 是否斜体 | 是否斜体 |
| `element.orderedList.list.symbolStyle.shd` |  | string | 背景色 | 背景色 |
| `element.orderedList.list.symbolStyle.strike` |  | boolean | 是否删除线 | 是否删除线 |
| `element.orderedList.list.symbolStyle.sz` |  | number | 字体大小 | 字体大小 |
| `element.paragraph` |  | object | paragraph 元素内容 | 段落元素包含的属性，blockType 为 paragraph 时必填，内容为空时须传 {}。 |
| `element.paragraph.folded` |  | boolean | 是否折叠 | 是否折叠段落（折叠 indent 值比当前段落大的块元素） |
| `element.paragraph.indent` |  | object | 缩进 | 缩进值，left 必须是大于 0 的整数 |
| `element.paragraph.indent.left` |  | number | 左缩进 | 缩进值，必须是大于 0 的整数 |
| `element.paragraph.text` |  | string | 段落文本内容 | 段落的文本内容 |
| `element.sheet` |  | object | sheet 元素内容 | 电子表格元素包含的属性，blockType 为 sheet 时必填。不支持 children。 |
| `element.sheet.headerVisible` |  | boolean | 是否显示表头 | 是否显示表头，默认 true |
| `element.sheet.maxHeight` |  | number | 最大高度 | 最大高度 |
| `element.sheet.sheetId` |  | string | Sheet ID | 单个 sheet 的 id |
| `element.sheet.workbookId` |  | string | 电子表格 ID | 电子表格的 workbookId |
| `element.slot` |  | object | slot 元素内容 | 插槽元素包含的属性，blockType 为 slot 时必填。 |
| `element.slot.markdownPlaceholder` |  | string | Markdown 占位内容 | Markdown 插槽的占位内容 |
| `element.slot.slotType` |  | string | 插槽类型 | 插槽类型：table（表格插槽）或 markdown（Markdown 插槽） |
| `element.slot.tableHeader` |  | array; items=string | 表头数组 | 表头数组，每项含 key 和 value |
| `element.slot.tableHeader[]` |  | string |  |  |
| `element.slot.tableName` |  | string | 表格名称 | 表格名称 |
| `element.table` |  | object | table 元素内容 | 表格元素包含的属性，blockType 为 table 时必填。暂不支持 children。 |
| `element.table.cells` |  | array; items=string | 单元格内容 | 单元格文本内容，二维 String 数组 |
| `element.table.cells[]` |  | string |  |  |
| `element.table.colSize` |  | number | 列数 | 列数 |
| `element.table.rolSize` |  | number | 行数 | 行数 |
| `element.unorderedList` |  | object | unorderedList 元素内容 | 无序列表元素包含的属性，blockType 为 unorderedList 时必填。 |
| `element.unorderedList.indent` |  | object | 缩进 | 缩进值 |
| `element.unorderedList.indent.left` |  | number | 左缩进 | 缩进值，必须是大于 0 的整数 |
| `element.unorderedList.list` |  | object | 列表属性 | 无序列表的具体属性，详见附录 E ListObject |
| `element.unorderedList.list.level` |  | number | 列表层级 | 列表层级（从 0 开始） |
| `element.unorderedList.list.listId` |  | string | 列表 ID | 列表 ID；同组多级列表需保持 listId 一致 |
| `element.unorderedList.list.listStyle` |  | object | 列表样式 | 列表样式，含 format、text、align |
| `element.unorderedList.list.listStyle.align` |  | string | 对齐方式 | 对齐方式：left/center/right |
| `element.unorderedList.list.listStyle.format` |  | string | 项目符号格式 | 项目符号格式 |
| `element.unorderedList.list.listStyle.text` |  | string | 文本 | 文本 |
| `element.unorderedList.list.listStyleType` |  | string | 列表样式类型 | 列表样式类型 |
| `element.unorderedList.list.symbolStyle` |  | object | 列表符样式 | 列表符样式，含 sz/shd/fonts/color/bold/strike/italic |
| `element.unorderedList.list.symbolStyle.bold` |  | boolean | 是否加粗 | 是否加粗 |
| `element.unorderedList.list.symbolStyle.color` |  | string | 字体颜色 | 字体颜色 |
| `element.unorderedList.list.symbolStyle.fonts` |  | string | 字体格式 | 字体格式 |
| `element.unorderedList.list.symbolStyle.italic` |  | boolean | 是否斜体 | 是否斜体 |
| `element.unorderedList.list.symbolStyle.shd` |  | string | 背景色 | 背景色 |
| `element.unorderedList.list.symbolStyle.strike` |  | boolean | 是否删除线 | 是否删除线 |
| `element.unorderedList.list.symbolStyle.sz` |  | number | 字体大小 | 字体大小 |
| `nodeId` | yes | string | 文档标识 | 目标文档的标识，支持传入文档链接 URL 或文档 ID（dentryUuid），系统自动识别。必填。 |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `blockId` | `--block-id` |  |  |  |  |
| `element` | `--element` | json_parse |  |  |  |
| `nodeId` | `--node` |  |  |  |  |

## dws doc update_permission

- Canonical path: `doc.update_permission`
- Product: `doc`
- Group: `-`
- Subcommand: `update_permission`
- Title: 变更节点成员角色
- Description: 修改企业用户在知识库节点上的角色（仅支持 USER 类型）。  通过传入 userIds 列表批量调整成员角色，同一成员在同一节点只能拥有一个角色，变更后旧角色自动替换。  注意事项： - OWNER 角色不可通过此接口变更。 - 若成员的角色来自父节点的权限继承（PASS_ON 模式），且继承的角色高于目标角色，接口会拒绝操作。 - **权限要求（业务规则）**：操作者必须在该节点上具备「可编辑（EDITOR）」及以上角色（OWNER / MANAGER / EDITOR）。 - 单次请求 `userIds` 列表最多传 30 个，超出需分批调用。 - 入参直接采用扁平的 `userIds: List<String>`，每个元素为钉钉 staffId（外部 userId 格式，由钉钉开放平台体系下颁发）。
- Required top-level parameters: `nodeId`, `roleId`, `userIds`
- Sensitive flag from schema: `false`
- Mutation risk: `mutating-review-first`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `nodeId` | yes | string | 节点标识 | 目标节点的 nodeId。 |
| `roleId` | yes | string | 变更后的角色 | 变更后的角色，取值：MANAGER、EDITOR、DOWNLOADER、READER。 |
| `userIds` | yes | array; items=string | 用户 userId 列表 | 要变更角色的用户 userId 列表（钉钉 staffId / 外部 userId 格式，由钉钉开放平台体系下颁发；通常为数字字符串），至少包含一个 userId，单次最多 30 个。如果你只有 unionId，请先调用钉钉开放平台「根据 unionId 获取 userId」接口换取 userId。 |
| `userIds[]` |  | string |  |  |
| `workspaceId` |  | string | 知识库标识 | 目标知识库的标识，选填。仅用于辅助构造返回的 docUrl，业务实际依赖 nodeId 定位节点；支持两种格式：1) 知识库 ID（纯字符串）；2) 知识库 URL，如 https://alidocs.dingtalk.com/i/spaces/{workspaceId}/overview，系统自动提取其中的 workspaceId。 |

### CLI flag overlay

- none
