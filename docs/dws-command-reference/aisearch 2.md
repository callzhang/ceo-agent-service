# dws aisearch Commands

AI 搜问：按姓名/工号/手机号/部门/职责/上下级等维度搜人

Commands in this file: 5

## Command Index

| CLI | Canonical path | Risk |
| --- | --- | --- |
| [`dws aisearch person`](#dws-aisearch-person) | `aisearch.enterprise_person_search` | `read-only` |
| [`dws aisearch search_enterprise`](#dws-aisearch-searchenterprise) | `aisearch.search_enterprise` | `read-only` |
| [`dws aisearch search_enterprise_behavior`](#dws-aisearch-searchenterprisebehavior) | `aisearch.search_enterprise_behavior` | `read-only` |
| [`dws aisearch search_enterprise_group`](#dws-aisearch-searchenterprisegroup) | `aisearch.search_enterprise_group` | `read-only` |
| [`dws aisearch search_enterprise_help_center`](#dws-aisearch-searchenterprisehelpcenter) | `aisearch.search_enterprise_help_center` | `read-only` |

## Group Summary

| Group | Commands |
| --- | ---: |
| `-` | 5 |

## dws aisearch person

- Canonical path: `aisearch.enterprise_person_search`
- Product: `aisearch`
- Group: `-`
- Subcommand: `person`
- Title: enterprise_person_search
- Description: 搜索企业人员（支持按姓名/部门/职位/职责/上下级/手机号/工号筛选）
- Required top-level parameters: `dimension`, `keyword`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `dimension` | yes | array; items=string; enum=supervisor/subordinate/name/department/position/duty/all/phone/jobNumber | 查询类型 | 查询维度，根据用户意图自动映射。可选值：\n- all: 默认，全部维度\n- name: 姓名（用户提到'姓名'、'叫什么'、'是谁'时使用）\n- department: 部门（用户提到'部门'、'团队'时使用）\n- position: 职位（用户提到'职位'、'岗位'时使用）\n- duty: 职责/技能（用户提到'负责'、'职责'、'技能'时使用）\n- supervisor: 上级（用户提到'上级'、'领导'、'主管'时使用）\n- subordinate: 下级（用户提到'下级'、'下属'时使用）\n- phone: 手机号（用户提到'手机号'、'电话'、'手机'时使用）\n- jobNumber: 工号（用户提到'工号'、'员工编号'时使用）\n\n【重要】：从用户问题中识别维度词并映射到此字段，不要将维度词放入keyword中。 |
| `dimension[]` |  | string; enum=supervisor/subordinate/name/department/position/duty/all/phone/jobNumber |  |  |
| `keyword` | yes | string | 查询语句 | 搜索关键词。仅填入实际的搜索目标（人名、技能关键词等），不包含查询维度词。 【关键词提取规则】： 以下维度词必须从keyword中排除，改为映射到dimension： - '上级'、'领导'、'主管' → dimension: ['supervisor'] - '下级'、'下属'、'团队成员' → dimension: ['subordinate'] - '部门'、'团队'、'组织' → dimension: ['department'] - '职位'、'岗位'、'职级' → dimension: ['position'] - '职责'、'负责'、'技能' → dimension: ['duty'] - '姓名'、'叫什么' → dimension: ['name'] 【示例】： - 用户问'五道的上级是谁' → keyword: '五道', dimension: ['supervisor'] - 用户问'张三负责什么' → keyword: '张三', dimension: ['duty'] - 用户问'AI搜问的负责人是谁' → keyword: 'AI搜问', dimension: ['duty'] - 用户问'产品部有谁' → keyword: '产品部', dimension: ['department'] - 用户问'李四是哪个部门的' → keyword: '李四', dimension: ['department']- 用户问'手机号13800138000是谁' → keyword: '13800138000', dimension: ['phone']\n- 用户问'工号A12345是谁' → keyword: 'A12345', dimension: ['jobNumber'] |

### CLI flag overlay

| Parameter path | CLI flag | Transform | Default | Env default | Extra |
| --- | --- | --- | --- | --- | --- |
| `dimension` | `--dimension` | csv_to_array | all |  |  |
| `keyword` | `--keyword` |  |  |  |  |

## dws aisearch search_enterprise

- Canonical path: `aisearch.search_enterprise`
- Product: `aisearch`
- Group: `-`
- Subcommand: `search_enterprise`
- Title: search_enterprise
- Description: 企业内部知识搜索工具。用于检索企业内部的文档、消息、日程、会议纪要、工作日志、多维表、企业百科等知识内容，基于关键词语义匹配返回相关资料。支持自然语言时间表达式（如"最近"、"上周"、"本季度"）。适用场景：查找某个主题的资料、搜索包含特定关键词的文档、了解某项目/产品相关信息、准备汇报材料等内容检索需求。
- Required top-level parameters: `timeRange`, `queries`, `searchTypes`
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `queries` | yes | array; items=string | queries | 搜索关键词列表，只放**内容关键词**。 **排除规则**： - 时间信息 放到 timeRange 字段 - 类型关键词（日志/文档/消息/会议/日程/纪要等） 放到 searchTypes 字段 - 汇总类词汇（工作总结/日报总结/周报总结/月报总结） 不作为关键词，通过searchTypes=['all']触发全量搜索 示例： - '2025-12-06 ~ 2025-12-19工作总结' queries=[]（时间走timeRange，工作总结触发all类型） - '智能化方案' queries=['智能化方案'] |
| `queries[]` |  | string |  |  |
| `searchTypes` | yes | array; items=string; enum=all/document/im/calendar/minute/report/notable/baike/mail | searchTypes | 搜索类型列表，支持同时搜索多种类型。可选值：all-全部, document-文档, im-消息, calendar-日程, todo-待办, minute-会议纪要/闪记/听记, report-日志, image-图片, link-链接, notable-多维表/ai表格, baike-百科, mail-邮件。 **特殊规则**： 1. report类型仅在用户query中**显式且仅限出现'日志'**一词时触发，'周报/日报/月报/工作汇报'等不触发report 2. mail类型仅在用户query中**显式出现'邮件/邮箱/mail/email'**等词汇时触发 3. '工作总结'、'日报总结'、'周报总结'、'月报总结'等汇总类场景，需要聚合所有工作内容，应使用['all'] 示例： - '本周的日志' searchTypes=['report'] - '我收到的邮件' searchTypes=['mail'] - '工作总结' searchTypes=['all']（需汇总所有内容） - '周报总结' searchTypes=['all']（需汇总所有内容） - 不填或['all'] 搜索全部类型，默认是['all'] |
| `searchTypes[]` |  | string; enum=all/document/im/calendar/minute/report/notable/baike/mail |  |  |
| `timeRange` | yes | string | timeRange | 时间范围。**提取规则**：仅当用户query中**显式出现**时间词汇时才填写，否则**必须留空**。**可识别的时间词汇**：- 相对时间：今天、昨天、本周、上周、这周、本月、上个月、最近、近期- 具体时间：9月、10月份、Q3、本季度、上半年、2024年- 时间范围：过去一周、最近三天**重要**：不要根据语义推测时间。如果用户没有使用上述时间词汇，即使语境暗示是近期，也**不填写**此字段。**示例**：- '本周的OKR文档' → timeRange='本周'- '9月的项目方案' → timeRange='9月'- '智能化相关文档' → timeRange不填写（无时间词）,默认不填写 |

### CLI flag overlay

- none

## dws aisearch search_enterprise_behavior

- Canonical path: `aisearch.search_enterprise_behavior`
- Product: `aisearch`
- Group: `-`
- Subcommand: `search_enterprise_behavior`
- Title: search_enterprise_behavior
- Description: 业内部行为记录搜索工具。用于检索用户的操作行为记录，如文档的发送、创建、分享、编辑等历史操作，支持按人员、动作类型进行精确查询。适用场景："我给某人发过什么"、"我创建过哪些文档"、"某人发给我的消息"、"我分享过的资料"等行为追溯需求。与知识搜索的区别：本工具关注"谁对什么做了什么"，而非内容本身。
- Required top-level parameters: -
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `behaviorType` |  | string; enum=all/send/create/share/edit/receive | behaviorType | 行为类型（可选）：all-全部, send-发送, create-创建, share-分享, edit-编辑, receive-接收 |
| `chatScope` |  | string | chatScope | 消息所在的会话/群范围（可选），仅用于IM类型搜索。当用户指定群名/会话名时填写，系统会自动搜索群获取cid。 **填写条件**：用户query中出现具体群名/会话名 **不填写情况**：无明确会话范围时留空 **示例**： - '我在scrum群里发了什么' chatScope='scrum群' - '产品群里讨论了什么' chatScope='产品群' - '我发给汐峰的消息' chatScope不填写（用direction处理人际关系） - '我今天发了什么消息' chatScope不填写（无具体群名） |
| `direction` |  | string | direction | 交互方向（可选），仅当用户query中**明确指定了交互对象（人名）**时填写。**格式**：'发起者->接收者' 或 '双向交互者<->另一方'，'我'代表当前用户。**填写条件**：用户query中必须出现具体人名/对象**不填写情况**：无明确交互对象时留空，由behaviorType控制行为筛选**重要**：direction描述的是**内容流向**（谁发给谁），而非谁执行了动作。当用户没有指定具体人名时，即使behaviorType有方向性（如receive/send），也**不要**填写direction，更**不要**使用'我->*'或'*->我'这类通配符形式。**示例**：- '我发给汐峰的消息'  direction='我->汐峰'- '汐峰发给我的文档'  direction='汐峰->我'- '我和汐峰的聊天记录'  direction='我<->汐峰'- '帮我总结今天干了什么'  direction不填写（无具体对象）- '我今天发的消息'  direction不填写（无具体对象）- '我接受了哪些日程'  direction不填写（无具体对象，用behaviorType=receive筛选）- '我创建了哪些文档'  direction不填写（无具体对象，用behaviorType=create筛选） |
| `queries` |  | array; items=string | queries | 搜索关键词列表，只放**内容关键词**。 **排除规则**： - 时间信息 放到 timeRange 字段 - 类型关键词（日志/文档/消息/会议/日程/纪要等） 放到 searchTypes 字段 - 汇总类词汇（工作总结/日报总结/周报总结/月报总结） 不作为关键词，通过searchTypes=['all']触发全量搜索 示例： - '2025-12-06 ~ 2025-12-19工作总结' queries=[]（时间走timeRange，工作总结触发all类型） - '智能化方案' queries=['智能化方案'] |
| `queries[]` |  | string |  |  |
| `searchTypes` |  | array; items=string; enum=all/document/im/calendar/minute/report/notable/baike/mail | searchTypes | searchTypes |
| `searchTypes[]` |  | string; enum=all/document/im/calendar/minute/report/notable/baike/mail |  |  |
| `timeRange` |  | string | timeRange | 时间范围。**提取规则**：仅当用户query中**显式出现**时间词汇时才填写，否则**必须留空**。**可识别的时间词汇**：- 相对时间：今天、昨天、本周、上周、这周、本月、上个月、最近、近期- 具体时间：9月、10月份、Q3、本季度、上半年、2024年- 时间范围：过去一周、最近三天**重要**：不要根据语义推测时间。如果用户没有使用上述时间词汇，即使语境暗示是近期，也**不填写**此字段。**示例**：- '本周的OKR文档'  timeRange='本周'- '9月的项目方案'  timeRange='9月'- '智能化相关文档'  timeRange不填写（无时间词），默认不填写 |

### CLI flag overlay

- none

## dws aisearch search_enterprise_group

- Canonical path: `aisearch.search_enterprise_group`
- Product: `aisearch`
- Group: `-`
- Subcommand: `search_enterprise_group`
- Title: search_enterprise_group
- Description: 在企业内搜索群聊。支持按群名搜索，或查找与指定成员的共同群。 适用场景：找某个项目群、找与某人的共同群、搜索技术交流群等。
- Required top-level parameters: -
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `dimension` |  | array; items=string; enum=all/name/member | 查询维度 | 查询维度。可选值：all(默认)、name(按群名搜索，queries填群名关键词)、member(按成员找共同群，keywords填成员姓名)。例如：['name'] |
| `dimension[]` |  | string; enum=all/name/member |  |  |
| `keywords` |  | array; items=string | 查询关键字 | 搜索关键词列表。根据 dimension 决定含义：当 dimension 为 name 时，填入群名关键词，如['项目群']；当 dimension 为 member 时，填入成员姓名，如用户问'我和张三的共同群'则填['张三'] |
| `keywords[]` |  | string |  |  |
| `offset` |  | string | 分页偏移量 | 分页偏移量，从0开始，用于翻页。例如 pageSize=10 时，offset=0 取第1页，offset=10 取第2页 |
| `pageSize` |  | string | 结果数量 | 每页返回的结果数量，默认10条 |

### CLI flag overlay

- none

## dws aisearch search_enterprise_help_center

- Canonical path: `aisearch.search_enterprise_help_center`
- Product: `aisearch`
- Group: `-`
- Subcommand: `search_enterprise_help_center`
- Title: search_enterprise_help_center
- Description: 帮助中心问答。基于企业帮助中心知识库进行问答匹配，返回产品使用帮助、操作指南等内容。适用于用户询问产品功能使用方法、操作流程、功能介绍等场景，如：如何使用钉钉审批、怎么发起会议、如何创建日程，怎么切换主组织等。
- Required top-level parameters: -
- Sensitive flag from schema: `false`
- Mutation risk: `read-only`

### Parameters and subparameters

| Parameter path | Required here | Type / enum / constraints | Title | Description |
| --- | --- | --- | --- | --- |
| `query` |  | string | 字段名 | 用户问题，描述需要查询的帮助内容 |

### CLI flag overlay

- none
