import { useEffect, useRef, useState, type SelectHTMLAttributes } from "react";
import { useSearchParams } from "react-router-dom";
import { Flag, Star } from "lucide-react";
import { confirmEmailClassification, getEmailClassification, listEmailClassifications, setEmailProviderSignal, type EmailCategoryConfig, type EmailClassificationDetail, type EmailClassificationItem, type EmailClassificationStatus, type EmailProviderClassification } from "../../api/console";
import { EmailReadingPanel } from "./EmailReadingPanel";
import { unsubscribeStateLabel } from "./Evidence";
import { configurableCategories, errorMessage, localTime, measured, sourceLabel, statusLabel } from "./shared";

function ShellSelect({children, ...props}: SelectHTMLAttributes<HTMLSelectElement>) {
  return <span className="filter-select email-select"><span className="filter-control-shell"><select {...props}>{children}</select></span></span>;
}

const ACTION_FILTERS=[{value:"failed",text:"邮箱动作失败"},{value:"pending",text:"邮箱动作待执行"},{value:"done",text:"邮箱动作已完成"},{value:"skipped",text:"邮箱动作已跳过"},{value:"none",text:"无邮箱动作"}];
const SOURCE_FILTERS=[{value:"model",text:"模型分类"},{value:"agent",text:"Agent 分类"},{value:"user",text:"人工确认"}];

/** Page numbers to show: the first, the last and the ones around the current page; 0 stands for a gap. */
function pageWindow(current:number,last:number) {
  const wanted=new Set([1,last,current-1,current,current+1].filter(value=>value>=1&&value<=last));
  const pages=[...wanted].sort((a,b)=>a-b);
  const out:number[]=[];
  pages.forEach((value,index)=>{if(index>0&&value-pages[index-1]>1)out.push(0);out.push(value);});
  return out;
}

function actionBadge(item:EmailClassificationItem) {
  const actions=item.mailbox_actions||[];
  const failed=actions.filter(action=>action.status==="failed");
  if(failed.length)return {tone:"failed",text:"动作失败",reason:failed[0].error};
  if(actions.some(action=>action.status==="pending"||action.status==="processing"))return {tone:"pending",text:"动作待执行",reason:""};
  return null;
}

