# Agent 优化最终验收对账

核查起始：2026-10-24；最近补证：2026-10-25 19:22 PDT，America/Los_Angeles。当前G2状态见第17节；此前带日期的API阻塞、未部署及旧门禁失败均保留为历史记录。

**整体结论：G2／PR325已按当前Codex CLI范围验收关闭；整轮尚未全部完成。** 生产fba909db9已包含修复，进程加载和生产回读已核实。用户已取消所有已配置路线均须验收的要求，API诊断不再是本轮前提。G1修复后的自然非重复写入行为仍独立待验收；其他既有验收与历史失败按原记录保留，不相互替代。

## 1. 基准、范围与状态口径

- 本轮起始远端 main/隔离开发 HEAD：`8f35fb3ef52fb991c85cad7d492fbf64d6799ac5`；起始生产：`7a6caf762265003d6ae33cd8d5e738c87fab75b8`，生产源码工作树干净。
- 核查期间其他会话发布并重启；本轮只读核对，未启动部署或干预进程。开发随后快进至 `878cfd723ebfb290e510b3a1c3028b1d6bc2a389`，生产也已到该版本。本文文档提交不需要重启服务。
- 当前公共预览声明四条路线：`codex_oauth/gpt-6-luna`、`codex_api/gpt-5.6-luna`、`qwen_gpu4/qwen3.8-27b`、`claude_oauth/sonnet`。出现在配置中不代表路线已通过验收。
- 状态仅使用：**已验收、已上线待行为验收、未完成、证据不足、已取消**。已验收项仍说明其限定范围，不推及所有模型、任务或未来配置。
- 首次对账未改运行代码、prompt、Skill、权限、Audit、重试或 session 策略，未调用模型；后续按用户“继续完成plan”授权补了匿名只读原生探针和DWS版本化重装，详见第9节。两阶段均未写真实文档、对外发送或重放生产任务。只读数据库连接使用 URI `mode=ro` 和 `query_only`，未构造生产 Store。

## 2. 六项目标逐项对账

| 目标 | 当前结论 | 已成立的部分 | 缺口及最小收尾动作 |
| --- | --- | --- | --- |
| 指令精简与按需注入 | **已验收**，限定为装配机制与已完成的固定对照 | 通用 Developer/角色/Profile 与每轮 Task 分开；任务流程由选定 Skill 承载，目录不预载依赖正文。原六场景有相同条件的前后对照和反馈闭环；后续 Consumer 原生 schema 去重也有独立验证。当前页面和预览仍使用分段来源。 | 不等于当前全部文字已最短、每条常识已删净，也不承诺当前任意任务的输入或费用都减少。当前业务变化的验证见第5项，不再为“更短”扩展改动。 |
| Settings 输入可追溯 | **已验收** | 当前 UI 解释各 prompt 的用途和拼接关系；模板渲染、公共预览、任务绑定及历史输入有明确区分。本轮三个历史 API 输入与对应原生记录逐字节相等，两份任务绑定输入可定位到保存的 run。 | 本轮无新增缺口。历史缺失的配置细分与渲染时间仍明确未记录；公共预览不冒充实际执行或完整模型上下文。 |
| session 与续跑 | **已上线待行为验收**，限最新文档格式修复后的非重复写入条件 | Consumer 按会话、路线、选定 Skill 和输出契约条件复用；Audit 有同任务/代次续会话。已有自然案例证明工件保留、反馈修订和最终投递；文档交接修复与“写入即成功”已发布。 | 服务回执修改已上线。续做已确认dev构建触发每日升级并恢复带SemVer元数据的DWS补丁；第14节已找到7次写入成功/核验失败自然回执，语义交接通过；仍有3组同目标同正文再次写入，非重复行为尚不能验收。第15节已按用户确认的钉钉Markdown语义修复核验、验证显式字面版本并安装；服务成功语义已验收，最新修复后的自然非重复行为待首个相关样本。 |
| 原生结构化输出 | **已验收**，限定当前Codex CLI本机OAuth/gpt-6-luna | PR325已合并并由生产fba909db9加载；Consumer/Audit冷/续、工具读取、严格解析及16项接续/恢复验证证据复用；实际进程/源码/schema/生产回读已补齐。 | G2已关闭，详见第17节。API及其他模型路线不属于本轮前提；旧全套测试/lint问题保留独立维护记录，不改标通过。 |
| 任务事实与业务行为 | **已验收**，限定当前OAuth原六个固定场景及反馈闭环 | 第10节以当前角色装配、当前schema、原事实/判断标准补7个阶段，保留首轮部分通过及夹具排除。跨午夜补取边界证据后闭环；工作跟踪收紧Skill绑定后通过。 | 不等于各路线业务覆盖、历史A/B重新复现或生产文档写入恢复；对应缺口分别保留于G2、G1。 |
| 发布与实际收益 | **已上线待行为验收** | 已有多个正式部署回读；文档修复在当前生产祖先链上。相同固定输入下的服务字符缩减有实测，见第6节。 | 已上线与字符减少不证明重复覆盖已停止，也不证明整体 token、费用或墙钟提速。G1/G3 关闭相应业务缺口；没有测得的收益保持“未测得”，不新增性能实验作为门槛。 |

### 指令装配与固定对照的证据

- [任务指令按需注入验收](task-scoped-prompts-validation.md)：实现审查 `adb23e1b`、测试修正 `ba3d5415`；联合70项、完整定时36项、前端85通过2跳过及发布器29项，分组有重叠，不能相加。匿名固定清单为 `evals/task_scoped_prompts/v1.json`。
- 原14次模型结果全部通过 Pydantic，候选初审6通过/1部分通过；Audit 最初漏报一个字段缺陷的事实保留。后续直接使用真实反馈完成 Consumer 修订和同 Audit session 精确 digest/revision 审核，没有用改 prompt 覆盖原失败。
- [冻结 Skill 续会话验证](frozen-skill-session-reuse-validation.md)记录 `dd9b587e` 的正式发布及当前/历史预览回读；它不证明自动压缩后完整内容永远可见。
- [Consumer 原生契约去重](consumer-native-schema-prompt-validation.md)只移除已有原生输出通道的重复 wire 文本，不移除严格业务校验。当前 `consumer_developer_sections_by_runtime` 仍按路线装配；Task 专属内容通过现有任务元数据和 Skill 入口提供。

## 3. Settings 本轮直接核对

生产 GET `/api/console/settings/prompts` 的保存 Developer 模板，经现有渲染函数得到的文本与 API `preview.developer` 完全一致，`preview_errors` 为空。Consumer/Audit 共享 Developer 与 Profile，只有 Consumer 使用 User 模板。

当前公共预览为 `mode=current/status=available/submission_state=preview`，明确未绑定任务、未运行或验证认证。一次快照 Consumer Developer 29,741字符、Audit 39,438字符，Task 均为24字符的未绑定说明；这些不是一次真实任务的完整输入基线，不能与不同版本/任务的历史长度直接相减。Consumer 的工作人格分段8,532字符、共同原则625字符、能力边界4,996字符；Audit 包含9,368字符的审核规则。长度用于解释来源，不用作必要性或质量评分。

三份历史输入经对应 runtime attempt 的原生读取核对，Developer、Task、submitted_input 均与生产 API 脱敏后的文本相等：

| run / attempt | Developer 字符 / SHA-256 | Task 字符 / SHA-256 | 来源结论 |
| --- | --- | --- | --- |
| 398794 / 398795 | 919 / `0e7daacc076ff478be3eb915c7adc6a446caf8ccad79d6bbb9cfa2b266a950d5` | 6,285 / `4c9098355cf820f7d7e8c5556ad37450b761dfd454f9cbceb3e815e88a62f22d` | 本轮原生 Developer/Task，`invoked` |
| 398797 / 398798 | 451 / `f722b5a3c453ad2bff4205c3a49381baf65271a3460adbef88565bf5151110dd` | 3,297 / `d31387e71e73d4010165b42b762415bac8e1ef228ff856ffc1ccbc3a95478d9a` | 本轮原生 Developer/Task，`invoked` |
| 396024 / 396025 | 29,319 / `effcb1f23a8f11c79e68bc63d8136011c3ce7a1bf470bbcce16b6000a5046b1c` | 5,101 / `d5c0db5ce0c66b9875c4f1728a4cb41e4d7905d586eb3487a433e8e4876941a8` | Developer 明确为 session 已保存内容，非本轮重新提交回执 |

任务绑定预览417384引用保存 run398794、Task6,285字符；417176引用 run396135、Task6,332字符，均标 `preview`，不冒充历史调用。以上数字对应不同调用，不构成缩减对照。

本轮还实际打开生产 Settings → Prompts：可见用途/顺序说明、Developer/User/Profile/运行输入入口；Rendered preview 明确只渲染保存模板。进入运行输入后，Consumer/codex_oauth/gpt-6-luna 公共预览加载完成，可见分段来源与顺序、未绑定任务和服务输入范围提示。仅切换只读页面，没有保存设置。历史 UI 的完整交互与五份跨 Codex/Claude 原生输入比对复用[历史输入发布验收](native-prompt-history-validation.md)，发布提交 `29e01567`。

## 4. session、文档恢复与自然运行

