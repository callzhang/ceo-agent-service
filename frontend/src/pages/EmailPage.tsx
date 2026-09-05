import { Fragment, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
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
  type EmailModelEvidence,
  type EmailObservabilityEvent,
} from "../api/console";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { SnapshotBadge } from "../components/status/SnapshotBadge";

const categories = ["important", "work", "personal", "notification", "billing", "shopping", "subscription", "junk"];
const actions = ["label", "mark_read", "archive", "move", "trash", "unsubscribe"];
const terminalActions = new Set(["archive", "move", "trash"]);
const emailTabs = [["processed", "已处理"], ["pending_feedback", "待反馈"], ["config", "邮件配置"], ["learning", "学习"]] as const;
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

function byteSize(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${Number((value / 1024).toFixed(1))} KB`;
  return `${Number((value / (1024 * 1024)).toFixed(1))} MB`;
}

function namedCounts(values: Record<string, number>) {
  const entries = Object.entries(values);
  if (!entries.length) return "无记录";
  return entries.map(([name, count]) => `${categoryLabels[name] ? `${categoryLabels[name]}（${name}）` : name}：${count}`).join("；");
}

function actionPlanEvidence(row: EmailClassificationItem) {
  const plan = row.action_plan;
  const planId = typeof plan.action_plan_id === "string" ? plan.action_plan_id : row.current_action_plan_id;
  const planVersion = typeof plan.action_plan_version === "number" ? plan.action_plan_version : null;
  const planActions = Array.isArray(plan.actions)
    ? plan.actions.filter((action): action is string => typeof action === "string")
    : [];
  return { planId, planVersion, planActions };
}

function PendingClassificationEvidence({ row }: { row: EmailClassificationItem }) {
  const alternatives = Object.entries(row.probabilities)
    .filter((entry): entry is [string, number] => typeof entry[1] === "number")
    .sort((left, right) => right[1] - left[1]);
  return <section aria-label={`${row.subject || "无主题"} 分类证据`}>
    <h3>分类证据</h3>
    <dl className="detail-definition-list">
      <div><dt>文本预览</dt><dd>{row.preview || "未提供"}</dd></div>
      <div><dt>置信度</dt><dd>{percent(row.confidence)}</dd></div>
      <div><dt>Margin</dt><dd>{percent(row.margin)}</dd></div>
      <div><dt>完整模型 ID</dt><dd>{row.model_version || "未提供"}</dd></div>
      <div><dt>配置版本</dt><dd>{row.config_version || "未提供"}</dd></div>
      <div><dt>模型候选</dt><dd>{alternatives.length ? <ol aria-label="模型候选">{alternatives.map(([category, probability]) => <li key={category}>{categoryLabels[category] || category} {percent(probability)}</li>)}</ol> : "无记录"}</dd></div>
      <div><dt>附件元数据</dt><dd>{row.attachment_metadata.length ? <ul aria-label="附件元数据">{row.attachment_metadata.map((attachment, index) => <li key={`${attachment.filename}-${index}`}>{attachment.filename || "未命名附件"} · {attachment.mime_type || "未知 MIME"} · {byteSize(attachment.size_bytes)} · {attachment.inline ? "内嵌附件" : "非内嵌附件"}</li>)}</ul> : "无附件"}</dd></div>
    </dl>
  </section>;
}

function ProcessedClassificationEvidence({ row }: { row: EmailClassificationItem }) {
  const plan = actionPlanEvidence(row);
  return <section aria-label="分类与 ActionPlan 证据">
    <h3>分类与 ActionPlan</h3>
    <dl className="detail-definition-list">
      <div><dt>完整模型 ID</dt><dd>{row.model_version || "未提供"}</dd></div>
      <div><dt>配置版本</dt><dd>{row.config_version || "未提供"}</dd></div>
      <div><dt>ActionPlan ID</dt><dd>{plan.planId || "未提供"}</dd></div>
      <div><dt>ActionPlan 版本</dt><dd>{plan.planVersion === null ? "未提供" : `版本 ${plan.planVersion}`}</dd></div>
      <div><dt>授权动作</dt><dd>{plan.planActions.length ? plan.planActions.join("、") : "无固定动作"}</dd></div>
    </dl>
  </section>;
}

function metricValue(name: string, value: unknown) {
  if (typeof value !== "number") return displayValue(value);
  const countMetrics = new Set([
    "support",
    "validation_sample_count",
    "validation_positive_support",
    "automatic_candidate_count",
    "minimum_validation_samples",
  ]);
  return countMetrics.has(name) ? String(value) : percent(value);
}

function ModelEvidenceCard({ model }: { model: EmailModelEvidence }) {
  const categoryMetrics = Object.entries(model.per_category_metrics);
  const lineage = [
    ["父模型 ID", model.parent_model_id],
    ["模型家族", model.model_family],
    ["分词器版本", model.tokenizer_version],
    ["特征版本", model.feature_version],
    ["训练数据版本", model.training_dataset_version],
  ].filter((entry): entry is [string, string] => typeof entry[1] === "string" && entry[1] !== "");
  return <article className="email-observability-item" aria-label={`模型 ${model.model_id}`}>
    <div className="card-head"><div><h3>{model.model_id}</h3><p className="muted">状态：{model.status}；完整性：{model.integrity_status}</p></div></div>
    <dl className="detail-definition-list">
      <div><dt>模型版本</dt><dd>{model.model_version}</dd></div>
      <div><dt>训练时间</dt><dd>{localTime(model.training_started_at)} → {localTime(model.training_finished_at || model.trained_at)}</dd></div>
      <div><dt>训练样本</dt><dd>{model.sample_count}（新增 {model.new_sample_count}）</dd></div>
      <div><dt>类别/来源覆盖</dt><dd>{namedCounts(model.category_counts)}</dd></div>
      <div><dt>邮箱账户覆盖</dt><dd>{namedCounts(model.account_counts)}</dd></div>
      <div><dt>验证方法</dt><dd>{model.validation_method || "未提供"}</dd></div>
      <div><dt>整体指标</dt><dd>准确率 {percent(model.accuracy)} / Macro F1 {percent(model.macro_f1)}</dd></div>
      <div><dt>分类指标</dt><dd>{categoryMetrics.length ? <ul>{categoryMetrics.map(([category, metrics]) => <li key={category}><strong>{categoryLabels[category] ? `${categoryLabels[category]}（${category}）` : category}</strong>：{Object.entries(metrics).map(([name, value]) => `${name}：${metricValue(name, value)}`).join("；")}</li>)}</ul> : "无记录"}</dd></div>
      <div><dt>预测延迟</dt><dd>P50 {model.prediction_latency_p50_ms.toFixed(1)} ms / P95 {model.prediction_latency_p95_ms.toFixed(1)} ms</dd></div>
      <div><dt>Artifact SHA-256</dt><dd>{model.artifact_sha256 || "未提供"}</dd></div>
      {lineage.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
      {model.candidate_reason && <div><dt>候选创建原因</dt><dd>{model.candidate_reason}</dd></div>}
      {model.promotion_reason && <div><dt>激活原因</dt><dd>{model.promotion_reason}</dd></div>}
      {model.rejection_reason && <div><dt>拒绝原因</dt><dd>{model.rejection_reason}</dd></div>}
      {model.failure_reason && <div><dt>失败原因</dt><dd>{model.failure_reason}</dd></div>}
      {model.superseded_reason && <div><dt>被替换原因</dt><dd>{model.superseded_reason}</dd></div>}
      {model.integrity_error && <div><dt>完整性错误</dt><dd>{model.integrity_error}</dd></div>}
      {!!model.lifecycle.length && <div><dt>生命周期事件</dt><dd><ol>{model.lifecycle.map((event) => <li key={event.event_id}>{event.status}：{event.reason}（{localTime(event.occurred_at)}）</li>)}</ol></dd></div>}
    </dl>
  </article>;
}

function observabilityLabel(event: EmailObservabilityEvent) {
  if (event.kind === "unsubscribe") return "自动退订";
  if (event.kind === "auto_reply") return "自动回复";
  return event.operation || "邮箱动作";
}

function ObservabilityDetails({ events }: { events: EmailObservabilityEvent[] }) {
  if (!events.length) return <p className="muted">暂无外部处理记录。</p>;
  return <div className="email-observability-list">
    {events.map((event, index) => {
      const recordedAt = event.completed_at || event.finished_at || event.created_at || "";
      return <article className="email-observability-item" key={`${event.kind}-${event.action_id || event.action_identity || index}`}>
      <div className="card-head"><div><h3>{observabilityLabel(event)}</h3><p className="muted">状态：{event.status}{recordedAt && <>；记录时间：{localTime(recordedAt)}</>}</p></div></div>
      {event.kind === "unsubscribe" ? <>
        {event.lifecycle_version === "email_unsubscribe_audited_v2" && <p><strong>Consumer → Audit</strong></p>}
        {event.result_text && <p className="email-observability-result">{event.result_text}</p>}
        <dl className="email-observability-meta">
          {event.lifecycle_version && <><dt>生命周期</dt><dd>{event.lifecycle_version}</dd></>}
          {event.task_id && <><dt>Task</dt><dd>Task {event.task_id} · {event.task_status || "未提供"}</dd></>}
          {!!event.consumer_run_ids?.length && <><dt>Consumer run</dt><dd>Consumer run：{event.consumer_run_ids.join("、")}</dd></>}
          {!!event.audit_run_ids?.length && <><dt>Audit run</dt><dd>Audit run：{event.audit_run_ids.join("、")}</dd></>}
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
    </article>})}
  </div>;
}

function EmailTabs({ tab, setTab }: { tab: string; setTab: (value: string) => void }) {
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % emailTabs.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + emailTabs.length) % emailTabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = emailTabs.length - 1;
    else return;
    event.preventDefault();
    setTab(emailTabs[next][0]);
    refs.current[next]?.focus();
  };
  return <div className="settings-pill-row" role="tablist" aria-label="邮件页面分区">
    {emailTabs.map(([value, label], index) => <button type="button" role="tab" id={`email-tab-${value}`} aria-controls={`email-panel-${value}`} aria-selected={tab === value} tabIndex={tab === value ? 0 : -1} className={tab === value ? "active" : ""} key={value} ref={(element) => { refs.current[index] = element; }} onKeyDown={(event) => onKeyDown(event, index)} onClick={() => setTab(value)}>{label}</button>)}
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
    {!!learning.registry_issues.length && <div className="page-state page-state-error" role="alert">模型 Registry 完整性异常：{learning.registry_issues.map((issue) => `${issue.model_id}（${issue.integrity_error}）`).join("；")}</div>}
    <div className="email-observability-list" aria-label="邮件模型版本">{learning.models.map((model) => <ModelEvidenceCard key={model.model_id} model={model} />)}</div>
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
        {pending && <tr key={`${row.id}-evidence`}><td colSpan={6}><PendingClassificationEvidence row={row} /></td></tr>}
        {!pending && expandedId === row.id && <tr key={`${row.id}-detail`}><td colSpan={5}><section aria-label="邮件处理详情"><h2>邮件处理详情</h2>{detailLoading && <div className="page-state" role="status">正在加载处理详情…</div>}{detailError && <div className="page-state page-state-error" role="alert">{detailError}</div>}{detail && <><ProcessedClassificationEvidence row={detail} /><ObservabilityDetails events={detail.observability || []} /></>}</section></td></tr>}
      </Fragment>)}</tbody>
    </table>
  </div>;
}

function ConfigPanel() {
  const [configs, setConfigs] = useState<EmailCategoryConfig[]>([]);
  const [selected, setSelected] = useState("important");
  const [description, setDescription] = useState("");
  const [threshold, setThreshold] = useState("0.90");
  const [selectedActions, setSelectedActions] = useState<string[]>([]);
  const [labelNames, setLabelNames] = useState("");
  const [moveTargetFolder, setMoveTargetFolder] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [version, setVersion] = useState("email-v1");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">("loading");
  const [saving, setSaving] = useState(false);

  const current = useMemo(() => configs.find((config) => config.category === selected), [configs, selected]);
  useEffect(() => {
    listEmailConfigs().then((result) => { setConfigs(result.items); setLoadState("ready"); }).catch((reason: unknown) => { setError(reason instanceof Error ? reason.message : "配置加载失败"); setLoadState("error"); });
  }, []);
  useEffect(() => {
    setDescription(current?.description || "");
    setThreshold(String(current?.threshold ?? 0.90));
    setSelectedActions((current?.actions || []).filter((action) => selected === "subscription" || action !== "unsubscribe"));
    const parameters = current?.action_parameters || {};
    const labels = parameters.label?.labels;
    setLabelNames(Array.isArray(labels) ? labels.filter((label): label is string => typeof label === "string").join(", ") : "");
    setMoveTargetFolder(typeof parameters.move?.target_folder === "string" ? parameters.move.target_folder : "");
    setEnabled(current?.enabled ?? true);
    setVersion(current?.config_version || "email-v1");
  }, [current]);

  useEffect(() => { setMessage(""); setError(""); }, [selected]);

  const toggleAction = (action: string) => {
    if (action === "unsubscribe" && selected !== "subscription") return;
    const removing = selectedActions.includes(action);
    const nextActions = removing
      ? selectedActions.filter((item) => item !== action)
      : [
          ...(terminalActions.has(action)
            ? selectedActions.filter((item) => !terminalActions.has(item))
            : selectedActions),
          action,
        ];
    setSelectedActions(nextActions);
    if (removing && action === "label") setLabelNames("");
    if (selectedActions.includes("move") && !nextActions.includes("move")) {
      setMoveTargetFolder("");
    }
  };

  const save = async () => {
    if (loadState !== "ready" || saving) return;
    setError(""); setMessage("");
    const parsedThreshold = Number(threshold);
    if (threshold.trim() === "" || !Number.isFinite(parsedThreshold) || parsedThreshold < 0 || parsedThreshold > 1) {
      setError("阈值必须是 0 到 1 之间的数字"); return;
    }
    const configVersion = version.trim();
    if (!configVersion) { setError("请填写配置版本"); return; }
    const actionParameters: Record<string, Record<string, unknown>> = {};
    if (selectedActions.includes("label")) {
      const labels = labelNames.split(",").map((label) => label.trim()).filter(Boolean);
      if (!labels.length) { setError("请至少填写一个标签"); return; }
      actionParameters.label = { labels };
    }
    if (selectedActions.includes("move")) {
      const targetFolder = moveTargetFolder.trim();
      if (!targetFolder) { setError("请填写目标文件夹"); return; }
      actionParameters.move = { target_folder: targetFolder };
    }
    const category = selected;
    setSaving(true);
    try {
      const result = await saveEmailConfig(category, { description, threshold: parsedThreshold, actions: selectedActions, action_parameters: actionParameters, enabled, config_version: configVersion });
      setConfigs((previous) => [...previous.filter((config) => config.category !== category), result.item].sort((a, b) => a.category.localeCompare(b.category)));
      setMessage("配置已保存：确定性动作由 Email worker 执行并回读；退订由 Consumer 提案、Audit 审核执行。邮件回复已全局禁用。");
    } catch (reason: unknown) { setError(reason instanceof Error ? reason.message : "配置保存失败"); }
    finally { setSaving(false); }
  };

  const controlsDisabled = loadState !== "ready" || saving;

  return <section className="console-card"><div className="card-head"><div><h2>邮件类型配置</h2><p className="muted">类别、描述、置信度阈值和固定动作。这里不直接执行邮箱动作。</p><p className="muted">label、mark_read、archive、move、trash 由 Email worker 直接执行并回读；unsubscribe 由 Consumer 提案、Audit 审核执行；邮件回复保持全局禁用。</p></div></div>
    {loadState === "loading" && <div className="page-state" role="status">正在加载邮件配置…</div>}
    <div className="settings-control-group"><span className="settings-control-label">邮件类型</span><div className="settings-pill-row">{categories.map((category) => <button type="button" aria-pressed={selected === category} disabled={controlsDisabled} className={selected === category ? "active" : ""} key={category} onClick={() => setSelected(category)}>{categoryLabels[category]}</button>)}</div></div>
    <p className="email-category-definition"><strong>{categoryLabels[selected]}</strong>：{categoryDescriptions[selected]}</p>
    <div className="email-config-field-grid">
      <label className="email-config-field email-config-field-description"><span>描述</span><input disabled={controlsDisabled} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="这个类别用于什么邮件" /></label>
      <label className="email-config-field"><span>自动处理阈值</span><input disabled={controlsDisabled} type="number" min="0" max="1" step="0.01" value={threshold} onChange={(event) => setThreshold(event.target.value)} /></label>
      <label className="email-config-field"><span>配置版本</span><input disabled={controlsDisabled} value={version} onChange={(event) => setVersion(event.target.value)} /></label>
      <label className="email-config-toggle"><input disabled={controlsDisabled} type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /><span>启用此类别</span></label>
    </div>
    <div className="settings-control-group"><span className="settings-control-label">固定动作</span><div className="settings-pill-row">{actions.map((action) => <button type="button" aria-pressed={selectedActions.includes(action)} disabled={controlsDisabled || (action === "unsubscribe" && selected !== "subscription")} className={selectedActions.includes(action) ? "active" : ""} key={action} onClick={() => toggleAction(action)}>{action}</button>)}</div>{selected !== "subscription" && <p className="field-help">退订只适用于订阅邮件。</p>}</div>
    {(selectedActions.includes("label") || selectedActions.includes("move")) && <div className="email-config-action-fields">
      {selectedActions.includes("label") && <label className="email-config-field"><span>标签</span><input disabled={controlsDisabled} aria-label="标签" value={labelNames} onChange={(event) => setLabelNames(event.target.value)} placeholder="多个标签用英文逗号分隔" /></label>}
      {selectedActions.includes("move") && <label className="email-config-field"><span>目标文件夹</span><input disabled={controlsDisabled} aria-label="目标文件夹" value={moveTargetFolder} onChange={(event) => setMoveTargetFolder(event.target.value)} placeholder="例如 Archive/Billing" /></label>}
    </div>}
    {error && <div className="page-state page-state-error" role="alert">{error}</div>}{message && <div className="page-state" role="status">{message}</div>}<button type="button" disabled={controlsDisabled} className="primary-button" onClick={() => void save()}>{saving ? "正在保存…" : "保存本地配置"}</button>
  </section>;
}

export function EmailPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTab = searchParams.get("tab") || "processed";
  const tab = emailTabs.some(([value]) => value === requestedTab) ? requestedTab : "processed";
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
    <div role="tabpanel" id={`email-panel-${tab}`} aria-labelledby={`email-tab-${tab}`}>{tab === "config" ? <ConfigPanel /> : tab === "learning" ? <LearningPanel /> : <section className="console-card">{feedbackMessage && <div className="page-state" role="status">{feedbackMessage}</div>}{error && <div className="page-state page-state-error" role="alert">{error}</div>}{state === "loading" && !rows.length ? <div className="page-state" role="status">正在加载邮件…</div> : <ClassificationTable rows={rows} pending={tab === "pending_feedback"} onConfirm={(row, category) => void confirm(row, category)} />}</section>}</div>
  </ConsolePageLayout>;
}
