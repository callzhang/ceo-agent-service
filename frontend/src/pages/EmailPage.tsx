import { Fragment, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import {
  confirmEmailClassification,
  displayValue,
  getEmailClassification,
  listEmailClassifications,
  listEmailConfigs,
  listEmailLearning,
  saveEmailConfig,
  type EmailCategoryConfig,
  type EmailClassificationItem,
  type EmailLearningEvidence,
  type EmailObservabilityEvent,
} from "../api/console";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { SnapshotBadge } from "../components/status/SnapshotBadge";

const categories = ["important", "work", "personal", "notification", "billing", "shopping", "subscription", "junk"];
const actions = ["label", "mark_read", "archive", "move", "trash", "unsubscribe", "auto_reply"];
const categoryLabels: Record<string, string> = {
  important: "重要", work: "工作", personal: "个人", notification: "通知",
  billing: "账单", shopping: "购物", subscription: "订阅", junk: "垃圾",
};
const categoryDescriptions: Record<string, string> = {
  important: "明确需要尽快关注或处理",
  work: "与日常工作有关，但不要求立即处理",
  personal: "真实个人关系或个人生活邮件",
  notification: "验证码、安全提醒、系统状态等时效通知",
  billing: "发票、账单、付款、续费",
  shopping: "订单确认、物流、退款和购物状态",
  subscription: "用户不希望继续接收的批量订阅",
  junk: "广告、营销、钓鱼或无价值邮件",
};

function localTime(value: string) {
  if (!value) return "未提供";
  const parsed = new Date(value.includes("T") ? value : `${value.replace(" ", "T")}Z`);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("zh-CN", { hour12: false });
}

function percent(value: number) { return `${(value * 100).toFixed(1)}%`; }

function observabilityLabel(event: EmailObservabilityEvent) {
  if (event.kind === "unsubscribe") return "自动退订";
  if (event.kind === "auto_reply") return "自动回复";
  return event.operation || "邮箱动作";
}

function ObservabilityDetails({ events }: { events: EmailObservabilityEvent[] }) {
  if (!events.length) return <p className="muted">暂无外部处理记录。</p>;
  return <div className="email-observability-list">
    {events.map((event, index) => <article className="email-observability-item" key={`${event.kind}-${event.action_id || event.action_identity || index}`}>
      <div className="card-head"><div><h3>{observabilityLabel(event)}</h3><p className="muted">状态：{event.status}；记录时间：{localTime(event.completed_at || event.finished_at || event.created_at || "")}</p></div></div>
      {event.kind === "unsubscribe" ? <>
        {event.result_text && <p className="email-observability-result">{event.result_text}</p>}
        <dl className="email-observability-meta">
          {event.evidence && <><dt>最终结果页证据</dt><dd>{event.evidence}</dd></>}
          {event.receipt_id && <><dt>Receipt</dt><dd>{event.receipt_id}</dd></>}
          {event.observation_digest && <><dt>观察摘要</dt><dd>{event.observation_digest}</dd></>}
        </dl>
        {!!event.steps?.length && <div><h4>退订步骤</h4><ol>{event.steps.map((step) => <li key={`${step.sequence}-${step.reference}`}>{step.operation}：{step.state}（{step.reference}）</li>)}</ol></div>}
      </> : <>
        {event.summary && <p>{event.summary}</p>}
        {event.provider_result_id && <p className="muted">Provider 结果：{event.provider_result_id}</p>}
        {event.error && <p className="page-state page-state-error">{event.error}</p>}
      </>}
    </article>)}
  </div>;
}

function EmailTabs({ tab, setTab }: { tab: string; setTab: (value: string) => void }) {
  const tabs = [["processed", "已处理"], ["pending_feedback", "待反馈"], ["config", "邮件配置"], ["learning", "学习"]];
  return <div className="settings-pill-row" role="tablist" aria-label="邮件页面分区">
    {tabs.map(([value, label]) => <button type="button" role="tab" aria-selected={tab === value} className={tab === value ? "active" : ""} key={value} onClick={() => setTab(value)}>{label}</button>)}
  </div>;
}

function LearningPanel() {
  const [learning, setLearning] = useState<EmailLearningEvidence | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    listEmailLearning().then((result) => setLearning(result.learning)).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "学习状态加载失败"));
  }, []);
  if (error) return <section className="console-card"><div className="page-state page-state-error" role="alert">{error}</div></section>;
  if (!learning) return <section className="console-card"><div className="page-state" role="status">正在加载学习状态…</div></section>;
  return <section className="console-card"><div className="card-head"><div><h2>分类器学习</h2><p className="muted">仅展示本地模型的可追溯证据；模型不会因为展示而自动获得退订资格。</p></div></div>
    <p>当前模型：<strong>{learning.active_model_id || "尚未晋升模型"}</strong>；待训练样本：{learning.pending_examples}；最近训练反馈数：{learning.last_trained_feedback_count}</p>
    <div className="responsive-table-wrap"><table className="settings-table" aria-label="邮件模型版本"><thead><tr><th>模型版本</th><th>状态</th><th>训练时间</th><th>样本数</th><th>准确率 / Macro F1</th><th>P95 延迟</th></tr></thead><tbody>{learning.models.map((model) => <tr key={model.model_id}><td>{model.model_version}</td><td>{model.status}</td><td>{localTime(model.training_finished_at || model.trained_at)}</td><td>{model.sample_count}（新增 {model.new_sample_count}）</td><td>{percent(model.accuracy)} / {percent(model.macro_f1)}</td><td>{model.prediction_latency_p95_ms.toFixed(1)} ms</td></tr>)}</tbody></table></div>
  </section>;
}

