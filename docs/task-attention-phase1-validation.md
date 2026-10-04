# 多来源项目关注第一版验收

最新实际读回（冻结da368453，2026-10-04）：真实W39副本两次run10662/10663均completed，三张实际关注卡分别支持中汽创智Task129、岚图Task130、质量Task131。第二次九张领域表全部逐行不变；卡片真实成员混淆未重现。259个原Tasks未增加；正式Projects从16到20，来源明确的四个Project实际登记。第一轮五个Task decisions包括来源明确共同负责人支持的Task129正式指派，不等于负责人已接受。第二轮task_decisions=[]。底层attempt18952正常完成，重复处理18953 superseded后18954通过既有result_validation_correction完成；这不是新增重试逻辑。

本轮原文业务审阅另外发现routine-progress语义FAIL：原文明确交付按计划、客户正常验收、回款和付款均按计划；实际outcome=insufficient_evidence，reason却以没有具体风险作为理由。这不是信息缺失，而是已有明确正常事实支持not_needed。该旧case只约束不误建卡，机械PASS不覆盖此区别；不得改旧expected/oracle掩盖问题。当前prompt/Skill/schema同时要求negative evidence，并笼统规定missing risk→insufficient_evidence，后段也将no risk直接归为insufficient；这一指导交界需继续核对，不以新增关键词分类器/validator强制改结果。risk-label-only正确返回insufficient_evidence，无Task信息和未知身份两例亦正确保留缺证据原因与空卡。普通正常进展的判断偏差与W39首次漏判断是两个独立业务门槛。

首次业务覆盖仍未通过：原W39项目清单包含中汽创智、岚图、项目管理、Einride POC、抽检包生命周期五项，第一轮仅四项project_assessments，漏掉Einride POC；第二轮才补充该项insufficient_evidence，保持null身份/空支持Task且无伪造Project、Task或卡片。不能用第二轮补齐替代首次逐项目覆盖验收，也不能由三张正确卡片或input模式passed=true宣称全部业务通过。实际native transcript中的第一轮415,113字符、第二轮440,319字符输入都包含POC原始项目行和逐来源Project判断要求，因此不是该行或指令未交付。只读重建上下文发现80个Signal、19个来源身份、17段不同截取正文，source_signals约243,163字符，其中evidence_text合计163,840字符；context_json仅约15,644字符。重复正文/输入体积是待验证的原因假设，不是已证实根因；尚未改检索预算、来源识别、模型或validator。

当前同冻结固定回放已有前11个独立case实际完成并通过原oracle（原多来源九例、Project身份竞争一例、report assessment一例）；第12项meeting assessment运行中。全20案例/22次固定回放仍未结束，不能继承旧46批次全通过。父PID3640/session44009仍有效，冻结源码不变；尚无push、PR、部署、全局Skill发布或生产修改。

当前冻结da368453dfd1914dd2052a75406cc4a3e10a5707：成员修复及整合的最终规格/质量复审均PASS，质量另独立2 focused passed/2.30秒。主Agent整合后14文件1234 passed/175.15秒，CLI22 passed/256 deselected/4.67秒，Ruff/五imports/diff通过；最新实际Agent prompt断言下multisource全文件151 passed/27.66秒；前端两页22 passed/1.16秒及TypeScript/Vite构建通过。CI Skill SHA256=c8c66dfbce241764c99dbb366c3600f7ef1498cac748f8c7170990687ac1f0f7。原19fixtures哈希未变，新独立成员fixture哈希9e5779976dbb229f94817c4dbd18b6f2a4d89d2ca964a2704108663e65cbf7fe。

新冻结原生批次已启动（父PID3640，观察session44009，2026-10-04）：先由immutable baseline备份为w39-real-final-da368453.sqlite3并quick_check，再真实input27465连续两次、比对九领域表；然后原19加新增成员case共20独立案例/22次固定回放（两个已有卡case各重复两次）。代码/Skill、原始事实/expected/oracle、native路由gpt-5.6-luna、900秒总时限/300秒idle/并发1固定；expected只在实际完成后的只读readback核验中使用，不进入Agent输入。当前仅启动/准备副本，不宣称任何新native或业务PASS。父进程遇到失败会保留原始DB/attempt并停止，不自动重跑或改写结果；旧观察句柄过期须先检查实际进程及保存run，不能因此重启。保留46批次与W39重复失败为阶段历史。尚无push/PR/部署/全局Skill发布/生产变更。

最新冻结46fa2ed6c0f63915335564a290bd08cff4a85db8批次已结束：原19个独立案例全部通过、20次Task回放完成。CI Skill SHA256=d371dce523c06eab8430e1025177c48695e1f998306b217b9b6902e7bd79352e，原固定事实、oracle、pinned baseline及模型/路由/900秒总时限/300秒idle/并发1未变。父进程的旧观察句柄已不存在，当前无该回放进程；主Agent据19份实际数据库重新运行只读oracle，核验全部保存决定、receipt和引用，不把句柄缺失当业务失败或重启批次。

机械与原文业务审阅分别完成：无字段变化的会议风险实际应用；聊天历史比较保存当前null-ID引用和历史Signal1原文，两源时间各自正确；两种模糊风险明确insufficient_evidence，正常交付/验收/付款为有事实支持的not_needed，均无卡；未知Project无正式Project/卡，无行动来源无Task/卡；同一Project两个真实Task共用单卡和两成员。合成W39的中汽创智、岚图两卡证明成立，但不是实际输入27465。

旧卡两次实际run completed、共享session01a1017a-8860-7d02-802c-9f23dae68f66，精确引用原Signal1及当前来源。九张领域表的所有行与原facts逐行相等：Task、Project、anchor、Attention/card成员/events、Task events/Signals/evidence。底层共23个CLI attempts，三例使用既有结构纠正（meeting-new-risk、assessment-chat-needs-attention、旧卡首次）；不把20次Task回放等同于20底层调用。

相同19份固定事实的已完成pinned baseline7bf7be5e结果也重新只读核验：4/19通过（原四个无误建卡负例），15例未通过；当前46候选19/19。基线正例包含缺卡、错Project身份/成员或缺原文证明，新assessment九例缺必填原始判断而失败；不能由候选默认值补齐旧输出，也不能以原native进程完成代替业务通过。此比较沿用已完成基线，并未伪报本轮重新运行baseline。

真实W39的新副本w39-real-final-46fa2ed6.sqlite3两次原生处理已结束，不能发布。初始quick_check=ok，259Tasks/16Projects/0cards/434Task events，精确input27465/source_ref成立。第一次run10662 completed，实际保存中汽创智、岚图及质量三张关注卡，成员分别为Task129、130、131；最终261Tasks/21Projects/3cards/448Task events/3Attention events。新增Task260来自本周明确负责人质量改进行动，Task261来自POC技术澄清工作；不能仅凭相似标题宣称260与下周不同执行者的131相同，也不能把工具input模式passed=true当成完整真实业务验收。

第二次run10663 failed：现有成员核验拒绝existing Attention card does not contain assessment supporting Tasks。实际attempt18955的原始decision对Project37声明task_ids=[131,260]、existing_attention_id=3，但卡3的实际成员仅[131]。第二次九张领域表没有新增或修改，说明未产生错误业务写入，不代表重复处理成功。根因追溯到retrieve/render接口：current_project_attention只包含id/anchor_id/why_attention/current_state/assessment_json/updated_at，遗漏真实成员task_ids；同项目关联Task不能代替卡片成员事实。正在补真实成员上下文及一致指引的RED/GREEN回归，保留现有成员核验，不自动扩大卡片成员，不修改原19案例或oracle。原始baseline、失败副本和attempt输出保留。

上游origin/main=d8805eef已正常整合为baa8d0a6，保留上游owner修复和退休Project处理。整合作者聚焦11文件1031 passed、CLI23 passed；这些是作者源码验证，不是主Agent最终独立验证或新冻结原生通过。上线仍须修复后的独立规格/质量验证、新冻结完整案例、真实两次业务结果和完整发布读回；没有push、部署、全局Skill发布或生产变更。

成员上下文修复e382acb5：真实Store回归先因缺task_ids而RED，指引三项亦先RED；仅为已选活动卡片读取实际成员，renderer输出真实task_ids，现有成员/proof核验不变。独立规格审阅PASS，36 passed/377 deselected；独立质量审阅PASS，9 focused passed/0.60秒，无待解决发现。主Agent独立11文件矩阵1046 passed/62.63秒，CLI process_work_items22 passed/256 deselected/3.73秒，Ruff、四imports、diff检查通过。实际前端两页22 passed/1.31秒，TypeScript/Vite构建通过。主Agent还以只读连接和修复版检索重建真实W39副本上下文，卡1/2/3分别输出实际task_ids=[129]/[130]/[131]，卡3不包含同Project的260。最新origin/main226272d6整合仍待完成，这些源码和只读上下文结果不能替代新冻结原生/真实W39复测或生产效果。

随后精确origin/main226272d6已正常合并为292db466，无冲突、无Task行为改动，保留外部runtime/CI提交且未合并分歧的本地主分支。整合作者相关Task/runtime文件1238 passed/62.96秒，CLI22 passed、lint/imports/diff通过，仍须主Agent最终独立验收。为补充真实多Task重复风险，另增独立version1 fixture task_attention_card_members_v1.json，只有一个case：同正式Project两Task、已有卡仅支持Task1、培训资料Task2不支持验收回款风险。原19case/expected及oracle不变。既有重放单测参数化覆盖原旧卡及新case，两次重放九张领域表逐行全部不变，2 passed/1.00秒；这是合成决定的领域控制，不是native通过。最终冻结将重跑原19及此新增case，并单独验证真实W39两次。

最终规格审阅对157ac5dc新增测试提出覆盖缺口：测试重新调用检索函数而没有证明实际Agent prompt含成员数据，因此不能排除传递层遗漏。修订测试直接从Codex收到的kwargs.prompt解析真实semantic context，断言卡片task_ids仅有实际成员；不再自行重建上下文。两次重复及九表不变的控制重新2 passed/1.76秒，Ruff/diff通过。仅测试证据修订，没有修改runtime、原19fixture/expected或oracle；等待正式复审后冻结。

阶段历史（2026-10-03）：冻结候选 `1732455c` 已正常结束，20次Task Agent回放机械18通过/2失败，按19个独立案例17通过/2失败，不能发布。此前47和421批次均已结束，失败原始记录保留；各版本结果不得继承。真实W39最新版副本重复回放、全局Skill发布、合并部署和上线读回均未完成。