export function EmailList({configs, status, onBusy}: {configs:EmailCategoryConfig[]; status:EmailClassificationStatus; onBusy:(value:boolean)=>void}) {
  const [params,setParams]=useSearchParams();
  const rawPage=Number(params.get("page"));
  const page=Number.isInteger(rawPage) && rawPage>0 ? rawPage : 1;
  const rawSize=Number(params.get("page_size"));
  const pageSize=[20,50,100].includes(rawSize)?rawSize:50;
  const selected=params.get("selected") || "";
  const query=params.get("q") || "";
  const categoryFilter=params.get("category") || "";
  const categoryChosen=categoryFilter?categoryFilter.split(",").filter(Boolean):[];
  const actionFilter=ACTION_FILTERS.some(item=>item.value===params.get("action_status"))?params.get("action_status") as string:"";
  const sourceFilter=SOURCE_FILTERS.some(item=>item.value===params.get("source"))?params.get("source") as string:"";
  const filtered=!!(categoryFilter||actionFilter||sourceFilter);
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
  const [checked,setChecked]=useState<Set<string>>(new Set());
  const [bulkCategory,setBulkCategory]=useState("");
  const [bulkBusy,setBulkBusy]=useState(false);
  const [bulkDone,setBulkDone]=useState<number|null>(null);
  const [bulkError,setBulkError]=useState("");
  const [signalBusy,setSignalBusy]=useState("");
  const [signalError,setSignalError]=useState("");
  const lock=useRef(false);
  const rowRefs=useRef(new Map<string,HTMLButtonElement>());
  const open=!!selected && closed!==selected;
  const options=[...configurableCategories(configs).filter(item=>item.enabled),{category_key:"junk",display_name:"垃圾（Trash）"}];
  const offered=(key?:string|null)=>!!key && options.some(option=>option.category_key===key);
  const label=(key?:string|null)=>key ? (configs.find(item=>item.category_key===key)?.display_name || key) : "未分类（留在收件箱）";
  useEffect(()=>{setSearchText(query);},[query]);
  useEffect(()=>{setChecked(new Set());setBulkDone(null);setBulkError("");},[status,page,pageSize,query,categoryFilter,actionFilter,sourceFilter]);
  function applyFilter(key:string,value:string) {
    setParams(previous=>{const next=new URLSearchParams(previous);if(value)next.set(key,value);else next.delete(key);next.set("page","1");next.delete("selected");return next;},{replace:true});
    setClosed("");setExpanded(false);
  }
  function clearFilters() {
    setParams(previous=>{const next=new URLSearchParams(previous);["category","action_status","source"].forEach(key=>next.delete(key));next.set("page","1");next.delete("selected");return next;},{replace:true});
  }
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
    listEmailClassifications(status,{page,page_size:pageSize,...(query.trim()?{q:query.trim()}:{}),...(status==="unsubscribe"?{}:{...(categoryFilter?{category:categoryFilter}:{}),...(actionFilter?{action_status:actionFilter}:{}),...(sourceFilter?{source:sourceFilter}:{})})},controller.signal).then(result=>{
      if(controller.signal.aborted)return;
      const last=Math.max(1,Math.ceil(result.meta.total/pageSize));
      if(page>last){navigate(last);return;}
      setRows(result.items);setTotal(result.meta.total);setLoading(false);
    }).catch(reason=>{if(!controller.signal.aborted){setError(errorMessage(reason));setLoading(false);}});
    return ()=>controller.abort();
  },[status,page,pageSize,revision,query,categoryFilter,actionFilter,sourceFilter]);
  useEffect(()=>{
    setCategory("");setDetail(null);setDetailError("");setSaveError("");
    if(!open)return;
    const controller=new AbortController();
    getEmailClassification(selected,controller.signal).then(result=>{
      if(!controller.signal.aborted){setDetail(result);setCategory(result.item.category || "");}
    }).catch(reason=>{if(!controller.signal.aborted)setDetailError(errorMessage(reason));});
    return ()=>controller.abort();
  },[selected,open,detailRevision]);
  // A mail an older model classified can carry a retired category. Offering
  // it as the saved value made the select show nothing, the button look ready,
  // and the confirmation submit a category the service refuses. Categories
  // load asynchronously, so this resolves itself once they arrive rather than
  // clearing a valid choice that was merely early.
  const editableCategory=offered(category) ? category : "";
  async function save() {
    if(lock.current || !editableCategory || !detail || loading)return;
    lock.current=true;setSaving(true);onBusy(true);setSaveError("");
    try {
      const result=await confirmEmailClassification(selected,editableCategory,`email-feedback:${selected}:${editableCategory}:${detail.item.current_action_plan_id || "initial"}`,detail.item.current_action_plan_id);
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
  const allChecked=rows.length>0 && rows.every(item=>checked.has(item.id));
  function toggleChecked(id:string) {
    setChecked(previous=>{const next=new Set(previous);if(next.has(id))next.delete(id);else next.add(id);return next;});
    setBulkDone(null);setBulkError("");
  }
  async function toggleSignal(item:EmailClassificationItem,signal:"star"|"flag") {
    if(signalBusy)return;
    const provider=item.provider_classification;
    const current=signal==="star" ? provider?.starred===true : provider?.important_flag===true;
    setSignalBusy(item.id+":"+signal);setSignalError("");
    try {
      const result=await setEmailProviderSignal(item.id,signal,!current);
      setRows(previous=>previous.map(row=>row.id===item.id ? {...row,provider_classification:{...(row.provider_classification||{state:"available",category_key:null,important:null}),starred:result.starred,important_flag:result.important_flag} as EmailProviderClassification} : row));
    } catch(reason){setSignalError(errorMessage(reason));}
    finally{setSignalBusy("");}
  }
  async function applyBulk() {
    const ids=rows.filter(item=>checked.has(item.id));
    if(lock.current || !bulkCategory || !offered(bulkCategory) || !ids.length)return;
    lock.current=true;setBulkBusy(true);onBusy(true);setBulkError("");setBulkDone(0);
    let done=0;const finished=new Set<string>();
    try {
      for(const item of ids) {
        const result=await confirmEmailClassification(item.id,bulkCategory,`email-feedback:${item.id}:${bulkCategory}:${item.current_action_plan_id || "initial"}`,item.current_action_plan_id);
        if(!result.ok)throw new Error(result.message || "保存失败，请重试");
        finished.add(item.id);done+=1;setBulkDone(done);
      }
    } catch(reason){setBulkError(`已标注 ${done} 封，第 ${done+1} 封失败：${errorMessage(reason)}`);}
    finally{
      setChecked(previous=>new Set([...previous].filter(id=>!finished.has(id))));
      setRevision(value=>value+1);setDetailRevision(value=>value+1);
      lock.current=false;setBulkBusy(false);onBusy(false);
    }
  }
  function closeReading() { setClosed(selected);setExpanded(false);requestAnimationFrame(()=>rowRefs.current.get(selected)?.focus()); }
  return <div className={`email-workspace${open ? " has-reading" : ""}${expanded ? " reading-expanded" : ""}`}>
    <section className="console-card email-dense-list" aria-label="邮件分类列表">
    <div className="email-list-controls">
    <nav className="email-list-toolbar" aria-label="邮件分页"><label className="email-bulk-all"><input type="checkbox" aria-label="全选本页" checked={allChecked} disabled={!rows.length||bulkBusy||saving} onChange={()=>{setChecked(allChecked?new Set():new Set(rows.map(item=>item.id)));setBulkDone(null);setBulkError("");}}/> 全选</label>
      <span className="email-pager" role="group" aria-label="页码">{pageWindow(page,Math.max(1,Math.ceil(total/pageSize))).map((entry,index)=>entry===0?<span key={"gap"+index} className="email-pager-gap" aria-hidden="true">…</span>:<button key={entry} type="button" className={`email-pager-page${entry===page?" is-current":""}`} aria-label={`第 ${entry} 页`} aria-current={entry===page?"page":undefined} disabled={saving||loading||entry===page} onClick={()=>navigate(entry)}>{entry}</button>)}</span>
      <span className="email-total">· 共 {total} 封</span>
      <ShellSelect aria-label="每页邮件数" value={pageSize} disabled={saving||loading} onChange={event=>navigate(1,Number(event.target.value))}>{[20,50,100].map(size=><option key={size} value={size}>{size} 封/页</option>)}</ShellSelect>
      {loading&&<span role="status">正在加载邮件…</span>}
    </nav>
    {status!=="unsubscribe"&&<div className="email-list-filters" role="group" aria-label="邮件筛选">
      <details className="email-multi-filter">
        <summary aria-label="按分类筛选" className={categoryChosen.length?"is-active":""}>{categoryChosen.length===0?"分类":categoryChosen.length===1?label(categoryChosen[0]):`分类 · ${categoryChosen.length}`}</summary>
        <div className="email-multi-filter-menu">{options.map(option=><label key={option.category_key}><input type="checkbox" checked={categoryChosen.includes(option.category_key)} disabled={saving||loading} onChange={event=>applyFilter("category",(event.target.checked?[...categoryChosen,option.category_key]:categoryChosen.filter(key=>key!==option.category_key)).join(","))}/> {option.display_name}</label>)}</div>
      </details>
      <ShellSelect aria-label="按邮箱动作状态筛选" value={actionFilter} disabled={saving||loading} onChange={event=>applyFilter("action_status",event.target.value)}><option value="">状态</option>{ACTION_FILTERS.map(option=><option key={option.value} value={option.value}>{option.text}</option>)}</ShellSelect>
      <ShellSelect aria-label="按判定者筛选" value={sourceFilter} disabled={saving||loading} onChange={event=>applyFilter("source",event.target.value)}><option value="">判定者</option>{SOURCE_FILTERS.map(option=><option key={option.value} value={option.value}>{option.text}</option>)}</ShellSelect>
      {filtered&&<button type="button" className="compact-button" disabled={saving||loading} onClick={clearFilters}>清除筛选</button>}
    </div>}
    <form className="email-search" role="search" onSubmit={event=>{event.preventDefault();if(!composing&&!saving)applySearch(searchText);}}>
      <input type="search" aria-label="搜索邮件" placeholder="搜索发件人、主题、正文…" maxLength={500} value={searchText} disabled={saving} onChange={event=>setSearchText(event.target.value)} onCompositionStart={()=>setComposing(true)} onCompositionEnd={()=>setComposing(false)}/>
      {searchText&&<button type="button" aria-label="清空搜索" disabled={saving} onClick={()=>{setSearchText("");applySearch("");}}>×</button>}
    </form></div>
    {status==="unsubscribe"&&<p className="muted">显示已入队、处理中和已完成的退订任务；打开邮件可查看执行证据。</p>}
    {error&&<p role="alert">{error} <button onClick={()=>setRevision(value=>value+1)}>重试</button></p>}
    {!loading&&!error&&!rows.length&&<p className="page-state">{filtered?"没有符合筛选条件的邮件":query.trim()?"未找到匹配邮件":status==="pending_feedback"?"当前没有待确认邮件":status==="unsubscribe"?"当前没有退订记录":"当前没有邮件"}</p>}
    <div className="email-row-list" aria-busy={loading}>
      {rows.map(item=><div className={`email-row-wrap${status==="all"&&item.status==="pending_feedback"?" is-pending":""}${checked.has(item.id)?" is-checked":""}`} key={item.id}>
        {(() => {
          const provider = item.provider_classification;
          const starred = provider?.starred === true;
          const flagged = provider?.important_flag === true;
          const busy = signalBusy !== "" || bulkBusy || saving;
          return <span className="email-row-controls">
            <input type="checkbox" aria-label={`选择邮件 ${item.subject || "无主题"}`} checked={checked.has(item.id)} disabled={bulkBusy||saving} onChange={()=>toggleChecked(item.id)}/>
            <button type="button" className={`email-signal-toggle${starred?" on":""}`} aria-pressed={starred} aria-label={`${starred?"取消":"标记"} Star：${item.subject || "无主题"}`} title="点击切换 Star" disabled={busy} onClick={()=>void toggleSignal(item,"star")}><Star size={14} fill={starred ? "currentColor" : "none"} aria-hidden="true"/></button>
            <button type="button" className={`email-signal-toggle${flagged?" on":""}`} aria-pressed={flagged} aria-label={`${flagged?"取消":"标记"} Flag：${item.subject || "无主题"}`} title="点击切换 Flag" disabled={busy} onClick={()=>void toggleSignal(item,"flag")}><Flag size={14} fill={flagged ? "currentColor" : "none"} aria-hidden="true"/></button>
          </span>;
        })()}
        <button type="button" ref={element=>{if(element)rowRefs.current.set(item.id,element);else rowRefs.current.delete(item.id);}} aria-label={`打开邮件 ${item.subject || "无主题"}`} aria-pressed={selected===item.id} disabled={saving||loading||bulkBusy} className={`email-dense-row${status==="unsubscribe"?" unsubscribe-row":""}`} onClick={()=>{navigate(page,pageSize,item.id);setClosed("");setSaved(false);}}>
        <span className="email-row-controls-space" aria-hidden="true"/>
        <span className="email-row-sender" title={item.sender}>{item.sender || "未提供发件人"}</span>
        <span className="email-row-content"><span className="email-mobile-sender">{item.sender} · </span><strong>{item.subject || "无主题"}</strong><span className="email-row-original-text">{item.message_text || "未提供正文"}</span></span>
        {status==="unsubscribe"
          ? (() => {const state=unsubscribeStateLabel(item.unsubscribe_state);return <span className={`email-row-category email-unsubscribe-state ${state.tone}`} title={state.reason ? `${state.text}：${state.reason}` : state.text}>{state.text}{state.reason && <small>{state.reason.replace(/。$/,"")}</small>}</span>;})()
          : <span className="email-row-category" title={categoryLabel(item.category)}>{item.status==="pending_feedback"?"建议：":""}{categoryLabel(item.category)}{item.status==="pending_feedback"&&<small> · {measured(item.confidence)}</small>}</span>}
        <span className="email-row-status">{sourceLabel(item.classification_source)}{status!=="unsubscribe"&&` · ${statusLabel(item.status)}`}{status!=="unsubscribe"&&(()=>{const badge=actionBadge(item);return badge?<small className={`email-action-badge ${badge.tone}`} title={badge.reason||badge.text}> · {badge.text}{badge.reason&&`：${badge.reason}`}</small>:null;})()}</span>
        <time title={localTime(item.received_at || item.updated_at)}>{localTime(item.received_at || item.updated_at)}</time>
      </button></div>)}
    </div>
    </section>
    {open && <EmailReadingPanel key={selected} detail={detail} error={detailError} saving={saving} loading={loading} category={editableCategory} saveError={saveError} saved={saved} configs={configs} options={options} position={rows.findIndex(item=>item.id===selected)} count={rows.length} expanded={expanded}
      onCategory={value=>{setCategory(value);setSaved(false);setSaveError("");}} onSave={()=>void save()} onClose={closeReading} onRetry={()=>setDetailRevision(value=>value+1)} onSignalChanged={()=>setRevision(value=>value+1)} onExpand={()=>setExpanded(value=>!value)}
      onPrevious={()=>{const index=rows.findIndex(item=>item.id===selected);if(index>0){navigate(page,pageSize,rows[index-1].id);setSaved(false);}}}
      onNext={()=>{const index=rows.findIndex(item=>item.id===selected);if(index>=0&&index<rows.length-1){navigate(page,pageSize,rows[index+1].id);setSaved(false);}}}/>}
    {(checked.size>0||bulkBusy||bulkError||bulkDone!==null||signalError)&&<div className="email-bulk-popup" role="group" aria-label="批量标注">
      {checked.size>0&&<><span className="email-bulk-count">已选 {checked.size} 封</span>
      <ShellSelect aria-label="统一标注类别" value={bulkCategory} disabled={bulkBusy} onChange={event=>setBulkCategory(event.target.value)}><option value="" disabled>选择类别</option>{options.map(option=><option key={option.category_key} value={option.category_key}>{option.display_name}</option>)}</ShellSelect>
      <button type="button" className="primary-button" disabled={!bulkCategory||bulkBusy||saving} onClick={()=>void applyBulk()}>{bulkBusy?`正在标注 ${bulkDone ?? 0}/${checked.size}…`:"统一标注"}</button>
      <button type="button" className="secondary-button" disabled={bulkBusy} onClick={()=>{setChecked(new Set());setBulkDone(null);setBulkError("");}}>取消选择</button></>}
      {!bulkBusy&&bulkDone!==null&&!bulkError&&<span role="status" className="email-saved">已标注 {bulkDone} 封</span>}
      {bulkError&&<span role="alert" className="email-signal-error">{bulkError}</span>}
      {signalError&&<span role="alert" className="email-signal-error">{signalError}</span>}
      {checked.size===0&&!bulkBusy&&<button type="button" className="secondary-button" onClick={()=>{setBulkDone(null);setBulkError("");setSignalError("");}}>关闭</button>}
    </div>}
  </div>;
}