- [Consumer 契约兼容](consumer-wire-session-compatibility-validation.md)：`c4f33241` 已发布；自然 OA 任务416047的386421→386436→386446沿同一兼容 session 修订，两次后续 Task 独立提交，没有重复读取 Skill；最终 Audit approve。不据此声称外部动作成功。
- [Audit 续会话](audit-revision-session-continuity-validation.md)：`b07bd987` 已发布，同任务/代次查找可续 session，最新不可续则新建；匿名原生冷启动/修订通过，现有自然业务结果未在该记录中验收。
- [通用续跑核查](scheduled-continuation-closure-verification.md)：完整15个原生范围证明任务417176保留51/51来源工件、两次反馈进入新修订，最终服务回执有 provider `sendStatus=SUCCESS`；来源、修订和发送三项成立。Consumer396024的62次文档写入包含52次已写入但核验失败，11组产生24次额外相同内容覆盖，此缺口不能由最终 done 隐藏。
- [文档修复验收](document-write-recovery-validation.md)：服务交接修复 `3638ea4a7`，成功语义 `b5028e7ef` 均已上线。确认写入即 `ok=true/status=success`，核验/读回失败独立保留 `verified=false`、目标和原始错误；未执行或提交未知仍报错。不生成虚假成功读回、不增加工具内部写重试。
- DWS 原始样本51处比较差异中，50处来自 Markdown 导出省略对齐，1处为文本片段空格；完整 JSONML 仍有47处右对齐。修复要求完整 JSONML 证明对齐后再比较正文，真实缺失仍失败。历史上于06:25 UTC安装 v1.0.65 + `87ddde41`，114项文档模块顶层测试及子场景、原始离线样本与只读实际 JSONML 回读通过。**这仅是历史安装事实，不是当前仍生效的证据。** 上游PR1551仍未合并；本机源码修复不冒充官方发行版。

**首次对账发现（07:11 UTC快照；续做修复见第9节）：DWS补丁当时不在运行路径。** 当前 `/Users/derek/.local/bin/dws --version` 为官方 `v1.0.65 (92c4f8c3...)`，SHA-256 为 `1d832e4fe77a8746e3546de026ffd3d21b0eaddcfcdea7f2027543989af83dd8`，与修复前原版一致，不是补丁的 `ec9a6d35f01cbc13ac5ae0fcc8608b282b807e2d135643c3c3963dcf669cf92d`。在内存中解析主进程88147的PATH，实际 `dws` 也解析至该文件；未输出完整环境或凭据。历史安装 receipt 仍记录06:25:06 UTC的补丁摘要，因此必须更正“DWS修复当前仍生效”的推断。替换发生时间、执行者和原因**未查实**；代码存在每日升级入口不等于证明本次由它替换。未重装二进制、修改升级策略或触发任务。

**本轮只读截止快照：**从2026-10-24 06:54:46 UTC（10月23日23:54:46 PDT）至2026-10-24 07:11:29 UTC（10月24日00:11:29 PDT）本轮数据库查询，新增 Agent run 为0，没有可读的修复后自然文档写入调用。因此服务回执的自然行为仍为“已上线待行为验收”，同时G1增加DWS补丁当前未保持生效的实现发布缺口。没有以正常写入、服务健康或本地 fake DWS 测试替代失败恢复生产验收，也未人工触发报告。

## 5. 当前路线与业务证据边界

当前 `app/agent_turn_runner.py::_native_output_schema_for_route` 为 Codex Consumer 选择原生 schema；Codex Audit 仅 `local_oauth` 选择原生 schema，service API Audit 返回 None。Claude 使用自己的原生投影及后续完整 Pydantic 校验。此处是代码事实，不是对未验证 API 路线的兼容承诺。

| 路线 | 已有证据 | 本轮只读的最近运行元数据 | 仍不能声称 |
| --- | --- | --- | --- |
| codex_oauth / gpt-6-luna | [Consumer](native-consumer-output-validation.md)有完整general/OA冷启动和续会话、假只读合同工具调用；[Audit](audit-native-output-validation.md)有return/approve冷启动和续会话 | Consumer398794/attempt398795、Audit398797/398798均completed，时间06:10 UTC，早于本次文档发布 | 当前所有业务场景、所有后续模型契约变化都已覆盖；Audit无工具探针也不能算带工具验收 |
| codex_api / gpt-5.6-luna | 旧记录有429与传输失败，未取得所需完整矩阵 | Consumer378951/attempt378956、Audit386851/386903，最近状态superseded，failure_code均`codex_transport_disconnected` | 此刻仍429或其根因已查实；原生schema及工具兼容已经通过 |
| qwen_gpu4 / qwen3.8-27b | 旧provider grammar/工具诊断与离线结果；Audit原生schema探针未通过 | Consumer392271/392272、Audit392269/392270均completed，时间10月22日08:50–08:51 UTC | 普通完成记录证明当前完整原生schema；API Audit 当前实际上未选择原生schema |
| claude_oauth / sonnet | 原生投影、冷启动/续会话及严格解析有阶段性证据；历史输入有真实回读 | Consumer388579/388586与Audit391716/391727均completed，分别10月21日/22日 | 所有当前schema、工具及固定业务标准的统一矩阵已完成 |

结果模型和 Task 来源读取已有后续变更：例如 `c0382cbb2` 移除服务记忆队列/相关输出字段；`878cfd723` 更新 Task 来源读取。当前本地Consumer general原生schema SHA为 `72ee10446972dde404f738df395701188a39d9d5e10005312a66fec3b11e9d7b`，Audit为 `ca522ed193ce692fae5a06177240203e4a3c0cf803b93b7e977382eebacd6937`。本轮85项定向测试证明当前装配、schema编解码、写入回执与来源投影的确定性行为；不将旧模型探针改标为这些当前字节的原生验收。

原固定案例中，跨午夜/时区、真实冲突、已处理邮件、文档冲突、已有工作事项和Audit反馈已有受控证据；[原任务按需指令验收](task-scoped-prompts-validation.md)明确真实完整轨迹仅确认日历，403份输入快照不等于四类生产轨迹。本轮增加了跨角色历史输入、已有定时轨迹与来源投影验证，仍不足以宣布当前四类任务/所有配置路线的业务判断全面无退化。

## 6. 可量化收益与取消项

| 对照 | 实测 | 可以支持什么 |
| --- | --- | --- |
| 原六个冷启动固定场景，相同事实/配置/模型 | 服务 Developer+Task字符215,702→158,018，减少26.74% | 该固定矩阵的提交文本减少；不是当前任意任务的token、缓存、费用或耗时减少 |
| Meeting PR132固定对照 | 每次减少17,415字符，约42%；18次原生运行，候选9/9业务检查通过，基线7/9 | [Meeting范围精简](meeting-managed-skill-scope-validation.md)及`01fbd2ff`发布成立；小样本不证明普遍质量提升 |
| Consumer原生schema重复文本 | 当时Developer减少13,116字符 | [原生契约去重](consumer-native-schema-prompt-validation.md)的历史同实现对照；当前模型已变，不能把该旧绝对值当作最新输入计量 |

当前生产总体 token、缓存/费用节省、端到端提速及节省的重复写入耗时：**未测得**。不把基线24次额外覆盖换算成节省时间，不把不同任务/版本的输入长度相减，不增加“必须测出整体提速”这个额外完成门槛。

