import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Flag, Star } from "lucide-react";
import { confirmEmailClassification, getEmailClassification, listEmailClassifications, type EmailCategoryConfig, type EmailClassificationDetail, type EmailClassificationItem, type EmailClassificationStatus } from "../../api/console";
import { EmailReadingPanel } from "./EmailReadingPanel";
import { configurableCategories, errorMessage, localTime, measured, sourceLabel, statusLabel } from "./shared";

export function EmailList({configs, status, onBusy}: {configs:EmailCategoryConfig[]; status:EmailClassificationStatus; onBusy:(value:boolean)=>void}) {
  const [params,setParams]=useSearchParams();
  const rawPage=Number(params.get("page"));
  const page=Number.isInteger(rawPage) && rawPage>0 ? rawPage : 1;
  const rawSize=Number(params.get("page_size"));
  const pageSize=[20,50,100].includes(rawSize)?rawSize:50;
  const selected=params.get("selected") || "";
  const query=params.get("q") || "";
  const [searchText,setSearchText]=useState(query);
  const [composing,setComposing]=useState(false);
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
  const [saved,setSaved]=useState(false);
  const [expanded,setExpanded]=useState(false);
  const lock=useRef(false);
  const rowRefs=useRef(new Map<string,HTMLButtonElement>());
  const open=!!selected && closed!==selected;
  const options=[...configurableCategories(configs).filter(item=>item.enabled),{category_key:"junk",display_name:"垃圾（Trash）"}];
  const label=(key?:string|null)=>key ? (configs.find(item=>item.category_key===key)?.display_name || key) : "未分类（留在收件箱）";
  useEffect(()=>{setSearchText(query);},[query]);
  function applySearch(value:string) {
    setParams(previous=>{const next=new URLSearchParams(previous);if(value.trim())next.set("q",value.trim());else next.delete("q");next.set("page","1");next.delete("selected");return next;},{replace:true});
    setClosed("");setExpanded(false);
  }
  useEffect(()=>{
    if(composing || saving || searchText.trim()===query.trim())return;
    const timer=window.setTimeout(()=>applySearch(searchText),300);
    return ()=>window.clearTimeout(timer);
  },[searchText,query,composing,saving]);
  function navigate(nextPage:number,nextSize=pageSize,id?:string) {
    setParams(previous=>{const next=new URLSearchParams(previous);next.set("page",String(nextPage));next.set("page_size",String(nextSize));if(id)next.set("selected",id);else next.delete("selected");return next;});
    setClosed("");
  }
  useEffect(()=>{
    const controller=new AbortController();setLoading(true);setError("");
    listEmailClassifications(status,{page,page_size:pageSize,...(query.trim()?{q:query.trim()}:{})},controller.signal).then(result=>{
      if(controller.signal.aborted)return;
      const last=Math.max(1,Math.ceil(result.meta.total/pageSize));
      if(page>last){navigate(last);return;}
      setRows(result.items);setTotal(result.meta.total);setLoading(false);
    }).catch(reason=>{if(!controller.signal.aborted){setError(errorMessage(reason));setLoading(false);}});
    return ()=>controller.abort();
  },[status,page,pageSize,revision,query]);
  useEffect(()=>{
    setCategory("");setDetail(null);setDetailError("");setSaveError("");
    if(!open)return;
    const controller=new AbortController();
    getEmailClassification(selected,controller.signal).then(result=>{
      if(!controller.signal.aborted){setDetail(result);setCategory(result.item.category || "");}
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
      if (status === "pending_feedback") {
        navigate(nextId ? page : Math.max(1,page-1),pageSize,nextId);
      } else {
        setCategory("");setSaved(true);setDetailRevision(value=>value+1);
      }
      setRevision(value=>value+1);
    } catch(reason){setSaveError(errorMessage(reason));}
    finally{lock.current=false;setSaving(false);onBusy(false);}
  }
  const categoryLabel=(key:string|null|undefined)=>key ? label(key) : "未分类（留在收件箱）";
  function closeReading() { setClosed(selected);setExpanded(false);requestAnimationFrame(()=>rowRefs.current.get(selected)?.focus()); }
  return <div className={`email-workspace${open ? " has-reading" : ""}${expanded ? " reading-expanded" : ""}`}>
    <section className="console-card email-dense-list" aria-label="邮件分类列表">
    <div className="email-list-controls">
    <nav className="email-list-toolbar" aria-label="邮件分页"><span>第 {page} / {Math.max(1,Math.ceil(total/pageSize))} 页 · 共 {total} 封</span>
      <label>每页邮件数 <select aria-label="每页邮件数" value={pageSize} disabled={saving||loading} onChange={event=>navigate(1,Number(event.target.value))}>{[20,50,100].map(size=><option key={size}>{size}</option>)}</select></label>
      <button className="compact-button" disabled={saving||loading||page===1} onClick={()=>navigate(page-1)}>上一页</button>
      <button className="compact-button" disabled={saving||loading||page*pageSize>=total} onClick={()=>navigate(page+1)}>下一页</button>
      {loading&&<span role="status">正在加载邮件…</span>}
    </nav>
    <form className="email-search" role="search" onSubmit={event=>{event.preventDefault();if(!composing&&!saving)applySearch(searchText);}}>
      <input type="search" aria-label="搜索邮件" placeholder="搜索发件人、主题、正文…" maxLength={500} value={searchText} disabled={saving} onChange={event=>setSearchText(event.target.value)} onCompositionStart={()=>setComposing(true)} onCompositionEnd={()=>setComposing(false)}/>
      {searchText&&<button type="button" aria-label="清空搜索" disabled={saving} onClick={()=>{setSearchText("");applySearch("");}}>×</button>}
    </form></div>
    {status==="unsubscribe"&&<p className="muted">显示已入队、处理中和已完成的退订任务；打开邮件可查看执行证据。</p>}
    {error&&<p role="alert">{error} <button onClick={()=>setRevision(value=>value+1)}>重试</button></p>}
    {!loading&&!error&&!rows.length&&<p className="page-state">{query.trim()?"未找到匹配邮件":status==="pending_feedback"?"当前没有待确认邮件":status==="unsubscribe"?"当前没有退订记录":"当前没有邮件"}</p>}
    <div className="email-row-list" aria-busy={loading}>
      {rows.map(item=><button type="button" key={item.id} ref={element=>{if(element)rowRefs.current.set(item.id,element);else rowRefs.current.delete(item.id);}} aria-label={`打开邮件 ${item.subject || "无主题"}`} aria-pressed={selected===item.id} disabled={saving||loading} className="email-dense-row" onClick={()=>{navigate(page,pageSize,item.id);setClosed("");setSaved(false);}}>
        {(() => {
          const provider = item.provider_classification;
          const signalsAvailable = provider && ("starred" in provider || "important_flag" in provider);
          const starLabel = provider?.starred == null ? "未知" : provider.starred ? "已标星" : "未标星";
          const flagLabel = provider?.important_flag == null ? "未知" : provider.important_flag ? "已标记" : "未标记";
          return <span className="email-important-signals" title={signalsAvailable ? `Star：${starLabel} · Flag：${flagLabel}` : "Star / Flag 状态未知"} aria-label={signalsAvailable ? `Star：${starLabel}，Flag：${flagLabel}` : "重要状态未知"}>
            <Star size={14} fill={provider?.starred === true ? "currentColor" : "none"} aria-hidden="true"/>
            <Flag size={14} fill={provider?.important_flag === true ? "currentColor" : "none"} aria-hidden="true"/>
          </span>;
        })()}
        <span className="email-row-sender" title={item.sender}>{item.sender || "未提供发件人"}</span>
        <span className="email-row-content"><span className="email-mobile-sender">{item.sender} · </span><strong>{item.subject || "无主题"}</strong><span className="email-row-original-text">{item.message_text || "未提供正文"}</span></span>
        <span className="email-row-category" title={status==="unsubscribe"?"退订任务":categoryLabel(item.category)}>{status==="unsubscribe"?"退订任务":item.status==="pending_feedback"?"建议：":""}{status!=="unsubscribe"&&categoryLabel(item.category)}{status!=="unsubscribe"&&item.status==="pending_feedback"&&<small> · {measured(item.confidence)}</small>}</span>
        <span className="email-row-status">{sourceLabel(item.classification_source)} · {statusLabel(item.status)}</span>
        <time title={localTime(item.received_at || item.updated_at)}>{localTime(item.received_at || item.updated_at)}</time>
      </button>)}
    </div>
    </section>
    {open && <EmailReadingPanel key={selected} detail={detail} error={detailError} saving={saving} loading={loading} category={category} saveError={saveError} saved={saved} configs={configs} options={options} position={rows.findIndex(item=>item.id===selected)} count={rows.length} expanded={expanded}
      onCategory={value=>{setCategory(value);setSaved(false);}} onSave={()=>void save()} onClose={closeReading} onRetry={()=>setDetailRevision(value=>value+1)} onExpand={()=>setExpanded(value=>!value)}
      onPrevious={()=>{const index=rows.findIndex(item=>item.id===selected);if(index>0){navigate(page,pageSize,rows[index-1].id);setSaved(false);}}}
      onNext={()=>{const index=rows.findIndex(item=>item.id===selected);if(index>=0&&index<rows.length-1){navigate(page,pageSize,rows[index+1].id);setSaved(false);}}}/>}
  </div>;
}
