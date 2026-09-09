import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { listEmailConfigs, listEmailLearning, type EmailCategoryConfig, type EmailLearningEvidence } from "../api/console";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { EmailList } from "./email/EmailList";
import { EmailConfig } from "./email/EmailConfig";
import { ModelTraining } from "./email/ModelTraining";
import { errorMessage } from "./email/shared";
import "./email/email.css";
const tabs=[["list","邮件分类"],["config","邮件配置"],["learning","模型训练"]] as const;

export function EmailPage() {
  const [params,setParams]=useSearchParams();
  const requested=params.get("tab") || "list";
  const tab=tabs.some(([key])=>key===requested)?requested:"list";
  const [configs,setConfigs]=useState<EmailCategoryConfig[]|null>(null);
  const [configError,setConfigError]=useState("");
  const [learning,setLearning]=useState<EmailLearningEvidence|null>(null);
  const [learningError,setLearningError]=useState("");
  const [runtimeVerified,setRuntimeVerified]=useState(true);
  const [busy,setBusy]=useState(false);
  const [retry,setRetry]=useState(0);
  const refs=useRef<Array<HTMLButtonElement|null>>([]);
  const learningRequest=useRef<AbortController|null>(null);
  const reload=useCallback(async()=>{
    learningRequest.current?.abort();
    const controller=new AbortController();learningRequest.current=controller;
    try {
      const result=await listEmailLearning(controller.signal);
      if(!controller.signal.aborted){setLearning(result.learning);setLearningError("");setRuntimeVerified(true);return result.learning;}
    } catch(reason) {if(!controller.signal.aborted)throw reason;}
  },[]);
  useEffect(()=>{
    const controller=new AbortController();setConfigError("");
    listEmailConfigs(controller.signal).then(result=>{if(!controller.signal.aborted)setConfigs(result.items);}).catch(reason=>{if(!controller.signal.aborted)setConfigError(errorMessage(reason));});
    return ()=>controller.abort();
  },[retry]);
  useEffect(()=>{
    void reload().catch(reason=>{if(!learningRequest.current?.signal.aborted)setLearningError(errorMessage(reason));});
    return ()=>learningRequest.current?.abort();
  },[reload,tab,retry]);
  function selectTab(value:string) {
    if(busy)return;
    setParams(previous=>{const next=new URLSearchParams(previous);next.set("tab",value);next.set("page","1");next.delete("selected");return next;});
  }
  function keyDown(event:KeyboardEvent<HTMLButtonElement>,index:number) {
    if(busy)return;
    const next=event.key==="ArrowRight"?(index+1)%tabs.length:event.key==="ArrowLeft"?(index+tabs.length-1)%tabs.length:event.key==="Home"?0:event.key==="End"?tabs.length-1:null;
    if(next===null)return;event.preventDefault();selectTab(tabs[next][0]);refs.current[next]?.focus();
  }
  return <ConsolePageLayout title="Email">
    <div className="settings-pill-row email-tabs" role="tablist" aria-label="邮件页面分区">{tabs.map(([key,label],index)=><button type="button" role="tab" id={"email-tab-"+key} aria-controls={"email-panel-"+key} key={key} disabled={busy} aria-selected={key===tab} tabIndex={key===tab?0:-1} ref={element=>{refs.current[index]=element;}} onClick={()=>selectTab(key)} onKeyDown={event=>keyDown(event,index)}>{label}{key==="learning"&&runtimeVerified&&learning?.runtime?.candidate_ready&&<span className="email-ready-dot" aria-label="候选模型已达标"/>}</button>)}</div>
    <div role="tabpanel" id={"email-panel-"+tab} aria-labelledby={"email-tab-"+tab}>
      {configError&&tab!=="learning"&&<p role="alert">邮件配置加载失败：{configError} <button onClick={()=>setRetry(value=>value+1)}>重新加载配置</button></p>}
      {tab==="config"?(configs?<EmailConfig configs={configs} onBusy={setBusy} onSaved={item=>setConfigs(previous=>[...(previous || []).filter(value=>value.category_key!==item.category_key),item])}/>:<p role="status">正在加载邮件配置…</p>)
        :tab==="learning"?<>{learningError&&<p role="alert">{learningError} <button onClick={()=>setRetry(value=>value+1)}>重新加载模型训练</button></p>}{learning?<ModelTraining learning={learning} configs={configs || []} reload={reload} runtimeVerified={runtimeVerified} onRuntimeUnverified={()=>setRuntimeVerified(false)} onBusy={setBusy}/>:!learningError&&<p role="status">正在加载模型训练…</p>}</>
        :<EmailList configs={configs || []} onBusy={setBusy}/>}
    </div>
  </ConsolePageLayout>;
}