当前源码进展：Derek已明确同意新证据可独立更新已有、已确认Task/正式Project的关注，不必制造Task字段变化。修订`3388c404`通过独立规格复审：首次字段更新后的同来源重放复用原Signal，不新增Signal、Task证据或Task/Attention事件；无效历史引文在新证据写入前拒绝；重复relevant值携带有效关注提案不改变Task事件。原有登记测试的断言保留。主Agent独立11文件矩阵1025 passed（57.15秒），CLI22 passed/254 deselected（2.06秒），Ruff、四个运行模块导入及diff检查通过。质量审阅仍在进行；下文f650失败及173原生失败是保留的历史证据，不代表新修订已原生或上线通过。

## 最新冻结候选1732455c最终读回

最终源码修订d9520ce3为Attention调用提供实际来源类型+ref的原文选择，普通owner调用维持既有行为。新增真实producer控制先RED后GREEN；独立规格和质量复审PASS，质量审阅另验证原文先到、更新的记忆引用后到，重放仍选择原文且Signal/evidence/Task事件/Attention事件全不变。主Agent最新11文件1026 passed（43.12秒）、CLI22 passed/254 deselected（2.28秒）、Ruff/四imports/diff通过。行为文档同步原文与provenance的选择规则，随后重新冻结全19案例/20回放；这不是native、真实W39或生产通过。

3388质量审阅另以真实两次apply_task_agent_decision复现来源身份缺口：先前memory/session引用可在同原始source_ref下建立provenance Signal；真实Work Item后来到达时，证据独立更新分支仅按ref选择最新Signal，误把cited来源当作observed来源，随后完整payload核验正确拒绝但也阻止有效新关注。该源码门槛FAIL，1025绿色测试不覆盖这项原文后来到达的控制；须以当前实际source_type和ref共同选择可复用原始Signal，保持旧provenance和真正原文重放的严格payload核验。正在补RED/GREEN回归，不启动新冻结原生批次。

2026-10-03后续源码修订（尚未原生验证）：实际native输入含正文和CI Skill的历史比较选择指令，但Pydantic输出schema的两处assessment_basis描述只定义两类判断，没有同步“当前明确比较历史且原始Signals已交付时必须核对原文”的选择要求。主Agent为两处描述写一致性回归，观察2例RED后仅同步字段说明；test_task_models.py120 passed，Ruff/diff通过。领域/结构validator、fixture/oracle、模型、检索和原始失败未改变。此证据证明指令一致性，不证明模型稳定性已修复；新冻结原生回放仍是验收门槛。

随后将d6a9b074单独冻结于原生候选checkout，仅回放原固定chat-with-report-context（fresh事实副本，同原模型/路由/900/300/并发1，oracle不进入输入）。实际native run/投影成功，固定oracle PASS；原始判断采用historical_comparison，同时引用当前聊天null-ID证据及历史Signal1/eval:historical-report原句，卡片实际保存两源及各自来源时间。该单项诊断已正常结束exit0，不证明全批次或长期稳定性；需在证据独立更新功能合入后重新冻结并验证全部原19案例和旧卡重复回放。

已批准的证据独立更新实现f6501188局部两文件373 passed，但主Agent独立11文件矩阵为1019 passed/2 failed（79.68秒），不能冻结发布：原有source_project_registration两例在首次正常字段更新、相同来源第二次进入无字段变化路径时另建Signal并改变卡片证明/事件。独立规格审阅以真实Store复现同一问题，另发现无效历史proposal引文会在投影拒绝前留下新增Signal/evidence，以及重复relevant值携带Attention时可能落回普通更新副作用路径。这些失败保留、正在修订；不改旧测试预期、不降低引用核验、不改变无Attention的既有相关性确认语义。CLI process_work_items独立22 passed/254 deselected（4.90秒），lint及运行模块导入通过；各自不是全业务通过证明。

同项目两个真实行动分别保留Task1/2，关联单一Project1/Card1；普通进展返回not_needed、无卡。无行动信息实际输出skip且Tasks=0；未知项目保留来源明确的核对候选行动但Projects=0/Cards=0，未制造官方身份。已有卡两次复用均receipt=existing，引用原始Signal1/ref/quote和当前null-ID来源，零Task decisions/proposals。主Agent按全部列逐行只读比较九张领域表（Task/Project/anchor、卡及成员/事件、Task事件/Signal/evidence）：原facts与最终副本完全相同，两条新Task Agent runs完成。共享native session及实际attempts另行读取，不以20次业务回放冒充20次底层CLI尝试。

原固定risk-label-only和assessment-vague-risk-not-needed两例均实际返回insufficient_evidence并保留真实来源行动、无关注卡；后者固定oracle通过。负面判断澄清已有本轮原生证据，但不等于全批次业务验收。

chat-with-report-context机械失败：输出current_observation且只引用当前聊天，漏掉原历史Signal1。主Agent只读重建原facts DB检索：Signal1完整原文、eval:historical-report及2026-09-24来源时间都在2,860字符context内。进一步读取421及173的实际native session用户输入，两轮完整发送内容分别88,542和89,230字符，均包含相同历史原文及要求历史比较的指令；421引用两源而173未引用。确认不是历史检索遗漏或该引文截断；整体指令长度是否影响选择尚属未验证假设，未据此修复或放宽oracle。

assessment-meeting-needs-attention机械失败：Agent run completed、判断needs_attention和当前原文引用成立，但update_fields没有实际Task业务字段变化；Attention projection按现有guard拒绝，reason=proposal has no applied Task decision。既有Task/正式Project关联真实，领域事件未新增。需要Derek确认“新证据可独立更新项目关注，无需修改Task字段”的业务规则后才可调整，不能伪造字段变化或暗中绕过已批准的guard。当前无相关代码改动，原批次已结束且结果冻结。

## 修复候选 47a56a7f 原生复测

后续冻结421批次已正常结束，20次调用机械19通过/1失败，按独立案例18/19；不能发布。原文换行两例不再被引用检查拒绝，旧卡两次复用均完成，receipt=existing，真实Task1/Project1/Card1保持不变。每次判断并列当前null-ID引文和卡片原始Signal1/ref/quote，没有新增Task决定或提案。人工业务复核另外保留risk-label-only的错误负面outcome，因此业务按独立案例17/19，而不是机械18/19。两个业务问题同属缺少风险影响证据却返回not_needed；固定assessment-vague案例机械失败已暴露同一问题。`853f590e`源码澄清须以新冻结候选重跑全部原19案例和最后一次重复，不继承421已通过项。
这里20次指Task Agent回放，实际持久化runtime attempts为22：18个normal completed、2个normal superseded、2个既有result_validation_correction completed；项目名称竞争与旧卡首次处理分别使用一次原有同session结构纠正，不是新重试循环。旧卡两个最终完成回合确实复用同一native session，第二回合的source_session_id指向第一回合session。与原facts DB逐表只读比较，Tasks、Projects、anchors、Attention及成员/事件、Task事件/Signals/evidence九张领域表的全部行均完全相同；只有两条新run及其运行记录，证明没有靠伪造字段变化完成复用。

后续冻结 `1732455cbe12c107f4e9e1a58fb1b6aef7ac8cd1`（功能修订853，Skill SHA256 `f96ccf69130db4a9ac39761659a5032e8fbfb6e1bcda90645a4736a734fa833d`），完成同19案例/20回放的原生重验，最终结果见上节。两批不并发执行，原421已终止才快进隔离候选checkout；固定事实、原文、oracle、pinned baseline和模型/路由/900/300/并发1配置不变。真实W39、生产及发布门槛均未完成。

此前冻结 `421af739` 运行期间，主 Agent 读取 `risk-label-only` 的原始 decision 后发现机械检查未覆盖的语义差异：输出为 `not_needed`，但理由明确说具体业务影响和材料性风险证据缺失，原文也仅有风险标签及汇总行动。旧九案例 oracle 正确证明没有误建卡，却没有要求新增 assessment outcome，因此 mechanical PASS 不等于该判断符合本轮 spec。该原始输出保留并记为 BUSINESS FAIL；应按已批准的“缺少经营影响证据 → insufficient_evidence”区分，而不是把未知当成不需关注。

对此只澄清 prompt、CI Skill 和 outcome 字段说明中的负面判断含义：not_needed 要有来源事实支持不需关注；风险或经营影响证据缺失须写明缺口并返回 insufficient_evidence。没有修改判定标准、fixture、结构验证、领域应用、投影或原始结果，没有关键词分类器、服务端 outcome 改写或自动补卡。该澄清在主开发工作区进行，冻结421的原生批次不受影响；源码和后续原生证据分开记录。
三个新增指令一致性回归先 RED；三项相关文件 487 passed（18.40 秒），独立源码审阅 42 项通过，Ruff/diff 检查通过。它们证明源码边界和规则一致，不证明新指令的原生效果；最新候选仍须冻结并重新实际验证。
`853f590e` 已提交上述澄清。随后冻结421跑到原固定 `assessment-vague-risk-not-needed`：原有引文问题不再发生，run 完成、真实候选行动和 Project 引用保存、无关注卡；但原始 outcome 仍为 not_needed，固定 oracle 明确返回 project_assessment_outcome_mismatch。因此这不是仅人工偏好或换行问题复发，而是已确认的同一负面判断语义偏差；853的效果仍需实际原生运行证明，不能继承421的通过项。

主 Agent 在853上再次运行相关开发矩阵：11个后端文件1015 passed（44.40秒）；process_work_items CLI22 passed /254 deselected（2.58秒）；配置的frontend Vitest4.1.10两页面22 passed（1.70秒）；TypeScript/Vite构建及四个运行模块导入通过。最初从仓库根目录误用npx导致未配置Vitest5启动失败，不是页面断言失败；改用frontend现有依赖后完成上述正确验证，没有修改依赖文件。未运行整套服务测试，未在生产checkout构建或运行测试。实际会议输入27478在生产与已准备副本中ref、来源时间及完整payload SHA256完全一致，属于后续真实非周报回放准备，尚未原生处理或修改生产。

本轮 19 个固定案例及已有卡第二次回放使用同一冻结源码、Skill SHA256 `118250132fd7579afaec136df7fd7feffa299e73e59faf1d31f112cc819eae97`、原固定事实和相同模型/路由/900 秒总时限、300 秒 idle、并发 1。基线沿用此前 pinned `7bf7be5e` 在同一事实和运行配置下的已完成结果，未替换或改写。expected 仍只在原生完成后用于只读核验，没有修改三份 fixture 或 oracle。

