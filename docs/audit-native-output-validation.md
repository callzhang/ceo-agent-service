# Codex Audit 原生输出格式验证

本次只处理 Codex Audit 的结构化输出传输。当前 main 的完整 Audit trajectory 曾出现零宽字符污染 JSON，以及 `return` 结果缺少必填 `feedback`；后者已由既有严格 Audit wire/Pydantic parser 拒绝，但模型格式失败会导致该轮无法完成。Consumer 原生输出修复已在当前 main，本次不改 Consumer 结构或行为。

Audit 使用现有 `AuditAgentWireResult` 生成 Codex 原生 Schema。为满足 Codex 最外层必须是 object 的限制，`outcome` 与 `feedback` 放在 `decision` 对象中，服务解析后还原成原 wire 结构。候选 revision/digest 仍从当前 `AuditTurnContext` 注入，候选字段断言仍经过原校验；业务审核和反馈规则没有变化。动态 JSON 值使用键值条目表示，并在严格 Pydantic 验证前还原。

验证记录：

- `tests/test_native_audit_output.py`、`tests/test_native_consumer_output.py`、`tests/test_agent_turn_runner.py`、`tests/test_audit_agent.py`：82 passed。
- Ruff 覆盖变更的五个 Python 文件，`git diff --check` 通过。
- 本机 Codex CLI 0.163.0 的只读匿名探针：首次尝试把条件 `oneOf` 放在最外层时，API 明确拒绝该 Schema；移入 `decision` 后，gpt-6-luna 冷启动生成完整 `return + feedback`，同一 session 续会话生成 `approve + feedback=null`。两轮没有调用工具或业务系统。
- 对冷启动返回的结果用服务端 `parse_native_result` 和当前 Audit parser 做本地回放，得到有效 return，revision 与 digest 由服务端注入。
- 生产配置的 `qwen_gpu4 / qwen3.8-27b / medium` 对照探针：三次使用原生 Schema 的隔离运行都未产生结果，完整轨迹显示模型调用 `exec_command`、`node_repl` 只读仓库查询，以及 Codex 未提供的 `request_user_input` / `update_goal`；没有连接业务 MCP 或执行外部动作。移除 Schema 并隔离用户工具后，同一服务 API 路线返回普通 JSON 并完成。结论是该服务 API 提供商尚未通过原生 Schema 验证；Audit 只在已验证的本地 OAuth 路线上选用原生 Schema，API 路线继续沿用原解析方式。

这证明 Codex OAuth / gpt-6-luna 的原生 schema 接受、冷启动和续会话格式；尚未证明其他 service API 提供商兼容性，也不是生产部署或生产回读证据。


## G2 认证无关原生格式候选（2026-10-25）

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