function ClassificationTable({ rows, pending, onConfirm }: { rows: EmailClassificationItem[]; pending: boolean; onConfirm: (row: EmailClassificationItem, category: string) => void }) {
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<EmailClassificationItem & { observability?: EmailObservabilityEvent[] } | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  if (!rows.length) return <div className="page-state">{pending ? "当前没有待反馈邮件" : "当前没有已处理邮件"}</div>;
  const toggleDetail = async (id: number) => {
    if (expandedId === id) { setExpandedId(null); return; }
    setExpandedId(id); setDetail(null); setDetailError(""); setDetailLoading(true);
    try {
      const result = await getEmailClassification(id);
      setDetail({ ...result.item, observability: result.observability });
    } catch (reason: unknown) {
      setDetailError(reason instanceof Error ? reason.message : "处理详情加载失败");
    } finally { setDetailLoading(false); }
  };
  return <div className="responsive-table-wrap">
    <table className="settings-table" aria-label={pending ? "待反馈邮件" : "已处理邮件"}>
      <thead><tr><th>邮件</th><th>模型分类</th><th>置信度 / Margin</th><th>来源</th><th>时间</th>{pending && <th>确认分类</th>}</tr></thead>
      <tbody>{rows.map((row) => <Fragment key={row.id}>
        <tr key={row.id}>
        <td><strong>{row.subject || "无主题"}</strong><br /><span className="muted">{row.sender || "未提供发件人"}</span><br /><span className="muted">{row.preview || "未提供摘要"}</span>{!pending && <><br /><button type="button" className="compact-button" aria-expanded={expandedId === row.id} onClick={() => void toggleDetail(row.id)}>{expandedId === row.id ? "收起处理详情" : "查看处理详情"}</button></>}</td>
        <td>{categoryLabels[row.category] || row.category}</td>
        <td>{percent(row.confidence)}<br /><span className="muted">{percent(row.margin)}</span></td>
        <td>{row.classification_source === "user" ? "人工确认" : "模型"}</td>
        <td>{localTime(row.received_at || row.updated_at)}</td>
        {pending && <td><div className="console-page-actions email-feedback-category-options">{categories.map((category) => <button type="button" className="compact-button email-feedback-category-option" key={category} onClick={() => onConfirm(row, category)}><strong>{categoryLabels[category]}</strong><small>{categoryDescriptions[category]}</small></button>)}</div></td>}
      </tr>
        {!pending && expandedId === row.id && <tr key={`${row.id}-detail`}><td colSpan={5}><section aria-label="邮件处理详情"><h2>邮件处理详情</h2>{detailLoading && <div className="page-state" role="status">正在加载处理详情…</div>}{detailError && <div className="page-state page-state-error" role="alert">{detailError}</div>}{detail && <ObservabilityDetails events={detail.observability || []} />}</section></td></tr>}
      </Fragment>)}</tbody>
    </table>
  </div>;
}

