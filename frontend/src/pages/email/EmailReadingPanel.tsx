import { useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Flag, Maximize2, Minimize2, Star, X } from "lucide-react";
import { setEmailProviderSignal, type EmailClassificationDetail, type EmailCategoryConfig } from "../../api/console";
import { ObservabilityDetails, ProcessedClassificationEvidence, unsubscribeStateLabel } from "./Evidence";
import { errorMessage, localTime, measured, sourceLabel, statusLabel } from "./shared";

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
  onSignalChanged: () => void;
}

export function EmailReadingPanel(props: Props) {
  const {detail, saving, loading, configs} = props;
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
  // A click is verified against the mailbox before it shows; until then the
  // page keeps what the last mailbox scan observed.
  const [toggled, setToggled] = useState<{starred: boolean; important_flag: boolean} | null>(null);
  const [togglingSignal, setTogglingSignal] = useState<"star" | "flag" | null>(null);
  const [signalError, setSignalError] = useState("");
  const starred = toggled ? toggled.starred : typeof provider?.starred === "boolean" ? provider.starred : null;
  const importantFlag = toggled ? toggled.important_flag : typeof provider?.important_flag === "boolean" ? provider.important_flag : null;
  const signalsAvailable = starred !== null || importantFlag !== null;
  const starLabel = starred !== null ? starred ? "已标星" : "未标星" : "未同步";
  const flagLabel = importantFlag !== null ? importantFlag ? "已标记" : "未标记" : "未同步";
  async function toggle(signal: "star" | "flag") {
    if (!item || togglingSignal) return;
    const current = signal === "star" ? starred : importantFlag;
    setTogglingSignal(signal); setSignalError("");
    try {
      const result = await setEmailProviderSignal(item.id, signal, current !== true);
      setToggled({starred: result.starred, important_flag: result.important_flag});
      props.onSignalChanged();
    } catch (reason) { setSignalError(errorMessage(reason)); }
    finally { setTogglingSignal(null); }
  }
  const rawSignals = Array.isArray(provider?.important_signals) ? provider.important_signals.join("、") || "无" : "未同步";
  const editable = item?.status === "pending_feedback" || item?.status === "processed";
  const events = detail?.observability || [];
  const newestUnsubscribe = [...events].reverse().find(event => event.kind === "unsubscribe");
  const unsubscribe = newestUnsubscribe ? unsubscribeStateLabel({status: newestUnsubscribe.status, outcome: newestUnsubscribe.outcome ?? null}) : null;
  const ranked = Object.entries(item?.probabilities || {}).sort(([, a], [, b]) => b - a);
  const shown = ranked.slice(0, 5);
  const restTotal = ranked.slice(5).reduce((sum, [, value]) => sum + value, 0);
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
        <p className="email-recipient-details">收件人：{item.recipients?.join("、") || "未提供"}{item.cc && ` · 抄送：${item.cc}`}</p>
        <div className="email-chips" aria-label="邮件状态">
          <span className="email-chip">{label(item.category)}</span>
          <span className="email-chip quiet">{sourceLabel(item.classification_source)} · {measured(item.confidence)}</span>
          <span className={`email-chip ${item.status === "pending_feedback" ? "pending" : "quiet"}`}>{statusLabel(item.status)}</span>
          {unsubscribe && <span className={`email-chip ${unsubscribe.tone}`}>{unsubscribe.text}</span>}
        </div>
        <div className="email-reading-actions">
          {editable && <form aria-label="分类确认" onSubmit={event => {event.preventDefault(); props.onSave();}}>
            <label className="filter-select email-select email-select-field"><span className="sr-only">选择分类</span><span className="filter-control-shell"><select aria-label="选择分类" value={props.category || ""} disabled={saving || loading} onChange={event => props.onCategory(event.target.value)}><option value="" disabled>选择类别</option>{props.options.map(option => <option key={option.category_key} value={option.category_key}>{option.display_name}</option>)}</select></span></label>
            <button type="submit" className="primary-button" disabled={!props.category || saving || loading}>{saving ? "正在保存…" : "保存修改"}</button>
          </form>}
          <span className="email-important-state" title={signalsAvailable ? `原始邮箱信号：${rawSignals}` : "Star / Flag 状态未同步"}>
            <button type="button" className={`email-signal-toggle${starred === true ? " on" : ""}`} aria-pressed={starred === true} aria-label={starred === true ? "取消 Star" : "标记 Star"} title="点击切换邮箱里的 Star" disabled={togglingSignal !== null} onClick={() => void toggle("star")}><Star size={15} fill={starred === true ? "currentColor" : "none"}/></button><span>Star：{starLabel}</span>
            <button type="button" className={`email-signal-toggle${importantFlag === true ? " on" : ""}`} aria-pressed={importantFlag === true} aria-label={importantFlag === true ? "取消 Flag" : "标记 Flag"} title="点击切换邮箱里的重要标记" disabled={togglingSignal !== null} onClick={() => void toggle("flag")}><Flag size={15} fill={importantFlag === true ? "currentColor" : "none"}/></button><span>Flag：{flagLabel}</span>
          </span>
          {signalError && <span role="alert" className="email-signal-error">{signalError}</span>}
        </div>
        {props.saveError && <p role="alert">{props.saveError}</p>}{props.saved && <p role="status" className="email-saved">分类已保存</p>}
      </header>
      <section aria-label="处理记录" className="email-reading-body email-reading-activity">
        <h3>处理记录 · {events.length}</h3>
        <ObservabilityDetails key={item.id} events={events} classificationId={item.id} entry={detail?.unsubscribe_entry}/>
        {provider && <p aria-label="邮箱观察事实" role="region" className="email-provider-state">邮箱文件夹：{String(provider.provider_folder_name || provider.category_key || "未知")} · 原始信号：{rawSignals}</p>}
      </section>
      <section aria-label="原文" className="email-reading-body">
        <h3>原文</h3>
        <section aria-label="邮件正文"><div className="email-body-text">{item.message_text || "这封邮件没有已保存的正文，请查看原邮件后分类。"}</div></section>
        {item.quoted_text && <section className="email-quoted" aria-label="引用邮件"><h3>引用邮件</h3><div className="email-body-text">{item.quoted_text}</div></section>}
        {!!item.attachment_metadata?.length && <section className="email-attachments" aria-label="附件元数据"><h3>附件</h3>{item.attachment_metadata.map((file,index) => <div key={index}>{file.filename}<small>{file.mime_type} · {file.size_bytes} bytes</small></div>)}</section>}
      </section>
        <section className="email-classification-details" aria-label="分类依据"><h3>分类依据 · {sourceLabel(item.classification_source)} · {measured(item.confidence)}</h3>
          <p>{item.status === "pending_feedback" ? "建议" : "当前分类"}：{label(item.category)}</p>
          <section aria-label="候选分布" className="email-candidate-distribution"><h3>候选分布</h3>{shown.map(([key,value]) => <div className="email-candidate-row" key={key}><span>{label(key)}</span><div className="email-probability-bar"><span style={{width: `${Math.max(0,Math.min(1,value))*100}%`}}/></div><strong>{measured(value)}</strong></div>)}{restTotal > 0 && <p className="email-candidate-rest">其余 {ranked.length - shown.length} 类合计 {measured(restTotal)}</p>}{!ranked.length && <p>未提供候选分布</p>}</section>
          <p>模型：{item.model_version || "未提供"} · 描述版本：{item.description_version || "未提供"}</p>
        </section>
      <section className="email-technical" aria-label="技术详情"><h3>技术详情</h3><ProcessedClassificationEvidence row={item}/>{provider && <pre>{JSON.stringify(provider,null,2)}</pre>}</section>
    </>}
  </section>;
}
