import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { confirmEmailClassification, listEmailClassifications, type EmailClassificationItem } from "../api/console";

const labels: Record<string, string> = { important: "重要", work: "工作", personal: "个人", notification: "通知", billing: "账单", shopping: "购物", subscription: "订阅", junk: "垃圾" };
const descriptions: Record<string, string> = { important: "明确需要尽快关注或处理", work: "与日常工作有关，但不要求立即处理", personal: "真实个人关系或个人生活邮件", notification: "验证码、安全提醒、系统状态等时效通知", billing: "发票、账单、付款、续费", shopping: "订单确认、物流、退款和购物状态", subscription: "用户不希望继续接收的批量订阅", junk: "广告、营销、钓鱼或无价值邮件" };
const percent = (n: number) => `${(n * 100).toFixed(1)}%`;
function date(value: string) {
  const parsed = new Date(value.includes(",") || value.includes("T") ? value : `${value.replace(" ", "T")}Z`);
  return Number.isNaN(parsed.getTime()) ? value || "时间未提供" : parsed.toLocaleString("zh-CN", { hour12: false });
}

export function PendingEmailFeedback() {
  const [params, setParams] = useSearchParams();
  const page = Math.max(1, Number(params.get("page")) || 1);
  const [rows, setRows] = useState<EmailClassificationItem[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [category, setCategory] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);
  const [error, setError] = useState("");
  const [saveError, setSaveError] = useState("");
  const [message, setMessage] = useState("");
  const [revision, setRevision] = useState(0);
  const [reading, setReading] = useState(false);
  const row = rows.find(item => item.id === selectedId) || rows[0];
  const changePage = (next: number) => { setParams({ tab: "pending_feedback", page: String(next) }); setReading(false); setCategory(""); };
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError("");
    listEmailClassifications("pending_feedback", { page, page_size: 20 }, controller.signal).then(result => {
      if (controller.signal.aborted) return;
      const lastPage = Math.max(1, Math.ceil(result.meta.total / 20));
      if (page > lastPage) { setParams({ tab: "pending_feedback", page: String(lastPage) }); return; }
      setRows(result.items); setTotal(result.meta.total);
      setSelectedId(previous => result.items.some(item => item.id === previous) ? previous : result.items[0]?.id ?? null);
      setLoading(false);
    }).catch(reason => { if (!controller.signal.aborted) { setError(reason instanceof Error ? reason.message : "邮件加载失败"); setLoading(false); } });
    return () => controller.abort();
  }, [page, revision, setParams]);
  const select = (item: EmailClassificationItem) => { setSelectedId(item.id); setCategory(""); setSaveError(""); setReading(true); };
  const save = async () => {
    if (!row || !category || savingRef.current) return;
    savingRef.current = true; setSaving(true); setSaveError("");
    try {
      const result = await confirmEmailClassification(row.id, category, `email-feedback:${row.id}`, row.current_action_plan_id);
      const index = rows.findIndex(item => item.id === row.id);
      setSelectedId(rows[index + 1]?.id ?? rows[index - 1]?.id ?? null);
      setRows(previous => previous.filter(item => item.id !== row.id));
      setTotal(previous => previous === null ? null : Math.max(0, previous - 1));
      setMessage(result.message); setCategory(""); setRevision(previous => previous + 1);
    } catch (reason) { setSaveError(reason instanceof Error ? reason.message : "反馈保存失败，请重试"); }
    finally { savingRef.current = false; setSaving(false); }
  };
  return <section className={`email-review ${reading ? "is-reading" : ""}`} aria-label="待反馈工作区">
    <header className="email-review-head"><div><strong>待反馈</strong> <span className="status-badge">{total === null ? "…" : total}</span></div><span className="muted">阅读邮件，选择分类，保存后继续下一封</span></header>
    {message && <div className="email-review-notice" role="status">{message}</div>}
    {error && <div className="page-state page-state-error" role="alert">{error} <button className="compact-button" onClick={() => setRevision(value => value + 1)}>重新加载</button></div>}
    {loading && !rows.length ? <div className="page-state" role="status">正在加载邮件…</div> : !rows.length && !error ? <div className="page-state">当前没有待反馈邮件</div> : <div className="email-review-body" aria-busy={loading}>
      <aside className="email-review-queue" aria-label="邮件队列">
        <div className="email-review-queue-items">{rows.map(item => <button key={item.id} className={`email-review-item ${row?.id === item.id ? "active" : ""}`} aria-pressed={row?.id === item.id} disabled={saving || loading} onClick={() => select(item)}>
          <strong>{item.subject || "无主题"}</strong><span className="muted">{item.sender || "未提供发件人"}</span><time className="muted">{date(item.received_at || item.updated_at)}</time><span className="status-badge">建议：{labels[item.category] || item.category} · {percent(item.confidence)}</span>
        </button>)}</div>
        <nav className="email-review-pager" aria-label="邮件分页"><span className="muted">第 {page} / {Math.max(1, Math.ceil((total || 0) / 20))} 页 · 共 {total ?? "…"} 封</span><div><button className="compact-button" disabled={page === 1 || loading || saving} onClick={() => changePage(page - 1)}>上一页</button><button className="compact-button" disabled={page * 20 >= (total || 0) || loading || saving} onClick={() => changePage(page + 1)}>下一页</button></div></nav>
      </aside>
      {row && <article className="email-review-reader" aria-label="当前邮件">
        <div className="email-review-content"><button className="compact-button email-review-back" disabled={saving} onClick={() => setReading(false)}>← 返回队列</button><h2>{row.subject || "无主题"}</h2><p className="muted">{row.sender || "未提供发件人"}<br />{date(row.received_at || row.updated_at)}</p>
          <p className="email-review-preview">{row.preview || "这封邮件没有可用的文本预览，请查看原邮件后分类。"}</p><p className="muted">邮件文本预览</p>
          {!!row.attachment_metadata?.length && <div className="email-review-attachments" aria-label="附件元数据">{row.attachment_metadata.map((file, index) => <span className="status-badge" key={index}>{file.filename || "未命名附件"} · {Math.round(file.size_bytes / 1024)} KB</span>)}</div>}
          <details key={row.id} className="email-review-evidence"><summary>查看分类依据</summary><section aria-label={`${row.subject || "无主题"} 分类证据`}><p>前两名概率差：{percent(row.margin)}</p><ol aria-label="模型候选">{Object.entries(row.probabilities).sort((a,b) => b[1]-a[1]).map(([key,value]) => <li key={key}>{labels[key] || key} {percent(value)}</li>)}</ol><p>完整模型 ID：{row.model_version || "未提供"}</p><p>配置版本：{row.config_version || "未提供"}</p></section></details>
        </div>
        <form className="email-review-decision" onSubmit={event => { event.preventDefault(); void save(); }}><strong>这封邮件属于哪一类？</strong><p className="muted">模型建议：{labels[row.category] || row.category} · {percent(row.confidence)}，仅供参考</p><div className="email-review-categories" role="group" aria-label="选择分类">{Object.entries(labels).map(([key,label]) => <button type="button" className="filter-chip" key={key} aria-pressed={category === key} disabled={saving || loading} onClick={() => { setCategory(key); setSaveError(""); }}>{label}</button>)}</div><p className="email-review-help muted">{category ? descriptions[category] : "请选择类别；选择后点击保存。"}</p>{saveError && <p role="alert" className="page-state-error">{saveError}</p>}<button type="submit" className="primary-button" disabled={!category || saving || loading}>{saving ? "正在保存…" : category ? `保存为「${labels[category]}」并继续 →` : "保存分类并继续 →"}</button></form>
      </article>}
    </div>}
  </section>;
}