| 已取消/范围外事项 | 决定与定位 | 不作为遗留的原因 |
| --- | --- | --- |
| Codex harness 自动压缩及阈值机制 | 用户明确属于harness；[原通用续跑spec](superpowers/specs/2026-10-23-scheduled-continuation-closure-design.md)排除 | 不开发项目自己的压缩机制，不再追查未确认自动阈值作为本轮门槛 |
| PR125 Task跨轮省略完整semantic context | PR已CLOSED且未merged；[no-go记录](trajectory-optimization-validation.md#task-semantic-context-reuse-draft-no-go-2026-10-18)31组相邻完成运行0组完整上下文指纹相同 | 候选没有通过收益/可用性前提，已撤回；不得重新激活。已独立发布的Consumer精确反馈修订机制不是该撤回Task候选 |
| 依赖闭包预注入/整段历史已在session所以省略 | [按需指令验收](task-scoped-prompts-validation.md)拒绝41条/约1.45万字符闭包；[跨轮省略门槛](trajectory-optimization-validation.md#cross-turn-omission-gate-previous-input-is-not-a-durable-evidence-store-2026-10-19)明确no-go | 不为证明“优化”而复活被否决方案；session相同不证明完整规则或事实仍可见 |
| DWS上游合并/官方发布 | 外部仓库[PR1551](https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli/pull/1551)仍OPEN；用户明确我们只提PR | 由维护者处理；本机修复的运行/业务验收独立完成，不重复请求上游合并权限 |

PR31本轮核实仍OPEN/draft、未merged。它不是“已取消”，也不是已发布的Consumer/Audit分阶段修复的替代证据；是否关闭草稿不作为产品行为验收门槛。

## 7. 有限遗留与终点

当前仅G1自然行为验收仍开放；G2已在第17节关闭，G3既有验收继续有效。以下按组对账，不自动执行新优化：

1. **G1 文档写入恢复——格式核验修复已发布，待自然行为验收。** 第15节完成原始样本/显式字面版本、严格负例、审查、DWS PR更新及本机版本化安装。服务成功语义此前已用7次自然返回验收。安装后单次查询截至2026-10-25 22:40:12 UTC没有新增Consumer/Audit Run；只在首个自然相关运行中核对新版本DWS回执与后续行为，不重放、不无限轮询。通过条件是该机制不再造成相同内容无效覆盖；不存在样本时明确保持待验收。
2. **G2 当前Codex CLI原生输出——已验收并关闭，详见第17节。** 原“所有已配置路线”要求已由用户明确缩减；PR325已合并，生产已加载。API历史诊断、旧全套测试和lint不再列为本轮G2遗留，历史事实继续保留。
3. **G3 当前事实与判断覆盖——当前OAuth固定案例已验收，详见第10节。** 以下保留原补证动作及范围： 对原四类任务逐项标明已有完整输入/工具返回/结果与其版本；优先复用当前可读取的保存轨迹。仅对后续输入/模型变化确实使旧证据失效的场景，复用原固定事实与判断标准补匿名对照；保持两臂同模型/事实/工具，检查时区/跨午夜、冲突、已处理事项、来源/引用及反馈修订。可与G2共用同一有效运行，不能重复调用。不得只看退出码/JSON，也不修改原始失败评分。通过条件是缺失场景有对应事实与业务判断证明，不要求遍历所有任务或制造生产动作。

本次**对账交付**的终点为：六项均有结论和证据、取消项分开、文档项查询更新、此文提交主线。原始**整轮优化**仅在G1–G3的有效要求均关闭，或用户明确缩减原目标后，才能宣布完成。以后只更新这份对账的缺口与对应证据，不自动开始其他trajectory优化。

## 8. 首次对账验证与发布记录

- 在隔离开发工作树运行 `pytest -q tests/test_prompt_composition.py tests/test_native_consumer_output.py tests/test_native_audit_output.py tests/test_document_write_recovery.py tests/test_task_source_documents.py --tb=short`：**85 passed，5.89秒**。这是当前确定性补证，没有重跑全量测试或已通过的模型实验。
- Settings生产模板渲染等值、两个公共角色预览、两个任务绑定预览、三个历史原生输入对照与只读页面核对均通过；正文/Work Profile/凭据未复制进本文，只有长度、身份和摘要。
- 文档修复生产加载与PID/健康/队列回读沿用[文档发布回读](document-write-recovery-validation.md#写入成功语义的生产发布回读2026-10-24-0656-utc)，本轮额外核实生产前进后仍包含修复；不是新部署。
- 07:11:29–07:11:31 UTC最终运行快照：生产`878cfd723`、主进程PID88147，health/status/Attention/History均HTTP200；Attention9、History37931。Reply/Attempt失败均13、Meeting失败0、Meeting Memory失败9；邮件provider pending113/processing1。仅记录观察，不推断历史计数变化归因。其后核实实际DWS为原版摘要，见第4节。文档提交不冒充运行版本发布。

## 9. 继续执行G1–G3（2026-10-24，PDT）

### G1：根因已确认，版本化补丁已恢复；自然业务验收仍待

07:00:02 UTC的升级State明确记录 `dev -> v1.0.65 / needs_upgrade=true`，07:00:09结束每日升级流程，与原版二进制回读一致。上次发布错误在于将默认dev开发构建作为运行修复包；没有依据修改服务自动升级策略。沿用同一补丁提交87ddde41，用原生版本/commit/build-time链接参数构建 `v1.0.65+ceo-jsonml.87ddde41`，于07:56:20 UTC原子安装，runtime payload及签名通过。新SHA为 `4efec8a5d6b80a34f98df6dc917497166050a3e6c89307556db3e905b0da7ad6`，服务实际PATH回读一致。原生升级检查明确返回 `needs_upgrade=false`，既有SemVer比较/NeedsUpgrade测试通过；未锁未来版本、关闭升级或覆盖Skill。详细receipt和验证见[文档恢复记录](document-write-recovery-validation.md)。

新增自然Consumer398975/attempt398976、Audit398977/398978均completed。完整读取57行和46行原生范围，SHA分别为 `0e8cace07cb002d4582579b4851c7ee18b9355ec07d3dad888452566777babb4`、`ca1575c00324e38bf3d4a95724e0928b99b7fc6e21f83a781db5db45c6f59947`。两轮读取Task Skill、消息及系统动作合同，没有consumer_document_write。因此打包及当前运行修复已完成，仍不能宣布重复覆盖生产验收通过；不人工触发文档任务。

### G2：补齐OAuth通道证据；API/Qwen有明确阻塞

使用当前完整Consumer general原生schema（72ee1044...）和Audit schema（ca522ed1...）、配置模型gpt-6-luna、空Work Profile、匿名已处理提醒事实和唯一假只读消息MCP。采用实际角色命令构造器，测试额外关闭shell/network以隔离生产；无业务MCP或真实发送入口。Consumer最终有效组使用当前服务Developer装配器（18,187字符、SHA `be60b20748a609b369bb850ce0f8d0d0baf08b4840c0dd9f0ba60bda6bde1b87`）；Audit为最小角色/契约通道检查，不冒充完整生产审核规则评测。

| 原生证据 | 结果与边界 | 完整原生记录摘要 |
| --- | --- | --- |
| OAuth Consumer `01a16cf9-864c-7b11-8a2b-5e3982577263` | 冷启动和同session续会话各读假当前来源1次，均no_action；精确来源ref和完整当前Pydantic通过，无重复提醒 | 45行，SHA `dd159059404a29b9b3e9911607bde1f62c0937abfe9835c1aae996819616834a` |
| OAuth Audit `01a16cf2-63d7-7720-b89d-a9b58d436ef9` | 冷/续各读假来源1次，均approve、来源ref正确；按现有接口绑定revision0和candidate digest `645a9882576e7baa747935ab73a77dbc4a0c696db306a52177de701ce82bee06` 后完整解析通过 | 49行，SHA `2470451a1159c0bd751e772cc07c559fae4e785d461aca10d9726b275d81f647` |
| API Consumer `01a16ced-528c-7391-9efd-d83145a8b17b` | 当前schema冷启动429；原session实际补发一次continue仍429，0工具调用。没有有效冷启动，续会话不能标通过；未循环重试或改路由 | 19行，SHA `751ae66445d0ede7bb13b60ff719a0423d810f172db4a32278bc509a57cd9021` |
| API Audit `01a16ced-618e-7222-9cf0-3f9383081930` | 当前完整schema隔离冷启动429，0工具调用；未启用生产API Audit原生schema | 10行，SHA `6325758afdc863f6706cecf0440c47711a72ef2c1fc775fdf32072ce8e120566` |
| Qwen Audit `01a16cf2-854b-7e22-82f3-83f6eac0fcaa` | 实际Audit角色工具控制/完整schema下，100秒重复假消息读取93次，无最终结果；读回均完整，问题位于取到来源后的结果生成，具体内部原因未查实。不续跑、不启用生产schema | 660行，SHA `df6add3e9b9c52b574e1dae32dfedecad638e0f798c9d740b5f04b40e370443f` |

另做一次Qwen相同Developer/Task（SHA分别 `4e35cbcd7a58d6d2a78673f26b6e23c5287543ca9093420d53632eec1193162c` / `62f9f092e4717cfa665a43390b023224f64e0ef9acedee7e60d191865d320077`）的关闭原生schema控制。模型结束并产生文字，但出现未提供工具调用，文字不满足完整Audit wire解析；不是可发布降级方案，也不能认定唯一根因。控制session `01a16cff-76ec-72d2-8a92-d96bcf5fa5c5`共48行，SHA `9cf4558315144f679a7dfdc73e71ca5cc9b975fa8526f9e5df8ad8bba5d0582a`。两臂服务输入逐字节相等，原生时钟/运行环境各自记录；单次控制不作普遍质量因果结论。

保留诊断边界：两个OAuth配置探针因测试给未定义provider添加retry选项而在模型启动前失败；一个Consumer探针重复skip-git标志，一个API resume先被不可信目录拦住，均不计路线/model结果。最初最小Consumer提示的输出因no_action附带decision_basis被完整业务校验拒绝，不计当前完整Developer业务退化；改用真实角色装配后取得有效组。Audit冷结果最初因评测回调漏传revision/digest报告TypeError，从同一份未改原生结果补齐正确调用参数解析，没有模型重跑。早期非角色Qwen诊断暴露额外get_goal调用，只作环境边界，不与实际Audit角色93次读取混计。未增加prompt规则、宽松解析、fallback、权限或生产路线变更；临时夹具已清理。

API恢复后才能补缺失格，429不作为schema根因。Qwen下一步是完整请求、工具返回与输出终止的provider/模型收尾定位，保留完整schema及工具读取标准；没有猜测修改共享GPU服务。Claude沿用现有原生Consumer投影和严格解析证据，本轮未重跑，也不额外要求其Audit新增原生schema实现。

### G3：原固定事实保留，完整原始输入复查仍有缺口

原六场景事实/标准仍在 `evals/task_scoped_prompts/v1.json`，独立结果和失败评分保留，未修改旧分数。`role_contract_matrix.results.v2.json` 的7个candidate阶段，本机JSONL均不可定位；旧私有冻结目录 `task-scoped-prompts-20261008` 不存在。未仅凭文件查找声称历史不存在：原生分页读取mail案例 `01a128a3-0cb6-7f73-b425-62c56a7ad5dd` 明确返回 `invalid paginated history lineage ... missing source rollout`，证明该入口当前不能还原此案例原始输入，不推及全部旧会话。

当前OAuth已处理事实的冷/续会话可以复用，但不能代替原日历、文档冲突与已有工作事项。G3仍证据不足。若其余冻结材料不可恢复，最小下一步是基于已版本化原事实/标准重新冻结当前角色和假工具输入，仅验证受影响的剩余场景；不能伪称重现原Profile/旧版本A/B，不重跑生产任务或新增性能实验。API和Qwen也不能因OAuth通过而关闭。

集成主线时复用新增的[日历时区转换验收](calendar-timezone-conversion-validation.md)：包含DST fold、明确UTC时刻后的同session续跑、匹配的Audit格式比较，以及保留首轮失败的证据。该记录明确使用隔离fixture/home，不证明生产装配或原上海跨午夜/真实冲突两例；只补充所验证的转换/续跑语义，不把它作为原六例的替代或重新运行理由。

### 续做收尾快照

01:32:55–01:32:58 PDT只读回读：生产已由其他会话推进到 `f89ee9f28`，主进程36721运行；health/status/Attention/History均HTTP200，Attention0、History37943。主进程实际PATH仍解析到本次版本化DWS，SHA等于 `4efec8a5...`。这些状态不归因于本次匿名探针，也不替代文档样本。

生产native输出转换、Consumer native schema、Audit schema、Consumer装配、AgentTurnRunner和wire模型与本次探针工作树逐字节相同；角色命令helper新增AI表格/日历只读工具及AI表格输出上限，未改动本探针使用的假消息读取或输出schema。本次只证明所声明假工具范围的原生通道，不证明新增产品工具或全生产目录；没有因此重跑已有效的探针。

本轮只交付DWS构建/安装修正和证据文档，未修改CEO应用逻辑或发起CEO部署。已移除自有claim，清理临时夹具，保留CLI原生会话和上述摘要以供核对；API容量、Qwen完整结果生成和G3缺失材料仍明确未关闭，整轮不宣称完成。

最终自然样本查询截止08:36:45 UTC（01:36:45 PDT），仍只有398975与398977两次completed运行；其各自完整原生范围均没有文档写入。结束本轮有限检查，不轮询或重放补证。


## 10. Qwen完整轨迹定位与G3当前固定案例（2026-10-24）

本次隔离开发基准更新为 `0d0f923ff124171bd11b14e228bc265d3b472152`。只新增匿名评测程序和证据，不改应用、prompt、Skill、路线、session、审核、权限或重试策略；不部署CEO服务。完整原生记录保留在CLI会话库，本文及结果文件不复制生产内容或凭据。

### Qwen：最小探针与真实装配必须分开

已完整查看原660行trajectory，93次相同读取均有完整匹配返回。第一次读后即能判断已处理，后续却在准确结果字段不清楚时继续调工具。原提示是最小Audit提示，schema通过解码约束提供，不能冒充完整生产Developer。Provider grammar同时允许工具与最终JSON，没有证据支持“服务反复重试”或“来源一直变化”。三个匿名直接对照证明最终JSON分支可用，并定位可见契约影响该最小探针的收尾。

进一步使用当前完整Audit Developer，Qwen session `01a16f62-9cfd-7951-9809-d66c12895639` 冷启动、同session续跑均完成假来源读取、输出approve并通过当前严格业务解析。分别读取6次和19次，效率问题仍保留；续轮候选旧引用和当前来源ref不同，不能把19次重复全部归因于契约可见性。依据、完整范围摘要与对照边界见[Qwen最终输出定位](qwen-final-output-investigation.md)。

这关闭的是“该原最小探针是否证明完整Audit装配无法输出”的诊断缺口，没有启用生产service API Audit原生schema，也没有替代Qwen Consumer和API的缺失矩阵。**G2仍未完成**；不加Qwen专属prompt补丁、force-none、fallback或读取上限。

### G3：当前输入与逐项业务结果

复用原 `evals/task_scoped_prompts/v1.json` 的六个事实和标准（SHA `9e6bb578a321cee3865c20051f47762de12710c1076b2e4fbf1c333008a192af`）。当前配置OAuth模型为gpt-6-luna，使用当前角色装配及原生完整schema、空Work Profile、原固定时钟。任务只获当前所选Skill的名称/读取入口，依赖通过 `read_skill` 读取；工具只接本地假来源、当前纯时区转换和动作合同查询，结果中的动作仅为提案，没有执行。

这是**当前固定案例复核，不是历史旧新A/B**：旧冻结Profile不可恢复、模型已变化、当前工具增加时区转换能力，因此不报告可比性能、质量提升或原始输入重现。可重跑程序见 [固定案例程序](../evals/trajectory_optimization/final_acceptance_fixed_cases.py)，完整结果/来源返回/输入与原生摘要见 [结果v1](../evals/trajectory_optimization/final_acceptance_fixed_cases.results.v1.json)。保存Skill摘要及长度，完整正文可由冻结内容及原生记录核对。

| 原固定阶段 | 已检查的来源与业务标准 | 实际结果及验收 |
| --- | --- | --- |
| 跨午夜 cold | 上海10月10日13:30–14:30换算洛杉矶10月9日22:30–23:30；应核对10月10日00:00开始的Blocked，没有重叠；仅响应原邀请 | 首轮时区、动作正确，但查询截止午夜未取到Blocked，**部分通过，保留**。同session据实际缺口反馈扩展读取至11日00:00，取回边界事件，明确23:30先结束、00:00后开始；仅提议接受cal-cross-midnight，未修改Blocked。**反馈后通过**，非首轮全通过。 |
| 跨午夜 resume | 当前邀请已accepted；重新读取当前状态，不重复响应 | 同一session返回no_action、无动作；引用本轮事件读取，**通过**。此阶段假来源先变accepted；后续纠正阶段明确上一提案未执行并恢复cold事实，两个阶段分别核对，不能混作一次真实执行。 |
| 真实重叠 cold | 同一上海邀请与洛杉矶23:00–23:30真实重叠；本人Blocked优先；通知仅限已核实邀请人 | 正确判断重叠，提议decline cal-real-overlap及给organizer-overlap的直接冲突说明，未修改Blocked，**通过**。 |
| 已处理邮件 cold | 当前会话有已批准续约及receipt renew-77、无需回复 | 读取当前完整假消息后no_action，无重复回复或续约动作，**通过**。 |
| 文档冲突 cold | rev-7中120/80门店、15/22日、500,000/明细合计620,000冲突；只回原触发会话 | 指出三项冲突且不批准；只提议回复eval-document-conversation / trigger-document-review，**通过**。 |
| 已有工作事项 cold | 认证回调阻塞威胁20日切换；已有task-auth-1、Integration Owner于12日核对，仍open | 初轮业务结果正确，但夹具把未选依赖也作为task Skill返回，**该轮排除验收**。收紧绑定后仅重跑此例，拒绝一次越界task Skill读取后模型改用installed read_skill，读取rev-3并复用已有task-auth-1；不建重复任务、不称完成，只回原会话，**恢复后通过**。原生共7次调用，6次成功返回和1次明确拒绝；不冒称全程无工具错误。 |
| Audit反馈 cold | rev-11仍有120/80、15/22冲突且未批准；候选错误声称一致/可批准 | 读取当前文档，return反馈要求列明两项冲突并撤回批准断言；revision0、candidate digest与输入精确绑定，**通过**。这是审核产生修订要求，不冒充已执行Consumer修订或已发布文档；既有完整反馈修订证据仍引用原记录。 |

共9次真实原生调用：原7阶段 + 跨午夜反馈补证1次 + 收紧工作跟踪夹具后1次。9次均能严格解析，**不能写成9/9业务首轮通过**。最终原7阶段都有对应验收结果：跨午夜cold在反馈后闭环，工作跟踪以严格绑定新轮为准；原始partial/excluded及全部会话ID、输入/schema/Skill摘要不覆盖。部分运行结束时会话查找尚未返回路径，其原生行数/摘要在本次收尾从实际JSONL补齐，并明确标注；其他已记录摘要全部按原前缀复核，续会话增长不重写冷启动摘要。

**G3结论：当前OAuth原六固定场景及反馈闭环已验收。** 这不把OAuth成功推广到各路线，不证明生产重复文档覆盖已停止，不重现历史A/B，也不证明整体提速。剩余仅G1自然文档行为回读和G2路线完整输出；Qwen过多重复读取列为已观察的后续效率问题，本次不扩展修复。

收尾只读版本核对：远端main前进至 `65697801a`，生产为 `701a8ce5c`（源码工作树干净）；新增变更是Meeting摘要读回与OA历史重跑入口，未修改本评测使用的角色装配、schema、假来源或工具入口。没有因无关主线变更重跑模型；这份证据提交不冒充新生产部署。

独立只读审查已通过：逐项核对原manifest、工具事实、业务结果和9份原生前缀摘要，没有发现需修正问题；认可限定的G3结论，G2继续保持未完成。静态Ruff与六例fixture完整性检查通过；没有重跑已通过模型案例或无关测试。


## 11. G1/G2仅补缺失验证（2026-10-24 20:16–20:20 UTC）

本轮继续沿用main `205674a5a` 及有效OAuth/G3、Qwen Audit、Claude阶段证据。没有重跑已通过格，没有新增业务写入、生产重放、prompt、权限、审核、重试或路由策略。文档/eval变更无需部署。

### G1：自然报告正常路径已验收；失败状态仍无样本

截止20:16:11 UTC的只读Run查询覆盖此前截止后的全部8条Consumer/Audit运行；可定位的全部原生范围按session完整读取，非文档任务不作为文档验收。Consumer399294/399296与Audit399295/399297为自然定时报告，已出现两次consumer_document_write。

创建正文SHA `04abf9eb...`，反馈后覆盖正文SHA `9b1e26b...`，两次目标相同、正文不同；provider均status=success/verified=true，服务正文SHA与各自artifact精确匹配，readback回执齐全。修订前重新读文档并使用expected_revision。**这验收正常创建、覆盖和使用已有文档修订，不能说它重写相同内容或绕过核验。** 当前DWS版本及二进制摘要仍与已验证版本化补丁一致。

完整原生范围、正文及回执摘要见[自然文档运行补证](document-write-recovery-validation.md#自然运行补证2026-10-24-2016-utc)。本轮没有出现“写入已执行但核验失败”；因此G1仍为**已上线待行为验收**。剩余动作只是在该状态自然发生时读取工具结果与后续行为。不制造失败、不人工重放、不持续轮询；现有正常样本不替代这个验收条件。

### G2：新增3次原生调用，保留失败，不循环重跑

复用原已处理邮件事实与当前Consumer完整装配、schema（`72ee1044...`），空Work Profile、假只读MCP。评测程序仅增加路线选择及一次显式continue入口，未改应用运行机制。完整摘要/工具返回/终止信息保存在[路线补证结果v1](../evals/trajectory_optimization/final_route_verification.results.v1.json)。

| 缺失格 | 本轮结果 | 验收结论 |
| --- | --- | --- |
| Qwen Consumer冷启动 | session `01a16f8f-3486-7420-bf58-3d1700fdd8c7`，124行SHA `ef98942f39f3417218714eb5eb927a4aee2cb02fdd1c798713a837bc2bfc1730`；180秒预算结束，无最终消息。原生18次调用：消息7、合同4、Task Skill3、installed Skill1、wait3；15次假MCP成功返回，3次wait因虚构cell ID失败 | **未通过**，没有有效冷启动，未再跑续会话。不是提供商429，也不是服务重试 |
| API Consumer冷启动 | session `01a16f8f-b637-7cc1-9686-315a03e40aeb`，原生10行；terminal error明确为 `response_too_many_failed_attempts/http_status_code=429`，0工具调用 | **容量阻塞**，不把它当schema缺陷 |
| 同一API session补发一次continue | 同session累计19行，仍为相同429，0工具调用 | **仍阻塞**；不算有效冷启动或续会话通过，不继续循环重试 |

Qwen完整读取124行：第28行已明确判断邮件已处理、应no_action，末段仍是这个判断，没有待补业务事实。其拟输出却按flat action/target/payload字段、数值risk组织，且反复说明不确定准确schema；当前原生schema要求decision包装、字符串风险等级及其他必填字段。这证明模型拟采用的格式与解码契约不一致，支持“原生输出契约向模型的传达/收尾”这一具体定位，而不证明唯一内部原因已查实。它一度转向无必要的文档artifact工作，但没有执行任何写入。3次wait工具确实存在于原生harness，失败是引用了不存在的执行cell，不是MCP工具不可用。

同一事实的当前OAuth已在G3通过；Qwen Audit完整指令冷/续也已有有效证据，不重复调用。**不能用两者替代Qwen Consumer的失败格。** 若继续使G2通过，Qwen需要单独解决已记录的输出契约/收尾机制问题并通过同案例；API需要容量恢复后取得有效冷启动和续会话；生产service API Audit不选原生schema的实现缺口仍在。以上不是继续重跑就能消除的证据空白，本轮按“只补验证”边界不改这些机制，不引入路线专属prompt或fallback。

本轮终点：已补缺失的自然报告和可运行路线证据，G3不重开；**G1仍缺指定自然失败状态，G2明确因Qwen Consumer无有效结果、API429和API Audit未启用而不能验收。** 整轮仍不能宣布完成。无新增性能实验或其他trajectory优化。

独立审查核对自然文档两次回执、原生摘要、Qwen18次调用与API两次429，认可上述边界。评测新增continue入口的Audit候选查找改用source_phase，避免不存在的continue事实阶段；实际Consumer探针不受影响，无需重跑。Ruff、fixture检查和diff检查通过。收尾集成的OA append_task主线增量未改变通用Consumer原生schema、Audit schema或文档写入路径，不为无关改动重复验收。


## 12. Qwen Consumer定向修复与发布

只处理第11节确认的Qwen Consumer原生收尾问题。API429保持阻塞，G1等自然样本，已通过的G3/OAuth/Qwen Audit未重跑。

原生格式传达与provider grammar各有独立证据缺陷：前者只给指纹、模型仍按扁平wire准备；后者把reasoning前缀绑在required-tool分支，排除了思考后的最终JSON。最终组合保留完整业务schema/严格校验和相同路线，通过显示实际native格式与修正provider组合解决，不加Qwen专属任务逻辑、停止提示、fallback或重试。

同一匿名邮件案例在私有canary的冷/续均no_action，当前来源读取完整，无重复回复提案；57/79行原生摘要和5/1次工具调用见[完整修复记录](qwen-final-output-investigation.md#最终根因与组合修复)。失败诊断和连接不可达运行分开保留，不把基础设施失败计为模型退化；两项单独候选均未作为发布依据。general服务输入为精确格式增加约1.17万字符，不声称继续精简或提速；必需协议不能由哈希替代。

当前状态：**Qwen Consumer组合修复已验收、已上线并完成匿名生产入口回读。** 这关闭本次指定的Qwen Consumer缺口，不宣称G2整体完成；API及API Audit原生启用缺口保持独立。


### 第12节发布与最终回读

- CEO修复提交 `37415c6cd`，经 `3fb2ce3ad3a43795a9d8970ae6fd08181626ba40` 合入main，正式 `python -m app.deploy` 返回 `deployed 549f471b -> 3fb2ce3a`。生产源码工作树干净，launchd新PID33493；healthz/status/Attention/History均HTTP200，Attention5、History38125，Reply及Attempt失败均13、Meeting失败0，dispatcher当时无在途、scheduled pending2。system_health另有1项Meeting停止推进及1份native delivery不可读的既有finding，相关Meeting更新时间06:48早于本次新进程；本轮未扩展修复，不把HTTP200说成全系统无告警。
- Settings当前general Consumer / qwen_gpu4公共预览中的native JSON与生产schema文件完整相等；Developer41794字符，SHA `cc4adc4df30e9ca01f660236a97485a5df9669b45ba720dcc2e263667ac0a54e`。本次未新增OA任务绑定预览验收。general格式段实际增加11813字符；不作token/费用/提速推断。
- 提供商代码[llm-gateway fce6e9f](https://github.com/callzhang/llm-gateway/commit/fce6e9f)及[发布记录eb1ac0c](https://github.com/callzhang/llm-gateway/commit/eb1ac0c)。已知请求持续刷新自然空闲计时，因此没有无限等待或修改管理器；按本次修复上线授权，在active/in-flight/waiting均0后通过现有admin_switch切换slot1一次，提前披露非原子准入竞态，不承诺零中断。无管理员重启、无新排空策略。旧PID2362797已退出，新PID2861779启动晚于源码安装且ready=true；已安装parser SHA `9bcfe24914a52cb4f4cd2fc45f47cdc0f62f1c4695172b0815f953a4b2d2be5f` 与验收候选相同，检查区间没有推理POST 5xx。旧源码已验证备份保留一份。
- 最后仅通过当前配置的生产入口补一次原匿名邮件cold回读，没有再次跑已通过cold/resume矩阵：session `01a171f1-acc3-7b62-9074-53cf042d5a75`，70行SHA `01d95910cadcc8dfec5237806d3a90422cc9ed23cde106a3275da86302274275`，5次成功只读调用，完整解析no_action，无回复提案。这是生产提供商/新代码的匿名回读，不是真实业务任务重放，也不替代G1自然失败恢复。

**本次用户指定的Qwen Consumer修复已完成。** 私有canary、SSH转发及临时目录已清理；provider回滚源码保留，CLI原生证据与精简验收摘要保留。G2剩余仍是API429阻塞和API Audit原生启用缺口；G1继续等指定自然样本。没有开启其他trajectory优化。


## 13. 恢复按需契约读取（用户纠偏）

第12节恢复输出的全量native schema注入偏离精简目标。已验证provider grammar保留；本项改为极简格式说明、同一schema指纹和agent_cli按需查询。验收要求：默认段落不含完整schema；Consumer查询返回实际decoder schema且单一文本块、不扩大任意文件或Audit权限；原匿名邮件cold/resume均读取当前业务事实并完整解析no_action；记录实际schema查询、输入长度及完整轨迹，未通过不发布。API/G1和其他任务不扩展。

直接给shell文件路径的候选已失败：完整轨迹中没有可用exec，provider的iter_response_function_tool_dicts过滤了CodeMode自定义工具。现有read_text_file又明确禁止仓库文件读取，因此不放宽其授权范围、不修整个custom-tool传输。采用现有agent_cli服务增加仅可读取声明Consumer schema的查询；这是一项metadata读取，不是任意shell命令入口。失败的纯路径提示及首轮查询结果保留；没有以退出码或读取成功代替最终业务结果。

注意：按需查询让冷启动仍可能读取整份schema，不能把prompt减少直接宣称为总token/耗时减少。续会话是否重读以实际轨迹为准，不实现服务端“session相同所以永远省略”的缓存或compaction假设。


验收结果：复用第12节已有效的全量基线，仅补本次按需方案。原生段从11909降至373字符（减少11536，96.9%）；不是删除必要格式，而是通过现有agent_cli查询完整契约。同一匿名邮件session `01a17215-b732-7902-a831-b5aa8068d441`：cold调用Skill、schema查询和当前消息，共3次；resume只读当前消息1次；两轮均严格解析no_action、无重复回复。cold第18行模型可见schema完整11656字符，与decoder文件等值；原生33/55行摘要及哈希见[按需验收记录](../evals/trajectory_optimization/consumer_schema_on_demand.results.v1.json)。本案例未测总token节省或提速，也不证明任意长会话始终无需重读。

定向测试50项通过；另一次角色/续跑选择34项通过，1项既有外发约束字面量断言失败（`test_consumer_and_audit_share_direct_external_delivery_boundary`，未改的plain builder不含其期望旧句），未扩展修改该策略。独立审查验证其中16项及实际原生轨迹，无功能阻塞；指出canonical文档仍说全量注入，已同步修正。API429、G1自然样本及其他任务不重跑。


第13节发布收口：修复提交 `6bc49ded2`，经 `7db7db8a77ad07e3443ebd82c81509e980d8fb12` 合入main。正式默认部署回执 `deployed ed7380e5 -> 7db7db8a`；随后另一会话通过正常部署发布Skill，最终生产源码仍为同一提交，新服务PID1046。生产相关3文件与验收候选字节一致，源码工作树干净。healthz正常，Attention5、History38138、Reply/Attempt既有失败13/13、Meeting失败0；dispatcher无running，scheduled pending6。system_health保留一项既有Meeting停止推进finding，native delivery不可读降为0；没有把此记录声称为全系统健康。

Settings general Consumer / qwen_gpu4公共预览确认完整schema不在Developer中、短查询入口及当前指纹存在；Developer30306字符，SHA `06eb1b2425d2b2d45776bfa8fee28cfd6cd49b4eba1c2259f44ba6ce2f692819`。总Developer还包含同期其他变更，不用前后整段差值冒充本次节省；可比数字仅为同一native段11909→373。未新增OA绑定预览或生产业务重放。**本次按需读取纠偏已测试、审查、上线并完成版本/Settings回读；原生冷/续行为依据为上述同案例记录。** G1/API等整体验收缺口保留，本轮到此结束。


## 14. G1/G2有限补证（2026-10-25 09:04 PDT）

基准：开发/main `acf1fea2c`，生产 `bf27bfaeb`；生产包含文档成功语义提交b5028e7ef。本轮只读自然记录及一次API continue，没有部署、业务写入、任务重放或已通过模型矩阵重跑。证据：[本轮版本化摘要](../evals/trajectory_optimization/g1_g2_closure_20261025.results.v1.json)。

### G1：样本已出现，成功语义通过；非重复写入未通过验收

从上次截止 `2026-10-24T20:16:11Z` 到本轮 `2026-10-25T16:04:53Z`，以SQLite `datetime` 归一化空格/ISO时间比较，读取全部66条Consumer/Audit Run、39个唯一session的保存原生范围，无缺失session；完整范围摘要逐个保留。4个session共47次文档工具调用。DWS仍为已验证补丁 `v1.0.65+ceo-jsonml.87ddde41`，二进制SHA `4efec8a5d6b80a34f98df6dc917497166050a3e6c89307556db3e905b0da7ad6`，没有版本回退。

自然定时报告session `01a17281-1d0e-78a2-9839-8328ca88d31f`，Run399361/399362/399365/399367，完整7636行SHA `8773a8602f850028eaec10c5a23f46e824c449df8d8192eee6c02dec5d2476bd`。其中40次文档工具调用：30次provider verified=true，7次已执行但核验失败的返回，3次没有可确认成功回执（中断、目标已出现、超时），后者不计成功。

- 第6579→6582行：创建已执行，服务返回 `ok=true/status=success/verified=false`，目标与正文SHA保留，provider错误明确create_document success、verify failed。这与第6643、6918、6942、7312、7338、7380行合计7份自然返回，**证明服务的“写入成功与核验分离”交接已生效**，不把它说成核验通过。
- 第6590、6599、6617行分别读Markdown、文档元数据和完整JSONML；结果Markdown18053字符，full reader complete=true、JSONML94785字符。随后第6643行仍以expected_revision=1覆盖同一目标、同一正文SHA `17bcacc84afcca01eb598164b4fbbf8d4dc6040fc05d2ac5917fa6313b3c2630`，再次得到写入成功/核验失败。其余两组失败后同目标同正文再次写入为6918→7338、6942→7380。
- 因此**不能验收“同一失败状态不再导致相同内容覆盖”**。读取过完整JSONML不等于已证明正文完整，也不能由重复调用直接认定模型进行了无必要重写；原cwd下main.md已不在，但第6533、6540行保存了15000+2834字符的两段工件读取；拼回17834字符后的SHA与两次写入回执完全相等。用DWS补丁源码87ddde41的原核验函数和当时Markdown/完整JSONML离线复现，仍拒绝；两张表的19行布局、对齐均通过。结构token为1652/1660，差异收敛到两处高亮span；纯文本比较明确4处`==`被删除，共8字符。4个原文片段在完整JSONML文本中均不存在，去掉`==`后均存在，证明真实文本已被格式语法消费，不能忽略差异改判成功。诊断通过Go overlay临时增加只读离线探针，未改DWS checkout或调用网络/写文档。其余已核验文档也有同正文覆盖，不能未经因果核对全部算为核验失败所致。

本轮结论：G1由“缺自然失败样本”更新为**服务回执已验收、非重复恢复未完成**。最小下一步仅限定为上述字面量`==`与服务高亮语法的共用文档表达机制，保留精确标识符原文、使用同一离线样本及严格负例验收；不原样覆盖、不放宽核验，不扩展任务prompt/Audit/重试。具体修复不在本轮只补验证范围。

### G2：复用Qwen已通过证据；API仍阻塞，生产Audit实现缺口独立

Qwen Consumer直接复用第13节同案例cold/resume、按需schema完整读回和生产版本/Settings证据；OAuth、Qwen Audit完整角色探针、G3等已通过项目未再调用。

对既有API Consumer失败session `01a16f8f-b637-7cc1-9686-315a03e40aeb`，只补发一次原生continue。累计28行SHA `993c78b99c7368f21d0c9a2b599d699d5c2445120d2c0c8179771fc5c08533cd`；第28行task_complete明确 `exceeded retry limit, last status: 429 Too Many Requests`，`response_too_many_failed_attempts.http_status_code=429`，0工具调用、无有效typed结果。没有有效cold，不计resume通过；不再试其他角色或循环重试，不将429归为schema错误。最小下一步是容量恢复后补缺失API冷/续，不能重跑已通过路线代替。

另只读确认 `app/agent_turn_runner.py:143–156`：Audit非LOCAL_OAUTH直接返回None，因此生产API及Qwen Audit都未选择原生schema。隔离完整Audit schema探针通过与服务生产启用分别记录。**这是独立实现缺口，不是容量问题**；本轮“只补验证”不修改启用机制，不切路线、不放宽校验。下一步若实施，须限定为该既有原生输出缺口的变更及定向验收。

**本轮补证收口，整体仍未完成。** 有限遗留为：G1已证实的字面量/高亮语法差异及无效同正文再次写入；API429容量阻塞；非OAuth Audit原生schema启用缺口。不再以“G1仅等样本”描述当前状态，也未自动展开其他trajectory优化。


## 15. G1 高亮语义修复、审查与本机发布（2026-10-25 15:40 PDT）

按用户批准保留钉钉Markdown语义：未转义的`==文字==`是高亮，精确标识符的双等号须用代码格式或转义。不是恢复未转义输入的字面标识符；原始历史样本验证格式语义，另以同内容显式字面版本验证原文保留。完整原始正文仅在本机临时目录离线使用，不进入上游PR。

**实现与边界：**DWS使用Goldmark作者侧分隔符解析，跳过代码、转义及链接目标，只在核验副本中投影为服务的HTML高亮表达；提交工件保持原样。每份作者高亮还必须取得完整JSONML，精确核对可见文字、规范化词间边界、有序范围及颜色，再运行现有整体结构/内容/表格对齐校验。不是全局删双等号或剥HTML。无新API/配置/返回字段，无重写重试，无CEO运行代码、任务prompt、Audit或session改动。现有dingtalk-doc Skill只加一条格式说明及两个最小例子。

**验证：**8个新增定向测试函数（含18个子场景）在上游候选和现用v1.0.65基线均通过；旧源码overlay对照明确拒绝合法高亮/create/update/append。覆盖错误范围/颜色/正文/数字/词间边界、损坏标识符、不完整或失败JSONML读取、代码/转义、链接/实体、旧高亮段后append、混合对齐表格和单次写入。原始17834字符样本恢复后SHA与回执相等；完整JSONML中2/2高亮、可见文字及边界一致，离线核验通过；显式字面版本也通过。没有重跑已通过模型案例或无关测试。go vet、diff检查通过。独立审查提出5项边界问题，均补失败回归并修正，复核无剩余阻塞；不把首次候选当作发布版本。

**提交与安装：**现有外部[DWS PR1551](https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli/pull/1551)已更新到`fd7b27cb4d0b08b003e00981c21bca323591d1b1`，仍OPEN、未合并。基于本机原v1.0.65+对齐修复的发布提交为`6747c6711b78cfa6781d4fcd6e68ee12660e0c70`，仅带入同一修复，未升级无关功能。原生Go构建注入准确版本/commit/commit time，运行payload五库六目标校验、darwin/arm64附加及严格签名验证通过。

2026-10-25 22:39:26 UTC（15:39:26 PDT）验证旧二进制未变并校验备份后，按原生staged-copy/atomic-rename方式安装到`/Users/derek/.local/bin/dws`。当前版本`v1.0.65+ceo-docfmt.6747c671`，SHA `3105fda34b277732a91070c38b677ad37162b8f3e70804fbec378bcbab74e97c`。原生升级检查按缓存发布信息返回latest v1.0.65、needs_upgrade=false，使用SemVer build metadata，没有锁升级或伪造未来版本。仅同步同一条Skill说明，安装前已确认其余已安装内容与源完全一致。

**生产回读与终点：**主进程PID6533未变，实际PATH解析到上述二进制且SHA相等；DWS逐次启动，未重启CEO。healthz正常，Attention4、History38236；安装前后system_health既有finding均2项，未扩展修复。生产HEAD为`dc07ddcbed7ee48896879db37b06d162a97a98d5`。单次只读查询截至2026-10-25 22:40:12 UTC，安装后新增Consumer/Audit Run为0，因此**代码已发布，生产非重复行为待回读，G1尚不能宣布最终完成**。等待首个自然相关样本，不人工生成、重放或无限轮询。G1交付前不进入G2；API429及非OAuth Audit启用缺口维持原记录，G3不重开。

版本、回归、安装和生产摘要见[发布证据](../evals/trajectory_optimization/g1_highlight_semantics_release.results.v1.json)。


### G1 自然样本续查（2026-10-25 15:52 PDT）

单次只读检查截至22:52:06 UTC，安装后新增Consumer/Audit Run仍为0，最新完成Run399428在19:11:14 UTC，早于22:39:26安装。进一步核对正在调度的定时项：没有active RuntimeAttempt或running Agent Run；occurrence406331/406332尚为pending且execution_id为空，不将调度租约计作已执行文档样本。DWS二进制仍为发布SHA3105fda3…，生产HEADdc07ddcbe，服务PID6533运行、healthz正常，既有system_health finding仍2项。

**G1保持已发布、生产非重复行为待验收；没有证据证明问题已消失或仍然发生。** 本轮未触发任务、回放、写文档、调用模型或重跑已通过验证；未进入G2。后续仅核对首个实际发生的自然文档写入及后续行为，具体检查摘要追加于现有发布证据natural_followup_checks。


## 16. G2 认证无关原生格式候选（2026-10-25）

用户最新指示允许G1待自然行为验收时先继续G2。候选只移除Codex Audit在schema选择、native解码、结果恢复指纹上的认证方式排除及多余内部参数；无审核规则、权限、prompt、session机制、配置或历史数据变更。

服务API实际AuditRunner的approve/return回归先失败于命令缺少--output-schema；恢复指纹回归先失败于schema改变而指纹不变。修复后5项定向测试通过、70项未运行，涵盖真实角色命令、完整native解码、revision/digest绑定及schema选择/恢复指纹。随后只补一项中断恢复集成：真实角色运行保存完成的原生结果后模拟服务中断，使用完全相同的提交契约恢复，执行器不得再次调用，revision/digest仍绑定当前候选；1项通过、31项未运行。合计6项定向测试通过，先前5项未重跑。Ruff与diff检查通过。

复用Qwen完整角色Audit session01a16f62-9cfd-7951-9809-d66c12895639的58/184行cold/resume，保存摘要重新核对一致；schema仍ca522ed1…，CLI仍0.163.0，提供商parser仍9bcfe249…；没有模型重跑。此格式证据不推广为当前全部业务规则验收。

对原API失败Consumer session只补一次continue仍429（0工具调用），累计38行SHA4abecfeaf92a5229366fff007752793c9e309f9cbc2b86dd7b6787baad082132。无有效cold/resume结果，不继续重试。另确认现有AgentTurnProcess._session_for_route只为codex_oauth保留Audit请求session；qwen_gpu4匿名只读诊断返回None，CLI续跑成功不能代替服务续会话验收。

**该阶段候选未合并、未部署。** API容量恢复后补缺失API有效冷/续；当时发现的非OAuth Audit服务接续缺口已按用户后续明确授权补齐本地代码和验证，见下节；发布状态仍为草稿。发布仍须满足原计划的配置路线验收及正式部署测试门禁，不做Qwen特例、不增加fallback或宽松解析。完整证据见[g2_native_audit_wiring.results.v1.json](../evals/trajectory_optimization/g2_native_audit_wiring.results.v1.json)。

### G2 非 OAuth Audit session 续接（2026-10-25）

按用户明确授权，在草稿 PR325 上修复服务接续链路。此前非 OAuth session 已写入 RuntimeAttempt，但 Audit 从 OAuth 专属 Run 投影请求 session，选择器又只放行 codex_oauth，因此重试与修订都会冷启动。

当前选择以同一 Task、execution generation、Audit 角色及当前 revision/Run 之前的原生 RuntimeAttempt 为来源，要求路线名、runtime kind、credential mode、model 与当前路线一致；只使用最新 Audit 所属会话，不读写 Consumer 会话槽。已有非 OAuth Run 投影为空的历史仍由原生记录证明归属，无历史迁移。CLI 原生会话事件中的所有 Audit 路线同步更新已有 Run session 投影，保证原有连续结果失败换新会话规则继续有效；不增加新字段或重试次数。force_new_session 仍优先，上一 revision 明确 session_continuable=False 同样禁止继承。

重试和修订回归先失败于缺少 resume；针对连续无效结果和跨 revision 不可续接的负例也先失败。最终只运行这条变更影响的10项本地测试（4个新增节点、6个直接受影响的既有节点）：10 passed、26 deselected、9.91s。覆盖实际 AuditRunner→CLI resume 命令、RuntimeAttempt.source_session_id、候选 revision/digest、不可变旧 attempt、Consumer 槽隔离、路线/模型/运行时/认证方式/Task/代次不匹配、显式换新及已有重复失败规则。独立只读复核通过，先前发现的两处关联回归均已关闭，复核未运行测试或模型。原生 schema 已通过6项和所有模型案例不重跑；没有外部模型、业务 MCP 或生产写入。

**服务接续代码及本地验收已补齐；草稿仍未合并、未部署。** API429 保持既有阻塞，本轮0次 API 探测，不循环重试。既有 Qwen/OAuth/G3 证据继续复用；缺失的 API 有效冷启动/续跑及正式发布、生产回读不能以本地通过替代。

### PR325 发布条件回核（2026-10-25 18:03 PDT）

在250c39867上按用户最新授权只重试一次原API失败session01a16f8f-b637-7cc1-9686-315a03e40aeb的continue。结果仍为HTTP429，exit1、0工具调用、无有效结果；原生轨迹累计47行，SHA256为6c0e06713e9240938fa953e200c7f9d304bd2673b3f2fa8affba356aa120a869。不继续循环，也不启动注定受相同容量阻塞的其他角色/冷启动。此前通过的6项原生格式测试、10项接续测试及Qwen/OAuth/G3模型案例均未重跑。

现有[Quality检查](https://github.com/callzhang/ceo-agent-service/actions/runs/40248553539)在Ruff阶段失败，测试未启动。本地只重现静态检查，得到相同44条lint错误、12个文件；逐个Git blob核对，这12个文件与当前main dd3c4933bbf02b7b89f74d704bb2368bc028bc05完全相同，PR修改文件中的错误为0。它是主线共有门禁缺口，不能冒充本PR通过，也不将无关12文件清理夹进本修复。

**发布条件未满足：PR325保持草稿，未合并、未部署、未作生产验收声明。** 核查时另有既有部署PID50850在运行，未启动第二个部署。有限下一步仍为：API恢复后仅补缺失的有效Consumer/Audit冷启动与续跑；复用主线后续修正来关闭Quality门禁；满足后再合并、正式部署并读回生产版本/PID/健康/队列/Attention/History。既有G1自然样本及其他优化不在本轮扩展范围内。

### 阻塞根因定位（2026-10-25 18:08 PDT）

**429已定位到中转商的模型渠道选择。** 对当前配置https://api.kksj.org/v1/responses只发送一次极简“Return exactly OK.”（相同凭据与gpt-5.6-luna，不带schema/工具/业务事实、无重试）。返回HTTP429、type=new_api_error、code=model_not_found，明确正文：`No available channel for model gpt-5.6-luna under group default (distributor)`；request id=202610260107593008780698268d9d6gjl1su4r。一次只读GET /models返回200，列有该模型（目录共227项），证明“目录列出”不能证明本凭据分组存在可用渠道。

这修正了先前笼统的“容量阻塞”描述：已确认的失败层是当前中转商的模型渠道不可用；不能据此认定普通请求频率限流、余额不足或OpenAI原生schema不兼容。未查实的是中转商后台为何没有可用渠道（分组映射、渠道配置/健康或上游状态）；需要该request id对应的提供商管理员日志，现有推理接口返回不包含这些证据。继续发送continue不能补出缺失渠道。

**Quality根因是主线共有的静态检查残留，非测试用例失败。** 44项=30未使用import(F401)、2重复import(F811)、2无占位符f-string(F541)、9导入顺序(E402)、1未使用局部赋值(F841)。具体重复import与f-string定位至b78604e32；tests/conftest.py的MemoryLimit导入位于可执行设置之后，行来源cfe3a1686；tests/test_store.py常量赋值插在后续import之前，行来源f2db64d6a；tests/test_error_code_migration.py未使用run_id赋值来源1fd657e91。2ef930ee3与feb963fc2删除测试后留下的import已通过提交diff核实；其余行来源与最小例子见JSON证据，不将“最后修改该文件的提交”冒充全部错误的引入提交。

GitHub npm test先执行Ruff，所以这些错误使pytest尚未启动；app.deploy的TEST_GATE_ARGS仅执行pytest，不能把部署测试通过当成Ruff已通过。没有变更或绕过任何门禁。修复静态残留时必须保留建样本调用的副作用、3GB上限和生产checkout拒绝逻辑。

当前有限处理方向：优先由中转商修复本凭据default分组下gpt-5.6-luna的可用渠道；若要换模型/路线需独立明确决定并验证，不能自动切换。主线lint清理独立处理，不夹入PR325。渠道恢复后仅补缺失API冷/续，门禁通过再合并、部署与生产回读。本次没有改代码、模型/路由/凭据或生产服务，也没有重跑通过的案例。

### 当前CLI验收范围确认（2026-10-25）

用户明确取消“所有已配置路线均需验收”的要求：本轮只验收当前Codex CLI、本机OAuth登录、gpt-6-luna。API中转路线的429/model_not_found不再是G2或PR325发布前提；不再测试、修复或等待该提供商，也不因此修改任何启用路线、模型或凭据。历史API与Qwen记录保留为有界诊断，不作为当前路线失败。

当前Consumer/Audit冷/续来源分别为session01a16cf9-864c-7b11-8a2b-5e3982577263（45行SHA dd159059404a29b9b3e9911607bde1f62c0937abfe9835c1aae996819616834a）与01a16cf2-63d7-7720-b89d-a9b58d436ef9（49行SHA2470451a1159c0bd751e772cc07c559fae4e785d461aca10d9726b275d81f647）。本次只读重新核对保存前缀SHA相等；当前Consumer/Audit schema仍为72ee1044…/ca522ed1…。工具调用、业务解析和候选绑定结论复用原记录；接续变化复用前述10项受影响测试及原生接线6项，不重新调用已通过模型案例。当前CLI行为验收条件已具备，发布及生产回读尚未完成。

主线共有44条lint诊断保留独立记录，不冒称通过，也不夹入本PR；正常仓库合并与正式部署门禁照常执行。部署脚本要求在隔离副本对准确目标提交运行pytest全套，不能跳过；该强制发布门禁与重复模型评测不同。只在发布回执和版本/PID/健康/队列/Attention/History回读完成后报告上线，不扩大G1或其他优化。

### PR325合并与正式发布门禁回执（2026-10-25 18:40 PDT）

当前CLI验收范围已按用户决定生效，API不再作为发布条件。PR325正常合并为bd3c8eb44cb58165dcf1e476a193ef870f8c1613（01:28:51 UTC）；与最新main合并时仅处理claims末尾双方追加行，全部保留，相关运行代码/parser/测试5文件与已验证250c39867逐Git blob相同。既有16项本地验证和当前gpt-6-luna冷/续轨迹复用，无模型重跑。

执行标准python -m app.deploy，准确目标bd3c8eb44。隔离pytest门禁出现失败后正常中止本次自有测试，以保留报告而不继续运行已知不能放行的全套；部分结果为7 failed、748 passed、6 warnings、1 collection error、507.09s，不冒称全套完成。部署返回“the test suite fails … nothing was deployed”，在服务停止/备份/切换之前退出，没有跳过门禁。

具体缺口仅定位到主线已有三份测试：test_action_delivery_bootstrap.py仍断言旧schema2026-10-14.1并操作已删除索引idx_actions_sent_history；test_cli.py存在旧tasks.payload_json、ceo_task_restore函数注册及WeChat可领取夹具问题；test_reply_input_reference.py导入已移除REPLY_TASK_INPUT_SQL，收集失败。三份测试及app/cli.py、app/store.py与合并前main dd3c4933b内容完全相同，未夹入PR325修改；完整失败节点及日志摘要SHA保存在既有JSON证据。没有顺手修改这些模块或重启服务。

门禁失败后生产回读：HEAD仍dc07ddcbed7ee48896879db37b06d162a97a98d5，工作树干净；launchd PID92610 running，healthz200/ok，queues/Attention/History均HTTP200，Attention4、History38259。该PID只表示回读当刻存活，不能把并行会话造成的进程变化归为本次部署；本次自身在测试阶段退出。**当前CLI行为已验收、PR已合并，生产发布仍未完成。** 有限下一步是所属范围内修复上述主线测试/夹具与当前数据模型的差异，随后重新走正式部署和生产回读；不再处理API，也不重跑模型矩阵。

## 17. G2／PR #325 最终验收关闭（2026-10-25 19:22 PDT）

**G2已验收并关闭，限定当前Codex CLI、本机OAuth、gpt-6-luna。** PR325合并提交bd3c8eb44已由后续发布包含于生产fba909db929c05ab2fa7e3f4135e5f71f741fcd0。生产工作树干净，三个相关运行文件与已验证250c39867逐字节相等，Consumer/Audit实际生产schema与已验收文件一致。没有将“生产包含提交”单独充当加载证明。

加载确认：launchd主进程99011（app.service_supervisor）及工作进程99030/99031/99032（app.cli）均在2026-10-26 01:42:02 UTC启动，cwd全部为/Users/derek/Services/ceo-agent-service；作业CEO_SERVICE_ROOT相同。最新相关代码写入时间为01:41:58.078 UTC，早于所有这些进程启动。回读结束PID仍99011、生产HEAD仍fba909db9，确认当前进程从包含修复的代码启动，无需重启。

当前配置仍为codex_oauth/local_oauth/gpt-6-luna。保存的Consumer45行与Audit49行cold/resume前缀SHA再次匹配，实际生产schema摘要仍为72ee1044…/ca522ed1…。完整行为判定及六项原生输出、十项接续测试与独立审查复用前述记录；本次没有重跑模型或测试，没有改代码、配置、prompt或权限。

生产回读：healthz200/ok，queues200（02:20:45 UTC），Attention200/4（02:20:57 UTC）。History首个10秒请求超时，如实保留；只补一次40秒上限回读，于02:22:29 UTC返回200、总数38269，未循环轮询。各接口计数是对应采样时刻的值，不宣称队列归零或全部历史问题已清理。

此前7项测试失败、1项收集错误及44条lint诊断仍作为历史维护事实保存，没有改标通过。主线随后已独立提交旧完整门禁暂停fbd0c89bb（TEST_GATE_ENABLED=False）；本次未改门禁、未重复发布或重启，不再把旧失败或API中转商作为当前G2前提。生产尚未包含的其他主线变更也不在本次发布范围。完整加载/摘要/进程/回读记录见既有G2 JSON的final_acceptance。

**本轮终点已达到：PR已合并，当前生产进程已加载，当前CLI证据有效，生产回读及验收记录齐全。** G1自然样本验收独立保留，整轮Agent优化仍不能据此全部宣布完成；不自动开启其他trajectory优化。

## 18. G1 自然样本有限续查（2026-10-25 19:54 PDT）

已同步最新main `1e840a685`后，只读检查修复安装后的18项已完成Consumer/Audit运行（上界Run399448），18个attempt、15个session、完整1141行非重复原生范围；包含从attempt恢复的Run399433，不因runs空session遗漏。全部工具调用及17次exec实际参数没有文档写入。DWS修复二进制版本、SHA与第15节一致，生产HEAD为`1e53f41793e63d8da6ab8bd51d80a7f96c084307`。

**结论：G1仍缺修复后自然文档调用，保持“已上线待行为验收”。** 这不是已确认的新回归，也不能以没有文档调用证明无效覆盖已停止。原服务成功语义、完整JSONML/高亮/字面内容与严格负例证据继续有效，不重复验证。下一步仍只核对首个自然相关文档写入的实际回执和后续行为；本轮无人工重放、业务写入、模型调用、运行代码变更或部署。G2关闭状态不变，整轮未全部完成。

详见[自然文档回读记录](document-write-recovery-validation.md)及[现有G1发布证据](../evals/trajectory_optimization/g1_highlight_semantics_release.results.v1.json)的最后一项`natural_followup_checks`（记录精确UTC查询时间、逐attempt范围及SHA）。