16 个独立案例实际通过；`meeting-new-risk`、`chat-with-report-context`、`same-project-two-actions`、`assessment-unconfirmed-project` 的原生失败已经转为通过。单独会议和聊天实际生成关注卡，未依赖周报；同项目两个真实行动保留两个 Task、一个 Project 和一个卡片，成员真实。未知 Project 保留来源行动候选但不伪造 Project/Card，回执没有借用未确认身份的 Signal。

`assessment-vague-risk-not-needed` 和 `assessment-routine-not-needed` 的原始经营判断分别为 insufficient_evidence 和 not_needed，但模型将两行原文拼成一行 source_excerpt，原有 immutable Work Item 引文检查拒绝，领域对象没有写入。`assessment-existing-card-idempotent` 两次原始输出均引用当前“项目群重申”全文而非卡片 assessment_json 内保存的原句，却声明 existing_attention_id=1；原始证明检查拒绝，Task、Project、卡片和事件没有新增。旧卡及其精确证据在 bounded context 中存在，不是来源遗漏。三类失败各自保留原始 attempt envelope 和数据库副本，不能因判定业务方向合理或身份未重复而计为通过。

`bc99e152` 仅统一 prompt、CI Skill 和字段说明中的连续逐字引用指引，保留标点、空格及换行，并删除旧文档矛盾说法。普通 Task 和 owner_evidence 的原有验证行为不变，日期及 Project assessment / Attention 按各自原有规则校验。新回归观察 RED 后 GREEN，三项相关文件 481 passed（18.39 秒），独立四项 source review 通过；这不是新版本的原生通过证据。冻结 47 的重点后端 11 文件 1006 passed，CLI process_work_items 22 passed / 254 deselected，两个页面 22 passed、TypeScript/Vite 构建通过，均与上线效果分开记录。

## 冻结配对最终读回与回执修复

原批次已正常结束，没有因空轮询重启。此前前 12 对的记录保留在下一节作为阶段历史；
本节补齐其余结果，code/Skill/fixture/model/timeout/concurrency 均未中途改变。

| 后续 case | 候选实际结果与人工来源复核 |
| --- | --- |
| assessment-chat-needs-attention | 通过：新聊天明确验收推迟及供应商条件性暂停供货，更新原 Task，并以当前真实 Signal 生成 Project1/Card1，不要求周报。 |
| assessment-vague-risk-not-needed | 失败：风险内容缺失的 insufficient_evidence 判断正确，真实汇总候选 Task1 已保存、无卡；已知 Project1 回执却漏掉支持 Task1，固定 oracle 拒绝。 |
| assessment-no-task-insufficient | 通过：满意度下降原因和经营影响未核实，且来源明确无行动；零 Task/卡，不为风险制造行动。 |
| assessment-unconfirmed-project | 失败：未知项目的不足证据判断正确，保留真实核对候选 Task1、无 Project/卡；项目线索回执没有 Task 身份，却附上该 Task 的 Signal1，Task-or-card 引文关系检查拒绝。 |
| assessment-routine-not-needed | 通过：正常验收及收付款均按计划，真实汇总 Task1/Project1，无卡；“CEO 介入”措辞仍是术语复核项，不等于误关注。 |
| assessment-two-tasks-one-project | 通过：80 万回款不能按计划实现及供应商到期付款有原文，两个真实 Task1/2、一个正式 Project1、一个 Card1，两成员都真实。 |
| assessment-existing-card-idempotent（两次） | 两次失败：原生判断同事实无需新 Task/proposal，但 existing_attention_id=1 只引用当前聊天，没有引用该卡原始 proof；领域检查拒绝，Task/Project/card/事件均无新增。身份未重复不等于成功复用。 |

最后案例的旧卡 `assessment_json`、原始 Signal 和支持 Task 都在当前检索 JSON 中；
失败不是来源未交付，而是输出没有履行原始引用要求。修复分支已有 `b965df0c` 的
原始 proof 声明澄清；须实际原生重跑才能证明有效，不能用源码文字测试代替。

`58b8e623` 修复两项真实回执问题，固定 oracle 和三个 fixture 完全未改：已解析正式
Project 的支持位置上实际保存的 Task 编号留在回执，即使没有 confirmed link；编号只是
支持引用，不宣称正式任务或项目成员。未确认 Project 仍是 evidence-only，回执 Task IDs
为空，当前引文不借用这些未列入支持任务的 Signal，保留 null ID 和来源时间。
原始 Task/Signal 的保存、Project 登记、卡片投影、no-field-change guard 均不改变。

两项真实 SQLite 回归先观察失败，再修复；模型/Agent/multisource 478 项通过（36.16 秒），
独立 17 项有界复核通过（1.89 秒），Ruff/diff 检查通过。每项都从同一固定 facts DB
另做 fresh backup，使用未改动的已保存原生 decision，仅应用新版领域代码；两个控制
均通过原固定 oracle。它们是受控领域证据，不是修复版原生运行。初次扩大到未知项目
Task ID 的控制仍失败，被保留，最终实现收窄到上述已批准边界。

原 batch 的七次失败仍保留。三个修复提交尚未使 native gate 通过；下一轮必须冻结
同一最新 code/Skill，用原 19 案例及最后案例两次重复执行，不继承旧候选通过。

历史进展：标题和 Project 身份修复均经独立复核；`c87752c0` 新副本实际更新六个原 Task，
无新增 Task，正式 Project 中汽创智和岚图登记正确；但 proposal_count=0，receipt=no_proposal，
没有卡片。当前输出不能区分已评估后不关注与漏评，也没有给出这两项风险不关注的理由。
不得将工具 input 模式 passed=true 或领域提交完成当作真实业务通过。详见末尾本轮结果。

## 19 案例冻结配对的前 12 对阶段历史

此节只记录已完成到 `assessment-meeting-needs-attention` 的前 12 对；后续案例及已有卡两次
重放仍在同一原生批次执行，不因某次观察没有输出而重启。固定 baseline 为
`7bf7be5e6dcdb181b0674e79ace17a598dcf87e0`，候选为
`fbba12e906888a59e5b7f8b7821c8ebcf44e9901`；各侧使用自己的 app 与 CI Skill。
实际调用均为 `codex_oauth` / `gpt-5.6-luna`，installed CLI 0.154.0，concurrency=1、
有效 total/idle 为 900/300 秒。baseline Skill 为 `5c2bcbee...`，冻结候选为 `246688c2...`。
先用领域命令建立同一份固定事实，再 SQLite backup 为两侧 fresh DB；原生只接收
exact input/ref，expected 仅在完成后由只读连接比较。副本和原始输出保留于
`/var/folders/74/yj2lxqs162q7rqzm0mj8nv1c0000gn/T/ceo-attention-assessment-20261002-wZmAbc`。

| 已完成 case | 候选实际结果与人工来源复核 |
| --- | --- |
| w39-project-risk | 通过：4 个真实候选 Task、2 个规范 Project、2 卡；两项目的经营影响均有原文，非项目行动不造 Project。此为固定样例，不是真实 input27465。 |
| meeting-new-risk | 失败：当前新风险提案同时声明 existing_attention_id，历史 seed 卡 assessment_json={}，领域原始证明检查拒绝整次写入。 |
| chat-with-report-context | 失败：同样把旧卡 ID 当更新目标，未证明旧卡自身保存的原始证据；不是当前/历史来源未交付。 |
| newer-conflicting-chat | 通过：新聊天和原始旧周报双方引用、时间并列，保留冲突待核对，不覆写登记。 |
| risk-label-only | 通过：具体风险内容和经营影响缺失，明确 insufficient_evidence，真实汇总行动保留、无卡。 |
| routine-progress | 通过：按计划进度、验收及收付款无重大变化，not_needed，无误关注。负面理由一次使用“介入”措辞，后续语义复核仍须遵守“关注不等于介入”。 |
| unconfirmed-project | 通过：真实行动候选保留，未确认项目身份写明缺口，不造正式 Project/卡。 |
| no-real-task | 通过：无行动项且经营影响待核实，明确 insufficient_evidence；零 Task/卡，不为风险造任务。 |
| same-project-two-actions | 失败：2 个真实 Task 与同一 Project 已正确落库；两 Attention 提案仅 current_state 的各 Task 行动摘要不同，投影明确拒绝 distinct proposals，零卡。 |
| current-authority-project-name-competition | 通过：复用当前权威完整项目名的实际 anchor、原 Task 和当前风险卡，旧简称项目登记不被改写。 |
| assessment-report-needs-attention | 通过：一个来源行动、一正式 Project、一卡，当前具体经营影响的显式判断与 actual receipt 一致；baseline 多提取一条登记范围 Task且无卡。 |
| assessment-meeting-needs-attention | 通过：已有 Task/Project支持单独会议中的验收与付款重大变化，当前 Signal和会议时间真实，无周报前置条件。 |

前四负例的 baseline 同样没有误关注，不能因它缺新 assessment 字段而宣称其业务结果全部失败。
新 assessment 案例的历史字段缺失单列为可观测性缺口；actual Task/Project/card 错误另行判定。
每个通过都经过原文、真实理由、Task/Project/card/引文读回，而非仅计机械 passed。

### 已确认失败的独立源码修复（原生效果仍待重验）

`b965df0c` 仅同步 prompt、CI Skill、existing_attention_id 字段说明与行为文档：该 ID
声明旧卡保存的原始证明，不是 upsert 目标。新风险的匹配 proposal 已通过 Project stable key
复用卡片；未引用并核实旧卡 proof 时可选 ID 保持 null。真实旧 proof 与当前 proposal 仍允许并存，
不放宽领域引用核验。三个新回归先失败；10 个重点控制与 473 个相关测试通过，独立源码审查
11 项通过、SOURCE PASS。控制回放只删除已保存原生 chat 输出中的可选旧卡声明，得到 completed、
同 Task1/Card1 和当前/历史原始 Signals；这是单变量领域证据，不是修复后 native PASS。

`cc5a52e1` 只明确同项目支持 Task 的 current_state 使用共享项目风险事实，完整 proposal
复制不变，各自行动留在 Task description/update_summary。三个新回归先失败；5 个重点控制与
333 个模型/Agent 测试通过，独立审查 6 项通过、SOURCE PASS。另一 fresh 事实副本只将原生
第二 proposal 的 current_state 复制为第一份，得到 2 Task/Project1/Card1 和 completed receipt，
证明失败来自内容分叉；没有让领域自动改写、放宽折叠/拒绝或更改固定 oracle。

