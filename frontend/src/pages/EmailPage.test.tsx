import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
const api = vi.hoisted(() => Object.fromEntries(["listEmailClassifications", "confirmEmailClassification", "listEmailConfigs", "saveEmailConfig", "createEmailCategory", "listEmailLearning", "getEmailClassification", "getEmailModelVersion", "saveEmailRuntimeMode", "saveEmailPromotionConfig", "listEmailCategoryHistory"].map(key => [key, vi.fn()])));
vi.mock("../api/console", async importOriginal => ({ ...await importOriginal<object>(), ...api }));
import { EmailPage } from "./EmailPage";
const config = { category_key: "work", display_name: "工作", core_description: "工作定义", include: ["项目"], exclude: ["私人"], threshold: .9, actions: ["move"], action_parameters: {}, enabled: true, config_version: "c1", description_version: "d1", updated_at: "", bindings: [] };
const row = (id: string) => ({ id, sender: "sender@example.com", subject: "邮件" + id, preview: "摘要", category: "work", confidence: .7, margin: .2, probabilities: {work:.7}, status: "pending_feedback", classification_source: "agent", model_version: "model-full-v1", config_version: "c1", attachment_metadata: [], action_plan: {}, current_action_plan_id: null, received_at: "", updated_at: "" });
const runtime = {mode:"agent_primary", active_model_id:null, candidate_model_id:"model-v2", candidate_ready:true, toggle_enabled:true};
const gateConfig = {macro_f1_min:.95, category_precision_min:.95, category_validation_samples_min:20, p95_latency_max_ms:500, config_version:"p1"};
function learning(overrides = {}) { return {runtime, promotion_gate:{config:gateConfig, promotion_eligible:true, checks:[]}, mode_transitions:[], models:[], staged_models:[], registry_issues:[], pending_examples:2, ...overrides}; }
function Location() {return <output aria-label="URL">{useLocation().search}</output>;}
function show(path = "/email") {return render(<MemoryRouter initialEntries={[path]}><Location/><EmailPage/></MemoryRouter>);}
function deferred<T>() {let resolve!: (value:T)=>void; let reject!: (error:Error)=>void; const promise=new Promise<T>((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
beforeEach(() => {
  vi.resetAllMocks();
  api.listEmailConfigs.mockResolvedValue({items:[config]});
  api.listEmailClassifications.mockResolvedValue({items:[row("1"),row("2")], meta:{total:55,page:1,page_size:50,snapshot_at:""}});
  api.getEmailClassification.mockImplementation(async (id:string)=>({item:{...row(id),message_text:"完整正文\n> 引用邮件",recipients:["to@example.com"],cc:"cc@example.com",attachment_metadata:[{filename:"a.pdf",size_bytes:1024,mime_type:"application/pdf",content:"SECRET"}]},observability:[]}));
  api.listEmailLearning.mockResolvedValue({learning:learning()});
});
it("uses shared rows, 50 default, URL pagination and page sizes", async()=>{
  const user=userEvent.setup();show();
  await screen.findByRole("button",{name:"打开邮件 邮件1"});
  expect(api.listEmailClassifications).toHaveBeenCalledWith("processed",{page:1,page_size:50},expect.any(AbortSignal));
  await user.selectOptions(screen.getByLabelText("每页邮件数"),"20");
  await waitFor(()=>expect(api.listEmailClassifications).toHaveBeenLastCalledWith("processed",{page:1,page_size:20},expect.any(AbortSignal)));
  await user.click(screen.getByRole("button",{name:"下一页"}));
  expect(screen.getByLabelText("URL")).toHaveTextContent("page=2");
  expect(screen.getByRole("tab",{name:/模型训练/})).toBeInTheDocument();
});
it("opens readonly body and metadata drawer and restores focus without losing selection",async()=>{
  const user=userEvent.setup();show("/email?page=1&page_size=50");const trigger=await screen.findByRole("button",{name:"打开邮件 邮件1"});await user.click(trigger);
  const drawer=await screen.findByRole("dialog",{name:"邮件详情"});
  expect(await within(drawer).findByText(/完整正文/)).toHaveTextContent("> 引用邮件");
  expect(drawer).toHaveTextContent("a.pdf");expect(drawer).not.toHaveTextContent("SECRET");
  expect(within(drawer).queryByRole("button",{name:/保存分类/})).not.toBeInTheDocument();
  await user.keyboard("{Escape}");expect(trigger).toHaveFocus();expect(screen.getByLabelText("URL")).toHaveTextContent("selected=1");
});
it("cancels stale detail requests when selection changes",async()=>{
  const user=userEvent.setup();const first=deferred<unknown>();api.getEmailClassification.mockReturnValueOnce(first.promise);show();
  await user.click(await screen.findByRole("button",{name:"打开邮件 邮件1"}));
  const signal=api.getEmailClassification.mock.calls[0][1] as AbortSignal;
  await user.click(screen.getByRole("button",{name:"打开邮件 邮件2"}));expect(signal.aborted).toBe(true);
  await act(async()=>first.resolve({item:{...row("1"),message_text:"STALE"},observability:[]}));
  expect(screen.getByRole("dialog")).not.toHaveTextContent("STALE");
});
it("locks feedback, preserves failure and advances only after success",async()=>{
  const user=userEvent.setup();const saving=deferred<unknown>();api.confirmEmailClassification.mockReturnValueOnce(saving.promise);show("/email?tab=pending_feedback&selected=1");
  await user.click(await screen.findByRole("button",{name:"工作"}));await user.click(screen.getByRole("button",{name:"保存分类并继续"}));
  expect(screen.getByRole("button",{name:"关闭详情"})).toBeDisabled();expect(screen.getByRole("button",{name:"打开邮件 邮件2"})).toBeDisabled();
  await act(async()=>saving.reject(new Error("文件夹移动失败，请重试")));
  expect(screen.getByRole("alert")).toHaveTextContent("文件夹移动失败");expect(screen.getByLabelText("URL")).toHaveTextContent("selected=1");
  api.confirmEmailClassification.mockResolvedValue({ok:true,message:"已保存"});
  api.listEmailClassifications.mockResolvedValue({items:[row("2")],meta:{total:1,page:1,page_size:50,snapshot_at:""}});
  await user.click(screen.getByRole("button",{name:"保存分类并继续"}));
  await waitFor(()=>expect(screen.getByLabelText("URL")).toHaveTextContent("selected=2"));
});
it("uses dynamic categories and three-field versioned saves with failure preservation",async()=>{
  const user=userEvent.setup();api.listEmailConfigs.mockResolvedValue({items:[config,...["junk","important","subscription","other"].map(category_key=>({...config,category_key,display_name:category_key}))]});
  api.saveEmailConfig.mockRejectedValueOnce(new Error("版本冲突，请刷新后重试"));show("/email?tab=config");
  const core=await screen.findByLabelText("核心定义");await user.clear(core);await user.type(core,"新定义");
  expect(within(screen.getByRole("navigation",{name:"邮件类型"})).getAllByRole("button")).toHaveLength(2);
  await user.click(screen.getByRole("button",{name:"保存配置"}));
  expect(api.saveEmailConfig).toHaveBeenCalledWith("work",expect.objectContaining({core_description:"新定义",include:["项目"],exclude:["私人"],expected_current_version:"c1"}));
  expect(await screen.findByRole("alert")).toHaveTextContent("版本冲突");expect(core).toHaveValue("新定义");
  expect(screen.getByText(/junk → Trash/)).toBeInTheDocument();
});
it("keeps mode server-controlled and retains ready marker on rejected switch",async()=>{
  const user=userEvent.setup();api.saveEmailRuntimeMode.mockRejectedValue(new Error("切换失败，请重试"));show("/email?tab=learning");
  const toggle=await screen.findByRole("switch",{name:"主模型"});expect(toggle).not.toBeChecked();await user.click(toggle);
  expect(api.saveEmailRuntimeMode).not.toHaveBeenCalled();await user.click(screen.getByRole("button",{name:"确认切换"}));
  expect(api.saveEmailRuntimeMode).toHaveBeenCalledWith(expect.objectContaining({mode:"model_primary",model_id:"model-v2",expected_mode:"agent_primary",expected_model_id:null,request_id:expect.any(String)}));
  expect(await screen.findByRole("alert")).toHaveTextContent("切换失败");expect(toggle).not.toBeChecked();
  expect(screen.getByRole("tab",{name:/模型训练.*候选模型已达标/})).toBeInTheDocument();
});
it("never enables a switch from client metric estimates",async()=>{
  api.listEmailLearning.mockResolvedValue({learning:learning({runtime:{...runtime,toggle_enabled:false,candidate_ready:false}})});show("/email?tab=learning");
  expect(await screen.findByRole("switch",{name:"主模型"})).toBeDisabled();
});
it("does not present a legacy registry active model as the realtime primary",async()=>{
  const user=userEvent.setup();
  api.listEmailLearning.mockResolvedValue({learning:learning({runtime:{...runtime,candidate_ready:false,toggle_enabled:false},models:[{model_id:"legacy-tfidf",status:"active",trained_at:"",sample_count:54,new_sample_count:9,integrity_status:"verified"}]})});
  show("/email?tab=learning");
  const table=await screen.findByRole("table",{name:"模型版本"});
  expect(table).toHaveTextContent("历史版本");
  expect(table).not.toHaveTextContent("主模型");
  await user.click(screen.getByRole("button",{name:"历史证据 legacy-tfidf"}));
  const detail=screen.getByRole("dialog",{name:"历史模型证据"});
  expect(detail).toHaveTextContent("历史登记状态：active");
  expect(detail).toHaveTextContent("不代表当前新邮件的主模型");
});
it("saves all four thresholds with server version and displays missing metrics as unmeasured",async()=>{
  const user=userEvent.setup();api.listEmailLearning.mockResolvedValue({learning:learning({staged_models:[{model_id:"model-v1",status:"candidate",trained_at:"",metrics:null,evaluation:null,split_counts:{train:4,validation:2,test:1},end_to_end_latency_ms:null}]})});
  api.saveEmailPromotionConfig.mockResolvedValue({ok:true});show("/email?tab=learning");
  await user.click(await screen.findByRole("button",{name:"保存晋升门槛"}));
  const {config_version,...values}=gateConfig;
  expect(api.saveEmailPromotionConfig).toHaveBeenCalledWith({...values,expected_current_version:config_version});
  const table=screen.getByRole("table",{name:"模型版本"});expect(table).toHaveTextContent("未测量");expect(table).not.toHaveTextContent("0.0%");
});
it("keeps 64-bit string IDs intact and blocks duplicate form submissions",async()=>{
  const id="8423079112545370123",user=userEvent.setup(),saving=deferred<unknown>();
  api.listEmailClassifications.mockResolvedValue({items:[row(id)],meta:{total:1,page:1,page_size:50}});
  api.confirmEmailClassification.mockReturnValue(saving.promise);
  show("/email?tab=pending_feedback&selected="+id);
  await user.click(await screen.findByRole("button",{name:"工作"}));
  const submit=screen.getByRole("button",{name:"保存分类并继续"});
  fireEvent.submit(submit.closest("form")!);fireEvent.submit(submit.closest("form")!);
  expect(api.confirmEmailClassification).toHaveBeenCalledTimes(1);
  expect(api.confirmEmailClassification.mock.calls[0][0]).toBe(id);
  expect(api.getEmailClassification).toHaveBeenCalledWith(id,expect.any(AbortSignal));
  expect(screen.getByRole("tab",{name:"已处理"})).toBeDisabled();
  await act(async()=>saving.reject(new Error("可重试")));
});
it("retains existing list while paging and ignores late page results",async()=>{
  const user=userEvent.setup(),late=deferred<unknown>();
  show();await screen.findByRole("button",{name:"打开邮件 邮件1"});
  api.listEmailClassifications.mockReturnValueOnce(late.promise);
  await user.click(screen.getByRole("button",{name:"下一页"}));
  const signal=api.listEmailClassifications.mock.calls.at(-1)![2] as AbortSignal;
  expect(screen.getByRole("button",{name:"打开邮件 邮件1"})).toBeInTheDocument();
  await user.click(screen.getByRole("tab",{name:"待反馈"}));
  await act(async()=>late.resolve({items:[row("STALE")],meta:{total:1,page:2,page_size:50}}));
  expect(signal.aborted).toBe(true);expect(screen.queryByRole("button",{name:"打开邮件 邮件STALE"})).not.toBeInTheDocument();
});
it("retains selection on failed response envelopes and retries body without using preview as body",async()=>{
  const user=userEvent.setup();
  api.getEmailClassification.mockRejectedValueOnce(new Error("正文连接失败"));
  api.getEmailClassification.mockResolvedValue({item:{...row("1"),message_text:"",recipients:[],attachment_metadata:[]},observability:[]});
  show("/email?tab=pending_feedback&selected=1");
  expect(await screen.findByRole("alert")).toHaveTextContent("正文连接失败");
  await user.click(screen.getByRole("button",{name:"重试正文"}));
  expect(await screen.findByText("这封邮件没有已保存的正文，请查看原邮件后分类。")).toBeInTheDocument();
  api.confirmEmailClassification.mockResolvedValue({ok:false,message:"文件夹动作失败"});
  await user.click(screen.getByRole("button",{name:"工作"}));await user.click(screen.getByRole("button",{name:"保存分类并继续"}));
  expect(await screen.findByRole("alert")).toHaveTextContent("文件夹动作失败");
  expect(screen.getByRole("button",{name:"工作"})).toHaveAttribute("aria-pressed","true");
  expect(screen.getByLabelText("URL")).toHaveTextContent("selected=1");
});
it("returns to a legal previous page after removing the last item",async()=>{
  const user=userEvent.setup();
  api.listEmailClassifications.mockResolvedValueOnce({items:[row("1")],meta:{total:51,page:2,page_size:50}});
  api.confirmEmailClassification.mockResolvedValue({ok:true,message:"已保存"});
  show("/email?tab=pending_feedback&page=2&page_size=50&selected=1");
  await user.click(await screen.findByRole("button",{name:"工作"}));await user.click(screen.getByRole("button",{name:"保存分类并继续"}));
  await waitFor(()=>expect(screen.getByLabelText("URL")).toHaveTextContent("page=1"));
});
it("preserves provider observations, unknown important state and rich action evidence",async()=>{
  const user=userEvent.setup();
  api.listEmailClassifications.mockResolvedValue({items:[{...row("1"),important:null,provider_classification:{state:"categorized",category_key:"legal",important:null}}],meta:{page:1,page_size:50,total:1}});
  api.getEmailClassification.mockResolvedValue({item:{...row("1"),message_text:"正文",recipients:["legal@example.com"],cc:"cc@example.com",description_version:"desc-v7",action_plan:{action_plan_id:"plan-full-id",action_plan_version:7,actions:["move","unsubscribe"]}},provider_classification:{state:"categorized",category_key:"legal",important:null,observed_at:"2026-09-08T00:00:00Z"},observability:[{kind:"unsubscribe",status:"done",lifecycle_version:"email_unsubscribe_audited_v2",result_text:"退订成功",task_id:42,task_status:"done",consumer_run_ids:[101],audit_run_ids:[102],receipt_id:"receipt-2",observation_digest:"digest-2",evidence:"最终结果页：已成功退订",steps:[{sequence:1,operation:"open_entry",state:"done",reference:"receipt-2"}]}]});
  show();await user.click(await screen.findByRole("button",{name:"打开邮件 邮件1"}));
  const drawer=await screen.findByRole("dialog");
  expect(await within(drawer).findByText("退订成功")).toBeInTheDocument();
  expect(drawer).toHaveTextContent("plan-full-id");expect(drawer).toHaveTextContent("版本 7");expect(drawer).toHaveTextContent("desc-v7");
  expect(drawer).toHaveTextContent("Consumer run：101");expect(drawer).toHaveTextContent("Audit run：102");expect(drawer).toHaveTextContent("receipt-2");expect(drawer).toHaveTextContent("digest-2");
  expect(within(drawer).getByRole("region",{name:"邮箱观察事实"})).toHaveTextContent("legal");
  expect(screen.getAllByLabelText("重要状态未知").length).toBeGreaterThan(0);
});
it("supports keyboard tabs and drawer tab traversal",async()=>{
  const user=userEvent.setup();show();
  const processed=screen.getByRole("tab",{name:"已处理"});processed.focus();await user.keyboard("{ArrowRight}");
  expect(screen.getByRole("tab",{name:"待反馈"})).toHaveFocus();
  expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby","email-tab-pending_feedback");
  await user.click(await screen.findByRole("button",{name:"打开邮件 邮件1"}));
  expect(screen.getByRole("button",{name:"关闭详情"})).toHaveFocus();
  await user.keyboard("{Shift>}{Tab}{/Shift}");expect(screen.getByRole("dialog")).toContainElement(document.activeElement as HTMLElement);
});
it("preserves category input, locks navigation and submits once during a pending save",async()=>{
  const user=userEvent.setup(),saving=deferred<unknown>();api.saveEmailConfig.mockReturnValue(saving.promise);show("/email?tab=config");
  const include=await screen.findByLabelText("包括场景");await user.clear(include);await user.type(include,"项目A\n项目B");
  const button=screen.getByRole("button",{name:"保存配置"});fireEvent.submit(button.closest("form")!);fireEvent.submit(button.closest("form")!);
  expect(api.saveEmailConfig).toHaveBeenCalledTimes(1);expect(include).toBeDisabled();expect(screen.getByRole("button",{name:"新增类别"})).toBeDisabled();
  await act(async()=>saving.reject(new Error("冲突，请重试")));expect(include).toHaveValue("项目A\n项目B");expect(screen.getByRole("alert")).toHaveTextContent("冲突");
});
it("creates category with folder target and all semantic fields, and renders server versions",async()=>{
  const user=userEvent.setup();api.createEmailCategory.mockResolvedValue({ok:true,item:{...config,category_key:"legal",display_name:"法务",config_version:"c-new",description_version:"d-new",bindings:[{account_id:"a",provider_folder_name:"Legal",binding_status:"active",last_verified_at:""}]}});show("/email?tab=config");
  await user.click(await screen.findByRole("button",{name:"新增类别"}));
  for(const [label,value] of [["类别标识","legal"],["类别名称","法务"],["目标文件夹","Legal"],["核心定义","法律事务"],["包括场景","合同"],["排除场景","日常工作"]])await user.type(screen.getByLabelText(label),value);
  await user.click(screen.getByRole("button",{name:"创建并验证文件夹"}));
  expect(api.createEmailCategory).toHaveBeenCalledWith(expect.objectContaining({category_key:"legal",display_name:"法务",provider_folder_name:"Legal",core_description:"法律事务",include:["合同"],exclude:["日常工作"]}));
  expect(await screen.findByText(/描述版本：d-new/)).toBeInTheDocument();expect(screen.getByText("active")).toBeInTheDocument();
});
it("only updates model switch after server readback and allows disabling active model",async()=>{
  const user=userEvent.setup(),readback=deferred<unknown>();api.saveEmailRuntimeMode.mockResolvedValue({ok:true,runtime:{...runtime,mode:"model_primary",active_model_id:"model-v2"}});
  show("/email?tab=learning");const toggle=await screen.findByRole("switch",{name:"主模型"});await user.click(toggle);
  api.listEmailLearning.mockReturnValueOnce(readback.promise);
  await user.click(screen.getByRole("button",{name:"确认切换"}));expect(toggle).not.toBeChecked();
  await act(async()=>readback.resolve({learning:learning({runtime:{...runtime,mode:"model_primary",active_model_id:"model-v2",candidate_ready:false}})}));
  await waitFor(()=>expect(toggle).toBeChecked());expect(screen.queryByLabelText("候选模型已达标")).not.toBeInTheDocument();
  await user.click(toggle);await user.click(screen.getByRole("button",{name:"确认切换"}));
  expect(api.saveEmailRuntimeMode).toHaveBeenLastCalledWith(expect.objectContaining({mode:"agent_primary",model_id:null,expected_mode:"model_primary",expected_model_id:"model-v2"}));
});
it("renders rich model detail on demand and keeps healthy inventory visible on detail failure",async()=>{
  const user=userEvent.setup();const model={model_id:"embedding-full-v9",status:"candidate",trained_at:"2026-09-08T00:00:00Z",metrics:{accuracy:.97,macro_f1:.96,categories:{work:{precision:.99,recall:.94,f1:.96,support:37,accepted_precision:.98,accepted_hits:30,independent_groups:25,threshold:.95}},important:{precision:.98,recall:.97,f1:.97,accepted_precision:.99}},evaluation:{protocol:"email-folder-heldout-v1",test_digest:"digest-9",comparability_key:"key-9"},head_timing_percentiles_ms:{p50:1,p95:2,p99:3},end_to_end_latency_ms:{p50:100,p95:200,p99:300},artifact_sha256:"a".repeat(64),compatibility:{enabled_categories:["work"],description_version:"d1",embedding_revision_reference:"emb-7"},split_counts:{train:80,validation:30,test:37},failure_reason:""};
  api.listEmailLearning.mockResolvedValue({learning:learning({staged_models:[model],registry_issues:[{model_id:"broken-v8",integrity_error:"artifact_digest_mismatch"}]})});
  api.getEmailModelVersion.mockRejectedValueOnce(new Error("详情不可用")).mockResolvedValue({ok:true,model});
  show("/email?tab=learning");await user.click(await screen.findByRole("button",{name:"查看 embedding-full-v9"}));
  expect(await screen.findByText("详情不可用")).toBeInTheDocument();expect(screen.getByRole("table",{name:"模型版本"})).toHaveTextContent("embedding-full-v9");
  await user.click(screen.getByRole("button",{name:"重试"}));const drawer=screen.getByRole("dialog");
  expect(await within(drawer).findByText("important 独立输出头")).toBeInTheDocument();
  expect(drawer).toHaveTextContent("37");expect(drawer).toHaveTextContent("99.0%");expect(drawer).toHaveTextContent("200.0 ms");expect(drawer).toHaveTextContent("digest-9");expect(drawer).toHaveTextContent("emb-7");expect(drawer).toHaveTextContent("a".repeat(64));
});
it("keeps junk confirmable as Trash while excluding it from business configuration",async()=>{
  const user=userEvent.setup();api.listEmailConfigs.mockResolvedValue({items:[config,{...config,category_key:"junk",display_name:"垃圾"}]});show("/email?tab=pending_feedback&selected=1");
  await user.click(await screen.findByRole("button",{name:"垃圾（Trash）"}));
  api.confirmEmailClassification.mockResolvedValue({ok:true,message:"已保存"});
  await user.click(screen.getByRole("button",{name:"保存分类并继续"}));
  expect(api.confirmEmailClassification).toHaveBeenCalledWith("1","junk",expect.any(String),null);
});
it("uses readable source, status, promotion check and mode transition labels",async()=>{
  const user=userEvent.setup();api.listEmailLearning.mockResolvedValue({learning:learning({promotion_gate:{config:gateConfig,promotion_eligible:false,checks:[{key:"category_precision:work",actual:.8,target:.95,operator:">=",passed:false,reason:"threshold_not_met"},{key:"system_integrity",actual:false,target:true,operator:"==",passed:false,reason:"model_evidence_or_configuration_not_ready"}]},mode_transitions:[{request_id:"r1",actor:"console-user",from_mode:"agent_primary",to_mode:"model_primary",from_model_id:null,target_model_id:"model-v2",switched_at:"2026-09-08T00:00:00Z",reason:"user_enabled_primary_model"}]})});
  show();expect(await screen.findAllByText("Agent 分类 · 待反馈")).toHaveLength(2);
  await user.click(screen.getByRole("tab",{name:/模型训练/}));
  expect(await screen.findByText("工作 · Precision")).toBeInTheDocument();expect(screen.getByText("尚未达到门槛")).toBeInTheDocument();
  const history=screen.getByRole("table",{name:"运行模式切换记录"});expect(history).toHaveTextContent("Agent 主分类");expect(history).toHaveTextContent("模型主分类");expect(history).toHaveTextContent("用户启用主模型");
});
it("loads immutable category history on demand and keeps current form on history failure",async()=>{
  const user=userEvent.setup();api.listEmailCategoryHistory.mockRejectedValueOnce(new Error("历史暂不可用")).mockResolvedValue({ok:true,items:[{revision_id:1,category_key:"work",config_version:"c-old",description_version:"d-old",created_at:"2026-09-07T00:00:00Z",config:{core_description:"历史定义",include:["旧场景"],exclude:["旧反例"]}}]});
  show("/email?tab=config");await screen.findByLabelText("核心定义");expect(api.listEmailCategoryHistory).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button",{name:"查看描述历史"}));expect(await screen.findByText("历史暂不可用")).toBeInTheDocument();
  expect(screen.getByLabelText("核心定义")).toHaveValue("工作定义");
  await user.click(screen.getByRole("button",{name:"重试历史"}));expect(await screen.findByText("c-old")).toBeInTheDocument();
  await user.click(screen.getByText("查看历史描述"));expect(screen.getByText("历史定义")).toBeInTheDocument();
  expect(api.listEmailCategoryHistory).toHaveBeenCalledWith("work",expect.any(AbortSignal));
});
it("renders training duration, coverage and safe parameters without inventing absent counts",async()=>{
  const user=userEvent.setup(),model={model_id:"training-v1",status:"candidate",trained_at:"2026-09-08T00:00:00Z",metrics:null,evaluation:null,head_timing_percentiles_ms:null,end_to_end_latency_ms:null,training:{started_at:"2026-09-07T23:59:00Z",completed_at:"2026-09-08T00:00:00Z",duration_ms:60000,sample_count:123,category_sample_count:111,account_count:2,group_count:97},parameters:{random_seed:20260905,solver:"lbfgs",max_iter:1000,regularization_alpha:.001,hidden_layer_sizes:[8],alpha:.6,beta:.4}};
  api.listEmailLearning.mockResolvedValue({learning:learning({staged_models:[model]})});api.getEmailModelVersion.mockResolvedValue({ok:true,model});show("/email?tab=learning");
  await user.click(await screen.findByRole("button",{name:"查看 training-v1"}));
  const training=await screen.findByRole("region",{name:"训练记录"});expect(training).toHaveTextContent("60000.0 ms");expect(training).toHaveTextContent("123 / 未测量");expect(training).toHaveTextContent("2 / 97");
  expect(screen.getByRole("region",{name:"训练参数"})).toHaveTextContent("20260905");expect(screen.getByRole("region",{name:"训练参数"})).toHaveTextContent("lbfgs");
});
it("reuses a mode request ID on retry and prevents duplicate submissions",async()=>{
  const user=userEvent.setup(),pending=deferred<unknown>();api.saveEmailRuntimeMode.mockReturnValueOnce(pending.promise).mockRejectedValueOnce(new Error("重试失败"));show("/email?tab=learning");
  await user.click(await screen.findByRole("switch",{name:"主模型"}));const button=screen.getByRole("button",{name:"确认切换"});
  fireEvent.click(button);fireEvent.click(button);expect(api.saveEmailRuntimeMode).toHaveBeenCalledTimes(1);
  await act(async()=>pending.reject(new Error("连接中断")));await user.click(screen.getByRole("button",{name:"确认切换"}));
  expect(api.saveEmailRuntimeMode.mock.calls[1][0].request_id).toBe(api.saveEmailRuntimeMode.mock.calls[0][0].request_id);
});
it("ignores an aborted learning rejection after a newer tab request succeeds",async()=>{
  const user=userEvent.setup(),first=deferred<unknown>();api.listEmailLearning.mockReturnValueOnce(first.promise);
  show();await screen.findByRole("button",{name:"打开邮件 邮件1"});
  await user.click(screen.getByRole("tab",{name:/模型训练/}));await screen.findByRole("switch",{name:"主模型"});
  await act(async()=>first.reject(new Error("STALE learning error")));
  expect(screen.queryByText("STALE learning error")).not.toBeInTheDocument();expect(screen.queryByRole("button",{name:"重新加载模型训练"})).not.toBeInTheDocument();
});
it("includes evidence summaries in drawer keyboard traversal",async()=>{
  const user=userEvent.setup();show("/email?selected=1");await screen.findByText(/完整正文/);
  const summary=screen.getByText("候选分布");expect(summary.closest("details")).not.toHaveAttribute("open");
  await user.keyboard("{Tab}");expect(summary).toHaveFocus();
  // jsdom does not implement the browser's Enter-to-click default for summary.
  // Actual Tab -> Enter opening is also verified in the local browser fixture.
  await user.click(summary);expect(summary.closest("details")).toHaveAttribute("open");
});
it("reconciles a lost switch response from server mode before claiming current state",async()=>{
  const user=userEvent.setup();api.saveEmailRuntimeMode.mockRejectedValueOnce(new Error("切换响应丢失"));show("/email?tab=learning");
  await user.click(await screen.findByRole("switch",{name:"主模型"}));
  api.listEmailLearning.mockResolvedValue({learning:learning({runtime:{...runtime,mode:"model_primary",active_model_id:"model-v2",candidate_ready:false}})});
  await user.click(screen.getByRole("button",{name:"确认切换"}));
  await waitFor(()=>expect(screen.getByRole("switch",{name:"主模型"})).toBeChecked());
  expect(screen.queryByRole("dialog",{name:"确认运行模式"})).not.toBeInTheDocument();expect(screen.queryByLabelText("候选模型已达标")).not.toBeInTheDocument();
});
it("marks mode unverified if both command and readback fail, then retries readback",async()=>{
  const user=userEvent.setup();api.saveEmailRuntimeMode.mockRejectedValueOnce(new Error("切换连接失败"));show("/email?tab=learning");
  await user.click(await screen.findByRole("switch",{name:"主模型"}));api.listEmailLearning.mockRejectedValueOnce(new Error("读取失败"));
  await user.click(screen.getByRole("button",{name:"确认切换"}));
  expect(await screen.findByRole("heading",{name:"运行模式未确认"})).toBeInTheDocument();expect(screen.queryByRole("switch",{name:"主模型"})).not.toBeInTheDocument();
  expect(screen.queryByRole("dialog",{name:"确认运行模式"})).not.toBeInTheDocument();
  api.listEmailLearning.mockResolvedValue({learning:learning({runtime:{...runtime,mode:"model_primary",active_model_id:"model-v2",candidate_ready:false}})});
  await user.click(screen.getByRole("button",{name:"重新读取运行模式"}));
  expect(await screen.findByRole("switch",{name:"主模型"})).toBeChecked();
});
it.each(["","-0.01","1.01"])("does not submit invalid category threshold %j",async(value)=>{
  const user=userEvent.setup();show("/email?tab=config");const input=await screen.findByLabelText("自动处理阈值");
  await user.clear(input);if(value)await user.type(input,value);
  fireEvent.submit(screen.getByRole("button",{name:"保存配置"}).closest("form")!);
  expect(api.saveEmailConfig).not.toHaveBeenCalled();expect(screen.getByRole("alert")).toHaveTextContent("阈值必须是 0 到 1");
});
it("uses the latest returned config version on subsequent saves",async()=>{
  const user=userEvent.setup();api.saveEmailConfig.mockResolvedValue({ok:true,item:{...config,description_version:"d2",config_version:"c2"}});show("/email?tab=config");
  await user.click(await screen.findByRole("button",{name:"保存配置"}));expect(await screen.findByText(/描述版本：d2/)).toBeInTheDocument();
  await user.click(screen.getByRole("button",{name:"保存配置"}));expect(api.saveEmailConfig).toHaveBeenLastCalledWith("work",expect.objectContaining({expected_current_version:"c2"}));
});
