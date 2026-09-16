import { useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Flag, Maximize2, Minimize2, Star, X } from "lucide-react";
import type { EmailClassificationDetail, EmailCategoryConfig } from "../../api/console";
import { ObservabilityDetails, ProcessedClassificationEvidence, actionResultLabel } from "./Evidence";
import { localTime, measured, sourceLabel } from "./shared";

interface Props {
  detail: EmailClassificationDetail | null;
  error: string;
  saving: boolean;
  loading: boolean;
  category: string;
  saveError: string;
  saved: boolean;
  configs: EmailCategoryConfig[];
  options: {category_key: string; display_name: string}[];
  position: number;
  count: number;
  expanded: boolean;
  onCategory: (value: string) => void;
  onSave: () => void;
  onClose: () => void;
  onRetry: () => void;
  onPrevious: () => void;
  onNext: () => void;
  onExpand: () => void;
}

export function EmailReadingPanel(props: Props) {
  const {detail, saving, loading, configs} = props;
  const [tab, setTab] = useState("body");
  const closeRef = useRef<HTMLButtonElement>(null);
  const onClose = useRef(props.onClose); onClose.current = props.onClose;
  const locked = useRef(saving); locked.current = saving;
  useEffect(() => {
    closeRef.current?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !locked.current) { event.preventDefault(); onClose.current(); }
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, []);
  const label = (key: string | null | undefined) => !key ? "未分类（留在收件箱）" : configs.find(item => item.category_key === key)?.display_name || (key === "junk" ? "垃圾（Trash）" : key);
  const item = detail?.item;
  const provider = detail?.provider_classification || item?.provider_classification;
  const signalsAvailable = provider && ("starred" in provider || "important_flag" in provider);
  const starLabel = provider?.starred == null ? "未知" : provider.starred ? "已标星" : "未标星";
  const flagLabel = provider?.important_flag == null ? "未知" : provider.important_flag ? "已标记" : "未标记";
  const rawSignals = Array.isArray(provider?.important_signals) ? provider.important_signals.join("、") || "无" : "未知";
  const editable = item?.status === "pending_feedback" || item?.status === "processed";
  const events = detail?.observability || [];
  return <section className="email-reading" role="region" aria-label="邮件详情">
    <div className="email-reading-toolbar">
      <button ref={closeRef} type="button" onClick={props.onClose} disabled={saving} aria-label="关闭详情"><X size={16}/> 返回列表</button>
      <div className="email-reading-nav"><button aria-label="上一封邮件" disabled={saving || loading || props.position <= 0} onClick={props.onPrevious}><ChevronLeft size={16}/></button><span>{props.position < 0 ? "当前邮件" : `${props.position + 1} / ${props.count}`}</span><button aria-label="下一封邮件" disabled={saving || loading || props.position < 0 || props.position >= props.count - 1} onClick={props.onNext}><ChevronRight size={16}/></button></div>
      <button type="button" onClick={props.onExpand} aria-label={props.expanded ? "收起阅读" : "展开阅读"}>{props.expanded ? <Minimize2 size={16}/> : <Maximize2 size={16}/>}</button>
    </div>
    {props.error ? <p role="alert" className="email-reading-empty">{props.error} <button onClick={props.onRetry}>重试正文</button></p> : !item ? <p role="status" className="email-reading-empty">正在加载邮件正文…</p> : <>
      <header className="email-reading-header">
        <h2>{item.subject || "无主题"}</h2>
        <div className="email-sender-line"><span>{item.sender || "发件人未知"}</span><time>{localTime(item.received_at)}</time></div>
        <details className="email-recipient-details"><summary>收件信息</summary><p>收件人：{item.recipients?.join("、") || "未提供"}</p>{item.cc && <p>抄送：{item.cc}</p>}</details>
        <div className="email-reading-actions">
          {editable && <form aria-label="分类确认" onSubmit={event => {event.preventDefault(); props.onSave();}}>
            <label><span className="sr-only">选择分类</span><select aria-label="选择分类" value={props.category || ""} disabled={saving || loading} onChange={event => props.onCategory(event.target.value)}><option value="" disabled>选择类别</option>{props.options.map(option => <option key={option.category_key} value={option.category_key}>{option.display_name}</option>)}</select></label>
            <button type="submit" className="primary-button" disabled={!props.category || saving || loading}>{saving ? "正在保存…" : "保存修改"}</button>
          </form>}
          <span className="email-important-state" title={signalsAvailable ? `原始邮箱信号：${rawSignals}` : "Star / Flag 状态未知"}>
            <Star size={15} fill={provider?.starred === true ? "currentColor" : "none"}/><span>Star：{signalsAvailable ? starLabel : "未知"}</span>
            <Flag size={15} fill={provider?.important_flag === true ? "currentColor" : "none"}/><span>Flag：{signalsAvailable ? flagLabel : "未知"}</span>
          </span>
        </div>
        {props.saveError && <p role="alert">{props.saveError}</p>}{props.saved && <p role="status" className="email-saved">分类已保存</p>}
        <details className="email-classification-details"><summary>{sourceLabel(item.classification_source)} · {measured(item.confidence)} <span>分类依据</span></summary>
          <p>{item.status === "pending_feedback" ? "建议" : "当前分类"}：{label(item.category)}</p>
          <section aria-label="候选分布" className="email-candidate-distribution"><h3>候选分布</h3>{Object.entries(item.probabilities || {}).sort(([,a],[,b]) => b-a).map(([key,value]) => <div className="email-candidate-row" key={key}><span>{label(key)}</span><div className="email-probability-bar"><span style={{width: `${Math.max(0,Math.min(1,value))*100}%`}}/></div><strong>{measured(value)}</strong></div>)}{!Object.keys(item.probabilities || {}).length && <p>未提供候选分布</p>}</section>
          <p>模型：{item.model_version || "未提供"} · 描述版本：{item.description_version || "未提供"}</p>
        </details>
      </header>
      <div className="email-reading-tabs" role="tablist" aria-label="邮件详情分区">{[["body","原文"],["activity",`处理记录 · ${events.length}`]].map(([key,title]) => <button key={key} role="tab" aria-selected={tab === key} aria-controls={`email-reading-${key}`} id={`email-reading-tab-${key}`} tabIndex={tab === key ? 0 : -1} onClick={() => setTab(key)} onKeyDown={event => {if(event.key === "ArrowLeft" || event.key === "ArrowRight"){event.preventDefault();setTab(key === "body" ? "activity" : "body");const next = event.currentTarget.parentElement?.querySelector<HTMLButtonElement>(`#email-reading-tab-${key === "body" ? "activity" : "body"}`);next?.focus();}}}>{title}</button>)}</div>
      {tab === "body" ? <div role="tabpanel" id="email-reading-body" aria-labelledby="email-reading-tab-body" className="email-reading-body">
        <section aria-label="邮件正文"><div className="email-body-text">{item.message_text || "这封邮件没有已保存的正文，请查看原邮件后分类。"}</div></section>
        {item.quoted_text && <details className="email-quoted"><summary>引用邮件</summary><div className="email-body-text">{item.quoted_text}</div></details>}
        {!!item.attachment_metadata?.length && <section className="email-attachments" aria-label="附件元数据"><h3>附件</h3>{item.attachment_metadata.map((file,index) => <div key={index}>{file.filename}<small>{file.mime_type} · {file.size_bytes} bytes</small></div>)}</section>}
        {!!events.length && <button className="email-result-strip" onClick={() => setTab("activity")}><span>{events.map(actionResultLabel).join(" · ")}</span><strong>查看处理记录 →</strong></button>}
      </div> : <div role="tabpanel" id="email-reading-activity" aria-labelledby="email-reading-tab-activity" className="email-reading-body">
        <ObservabilityDetails key={item.id} events={events} classificationId={item.id} entry={detail?.unsubscribe_entry}/>
        {provider && <section aria-label="邮箱观察事实" className="email-provider-state"><h3>邮箱当前状态</h3><p>文件夹：{String(provider.provider_folder_name || provider.category_key || "未知")}</p><p>Star：{signalsAvailable ? starLabel : "未知"} · Flag：{signalsAvailable ? flagLabel : "未知"}</p><p>原始信号：{rawSignals}</p></section>}
        <details className="email-technical"><summary>技术详情</summary><ProcessedClassificationEvidence row={item}/>{provider && <pre>{JSON.stringify(provider,null,2)}</pre>}</details>
      </div>}
    </>}
  </section>;
}