这两项在独立 `attention-native-result-fix` 工作区提交，原 fbba 冻结 checkout/fixture 仍不变。
修复后 CI Skill SHA256 为 `118250132fd7579afaec136df7fd7feffa299e73e59faf1d31f112cc819eae97`；
原三份 fixture hash 全部未变。该 Skill 尚未发布，修复版本尚未原生重跑，不继承 fbba 或历史版本成绩。

会议控制同时发现无字段变化 Task 的 proposal 按现契约被跳过，产生 partial receipt。
允许新风险证据独立更新卡片、而 Task 字段不变，会改变已批准的明确跳过规则；已单独向 Derek
提出选择，尚未得到回复，源码没有改动这个边界。真实原始 W39 input27465/ref、259 Tasks/16 Projects/
0 卡及 Task129–134 无 Project links已只读再次核对；这只是下一步回放准备，不是新版真实副本通过。

## 四个独立门槛（以下为较早阶段记录；本轮以明确冻结版本读回为准）

| 门槛 | 当前证据 | 仍需完成 |
| --- | --- | --- |
| 代码测试 | Tasks 1–6 已独立复核。Task 6 的 `128ea5b4` / `f92d0a92`：7 项 API、22 项页面测试与构建通过；主 Agent 检查模拟列表及 decision/watch 详情，桌面及 390×844 窄屏亮/暗色可读。Task 7 的固定输入、精确回放、旧 run 保留、幂等卡片/事件、落库依据与 expected 隔离回归先 RED 后 GREEN。 | 合并前重跑本次有关文件；模拟页面及 fake runner 不证明真实业务效果。 |
| 语义评估 | 固定 version 1 的 9 个 case；expected 与输入分开。baseline `7bf7be5e6dcdb181b0674e79ace17a598dcf87e0` 为 4/9 通过。最新冻结候选 `fc58e803` 为 9/9 通过，全部同一 code/Skill、模型/路由、timeout、concurrency=1。两聊天实际引用历史和当前原文；同项目两行动确为两 Task 同一卡成员，四负例无误关注。具体比较及旧失败保留于末尾。 | 真实副本尚未通过；如再改核心、schema 或 Skill，最新修订须重新验证，不能继承本轮通过。 |
| W39 数据库副本 | SQLite backup 初态完整性验证通过，259 Tasks、16 Projects、0 Attention。精确输入 27465/ref、原 run 10634 已保留。候选首次回放生成 run 10662，提交前因 title 缺失失败，无领域部分写入；见末尾。 | 修正已确认契约问题并诊断 Attention 提案缺失后，再实际回放及重复验证 IDs、关联、依据和事件。不得为落卡编造字段变化、强行合并简称或忽略额外 Task。 |
| 上线 | 全局权威 Skill 仍为 version 2，SHA256 `5c2bcbee182ed0872a55f35193c8815a51dd3e0ba0ca92b8e1eaa55e20cc1992`；候选使用隔离 `ci/shared-skills` version 3。 | PR + 固定 eval 对比通过后按既有部署流程发布、读回运行健康及队列、发布权威 Skill、单输入生产回放及真实页面核对。尚未上线。 |

SQLite backup 初态由主 Agent 持有：
`/var/folders/74/yj2lxqs162q7rqzm0mj8nv1c0000gn/T/ceo-attention-eval-40j4skvv/baseline.sqlite3`。
此文件不作为脚本直接运行目标；候选回放使用另一个由 SQLite backup 生成的副本。最终报告后按既有备份清理规则处理。

## 原生 runner 调用

脚本仅处理一个 ID 或一个固定 case，不调用业务 CLI 的 pending 遍历、扫描器、outbound DWS 或恢复流程。它取得所传副本的现有 TaskAgentSessionLease，调用现有 process_work_item，并通过 scoped TaskAgentRunner 使用 `task-agent:attention-eval:v1`，不续接生产 scope 的 session 指针。

执行环境先注入生产 launchd 的环境参数，并指定 `CEO_ENV_FILE` 为真实生产 `.env`；脚本在选定 `--code-root` 后才 import app 并读取该配置。`CEO_WORKER_DB` 必须保留生产路径，`--db` 独立选择评估副本：脚本在打开 store 前拒绝等于 worker_db_path 的路径。`CEO_SERVICE_MCP_CONFIG_PATH` 等现有相对配置由调用者解析为实际绝对路径；不建立临时 MCP 配置。须明确提供生产 `CEO_TASK_CODEX_TIMEOUT_SECONDS` 和 `CEO_TASK_CODEX_IDLE_TIMEOUT_SECONDS`；脚本按相同版本业务 CLI 的 TASK_AGENT_MAX 常量取 min，输出实际有效值。现有 prompt 的外部写禁止仍为 best-effort，不声称硬隔离。

以下是 Task 7 原九样例当时的历史调用：baseline checkout 与其 version 2
Skill、候选 checkout 与 version 3 Skill 分别用 fresh case DB 顺序执行，
concurrency=1，不用候选 app 冒充 baseline。这两条 `--case-id` 命令只对应
当时的源码/样例，保留作历史证据；当前 assessment fixture 的已有卡 seed
包含新 `assessment_json` 事实，pinned baseline constructor 不能直接构造它，
不得从当前脚本重放这两条命令来宣称新配对评测完成。

```sh
CEO_SKILLS_ROOT=/Users/derek/Projects/ceo-agent-service/.worktrees/attention-eval-baseline/ci/shared-skills \
python /Users/derek/.codex/worktrees/task-attention-multisource/ceo-agent-service/scripts/replay_task_attention.py \
  --code-root /Users/derek/Projects/ceo-agent-service/.worktrees/attention-eval-baseline \
  --fixtures /Users/derek/.codex/worktrees/task-attention-multisource/ceo-agent-service/tests/fixtures/task_attention_multisource.json \
  --db /ABSOLUTE/EVAL/baseline-w39-project-risk.sqlite3 --case-id w39-project-risk

CEO_SKILLS_ROOT=/Users/derek/.codex/worktrees/task-attention-multisource/ceo-agent-service/ci/shared-skills \
python /Users/derek/.codex/worktrees/task-attention-multisource/ceo-agent-service/scripts/replay_task_attention.py \
  --code-root /Users/derek/.codex/worktrees/task-attention-multisource/ceo-agent-service \
  --fixtures /Users/derek/.codex/worktrees/task-attention-multisource/ceo-agent-service/tests/fixtures/task_attention_multisource.json \
  --db /ABSOLUTE/EVAL/candidate-w39-project-risk.sqlite3 --case-id w39-project-risk
```

对真实 W39 副本执行两次相同命令；它核对固定 source_ref 后，只将这条副本输入标为 processing（现有 begin_task_agent_run 要求该状态），保留 attempts、payload 及所有旧 runs，每次创建独立新 run。

```sh
CEO_SKILLS_ROOT=/Users/derek/.codex/worktrees/task-attention-multisource/ceo-agent-service/ci/shared-skills \
python /Users/derek/.codex/worktrees/task-attention-multisource/ceo-agent-service/scripts/replay_task_attention.py \
  --db /ABSOLUTE/EVAL/w39-candidate.sqlite3 --input-id 27465
```

固定 source_ref 为：
`dingtalk-doc:a9E05BDRVQvy7QEacPZLB4anJ63zgkYA#sha256=21661643562265ca27e3369112a7ce3e91d9cbb6d21733050b5c3e7a9d42bf1e`。

stdout JSON 保存 code revision、实际 Skill 路径/hash、配置路由/model、有效 timeout、proposal 数、独立 projection 回执、实际卡片及精确 evidence、Task/Project/事件 ID 变化、失败原因。baseline 缺少新 projection 列时回执为 `{}`，不伪造成功；旧 response/schema 失败按其真实版本保留，不改写成候选 evidence。expected 只在原生 turn 之后比较，不能进入 prompt 或历史种子。真实输入模式不自动断言 W39 两卡业务正确，需主 Agent 依据实际 IDs、项目、任务及原文回读验收。

## 接受标准

固定正例全部达到项目卡预期；负例无误关注、伪项目或伪 Task；真实卡片引用有可核对的 signal/source_ref/连续原文/来源时间及支持 Task；每张目标项目卡都必须包含 expected.required_source_refs 指定的当前来源，聊天综合及来源冲突样本还须包含历史周报来源，不以一张历史卡或来源数量替代本轮证据。required_source_refs 仍只在落库后断言，不进入 Agent 输入。当前有关注提议时，已记录的 projection 必须 completed；pending/partial/failed/no_proposal、rejected/error outcome 或 recompute_error 都使本轮评估失败。无提议时已记录回执只允许 completed/no_proposal 且无失败结果；实际 baseline 的未记录空回执 `{}` 保持独立诊断，不补造也不因此自动判失败。同项目两行动保留两 Task 一卡；会议/聊天更新复用当前卡；来源冲突保留双方证据而不改写登记字段。W39 两次回放不重复 Task/Project/card；卡片关联中汽创智及岚图的真实任务和原文；第二次卡 ID 不变且未变化不追加事件。任何真实模型失败都记录原 case 并修正 prompt/context 后重做相同对比，不用 fake 输出替代模型门槛。

Task 7 规格复核补强：`2abb82fd` 的评估工具曾可能把仅历史证据的更新或未成功投影的旧卡计为通过；已用真实 Store/full process_work_item 的确定性失败回归复现并修正。此修正仅改变一次性评估断言，不改变生产 Task Agent、投影或权限行为；真实语义比较仍待执行。

Task 7 质量复核补强：同一项目两个行动的 fixture 在 expected.required_project_member_counts 中明确要求该项目卡包含两个不同 Task；实际已有 task_count=2 不能替代卡片成员验证。full process_work_item 回归包含两个引用真实原文且确认关联同一 Project 的 Task，只有首项提出关注时曾被误计通过；新增落库后成员数量断言将其记录为 missing_project_task_member。两个行动都实际成为同一卡成员时通过；不按 Task 标题固定措辞判定，也不将 expected 送进 Agent。尚未据此更改生产 prompt 或核心行为。

## 首个真实模型失败与限定修订

2026-10-02，主 Agent 以同一固定 `w39-project-risk` 输入、`codex_oauth` 路由、
`gpt-5.6-luna` 模型和 concurrency=1 跑完 baseline 与候选 `4f0e06e5`；实际 Skill
路径/hash 已读回，候选使用隔离 revision 3。两侧运行均 completed，均没有关注提议或卡片；
baseline 落库 4 Tasks/2 Projects，候选落库 3 Tasks/2 Projects。此处是固定脱敏样本，
不是精确生产输入 27465 的数据库副本回放。

