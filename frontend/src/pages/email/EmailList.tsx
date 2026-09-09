import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { confirmEmailClassification, getEmailClassification, listEmailClassifications, type EmailCategoryConfig, type EmailClassificationDetail, type EmailClassificationItem } from "../../api/console";
import { EmailDrawer } from "./EmailDrawer";
import { ObservabilityDetails, ProcessedClassificationEvidence } from "./Evidence";
import { configurableCategories, errorMessage, localTime, measured, sourceLabel, statusLabel } from "./shared";

export function EmailList({pending, configs, onBusy}: {pending:boolean; configs:EmailCategoryConfig[]; onBusy:(value:boolean)=>void}) {
  const [params,setParams]=useSearchParams();
  const rawPage=Number(params.get("page"));
  const page=Number.isInteger(rawPage) && rawPage>0 ? rawPage : 1;
  const rawSize=Number(params.get("page_size"));
  const pageSize=[20,50,100].includes(rawSize)?rawSize:50;
  const selected=params.get("selected") || "";
  const [closed,setClosed]=useState("");
  const [rows,setRows]=useState<EmailClassificationItem[]>([]);
  const [total,setTotal]=useState(0);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState("");
  const [revision,setRevision]=useState(0);
  const [detail,setDetail]=useState<EmailClassificationDetail|null>(null);
  const [detailError,setDetailError]=useState("");
  const [detailRevision,setDetailRevision]=useState(0);
  const [category,setCategory]=useState("");
  const [saveError,setSaveError]=useState("");
  const [saving,setSaving]=useState(false);
  const lock=useRef(false);
  const rowRefs=useRef(new Map<string,HTMLButtonElement>());
  const open=!!selected && closed!==selected;
  const options=[...configurableCategories(configs).filter(item=>item.enabled),{category_key:"junk",display_name:"垃圾（Trash）"}];
  const label=(key:string)=>configs.find(item=>item.category_key===key)?.display_name || key;
  function navigate(nextPage:number,nextSize=pageSize,id?:string) {
    setParams(previous=>{const next=new URLSearchParams(previous);next.set("page",String(nextPage));next.set("page_size",String(nextSize));if(id)next.set("selected",id);else next.delete("selected");return next;});
    setClosed("");
  }
  useEffect(()=>{
    const controller=new AbortController();setLoading(true);setError("");
    listEmailClassifications(pending?"pending_feedback":"processed",{page,page_size:pageSize},controller.signal).then(result=>{
      if(controller.signal.aborted)return;
      const last=Math.max(1,Math.ceil(result.meta.total/pageSize));
      if(page>last){navigate(last);return;}
      setRows(result.items);setTotal(result.meta.total);setLoading(false);
    }).catch(reason=>{if(!controller.signal.aborted){setError(errorMessage(reason));setLoading(false);}});
    return ()=>controller.abort();
  },[pending,page,pageSize,revision]);
  useEffect(()=>{
    setCategory("");setDetail(null);setDetailError("");setSaveError("");
    if(!open)return;
    const controller=new AbortController();
    getEmailClassification(selected,controller.signal).then(result=>{
      if(!controller.signal.aborted)setDetail(result);
    }).catch(reason=>{if(!controller.signal.aborted)setDetailError(errorMessage(reason));});
    return ()=>controller.abort();
  },[selected,open,detailRevision]);
  async function save() {
    if(lock.current || !category || !detail || loading)return;
    lock.current=true;setSaving(true);onBusy(true);setSaveError("");
    try {
      const result=await confirmEmailClassification(selected,category,`email-feedback:${selected}:${category}:${detail.item.current_action_plan_id || "initial"}`,detail.item.current_action_plan_id);
      if(!result.ok)throw new Error(result.message || "保存失败，请重试");
      const index=rows.findIndex(item=>item.id===selected);
      const nextId=rows[index+1]?.id || rows[index-1]?.id;
      setRows(previous=>previous.filter(item=>item.id!==selected));
      setTotal(previous=>Math.max(0,previous-1));
      navigate(nextId?page:Math.max(1,page-1),pageSize,nextId);
      setRevision(value=>value+1);
    } catch(reason){setSaveError(errorMessage(reason));}
    finally{lock.current=false;setSaving(false);onBusy(false);}
  }
  return <section className="console-card email-dense-list" aria-label={pending?"待反馈邮件":"已处理邮件"}>
    <nav className="email-list-toolbar" aria-label="邮件分页"><span>第 {page} / {Math.max(1,Math.ceil(total/pageSize))} 页 · 共 {total} 封</span>
      <label>每页邮件数 <select aria-label="每页邮件数" value={pageSize} disabled={saving||loading} onChange={event=>navigate(1,Number(event.target.value))}>{[20,50,100].map(size=><option key={size}>{size}</option>)}</select></label>
      <button className="compact-button" disabled={saving||loading||page===1} onClick={()=>navigate(page-1)}>上一页</button>
      <button className="compact-button" disabled={saving||loading||page*pageSize>=total} onClick={()=>navigate(page+1)}>下一页</button>
      {loading&&<span role="status">正在加载邮件…</span>}
    </nav>
    {error&&<p role="alert">{error} <button onClick={()=>setRevision(value=>value+1)}>重试</button></p>}
    {!loading&&!error&&!rows.length&&<p className="page-state">当前没有{pending?"待反馈":"已处理"}邮件</p>}
    <div className="email-row-list" aria-busy={loading}>
      {rows.map(item=><button type="button" key={item.id} ref={element=>{if(element)rowRefs.current.set(item.id,element);else rowRefs.current.delete(item.id);}} aria-label={`打开邮件 ${item.subject || "无主题"}`} aria-pressed={selected===item.id} disabled={saving||loading} className="email-dense-row" onClick={()=>{navigate(page,pageSize,item.id);setClosed("");}}>
        <span title={item.important==null?"重要状态未知":item.important?"重要 · Star / Flag":"未标记重要"} aria-label={item.important==null?"重要状态未知":item.important?"重要":"未标记重要"}>{item.important==null?"?":item.important?"★":"☆"}</span>
        <span className="email-row-sender" title={item.sender}>{item.sender || "未提供发件人"}</span>
        <span className="email-row-content"><span className="email-mobile-sender">{item.sender} · </span><strong>{item.subject || "无主题"}</strong><span className="muted"> — {item.preview || "未提供摘要"}</span></span>
        <span className="email-row-category" title={label(item.category)}>{pending?"建议：":""}{label(item.category)}{pending&&<small> · {measured(item.confidence)}</small>}</span>
        <span className="email-row-status">{pending?"待反馈":`${sourceLabel(item.classification_source)} · ${statusLabel(item.status)}`}</span>
        <time title={localTime(item.received_at || item.updated_at)}>{localTime(item.received_at || item.updated_at)}</time>
      </button>)}
    </div>
    {open&&<EmailDrawer title="邮件详情" locked={saving} returnFocus={()=>rowRefs.current.get(selected) || null} onClose={()=>setClosed(selected)}>
      {detailError?<p role="alert">{detailError} <button onClick={()=>setDetailRevision(value=>value+1)}>重试正文</button></p>:!detail?<p role="status">正在加载邮件正文…</p>:<>
        <div className="email-drawer-content"><h3>{detail.item.subject || "无主题"}</h3><p>发件人：{detail.item.sender}</p><p>收件人：{detail.item.recipients?.join("、") || "未提供"}</p><p>抄送：{detail.item.cc || "未提供"}</p><p>收件时间：{localTime(detail.item.received_at)}</p>
        <section aria-label="邮件正文"><h3>邮件正文</h3><div className="email-body-text">{detail.item.message_text || "这封邮件没有已保存的正文，请查看原邮件后分类。"}</div>
          {detail.item.quoted_text&&<details><summary>引用邮件</summary><div className="email-body-text">{detail.item.quoted_text}</div></details>}
        </section>
        {!!detail.item.attachment_metadata?.length&&<section aria-label="附件元数据"><h3>附件（仅元数据）</h3>{detail.item.attachment_metadata.map((file,index)=><p key={index}>{file.filename} · {file.mime_type} · {file.size_bytes} bytes</p>)}</section>}
        <p>分类来源：{sourceLabel(detail.item.classification_source)} · 置信度：{measured(detail.item.confidence)} · 描述版本：{detail.item.description_version || "未提供"}</p>
        <ProcessedClassificationEvidence row={detail.item}/>
        {(detail.provider_classification || detail.item.provider_classification)&&<section aria-label="邮箱观察事实"><h3>邮箱观察事实</h3><p>已观察到的文件夹与 Star / Flag 状态：</p><pre>{JSON.stringify(detail.provider_classification || detail.item.provider_classification,null,2)}</pre></section>}
        <details><summary>候选分布</summary>{Object.entries(detail.item.probabilities).map(([key,value])=><p key={key}>{label(key)}：{measured(value)}</p>)}</details>
        <ObservabilityDetails events={detail.observability}/>
        </div>
        {pending&&<form className="email-drawer-footer" onSubmit={event=>{event.preventDefault();void save();}}>
          <p>建议：{label(detail.item.category)} · {measured(detail.item.confidence)}，请选择类别后保存。</p>
          <div className="settings-pill-row" role="group" aria-label="选择分类">{options.map(item=><button type="button" key={item.category_key} disabled={saving||loading} aria-pressed={category===item.category_key} onClick={()=>setCategory(item.category_key)}>{item.display_name}</button>)}</div>
          {!options.length&&<p>暂无可用类别，请先检查邮件配置。</p>}{saveError&&<p role="alert">{saveError}</p>}
          <button type="submit" className="primary-button" disabled={!category||saving||loading}>{saving?"正在保存…":"保存分类并继续"}</button>
        </form>}
      </>}
    </EmailDrawer>}
  </section>;
}
