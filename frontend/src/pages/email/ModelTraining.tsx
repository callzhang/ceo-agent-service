import { useEffect, useRef, useState } from "react";
import { Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { getEmailModelVersion, saveEmailPromotionConfig, saveEmailRuntimeMode, type EmailCategoryConfig, type EmailLearningEvidence, type EmailPromotionConfig, type EmailRuntime, type EmailStagedModel } from "../../api/console";
import { EmailDrawer } from "./EmailDrawer";
import { checkLabel, checkValue, errorMessage, localTime, measured, modeLabel, reasonLabel, statusLabel } from "./shared";
import { modelMetric, trendPoints, type TrendMetric } from "./modelTrend";

type RefreshLearning = () => Promise<EmailLearningEvidence | undefined>;
export function ModelTraining({learning,configs,reload,runtimeVerified,onRuntimeUnverified,onBusy}: {learning:EmailLearningEvidence;configs:EmailCategoryConfig[];reload:RefreshLearning;runtimeVerified:boolean;onRuntimeUnverified:()=>void;onBusy:(busy:boolean)=>void}) {
  const runtime=learning.runtime;
  const [confirm,setConfirm]=useState<EmailRuntime|null>(null);
  const [switchError,setSwitchError]=useState("");
  const [switching,setSwitching]=useState(false);
  const [thresholdBusy,setThresholdBusy]=useState(false);
  const [selected,setSelected]=useState("");
  const [legacy,setLegacyModel]=useState<EmailLearningEvidence["models"][number]|null>(null);
  const [detail,setDetail]=useState<EmailStagedModel|null>(null);
  const [detailError,setDetailError]=useState("");
  const [retry,setRetry]=useState(0);
  const lock=useRef(false);
  const requestId=useRef("");
  const models=learning.staged_models || [];
  useEffect(()=>{
    setDetail(null);setDetailError("");
    if(!selected)return;
    const controller=new AbortController();
    getEmailModelVersion(selected,controller.signal).then(result=>{if(!controller.signal.aborted)setDetail(result.model);}).catch(reason=>{if(!controller.signal.aborted)setDetailError(errorMessage(reason));});
    return ()=>controller.abort();
  },[selected,retry]);
  async function switchMode(){
    if(lock.current||!confirm)return;
    lock.current=true;setSwitching(true);setSwitchError("");onBusy(true);
    try{
      const mode=confirm.mode==="model_primary"?"agent_primary":"model_primary";
      const result=await saveEmailRuntimeMode({mode,model_id:mode==="model_primary"?confirm.candidate_model_id:null,request_id:requestId.current,expected_mode:confirm.mode,expected_model_id:confirm.active_model_id});
      if(!result.ok)throw new Error("切换失败，请刷新后重试");
      await reload();setConfirm(null);
    }catch(reason){
      const message=errorMessage(reason);
      onRuntimeUnverified();
      try {
        const current=await reload();
        if(!current)throw new Error("服务器读取已取消");
        if(current.runtime.mode!==confirm.mode||current.runtime.active_model_id!==confirm.active_model_id)setConfirm(null);
        setSwitchError(message+"；已重新读取服务器状态。");
      }catch(readError){
        setConfirm(null);
        setSwitchError(message+"；当前运行模式未确认："+errorMessage(readError));
      }
    }
    finally{lock.current=false;setSwitching(false);onBusy(false);}
  }
  async function rereadMode() {
    if(lock.current)return;
    lock.current=true;setSwitching(true);onBusy(true);
    try{await reload();setSwitchError("");}catch(reason){setSwitchError("当前运行模式未确认："+errorMessage(reason));}
    finally{lock.current=false;setSwitching(false);onBusy(false);}
  }
  return <section className="console-card email-training">
    <header className="email-mode-header"><div><h2>{!runtimeVerified?"运行模式未确认":runtime?.mode==="model_primary"?"模型主分类":runtime?.candidate_ready?"已达标，等待用户切换":"Agent 主分类"}{runtimeVerified&&runtime?.candidate_ready&&<span className="email-ready-dot" aria-label="候选模型已达标"/>}</h2>
      <p>{!runtimeVerified?"无法验证当前分类方式，请重新读取服务器状态。":runtime?.mode==="model_primary"?"新邮件由主模型分类；拒判、超时、不可用时由 Classifier Agent 处理。":"新邮件由 Classifier Agent 分类；模型仅进行阶段性训练和离线验证。"}</p>
      {runtimeVerified&&<p>主模型：{runtime?.active_model_id || "无"} · 候选：{runtime?.candidate_model_id || "无"} · 待训练样本：{learning.pending_examples}</p>}</div>
      {runtimeVerified?<label className="email-inline-label"><input role="switch" aria-label="主模型" aria-describedby="email-mode-help" type="checkbox" checked={runtime?.mode==="model_primary"} disabled={!runtime?.toggle_enabled||switching||thresholdBusy} onChange={()=>{requestId.current=crypto.randomUUID();setConfirm({...runtime});setSwitchError("");}}/>主模型</label>:<button className="compact-button" disabled={switching} onClick={()=>void rereadMode()}>重新读取运行模式</button>}
    </header>
    <p id="email-mode-help" className="muted">{!runtimeVerified?"当前状态未确认，已暂停显示模式开关。":runtime?.toggle_enabled?"切换需要确认，以服务器读取结果为准。":"服务器尚未允许切换；请查看下方晋升检查和完整性证据。"}</p>
    {switchError&&<p role="alert">{switchError}</p>}
    {confirm&&<EmailDrawer title="确认运行模式" locked={switching} onClose={()=>setConfirm(null)}><div className="email-drawer-content">
      <p>{confirm.mode==="model_primary"?"恢复 Agent 主分类，模型回到影子模式。":"将候选模型 "+confirm.candidate_model_id+" 切换为新邮件的主分类模型。"}</p>
      <button className="primary-button" disabled={switching} onClick={()=>void switchMode()}>{switching?"正在切换…":"确认切换"}</button>
    </div></EmailDrawer>}
    {learning.promotion_gate?<><ThresholdEditor key={learning.promotion_gate.config.config_version} config={learning.promotion_gate.config} reload={reload} disabled={switching} onBusy={value=>{setThresholdBusy(value);onBusy(value);}}/>
      <h3>晋升检查 · {learning.promotion_gate.promotion_eligible?"已达标":"未达标"}</h3>
      <div className="responsive-table-wrap"><table className="settings-table"><thead><tr><th>检查项</th><th>实际值</th><th>目标</th><th>结果 / 原因</th></tr></thead><tbody>{learning.promotion_gate.checks.map(check=><tr key={check.key}><td>{checkLabel(check.key,configs)}</td><td>{checkValue(check.key,check.actual)}</td><td>{check.operator} {checkValue(check.key,check.target)}</td><td>{check.passed?"通过":"未通过"} · <span>{reasonLabel(check.reason)}</span></td></tr>)}</tbody></table></div>
      <details><summary>原始晋升检查证据</summary><pre>{JSON.stringify(learning.promotion_gate.checks,null,2)}</pre></details>
    </>:<p role="alert">晋升配置暂不可用，请刷新重试。</p>}
    {!!learning.registry_issues?.length&&<p role="alert">模型 Registry 完整性异常：{learning.registry_issues.map(issue=>issue.model_id+"（"+issue.integrity_error+"）").join("；")}</p>}
    <ModelTrend models={models} config={learning.promotion_gate?.config}/>
    <h3>模型版本</h3>
    {!models.length&&!learning.models?.length?<p>暂无训练版本。</p>:<div className="responsive-table-wrap"><table className="settings-table email-model-table" aria-label="模型版本"><thead><tr><th>完整模型名与版本</th><th>状态</th><th>训练时间</th><th>样本数</th><th>Macro F1</th><th>端到端 P95</th><th>完整性</th><th>详情</th></tr></thead><tbody>
      {models.map(model=><tr key={model.model_id}><td>{model.model_id}</td><td>{statusLabel(model.status)}{runtime?.active_model_id===model.model_id?" · 主模型":""}</td><td>{localTime(model.trained_at)}</td><td>{model.training?.sample_count ?? (model.split_counts?model.split_counts.train+model.split_counts.validation+model.split_counts.test:"未测量")}</td><td>{measured(model.metrics?.macro_f1)}</td><td>{measured(model.end_to_end_latency_ms?.p95," ms")}</td><td>{integrityLabel(model.integrity_status,model.failure_reason)}</td><td><button className="compact-button" aria-label={"查看 "+model.model_id} onClick={()=>setSelected(model.model_id)}>查看</button></td></tr>)}
      {learning.models?.filter(model=>!models.some(item=>item.model_id===model.model_id)).map(model=><tr key={model.model_id}><td>{model.model_id}</td><td>历史版本</td><td>{localTime(model.trained_at)}</td><td>{model.sample_count}</td><td>未测量</td><td>未测量</td><td>{integrityLabel(model.integrity_status,model.integrity_error)}</td><td><button className="compact-button" aria-label={"历史证据 "+model.model_id} onClick={()=>{setSelected("");setDetail(null);setLegacy(model);}}>查看</button></td></tr>)}
    </tbody></table></div>}
    <h3>运行模式切换记录</h3>{learning.mode_transitions?.length?<><div className="responsive-table-wrap"><table className="settings-table" aria-label="运行模式切换记录"><thead><tr><th>时间 / 操作者</th><th>原模式</th><th>目标模式</th><th>模型变化</th><th>结果</th></tr></thead><tbody>{learning.mode_transitions.map((event,index)=><tr key={String(event.request_id || index)}><td>{localTime(typeof event.created_at==="string"?event.created_at:null)} · {event.actor==="console-user"?"控制台用户":String(event.actor || "未提供")}</td><td>{modeLabel(String(event.from_mode))}</td><td>{modeLabel(String(event.to_mode))}</td><td>{String(event.from_model_id || "无")} → {String(event.target_model_id || "无")}</td><td>{statusLabel(String(event.status || ""))} · {reasonLabel(String(event.reason || ""))}</td></tr>)}</tbody></table></div><details><summary>原始切换证据</summary><pre>{JSON.stringify(learning.mode_transitions,null,2)}</pre></details></>:<p>暂无切换记录。</p>}
    {selected&&<EmailDrawer title="模型版本详情" onClose={()=>setSelected("")}>{detailError?<p role="alert">{detailError} <button onClick={()=>setRetry(value=>value+1)}>重试</button></p>:detail?<ModelDetails model={detail}/>:<p role="status">正在加载模型证据…</p>}</EmailDrawer>}
    {legacy&&<EmailDrawer title="历史模型证据" onClose={()=>setLegacy(null)}><div className="email-drawer-content"><h3>{legacy.model_id}</h3><p>历史版本未提供可比较的评测协议，趋势指标显示为未测量。历史登记状态不代表当前新邮件的主模型。</p><p>历史登记状态：{legacy.status} · 样本 {legacy.sample_count}（新增 {legacy.new_sample_count}）</p><p>训练：{localTime(legacy.training_started_at)} → {localTime(legacy.training_finished_at)}</p><details><summary>原始历史模型证据</summary><pre>{JSON.stringify(legacy,null,2)}</pre></details></div></EmailDrawer>}
  </section>;
  function setLegacy(value:EmailLearningEvidence["models"][number]|null){setLegacyModel(value);}
}

const thresholdFields=[["macro_f1_min","Macro F1 最低值",0,1,"any"],["category_precision_min","每类别 Precision 最低值",0,1,"any"],["category_validation_samples_min","每类别独立验证样本数",1,undefined,1],["p95_latency_max_ms","端到端 P95 上限（ms）",0,undefined,"any"]] as const;
function ThresholdEditor({config,reload,disabled,onBusy}: {config:EmailPromotionConfig;reload:RefreshLearning;disabled:boolean;onBusy:(busy:boolean)=>void}) {
  const [values,setValues]=useState(Object.fromEntries(thresholdFields.map(([key])=>[key,String(config[key])])) as Record<typeof thresholdFields[number][0],string>);
  const [busy,setBusy]=useState(false);const lock=useRef(false);const [error,setError]=useState("");
  async function save(){
    if(lock.current)return;
    for(const [key,,min,max] of thresholdFields){const value=Number(values[key]);if(!values[key].trim()||!Number.isFinite(value)||value<=0||value<min||(max!==undefined&&value>max)||(key==="category_validation_samples_min"&&!Number.isInteger(value))){setError("请填写有效门槛；比例大于 0 且不超过 1，样本数为正整数，延迟为正数。");return;}}
    lock.current=true;setBusy(true);onBusy(true);setError("");
    try{
      const result=await saveEmailPromotionConfig({macro_f1_min:Number(values.macro_f1_min),category_precision_min:Number(values.category_precision_min),category_validation_samples_min:Number(values.category_validation_samples_min),p95_latency_max_ms:Number(values.p95_latency_max_ms),expected_current_version:config.config_version});
      if(!result.ok)throw new Error("门槛保存失败，请重试");await reload();
    }catch(reason){setError(errorMessage(reason));}finally{lock.current=false;setBusy(false);onBusy(false);}
  }
  return <form onSubmit={event=>{event.preventDefault();void save();}}><h3>晋升门槛 <small>版本 {config.config_version}</small></h3><fieldset disabled={busy||disabled} className="email-threshold-fields">{thresholdFields.map(([key,label,min,max,step])=><label key={key}>{label}<input type="number" min={min} max={max} step={step} value={values[key]} onChange={event=>setValues(previous=>({...previous,[key]:event.target.value}))}/></label>)}</fieldset>{error&&<p role="alert">{error}</p>}<button className="compact-button" disabled={busy||disabled} type="submit">{busy?"正在保存…":"保存晋升门槛"}</button></form>;
}

function ModelTrend({models,config}: {models:EmailStagedModel[];config?:EmailPromotionConfig}) {
  const [metric,setMetric]=useState<TrendMetric>("macro_f1");const [category,setCategory]=useState("");
  const categories=Array.from(new Set(models.flatMap(model=>Object.keys(model.metrics?.categories || {}))));
  const activeCategory=category || categories[0] || "";
  const points=trendPoints([...models].sort((a,b)=>a.trained_at.localeCompare(b.trained_at)),metric,activeCategory);
  const segments=Array.from(new Set(points.map(point=>point.segment)));
  const data=points.map(point=>({name:point.model.model_id,["segment"+point.segment]:point.value,description:[point.model.model_id,localTime(point.model.trained_at),point.model.split_counts?JSON.stringify(point.model.split_counts):"样本数未测量",point.model.evaluation?.test_digest || "未提供",point.reason].join(" · ")}));
  const latency=metric==="p50"||metric==="p95"||metric==="p99";
  const target=metric==="macro_f1"?config?.macro_f1_min:metric==="precision"?config?.category_precision_min:metric==="p95"?config?.p95_latency_max_ms:undefined;
  return <section aria-label="模型能力趋势"><h3>能力趋势 · 最近 10 个版本</h3><div className="email-list-toolbar">
    <label>指标 <select value={metric} onChange={event=>setMetric(event.target.value as TrendMetric)}>{["macro_f1","accuracy","precision","recall","f1","p50","p95","p99"].map(key=><option key={key}>{key}</option>)}</select></label>
    {["precision","recall","f1"].includes(metric)&&<label>类别 <select value={activeCategory} onChange={event=>setCategory(event.target.value)}>{categories.map(key=><option key={key}>{key}</option>)}</select></label>}
  </div>
  {points.filter(point=>point.value!==null).length<2?<p>暂无足够的可比较趋势数据，缺失指标为未测量。</p>:<div className="email-trend-chart"><ResponsiveContainer width="100%" height={240}><LineChart data={data} accessibilityLayer><XAxis dataKey="name" tickFormatter={name=>String(name).slice(-8)} interval="preserveStartEnd" tick={{fontSize:11}}/><YAxis domain={latency?[0,"auto"]:[0,1]}/><Tooltip labelFormatter={(_label,items)=>items[0]?.payload.description} formatter={value=>measured(value,latency?" ms":"%")}/>{target!==undefined&&<ReferenceLine y={target} stroke="var(--ink-soft)" strokeDasharray="4 4" label="晋升门槛"/>}{segments.map(segment=><Line key={segment} dataKey={"segment"+segment} name={metric} type="linear" stroke="var(--accent)" connectNulls={false} isAnimationActive={false}/>)}</LineChart></ResponsiveContainer></div>}
  <p className="muted">只连接评测协议、测试集和类别集合一致的相邻版本；缺失数据和不可比较版本显示断点。</p>
  <details><summary>趋势文字数据（键盘可读）</summary><table className="settings-table"><thead><tr><th>模型 / 训练时间 / 样本</th><th>指标</th><th>评测 / 断点原因</th></tr></thead><tbody>{points.map(point=><tr key={point.model.model_id}><td>{point.model.model_id} · {localTime(point.model.trained_at)} · {JSON.stringify(point.model.split_counts)}</td><td>{measured(modelMetric(point.model,metric,activeCategory),latency?" ms":"%")}</td><td>{point.model.evaluation?.test_digest || "未提供"} · {point.reason}</td></tr>)}</tbody></table></details>
  </section>;
}
function ModelDetails({model}: {model:EmailStagedModel}) {
  return <div className="email-drawer-content"><h3>{model.model_id}</h3><p>状态：{statusLabel(model.status)} · 训练时间：{localTime(model.trained_at)}</p><p>Accuracy {measured(model.metrics?.accuracy)} · Macro F1 {measured(model.metrics?.macro_f1)}</p>
    <section aria-label="训练记录"><h4>训练记录</h4><dl className="detail-definition-list">
      <div><dt>开始时间</dt><dd>{localTime(model.training?.started_at)}</dd></div>
      <div><dt>完成时间</dt><dd>{localTime(model.training?.completed_at)}</dd></div>
      <div><dt>训练耗时</dt><dd>{measured(model.training?.duration_ms," ms")}</dd></div>
      <div><dt>总样本 / 新增样本</dt><dd>{model.training?.sample_count ?? "未测量"} / {model.training?.new_sample_count ?? "未测量"}</dd></div>
      <div><dt>分类样本</dt><dd>{model.training?.category_sample_count ?? "未测量"}</dd></div>
      <div><dt>邮箱账户 / 事项组覆盖</dt><dd>{model.training?.account_count ?? "未测量"} / {model.training?.group_count ?? "未测量"}</dd></div>
      <div><dt>训练 / 验证 / 测试样本</dt><dd>{model.split_counts?.train ?? "未测量"} / {model.split_counts?.validation ?? "未测量"} / {model.split_counts?.test ?? "未测量"}</dd></div>
    </dl></section>
    <section aria-label="模型版本与评测"><h4>模型版本与评测</h4><dl className="detail-definition-list">{[
      ["模型家族",model.compatibility?.head_format],["Embedding 版本",model.compatibility?.embedding_revision_reference],["描述版本",model.compatibility?.description_version],["输入 Schema",model.compatibility?.input_schema_version],["训练数据版本",model.training_snapshot_id],["评测方法",model.evaluation?.protocol],["测试集摘要",model.evaluation?.test_digest],["Artifact SHA-256",model.artifact_sha256]
    ].map(([label,value])=><div key={String(label)}><dt>{String(label)}</dt><dd>{value==null?"未提供":String(value)}</dd></div>)}</dl></section>
    <section aria-label="训练参数"><h4>训练参数</h4><dl className="detail-definition-list">{[
      ["随机种子",model.parameters?.random_seed],["求解器",model.parameters?.solver],["最大迭代次数",model.parameters?.max_iter],["正则化系数",model.parameters?.regularization_alpha],["隐藏层",model.parameters?.hidden_layer_sizes],["描述权重 α",model.parameters?.alpha],["模型权重 β",model.parameters?.beta]
    ].map(([label,value])=><div key={String(label)}><dt>{String(label)}</dt><dd>{value==null?"未测量":Array.isArray(value)?value.join("、"):String(value)}</dd></div>)}</dl></section>
    <h4>每类别指标</h4><div className="responsive-table-wrap"><table className="settings-table"><thead><tr><th>类别</th><th>Precision</th><th>Recall</th><th>F1</th><th>support</th><th>接受准确率</th><th>接受数量 / 独立事项组</th><th>阈值</th></tr></thead><tbody>{Object.entries(model.metrics?.categories || {}).map(([key,values])=><tr key={key}><td>{key}</td><td>{measured(values.precision)}</td><td>{measured(values.recall)}</td><td>{measured(values.f1)}</td><td>{values.support ?? "未测量"}</td><td>{measured(values.accepted_precision)}</td><td>{values.accepted_hits ?? "未测量"} / {values.independent_groups ?? "未测量"}</td><td>{measured(values.threshold)}</td></tr>)}</tbody></table></div>
    <h4>important 独立输出头</h4>{["precision","recall","f1","accepted_precision"].map(key=><p key={key}>{key}：{measured(model.metrics?.important?.[key])}</p>)}
    <h4>延迟</h4>{(["p50","p95","p99"] as const).map(key=><p key={key}>{key} · 端到端 {measured(model.end_to_end_latency_ms?.[key]," ms")} · 输出头 {measured(model.head_timing_percentiles_ms?.[key]," ms")}</p>)}
    <p>首次调用 / 模型加载耗时：未测量（当前 API 未提供测量证据）</p>
    <details><summary>原始版本、覆盖、评测与完整性证据</summary><pre>{JSON.stringify(model,null,2)}</pre></details>
  </div>;
}
function integrityLabel(status:string|undefined,error:string|undefined) {return status==="verified"?"已验证":status==="corrupt"||error?"异常":"未提供";}