候选固定样本库只读核对路径：
`/var/folders/74/yj2lxqs162q7rqzm0mj8nv1c0000gn/T/ceo-attention-eval-40j4skvv/candidate-w39-project-risk.sqlite3`。
run 1 的顶层 update_summary 明说项目带报告注册提案，但任务缺明确负责人而保留候选，
且未创建 CEO Attention。两个项目 Task 的逐项 update_summary 明确写出
“当前没有已注册项目锚点或可验证的新增材料触发”。原 prompt 首句要求 registered official Project anchor，
后文才允许同一 decision 的 null anchor；Skill 第 9 步也先要求 confirmed Project。
此外原规则笼统排除 static facts，容易把首次观察到的当前未解决重大风险当作没有新变化。
这支持一个需模型重跑核验的资格解释假设，不证明只改文字已经解决行为。

限定修订仅同步 build_task_agent_prompt 与隔离 Skill：正式 Project 可为已有已确认对象，
或同一 decision 的有效当前权威 ProjectProposal 本轮解析出的对象；首次观察到有具体
经营影响的未解决重大风险可 watch，无需已有卡或对不存在的旧评估证明新变化。
已有卡已反映同一事实时不重复提案，标签、普通进展和日期临近仍不足；真实候选 Task
无需补造负责人或承诺即可支持风险。保持引用核验、真实 Task 和当前无需处理的 watch 表述。
不修改 Project 注册、投影、parser、transition 或全局 Skill，不增加 Agent/队列/策略层，
不把样本业务名、金额或 expected 注入规则。

两个新回归先因缺少资格文字失败，再验证 prompt 与 fresh module 通过 CEO_SKILLS_ROOT
实际选择并完整加载的 Skill 内容；相关 task_agent/retrieval/session 142 项通过。
此轮回归只证明文字契约和实际加载路径；后续 W39 固定样本两卡已验证，最新关联修订的全 9 样本及生产副本回放仍待验证。

## 同项目两个新行动的原生成员失败与限定修订

2026-10-02，固定 `same-project-two-actions` 原生候选运行 completed；只读核对
`/var/folders/74/yj2lxqs162q7rqzm0mj8nv1c0000gn/T/ceo-attention-eval-40j4skvv/candidate-same-project-two-actions.sqlite3`
run 1：三个候选 Task、一个 Project、一张 Attention 卡，卡成员只有 Task 1。
Task 1 引用项目登记行的负责范围，成为泛化的验收/付款协调 Task；Task 2 与 Task 3
分别引用两条明确行动，但 attention_proposal 均为空，未成为卡片成员。
这是实际成员缺失证据，不以一张卡或总 Task 数替代关联核对。

原因对应两处文字缺口：登记范围在具体行动已覆盖同一工作时仍被提取为额外 Task；
“每项目每轮最多一个关注提案”与仅接受已有 ID 的 related_task_ids 组合，使本轮多个
新 Task 难以同时表达支持成员。既有投影已把除 anchor/related IDs 外完全一致的提案
折叠为一张卡，并合并每项实际应用 Task ID，无需修改领域或投影代码。

限定文字修订同步 prompt 与隔离 revision 3 Skill：登记范围/目标/类别不在具体来源行动
已覆盖时另建泛化 Task；任何章节中真实明确行动仍可保留，不限制为下周重点。
每轮每 Project 一个唯一 assessment/卡片，多个新建且实际支持该风险的 TaskDecision
各附完全相同的 attention_proposal，沿用既有折叠行为完成成员合并。
related_task_ids 继续仅接受真实已有 ID，无关项目 Task 不加入，冲突内容仍拒绝。
不修改核心/API/模型、全局 Skill 或固定样本 oracle，不写业务名、金额或预期数量进规则。

两个通用回归分别验证 prompt/Skill 的登记范围与新 Task 成员表述，共四个参数化用例
先 RED 后 GREEN；fresh Skill 加载回归也验证新增文字实际进入 runner。
相关 task_agent/retrieval/session 146 项通过。此处只证明规则契约与加载，
后续 fresh case 9 已验证两真实 Task 同一卡两成员；最新关联修订及其他样本效果仍待验证。

固定计数另经原生结果后的来源/独立交付契约复核，由主 Agent 单独修订评估 oracle：
W39 显式允许 [3,4,5]，meeting-new-risk 允许 [1,2]，其他样本保持原精确计数，
不改为全局最小数量门槛。W39 的五项都有真实行动依据：进展章节的“需同步”不能因
所在章节被排除，同句对账与方案沟通可以是独立可完成结果；Task 3 的描述仍含方案沟通，
与 Task 4 有范围重叠，保留人工身份复核注意点，不据计数放行把重复身份说成已排除。
两侧 baseline/candidate 以同一修订后 oracle 重新评估，expected 仍仅用于运行后比较，
不进入 Agent 输入、routing 或规则。首次资格修订后的 W39 已实际生成两张卡且依据有效；
同项目多新行动的实际成员门槛未放宽，后续 case 9 fresh rerun 已通过该门槛。

## 已有 Project 下新会议/聊天 Task 的关联缺口与契约修订

后续原生结果由主 Agent 保存并复核：`1ed0f3f2` 的隔离 Skill hash 以 `3ec776` 开头，
同路由/model 的 candidate-final-w39-project-risk 为 3 Tasks/2 卡；
candidate-same-project-two-actions-rerun1 为 2 Tasks/1 卡且两实际成员。baseline 9 样本
已完成，4 负例通过、5 正例失败。它们是该修订前的模型证据，不证明本节新增契约效果。

新的失败来自 `candidate-final-meeting-new-risk.sqlite3` run 1，已独立只读核对。
该 run completed，两个 Task、两个相同 Attention 提案，projection_json 为 failed，
project_link_count=0，两项 rejected 原因均为支持 Task 必须 relevant、open/waiting
并确认到同一活动 Project。既有 Task 1 已 relevant/open 且 confirmed anchor 1；
新 Task 2 是 unknown/open、无 Project link。两决定的 anchor_match_proposals 都为空，
Attention.anchor_id=1 只选择卡片目标，并未确认 Task 关联。

代码检查证明仅改 prompt 不足：原 TaskAnchorMatchProposal 只有 anchor_id/reason，
应用只产生 proposed；新 Task 决定也不能直接设置 business_relevance，因为该字段须
update_fields。普通会议/聊天当前来源不能为关联而伪造项目立项提案。

因此在原已批准的已有项目关联目标内新增 TaskProjectLinkProposal：严格正整数 anchor_id、
非空 source_excerpt/reason，TaskDecision.project_link_proposal 可选；与登记 proposal
互斥，存在 Attention 时其 anchor 须与链接一致。领域应用要求当前来源引用/原始证据，
目标为活动已注册正式 Project；逐字引用与 Task 的行动引用互相包含并包含存储的
Project/anchor 标题。引用/标题检查是最小来源和名字引用证明，完整身份语义仍由 Agent
判断，不推断简称别名或凭前缀自动匹配。通过后调用既有 confirm_anchor_match，只确认
实际应用 Task 的关联并派生 relevant，计入 project_link_count；不注册或改写 Project，
不升级 Task stage，不补造负责人/日期/承诺。旧 uncertain anchor proposal 仍 proposed。
原无字段实际变化的 update guard 在关联/投影前保留；不能以新增链接绕过该 guard。
update 信号效果身份包含实际关联目标/引用，不包括 reason；创建身份不因关联说明而改变。

新增模型、prompt/Skill、update 信号身份回归先 RED 后 GREEN；full process_work_item
会议及聊天均使同一已有卡包含原 Task 与新 Task、当前原文引用、新 Task relevant/confirmed，
且不新建 Project 或升级候选。缺失/非正式/非活动目标、其他 Project、不在当前源的引文、
其他段落而非本行动的引文、来源引用不符均原子回滚；session/memory、同时登记/链接和
Attention 目标不符被模型拒绝。未确认既有 Task 的真实字段更新可确认，restating 更新仍
跳过且保持 rejected 回执，旧 uncertain 匹配不确认。相关 305 项通过，ruff 及服务 imports
通过。此处是确定性契约验证；新增契约后的原生会议/聊天、全部候选 9 样本、W39
生产副本两次回放及上线仍待主 Agent 的独立复核、fresh 原生比较和发布验收。

## 已交付历史事实的比较归因遗漏

后续专用关联修订的实际原生样本已由主 Agent 复核通过；上述缺口记录保留为此前失败事实，
不再代表该修订的当前状态。本轮是新的证据归因原因，不是重复关联假设。
只读核对 `candidate-verified-chat-with-report-context.sqlite3` run 1：completed，复用并更新
既有卡，当前证据引用有效，但 evidence 只有当前聊天，评估报 missing_evidence_source 与
missing_required_source。update_summary 明确比较已有风险并升级，why_attention 使用再次/仍未
解决等延续判断；这些判断没有同时引用原始历史报告。

实际 Codex transcript
`/Users/derek/.codex/sessions/2026/10/02/rollout-2026-10-02T03-26-09-01a0fc26-622a-7481-8ace-103ebae9e6a7.jsonl`
中交付的 Current semantic Task context.source_signals 已含真实 Signal 1，来源
project_weekly_report、ref `eval:historical-report`、source_time `2026-09-24T12:00:00Z`，
原文记录验收延迟两周及回款推迟导致供应商付款协调；与数据库该 Signal 原文一致。
因此是已有上下文的输出归因遗漏，未据此改变检索、领域投影、路由/model 或评估 expected。

prompt 和隔离 CI Skill 同步明确：比较、延续、升级或冲突判断要选择实际用于判断的原始
历史 Signals，与当前证据一并引用，保留真实正 ID、source_ref 与逐字原文。当前来源对旧报告
的转述不能代替原报告引用；不要求周报、不全量引用检索结果，首次仅凭当前事实仍可评估。
原历史不可用则比较标明不确定，不编造引文或使用 session/memory 作为原始证明。
通用 prompt/Skill 断言及 fresh Skill 交付断言先 RED（3 项缺少此规则）后 GREEN；
Task Agent、混合来源投影、检索和 session 的聚焦回归共 268 项通过，ruff 及 diff 检查通过。
上述文字修订后的真实模型效果未通过，详见以下 fresh rerun；原确定性测试结果不证明引用行为已修复。

## 比较归因文字修订后的原生复测失败

