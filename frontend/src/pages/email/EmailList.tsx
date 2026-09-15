import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { confirmEmailClassification, getEmailClassification, listEmailClassifications, type EmailCategoryConfig, type EmailClassificationDetail, type EmailClassificationItem } from "../../api/console";
import { EmailReadingPanel } from "./EmailReadingPanel";
import { configurableCategories, errorMessage, localTime, measured, sourceLabel, statusLabel } from "./shared";

const filters=[["all","全部"],["pending_feedback","待确认"],["processed","已处理"],["unsubscribe","退订"]] as const;
type EmailFilter=typeof filters[number][0];

export function EmailList({configs, onBusy}: {configs:EmailCategoryConfig[]; onBusy:(value:boolean)=>void}) {
  const [params,setParams]=useSearchParams();
  const requestedFilter=params.get("filter");
  const filter:EmailFilter=filters.some(([key])=>key===requestedFilter) ? requestedFilter as EmailFilter : "all";
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
  const [saved,setSaved]=useState(false);
  const [expanded,setExpanded]=useState(false);
  const lock=useRef(false);
  const rowRefs=useRef(new Map<string,HTMLButtonElement>());
  const open=!!selected && closed!==selected;
  const options=[...configurableCategories(configs).filter(item=>item.enabled),{category_key:"junk",display_name:"垃圾（Trash）"}];
  const label=(key?:string|null)=>key ? (configs.find(item=>item.category_key===key)?.display_name || key) : "未分类（留在收件箱）";
  function navigate(nextPage:number,nextSize=pageSize,id?:string) {
    setParams(previous=>{const next=new URLSearchParams(previous);next.set("page",String(nextPage));next.set("page_size",String(nextSize));if(id)next.set("selected",id);else next.delete("selected");return next;});
    setClosed("");
  }
  useEffect(()=>{
    const controller=new AbortController();setLoading(true);setError("");
    listEmailClassifications(filter,{page,page_size:pageSize},controller.signal).then(result=>{
      if(controller.signal.aborted)return;
      const last=Math.max(1,Math.ceil(result.meta.total/pageSize));
      if(page>last){navigate(last);return;}
      setRows(result.items);setTotal(result.meta.total);setLoading(false);
    }).catch(reason=>{if(!controller.signal.aborted){setError(errorMessage(reason));setLoading(false);}});
    return ()=>controller.abort();
  },[filter,page,pageSize,revision]);
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
      if (filter === "pending_feedback") {
        navigate(nextId ? page : Math.max(1,page-1),pageSize,nextId);
      } else {
        setCategory("");setSaved(true);setDetailRevision(value=>value+1);
      }
      setRevision(value=>value+1);
    } catch(reason){setSaveError(errorMessage(reason));}
    finally{lock.current=false;setSaving(false);onBusy(false);}
  }
  function selectFilter(nextFilter:EmailFilter) {
    setParams(previous=>{
      const next=new URLSearchParams(previous);
      next.set("filter",nextFilter);
      next.set("page","1");
      return next;
    });
  }
  const categoryLabel=(key:string|null|undefined)=>key ? label(key) : "未分类（留在收件箱）";
  const filterLabel=filters.find(([key])=>key===filter)?.[1] || "全部";
  function closeReading() { setClosed(selected);setExpanded(false);requestAnimationFrame(()=>rowRefs.current.get(selected)?.focus()); }
  return <div className={`email-workspace${open ? " has-reading" : ""}${expanded ? " reading-expanded" : ""}`}>
    <section className="console-card email-dense-list" aria-label="邮件分类列表">
    <div className="email-filter-row" role="group" aria-label="邮件筛选">{filters.map(([key,text])=><button type="button" key={key} aria-pressed={filter===key} disabled={saving} onClick={()=>selectFilter(key)}>{text}</button>)}</div>
    <nav className="email-list-toolbar" aria-label="邮件分页"><span>第 {page} / {Math.max(1,Math.ceil(total/pageSize))} 页 · 共 {total} 封</span>
      <label>每页邮件数 <select aria-label="每页邮件数" value={pageSize} disabled={saving||loading} onChange={event=>navigate(1,Number(event.target.value))}>{[20,50,100].map(size=><option key={size}>{size}</option>)}</select></label>
      <button className="compact-button" disabled={saving||loading||page===1} onClick={()=>navigate(page-1)}>上一页</button>
      <button className="compact-button" disabled={saving||loading||page*pageSize>=total} onClick={()=>navigate(page+1)}>下一页</button>
      {loading&&<span role="status">正在加载邮件…</span>}
    </nav>
    {filter==="unsubscribe"&&<p className="muted">显示已入队、处理中和已完成的退订任务；打开邮件可查看执行证据。</p>}
    {error&&<p role="alert">{error} <button onClick={()=>setRevision(value=>value+1)}>重试</button></p>}
    {!loading&&!error&&!rows.length&&<p className="page-state">当前没有{filterLabel}邮件</p>}
    <div className="email-row-list" aria-busy={loading}>
      {rows.map(item=><button type="button" key={item.id} ref={element=>{if(element)rowRefs.current.set(item.id,element);else rowRefs.current.delete(item.id);}} aria-label={`打开邮件 ${item.subject || "无主题"}`} aria-pressed={selected===item.id} disabled={saving||loading} className="email-dense-row" onClick={()=>{navigate(page,pageSize,item.id);setClosed("");setSaved(false);}}>
        <span title={item.important==null?"重要状态未知":item.important?"重要 · Star / Flag":"未标记重要"} aria-label={item.important==null?"重要状态未知":item.important?"重要":"未标记重要"}>{item.important==null?"?":item.important?"★":"☆"}</span>
        <span className="email-row-sender" title={item.sender}>{item.sender || "未提供发件人"}</span>
        <span className="email-row-content"><span className="email-mobile-sender">{item.sender} · </span><strong>{item.subject || "无主题"}</strong><span className="email-row-original-text">{item.message_text || "未提供正文"}</span></span>
        <span className="email-row-category" title={filter==="unsubscribe"?"退订任务":categoryLabel(item.category)}>{filter==="unsubscribe"?"退订任务":item.status==="pending_feedback"?"建议：":""}{filter!=="unsubscribe"&&categoryLabel(item.category)}{filter!=="unsubscribe"&&item.status==="pending_feedback"&&<small> · {measured(item.confidence)}</small>}</span>
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