function ConfigPanel() {
  const [configs, setConfigs] = useState<EmailCategoryConfig[]>([]);
  const [selected, setSelected] = useState("important");
  const [description, setDescription] = useState("");
  const [threshold, setThreshold] = useState("0.90");
  const [selectedActions, setSelectedActions] = useState<string[]>(["label"]);
  const [enabled, setEnabled] = useState(true);
  const [version, setVersion] = useState("email-v1");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const current = useMemo(() => configs.find((config) => config.category === selected), [configs, selected]);
  useEffect(() => {
    listEmailConfigs().then((result) => setConfigs(result.items)).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "配置加载失败"));
  }, []);
  useEffect(() => {
    setDescription(current?.description || "");
    setThreshold(String(current?.threshold ?? 0.90));
    setSelectedActions(current?.actions || ["label"]);
    setEnabled(current?.enabled ?? true);
    setVersion(current?.config_version || "email-v1");
  }, [current]);

  const save = async () => {
    setError(""); setMessage("");
    try {
      const result = await saveEmailConfig(selected, { description, threshold: Number(threshold), actions: selectedActions, enabled, config_version: version });
      setConfigs((previous) => [...previous.filter((config) => config.category !== selected), result.item].sort((a, b) => a.category.localeCompare(b.category)));
      setMessage("配置已保存：确定性动作由 Email worker 执行；自动回复经过 Audit Agent；退订由 Consumer-direct 退订 Agent 处理。");
    } catch (reason: unknown) { setError(reason instanceof Error ? reason.message : "配置保存失败"); }
  };

  return <section className="console-card"><div className="card-head"><div><h2>邮件类型配置</h2><p className="muted">类别、描述、置信度阈值和固定动作。这里不直接执行邮箱动作。</p></div></div>
    <div className="settings-control-group"><span className="settings-control-label">邮件类型</span><div className="settings-pill-row">{categories.map((category) => <button type="button" className={selected === category ? "active" : ""} key={category} onClick={() => setSelected(category)}>{categoryLabels[category]}</button>)}</div></div>
    <p className="email-category-definition"><strong>{categoryLabels[selected]}</strong>：{categoryDescriptions[selected]}</p>
    <label className="settings-field">描述<input value={description} onChange={(event) => setDescription(event.target.value)} placeholder="这个类别用于什么邮件" /></label>
    <label className="settings-field">自动处理阈值<input type="number" min="0" max="1" step="0.01" value={threshold} onChange={(event) => setThreshold(event.target.value)} /></label>
    <label className="settings-field">配置版本<input value={version} onChange={(event) => setVersion(event.target.value)} /></label>
    <label className="settings-field"><span>启用 <input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /></span></label>
    <div className="settings-control-group"><span className="settings-control-label">固定动作</span><div className="settings-pill-row">{actions.map((action) => <button type="button" className={selectedActions.includes(action) ? "active" : ""} key={action} onClick={() => setSelectedActions((previous) => previous.includes(action) ? previous.filter((item) => item !== action) : [...previous, action])}>{action}</button>)}</div></div>
    {error && <div className="page-state page-state-error" role="alert">{error}</div>}{message && <div className="page-state" role="status">{message}</div>}<button type="button" className="primary-button" onClick={() => void save()}>保存本地配置</button>
  </section>;
}

export function EmailPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = searchParams.get("tab") || "processed";
  const status = tab === "pending_feedback" ? "pending_feedback" : "processed";
  const [rows, setRows] = useState<EmailClassificationItem[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [snapshot, setSnapshot] = useState("");
  const [feedbackMessage, setFeedbackMessage] = useState("");

  useEffect(() => {
    if (tab === "config" || tab === "learning") return;
    const controller = new AbortController(); setState("loading"); setError("");
    listEmailClassifications(status, {}, controller.signal).then((result) => { setRows(result.items); setSnapshot(result.meta.snapshot_at); setState("ready"); }).catch((reason: unknown) => { if (controller.signal.aborted) return; setError(reason instanceof Error ? reason.message : "邮件加载失败"); setState("error"); });
    return () => controller.abort();
  }, [status, tab]);

  const confirm = async (row: EmailClassificationItem, category: string) => {
    try {
      const result = await confirmEmailClassification(
        row.id,
        category,
        `email-feedback:${row.id}`,
        row.current_action_plan_id,
      );
      setFeedbackMessage(result.message);
      setRows((previous) => previous.filter((item) => item.id !== row.id));
    } catch (reason: unknown) { setError(reason instanceof Error ? reason.message : "反馈保存失败"); }
  };
  const setTab = (next: string) => setSearchParams(next === "processed" ? {} : { tab: next });
  return <ConsolePageLayout title="Email" actions={<SnapshotBadge timestamp={snapshot} refreshing={state === "loading"} />}>
    <section className="console-card"><EmailTabs tab={tab} setTab={setTab} /><p className="muted">高置信度分类进入已处理；中低置信度保留模型建议，等待人工反馈。Attention 只承接真正异常。</p></section>
    {tab === "config" ? <ConfigPanel /> : tab === "learning" ? <LearningPanel /> : <section className="console-card">{feedbackMessage && <div className="page-state" role="status">{feedbackMessage}</div>}{error && <div className="page-state page-state-error" role="alert">{error}</div>}{state === "loading" && !rows.length ? <div className="page-state" role="status">正在加载邮件…</div> : <ClassificationTable rows={rows} pending={tab === "pending_feedback"} onConfirm={(row, category) => void confirm(row, category)} />}</section>}
  </ConsolePageLayout>;
}