候选 revision `dd073b2578ebfdeec2f7678b7e93fef34e745e5d`、CI Skill SHA256
`4ab5fb97f86004d160b61817f7e449d0ffc0936dcf203ea431f8cfe38bc9c1b4`，
实际路由/model 均为 `codex_oauth` / `gpt-5.6-luna`，concurrency=1；两次独立 fresh
固定样本复测的 runner 均正常 completed，没有切换模型或放宽 expected。

| case | 实际落库和回执 | 失败原因 |
| --- | --- | --- |
| chat-with-report-context | 2 Tasks；Task 1 更新已纳入补充内容，Task 2 又记录相同新增内容且 unknown/未确认 Project link；两项相同 Attention 提案因支持成员不合格全部 rejected，旧卡未更新。 | projection_not_successful、unverifiable_attention_evidence、task_count_mismatch、missing_evidence_source、missing_required_source |
| newer-conflicting-chat | 1 Task、1 卡更新，projection completed/applied_count=1；当前原文引用有效，登记字段未改写。state/inference 描述与上期正式周报冲突，evidence 却只有当前聊天。 | missing_evidence_source、missing_required_source |

对应副本为上述评估目录下的 `candidate-citations-chat-with-report-context.sqlite3` 与
`candidate-citations-newer-conflicting-chat.sqlite3`。原始历史 Signal 1 已交付给 Agent；
当前 schema 允许 optional link=None 与单当前证据，因此输出形状有效，既有纠正机会未触发。
下一修订针对输出契约及任务交付边界，不新增 Agent、队列、关键词判定或自动补链。

独立业务复核确认：聊天行动的“增加供应商停交风险及延期付款协商结果”是既有复核内容的
补充，且本轮 Task 1 已包含该内容，第二项造成重复覆盖；保留精确 Task 数量 1，不放宽。
供应商本周不能付款将停交的当前事实可以独立支持 watch，并不要求历史周报才能形成风险。
但原始历史的延续/升级/冲突比较需要比较两侧的实际引用，本固定样本专门验证此能力，
保留双来源要求；不能把“当前风险有效”等同于“多来源综合归因已通过”。

## 必填评估依据与新行动关联的输出契约修订

在两次真实失败之后，新增必填 assessment_basis，不提供旧输出兼容默认：
current_observation 只断言当前事实，需要 null-ID 当前引用，允许历史佐证；
historical_comparison 依赖历史比较、延续、升级或冲突，必须同时引用当前 null-ID 与
实际持久化原始 positive-ID 来源。只检查此输出形状，不查询 Store 或用关键词机器分类。
原始 ID/ref/原文及支持成员有效性仍由现有投影核验。assessment_json 保存 basis，
同轮提案折叠自然包含该字段，API 原样读回，不新增 UI 标签或服务判断规则。

新行动 record_candidate/create_task 对已有正 anchor 提出 Attention 时必须有同目标
project_link_proposal；有效同决定新注册继续走 project_proposal，已知 confirmed Task
按真实 ID 更新并复用链接。检查既有 dedupe 路径：仅按同来源信号的 dedupe key 与事件
重放原创建结果，不按标题自动复用；有效新行动提案保留链接可重放，不增加 linkless fallback。
字段旁描述明确独立可完成交付才创建 Task，既有范围/内容补充更新同 Task；不用同引文
机械去重。prompt/CI Skill 替换旧比较段，要求已交付匹配原始 Signals 的显式来源比较实际
核对两侧，不以复述当前转述替代；相关历史缺失则比较待核对，仅当前事实有效。

必填模式、双侧引用及新行动链接的通用模型回归先 RED 后 GREEN；模拟现有同会话纠正
回归验证缺 link 或历史比较缺历史引用均成为具体字段错误，原输出拒绝、一次修正后接受。
合法当前/比较、当前附带历史佐证、已有 confirmed Task 复用、新注册、创建重放、无变化
update guard、混合来源存储及 API basis 读回均保留聚焦验证。真实模型效果仍待主 Agent
独立规格/质量复核后冻结相同输入/model 原生重跑，不将确定性测试当作语义通过或上线。
本次指定 9 文件聚焦验证 805 项通过（32.56 秒），补充 API basis 读回后该文件 7 项通过；
ruff、diff 检查及服务 CLI/worker/supervisor imports 通过。未执行全套测试或本次原生 turn。

## 冻结契约修订的完整原生比较：8/9，尚未通过发布门槛

规格复核独立 22 项通过，质量复核独立 31 项通过；主 Agent 独立模型/API 56 项通过。
随后冻结 code `233b9667cf18eff12cf5e7222150b4d317e47a55`、CI Skill SHA256
`828342187afbd95d2f89b2d89f5161b7a2fd615842dd6991ff43894147f59d22`，
同一 version 1 固定输入、相同 expected、`codex_oauth` / `gpt-5.6-luna`、有效 timeout
900/300 秒、concurrency=1，完成全部九个 fresh 原生候选 case。各调用正常完成，
失败 case 在后续日期领域验证被拒；正常 CLI 退出不等于业务提交成功。

| case | baseline 7bf7be5e | 候选 233b9667 实际结果 |
| --- | --- | --- |
| w39-project-risk | 未通过 | 通过：4 Task、2 卡；中汽创智成员 1，岚图成员 2/3，NPS Task 4 无 Project/Attention。 |
| meeting-new-risk | 未通过 | 通过：2 Task、原卡更新，成员 1/2，既有正式 Project 未重建；当前事实模式。 |
| chat-with-report-context | 未通过 | 通过：更新 Task 1、原卡 ID 1，当前和原始历史引用并列，historical_comparison；无重复 Task。 |
| newer-conflicting-chat | 未通过 | 通过：1 Task/1 卡，双方原文和时间并列，冲突待核对，不改登记字段。 |
| risk-label-only | 通过 | 通过：1 真实行动 Task、0 卡。 |
| routine-progress | 通过 | 通过：1 真实行动 Task、0 卡。 |
| unconfirmed-project | 通过 | 通过：1 Task、0 正式 Project/卡。 |
| no-real-task | 通过 | 通过：0 Task/卡。 |
| same-project-two-actions | 未通过 | 未通过：日期 actor 错误，0 Task/Project/卡；原输出提出了 3 个 TaskDecision，不是 3 个已提交 Task。 |

失败副本 `candidate-typed-same-project-two-actions.sqlite3`，实际 native session
`01a0fc4f-3f15-7993-8f28-0a095d8c7f3d`。完整原始输出在该库
agent_runtime_attempts.result_envelope_json 中保留。额外 Task 使用整个项目登记行的负责范围，
两条具体行动已覆盖该工作；该项还把登记 DDL 作为 external_deadline_at，value 为不完整日期，
quote 为整行，actor_name 填来源类型“项目周报”，而可信 sender/name/ID 均为空。
失败原因 `date actor_name must match the trusted date actor`；不能跳过拒绝条件或放宽计数。

独立隔离复现：原输出拒绝且无提交；仅清空 actor 或移除日期会提交 3 Tasks，仍多余；
仅保留两条明确行动得到 2 Tasks/1 Project/1 卡。说明日期错误遮住了任务粒度问题，
只修日期不能宣称语义修复。当前字段已能表达正确输出，下一轮只限定字段旁的来源/日期
说明，不新增自报标签或业务关键词分类；指导不能确定性证明自然语言独立交付，必须重复
原生验证。本次四负例通过及两个多来源比较通过不代表最新修改或生产已通过。

## 字段指导修订的重复成功与新任务关系端点缺口

`f5541bed` 字段就近指导由规格复核独立 15 项、质量复核独立 24 项通过；九文件聚焦
808 项通过。冻结 code `e4a66ae83d4bd7a44fa43f4793bb78bc347210eb`、CI Skill SHA256
`4945a0e53d4007a653546dacbe7aff457308fa6ef40686137f5cb71f3caa46e1`，
仍为同模型/路由、固定来源、expected、有效 timeout 和 concurrency=1：

- same-project-two-actions 两次独立 fresh case 均通过：2 Tasks/1 Project/1 卡，两个真实
  行动同一卡成员，无额外登记范围 Task 或错误日期。
- w39-project-risk 通过：5 Tasks/2 卡；Task 身份仍须按实际交付边界人工复核，计数允许范围
  不能替代该检查。
- meeting-new-risk 未通过：正常 native 输出后，业务提交因关系字段被拒，实际仍只有
  种子 Task 1 和原卡 1，未更新。其他八项中的聊天及负例尚未跑完这个修订，不能复用上一版
  的通过宣称完整九样本已通过。

失败副本 `candidate-fields-meeting-new-risk.sqlite3`，native session
`01a0fc62-7d27-7d71-8b88-29ecd4859a40`。原始两项决定中，既有 Task 1 的无实际变化
update 按原 guard 跳过；新候选行动附 `relation_proposals` 为 from_task_id=1、to_task_id=1，
两端均填已有 Task 1，而本项真实新 Task ID 尚未分配。服务正确报
`relation proposal must include the Task evidenced by this decision`，事务未提交；原卡不是
本轮更新成功。原始输出保留在 runtime_attempts.result_envelope_json。

当前双端数值关系模型对新 Task 有表达缺口：输出时不能知道服务即将分配的自身 ID。
下一步让关系以本条实际应用 Task 为一端，只引用已存在的另一端及方向，服务在取得实际
Task ID 后派生旧领域命令的 from/to；不猜测编号、不增加 Agent/队列、兼容分支或新业务门禁。
同一原始输出的项目链接 quote 也只选了新行动子句，未包含正式项目名；完整当前行动句
包含项目名及该行动，应作为关联引用。下一修订只明确该字段的既有出处要求，不放宽核验。

## 相对 Task 关系契约：完整九样本通过，真实副本尚未通过

功能 `f9667d2c`，冻结 HEAD `fc58e803cdc260546463aafc6e382a60a1dd65a0`；
CI Skill SHA256 `e5a04572acbe5e9657d50b9a76ff4db3cd8531ea2410ac02a1651c147ddaa8e5`，
fixture SHA256 `c2d0846919ffe9452906a6c08b2d17b729db120c102c7ec00e72c9f2ee703338`。
Worker 九文件聚焦 827 passed，独立规格 31 passed、质量 42 passed、主 Agent 专项
18 passed。质量复核另外在四个隔离数据库验证 merge + relation 双方向及实际 target-self
回滚。均未改变 Graph SQL、原无变化 update guard、计数 oracle 或模型路由。

完整 fresh `candidate-relative-*` 九案例仍使用 codex_oauth / gpt-5.6-luna、有效 timeout
900 秒、idle 300 秒、concurrency=1。两聊天案例沿既有一次形状修正，各有 superseded
和 completed attempt；其他七项单次 completed。没有新增重试或替换模型。

| case | Tasks / Projects / active cards | 实际结果 |
| --- | --- | --- |
| meeting-new-risk | 2 / 1 / 1 | 通过；旧任务与新增供应商付款任务同一卡成员，同时引用当前会议及历史报告。 |
| same-project-two-actions | 2 / 1 / 1 | 通过；两个实际行动均为卡片成员，没有多余登记范围 Task。 |
| w39-project-risk | 4 / 2 / 2 | 通过；中汽创智复核计划一项、岚图对账及法律商务沟通两项；NPS 无 Project/Attention。 |
| chat-with-report-context | 1 / 1 / 1 | 通过；既有 Task 更新，当前聊天及历史原文共同支持升级风险。 |
| newer-conflicting-chat | 1 / 1 / 1 | 通过；保留新旧不同时间的原文，记录验收、回款冲突待核对。 |
| risk-label-only | 1 / 1 / 0 | 通过；单独风险标签不足以进入关注。 |
| routine-progress | 1 / 1 / 0 | 通过；例行进展无误关注。 |
| unconfirmed-project | 1 / 0 / 0 | 通过；不因聊天自动登记项目。 |
| no-real-task | 0 / 1 / 0 | 通过；没有编造任务或关注。 |

全部五正例的 projection 为 completed，四负例为 no_proposal；引文原文、source_ref、
来源时间及成员关联均通过落库核验。此 9/9 相对 baseline 4/9 是同固定样本比较，
不替代真实数据库历史任务的业务效果。

首次真实副本回放：`w39-candidate.sqlite3`，精确 input 27465/source_ref 不变，
保留历史 run 10634；新增 run 10662、attempt 18952。native 正常 completed，
Task run/input 为 failed，错误 `non-skip task decision requires title`。
原输出七项决定：Task 129 promote_candidate 带 title；130–134 的 update_fields
省略 title；另有 Einride POC 新候选。所有决定均未给 Attention 提案。
原输出保留在 attempt.result_envelope_json；没有补造 field、alias 或 card。

实测契约不一致：TaskDecision.title 仍默认空，形状解析接受省略；process_work_item
在 runner 返回后用 _validate_task_agent_decision 要求非跳过 title 非空，因而未走
既有形状一次修正。必须先诊断并统一契约，不能增加另一重试层掩盖。
Attention 缺失原因尚未确认，需比较真实 WorkItem/既有六 Task 的 context 与固定样本。
失败前后领域计数、IDs/events 不变：259 Tasks / 16 Projects / 0 cards / 434 Task events；
原 Task 129–134 未修改。第二次副本回放、PR/合并/push、部署、全局 Skill 发布、
精确生产回放和真实页面验收均未执行。

## 真实副本标题失败的契约修复（native 待重跑）

`daa508a2` / `615e31b7` 区分新建与已有 ID 更新：新候选或正式任务需要非空白标题，
在 TaskDecision 形状解析中拒绝遗漏，沿用已有一次同会话纠正；已有 ID 更新允许省略标题。
仅 update_fields 修改显式提供的标题；晋升、接受和合并沿原命令保留已存标题。
晋升所用 deliverable 信息来自真实持久化 Task，不以省略标题推断没有交付物。
原提交后无条件标题要求已删除，来源/负责人/分配/接受/日期与无变化 guard 不变。

Worker 11 项和追加 3 项均观察旧行为 RED、修复后 GREEN；两文件 209 passed。
主 Agent 九个相关 Python 文件 491 passed，页面两文件 22 passed、类型检查/构建及服务
imports 通过。独立规格 33 passed。质量复核发现显式纯空白更新标题仍在解析后到达
数据库 CHECK，导致整批回滚并绕过已有形状纠正；这是本次必须补齐的边界，尚未质量 PASS。
不把数据库拒绝误写成成功保存了空白标题，也不通过自动 trim 或另增重试掩盖。

边界修复 `c75bb21f` / claim release `c6781619` 已完成：nonempty 纯空白更新标题在
形状解析拒绝；省略、空字符串及合法标题分别验证原有语义。回归旧版 1 failed / 3 passed，
修复后 4 passed；两文件 213 passed。最新修复的规格与质量复核、native 仍待完成。

真实副本上下文实际为 320,176 字符：20 候选、20 正式、20 未验证正式 Task、75 Signals、
16 正式 Projects、0 Attention。Task 129–134 全部交付；真实完整周报保留具体现金流、
结算争议、五行登记及行动。上下文规模差异是已测事实，不证明其导致 Attention 遗漏，
本轮没有因此改检索、模型路由、fixture 或 oracle。最新标题修订仍需 native 回放，
不得继承 `fc58e803` 的九样本通过作为新版本或真实业务通过。

## 标题修复后的真实副本：提交成功但 Project 身份未通过

冻结 `b5f9bd5f7f85d712edced8bcbc1a60ed80a89b94`，CI Skill SHA256
`2c325b786d30832dcab0d79b886e8c8d1cc4e39cd954bb73d5bc1859dfe6b5d0`。
空白边界独立规格 20 passed、质量 15 passed，原复现现在在 shape validation 拒绝；
主 Agent 标题专项 17 passed。精确 input 27465 的副本 run 10663 为 completed/input done。
原有失败 run 10662 不改写。三次 native attempt 均 codex_oauth/gpt-5.6-luna：
18953 normal superseded(runtime_unclassified)，18954 normal superseded
(runtime_result_validation_failed)，18955 既有 result_validation_correction completed。
没有为本实验另增重试或模型切换。

领域实测：259→261 Tasks，16→19 Projects，0→3 cards；旧 Task 129–134 均更新，
129 提升为 assigned_unaccepted 的正式任务，其他五项保持候选。新增 Task 260
「推进一次通过率统计及HLL低通过率改进」、261「补入管理周会文档中的健康度指标及ISO进度」。
岚图 anchor 35、项目管理 anchor 36、抽检包生命周期 anchor 37 来自当前登记行。
三张卡为：中汽(anchor 23, Task129)、岚图(anchor35, Task130)、项目管理(anchor36, Task260)。
receipt 为 completed：task_decision_count=8、proposal_count=3、project_link_count=5、
registry_row_count=5、applied_count=3；引文原文、来源及成员链接检查通过。

这仅证明提交与出处核验成功，不证明项目身份正确。Task129 的 project_link_proposal
选旧 anchor23「中汽」，引文来自下周行动「中汽回款计划」；其 Attention current_state
和风险引文却说「中汽创智」，当前正式登记行也明确为「中汽创智」。最终 update_summary
说明输出纠正删除了冲突的 project_proposal，保留旧 anchor23 link。本版没有授权或证明
这两个名称为同一项目；引文包含短标题不能替代该身份。真实副本业务验收因此失败。
工具在 input 模式没有这份人工业务预期，passed=true 不覆盖此项身份验收。

Task260 的统计部分与旧 Task131 有交叠，HLL改进是否构成独立交付尚在复核；
Task261 的健康指标文档更新有来源，标题中额外 ISO 内容也需核对，不因新增数量就判错或忽略。
项目管理卡有低验收率与统计口径缺失的真实引用；第三张卡不是仅凭数量即可认定误关注。
仍不改 alias、静态业务规则、来源、旧 runs 或生产状态；最新完整九样本、精确副本幂等、
PR/合并/push、部署、全局 Skill、生产回放与页面验收均未完成。

## 当前权威 Project 身份：登记与复用修复（效果验证待完成）

功能提交 `f4566806`，释放子 claim 后冻结代码 `394fac8c`。报告与明确会议登记现在
共用 `register_source_project`：复用唯一活动且精确同名的正式 Project 实际 anchor，
保留原登记 provenance；无匹配沿原标题 hash 登记；多个活动同名正式对象使事务因
身份冲突回滚。不改通用 anchor 的 type/ref 身份，也不把短名称推断为别名。
Project proposal 的字段、prompt、CI Skill 与架构/运行/设计文档同步解释为采用当前
权威定义并登记或复用，不仅用于新建。当前不同的正式名称不能因行动简称被旧名称替代。

新增回归旧行为 `7 failed / 2 passed`；开发 Agent 五个相关文件 `351 passed`。
主 Agent 独立运行最终新测试文件 `9 passed in 6.86s`，覆盖报告和会议实际 Attention
投影的 canonical anchor、成员、stable key 与重复卡/事件身份，以及旧 ref 复用、
不同短名称、活动正式对象选择和同名冲突回滚。冻结 `394fac8c` 的独立规格复核
`351 passed in 71.32s`、独立质量复核 `236 passed in 40.89s`，两者均 PASS。
质量复核还逐一比较歧义 report/meeting 失败前后的全部 `business_*` 表，确认完整回滚；
独立单次登记/重复登记实测同 ID、同 anchor、原 provenance 保留。上述复核没有运行 native。

独立 version 1 竞争样例 `tests/fixtures/task_attention_project_identity.json` 的
SHA256 为 `9b56eec4bb74999a40448b1992ea0aed7caa7fff195c7c0c9e8a1acd859dfdf6`；
当前 CI Skill SHA256 为
`2b557b4754c39c688bc0533f04e152157b70fcc519bb4647f12bdbe330bb8d70`。
原九样例和 replay tool 未改；该新增样例的 pinned baseline/candidate native 对比、
最新完整九样例和真实 W39 新副本验证尚未通过，不能继承旧版本结果。
生产、全局 Skill 和原失败副本均未修改。

新增竞争样例 pinned baseline `7bf7be5e` 的 native 结果已完成：codex_oauth /
gpt-5.6-luna、concurrency=1、900s 总时限/300s idle，一次 normal attempt completed，
input done、run completed，但业务验收失败。旧版把已有「示例创智」再次登记成第三个
Project，把已有行动再建为第二个 candidate Task，且没有 Attention；失败项为
`attention_project_mismatch`、`official_project_mismatch`、`task_count_mismatch`、
`project_registry_changed`。这是一份真实失败基线，不以队列 done 替代业务通过。
结果保存在本工作流独立 `baseline-project-identity.sqlite3`，候选最终对比待读回。

真实 W39 新副本 `w39-project-identity-candidate.sqlite3` 从不可变初态通过 SQLite
backup 创建并完成完整性核验 `ok`：259 Tasks、16 Projects、0 Attention；input27465
仍为 done，完整来源 ref 与固定目标一致。它没有继承上轮错误投影；尚未原生重跑。
候选竞争样例已在代码 `70e2cdf6` 上启动；该提交相对复核代码仅增加验证文档，
code/CI Skill 无变化。运行最终结果仍待读回，不将启动或 shape/local 测试视为业务通过。

竞争样例候选已完成并通过，最终读回代码 revision `abaae124`（运行期间新增的文档
提交，行为代码/Skill 与复核冻结 `394fac8c` 一致）。相同 fixture、路由顺序、实际
codex_oauth/gpt-5.6-luna、concurrency=1、900s/300s 时限；一次 normal attempt
completed，无纠正或模型切换。实际 Task 1 保持 candidate/open 且仅更新，无新 Task；
两 Project 不新增、不改写，卡 1 关联「示例创智」真实 anchor2、成员 Task1。
receipt completed、1 proposal/1 applied；风险原句、来源与 source_time 核验通过，
无重复卡、failures=[]。这是同一新增样例 baseline FAIL / candidate PASS，
不是最新九样例或真实 W39 已通过。候选证明保存在 `candidate-project-identity.sqlite3`。

## Project 身份修复后的真实 W39：无重复任务，但关注仍遗漏

冻结 `c87752c0bfe2a7aa75235c607baeebe6f31fcb19`、CI Skill
`2b557b4754c39c688bc0533f04e152157b70fcc519bb4647f12bdbe330bb8d70`。
在新副本 `w39-project-identity-candidate.sqlite3` 精确回放 input27465，run10662、
native attempt18952 normal completed，codex_oauth/gpt-5.6-luna，无纠正或模型切换。
这里的 run/attempt 数字属于这一新副本，不与另一副本中的历史失败互相覆盖。

Task 数保持259，原129–134更新；129为formal/open，其余五项仍candidate/open；
没有新Task260/261，本轮不能据旧副本新增标题就断定新版本仍扩展ISO行动范围。
Project16→20：中汽创智anchor35、岚图anchor36、项目管理anchor37、抽检包生命周期
anchor38来自当前登记；旧「中汽」anchor23原样保留，当前Task129关联完整项目名称。
领域事件434→444。六条决定的attention_proposal全部null，proposal_count=0，
receipt=no_proposal、project_link_count=4、registry_row_count=5，卡片仍0。
工具input模式没有业务expected，因此其passed=true仅证明运行和出处等机械检查，
不覆盖要求中汽创智及岚图目标关注卡的业务验收；本轮业务结果明确FAIL。

已亲读当前完整周报和最终决定：原文保留已交付收入确认延迟、回款与供应商付款
节奏、岚图存量结算争议及暂停增量业务。最终update_summary说明未将一般风险另拆
为独立任务，但未说明为何已关联真实Task和正式Project的两项具体风险不提出关注。
代码收到零proposal后没有投影可应用；不属于页面过滤或投影拒绝。
当前可观测性只能判定未提出，不能证明Agent已评估并拒绝，也不能精确证明内部漏评原因。
在用户确认新的判断输出契约前不增Agent、补偿循环、硬编码风险或业务审核层。

当前代码的主Agent相关十个Python文件410passed（121.79s）、两页面22passed，
TypeScript/Vite build与app.cli/worker/email_worker/service_supervisor imports通过。
这些局部验证不替代最新完整九样例、真实副本重复幂等或生产结果。

## Project assessment 来源接入与冻结 oracle（开发完成，native 待执行）

当前 source integration 的规格和质量评审均已通过（最终功能修订 `e8866186`）。
集成 pushed `origin/main` 的 `9493fc87` 后，`8df4291e` 开发快照独立验证：
十一项相关 backend test files 共 554 passed / 112.56s；限定 CLI 22 passed / 7.47s；
两真实 page test files 共 22 passed / 6.59s；TypeScript/Vite build 和四项 runtime imports
通过。未运行全服务测试集，也未在 production checkout 测试、编辑或重启。
这批结果不继承旧的 native 9/9 或 W39 结论；后续 19 个固定案例配对回放必须记录同一
冻结候选 code revision、各侧实际 Skill hash/route/model 和有效 900/300 秒超时、
concurrency=1。生产及全局 Skill 尚未修改。

Task 4 只更新当前测试/评估来源和一次性 replay 读回，不改变生产 Task Agent、领域应用、
投影、路由、重试或 timeout。Project 登记、混合来源、Web Project summary 以及四个
`process-work-items` fake producer 现在都显式输出必填 `project_assessments`；正例使用真实
Project selector、Task ID 和原始引文，负例/未知项写出 `not_needed` 或
`insufficient_evidence` 的具体理由。Web summary fixture 以当前周报登记行提供显式
`project_proposal`，不再依赖隐式登记；Project identity 专测仅在不测试 Attention 的分支
移除额外风险句，短名称、活动正式对象、同名冲突、登记/复用和原断言保持不变。

新增 version 1 fixture `task_attention_project_assessments_v1.json`，冻结 9 个 assessment
案例：报告、会议、聊天三类明确关注；仅风险标签、无真实 Task、未确认 Project、例行进展
四类负面/证据不足判断；同一 Project 两个 Task 由一个 assessment 和同一卡成员覆盖；
以及已有卡的真实保存 proof/实际 ID 重放幂等。expected 仍与 Work Item / existing facts
分离，测试确认 secret expected 不进入 payload 或 prompt。原 `task_attention_multisource.json`
九案例及 `task_attention_project_identity.json` 竞争案例未修改，SHA256 仍分别为
`c2d0846919ffe9452906a6c08b2d17b729db120c102c7ec00e72c9f2ee703338` 和
`9b56eec4bb74999a40448b1992ea0aed7caa7fff195c7c0c9e8a1acd859dfdf6`。

冻结 oracle 直接读取 run 的原始 `decision_json`；缺少 `project_assessments` 保持可观测，
不经候选模型默认值补齐。它按唯一 Project title 匹配原始 assessment，再用该原始位置读取
独立 projection receipt，不强制模型输出顺序。逐案例要求已审阅 outcome 与必需的
`source_ref + 经营影响原句` 被实际引文覆盖；允许同一 ref 的更长连续原文和额外真实引文，
但逐一核验所有额外引文。当前 null-signal 引文必须来自 immutable Work Item 并读回原
source_time/空 link；正 signal 必须核对真实 Signal 的 ref/原文/time/link，以及它与回执
支持 Task 或已验证卡 proof 的实际关系。即使合法负面判断的 receipt 没有 Task 和卡片，
额外正 signal 也不能跳过这项关联核验或凭真实元数据自证。原始 assessment 中额外的正 signal 还必须在独立
projection receipt 中以相同 signal/ref/兼容的连续原文出现，且关联 receipt 实际 Task 或
receipt 指向卡片的精确 proof；不能由原始 assessment 自报 Task ID 补足。回执 Task 数按
真实且不重复的 ID 计算，`[1, 1]` 不可冒充两个 Task。回执还核对实际 anchor、Task IDs、
card ID、status 和成员；未知 Project 可由 case 明确要求 `anchor_id=null`。重复已有卡 case
两次回放保持同 card ID 且不新增 Task、Project 或 Attention event。

这些机械检查能证明显式判断覆盖、原始引文、持久化身份和应用结果，不能仅凭非空理由和
正确引用独立证明自由文本理由的业务语义正确。没有加入关键词、正则、短语黑名单、第二 judge
或 fixture-exact reason。主 Agent 仍须逐案例对照原文和预期经营影响，并人工审阅真实 W39
保存理由；机械 `passed=true` 不能标记 business PASS。

确定性 oracle 回归覆盖：原始字段缺失、Project title 覆盖错位、outcome 错误、缺理由、
缺必需原句、伪造额外引文、receipt 缺失/错 status/伪 existing card ID、伪 signal ID、
未关联但元数据相同的真实 signal、错误 time/link，以及正确前缀/额外真实引文正控制。
SPEC 修复进一步以真实未关联第二 Signal 复现“只改 raw assessment、receipt 不变”误通过，
以两真实 Task/两卡成员但 receipt `[1, 1]` 复现重复 ID 误计数；两项均先 RED 后 GREEN，
并保留已关联、receipt 亦保存的更长额外引文正控制。既有 Project link 的七个拒绝 case
改用独立 unknown-clue companion，明确绕过 selector coverage 只测试 application guard，
逐项断言原 missing/unofficial/inactive、cross-project/quote 及 source-ref 错误，不把无效
link anchor 复制进 assessment 造成提前失败。最终补充冻结 no-Task `insufficient_evidence`
正控制，并以 receipt-only 追加真实但未关联 Signal 复现空支持集合误通过；移除空集合跳过后，
正常负面判断仍不创建 Task/卡片，篡改回执明确报 `project_assessment_receipt_mismatch`。
质量复审再补三项严格读回：raw assessment 与 receipt 必须由唯一完整的
`assessment_index` 集合一一对应，重复或未匹配的额外 receipt 均失败，但两个真实 assessment
的 receipt 逆序仍通过；reason 必须是非空字符串，`null` 不经字符串转换冒充理由；每条引文
excerpt 必须是非空字符串，空白不能利用 substring 规则冒充来源原句。这里仍不要求 reason
精确等于 fixture、不加关键词分类，也不把引用支持关系升级为所有负面 Task 必须确认 Project。
最终 assessment oracle `26 passed / 117 deselected`；
source registration + multisource + Web summary `153 passed`，CLI process-work-items
`22 passed / 254 deselected`。本节未运行
native Agent、provider、真实 W39、生产数据库、全局 Skill 发布、push、PR、合并或部署。

已有卡 case 的新 proof 字段不能由 pinned baseline `7bf7be5e` 的旧 constructor 原生 seed；
不得为此增加旧 schema fallback。当前配对 native 工作流是：先用候选
domain command 预备一份包含 Work Item、Project、Task、Signal 和已核验卡 proof 的
固定事实数据库；用 SQLite backup 复制为 baseline/candidate 两份 fresh 副本；
两边均以各自真实 app 源码和 Skill 走 `--input-id` 且核对 exact source_ref，
不运行 case seed；运行后再由同一冻结 oracle 以候选读取契约分别只读
两份 DB，expected 只在 run 后参与。baseline 缺 raw assessment/receipt 应被如实
记录，不用 candidate runner 代替 baseline 执行。这个公平性组织步骤和 native
结果由主 Agent 后续执行，本轮没有把 seed constructor 不兼容误记成业务失败。
