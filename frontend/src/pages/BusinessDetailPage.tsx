import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { command, displayValue, getResource } from "../api/console";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { SnapshotBadge } from "../components/status/SnapshotBadge";
import { StatusBadge } from "../components/status/StatusBadge";
import { SummaryText } from "../components/data/SummaryText";
import "../meeting-attempt.css";

type MeetingMetadata = { label: string; value: string };
type MeetingDetail = {
  id: number;
  title: string;
  status: string;
  conversation: { label: string; title: string; subtitle: string };
  metadata: MeetingMetadata[];
  trigger: { title: string; text: string };
  audit_explanation: { title: string; text: string };
  generated_reply: { title: string; text: string };
  audit_summary: string;
  tool_uses: unknown[];
  runtime: Record<string, unknown>;
  actions: { agent_url: string };
};

function MeetingSection({ title, value }: { title: string; value: unknown }) {
  return <section className="meeting-review-block"><h2>{title}</h2><SummaryText value={displayValue(value)} lines={5} /></section>;
}

function MeetingToolUses({ uses }: { uses: unknown[] }) {
  return <details className="console-card meeting-tool-card"><summary><strong>Tool uses</strong><span>{uses.length} 条调用记录</span></summary><div className="meeting-tool-list">{uses.length ? uses.map((use, index) => <div className="meeting-tool-item" key={index}>{displayValue(use)}</div>) : <p className="console-card-muted">没有工具调用记录。</p>}</div></details>;
}

export function MeetingAttemptPage({ endpoint }: { endpoint: string }) {
  const { runId = "" } = useParams();
  const [payload, setPayload] = useState<MeetingDetail | null>(null);
  const [snapshot, setSnapshot] = useState("");
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [message, setMessage] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setState("loading");
    setMessage("");
    getResource(endpoint.replace(":id", encodeURIComponent(runId)), controller.signal).then((response) => {
      setPayload(response.item as MeetingDetail);
      setSnapshot(response.meta.snapshot_at);
      setState("ready");
    }).catch((error: unknown) => {
      if (controller.signal.aborted) return;
      setMessage(error instanceof Error ? error.message : "Meeting Attempt 加载失败");
      setState("error");
    });
    return () => controller.abort();
  }, [endpoint, runId]);

  return <ConsolePageLayout title={payload ? `Meeting Attempt #${payload.id}` : "Meeting Attempt"} actions={<><SnapshotBadge timestamp={snapshot} refreshing={state === "loading"} /><Link className="secondary-button" to="/history">返回 History</Link></>}>
    {state === "loading" && !payload && <section className="console-card page-state" role="status">正在加载…</section>}
    {state === "error" && <section className="console-card page-state page-state-error" role="alert">{message}</section>}
    {payload && <>
      <section className="console-card compact-card meeting-conversation-banner">
        <div className="meeting-conversation-main"><div className="meeting-conversation-label">{payload.conversation.label}</div><div className="meeting-conversation-title">{payload.conversation.title}</div>{payload.conversation.subtitle && <div className="meeting-conversation-sub">{payload.conversation.subtitle}</div>}</div>
        <div className="meeting-banner-actions">{payload.actions.agent_url ? <Link className="agent-log-button" to={payload.actions.agent_url}>agent 执行记录</Link> : <span className="muted">No agent execution record</span>}</div>
      </section>
      <section className="console-card meeting-metadata-card"><div className="meeting-metadata-grid">{payload.metadata.map((field) => <div className="meeting-metadata-item" key={field.label}><span>{field.label}</span><strong>{field.value || "未记录"}</strong></div>)}</div></section>
      <section className="console-card meeting-review-card"><div className="meeting-reply-meta"><StatusBadge value={payload.status} /></div><MeetingSection title={payload.trigger.title} value={payload.trigger.text} /><MeetingSection title={payload.audit_explanation.title} value={payload.audit_explanation.text} /><MeetingSection title={payload.generated_reply.title} value={payload.generated_reply.text} /></section>
      <section className="console-card meeting-summary-card"><h2>Audit summary</h2><SummaryText value={payload.audit_summary} lines={4} /></section>
      <MeetingToolUses uses={payload.tool_uses} />
      <details className="console-card meeting-runtime-card"><summary><strong>Runtime details</strong><span>{payload.tool_uses.length} 条工具记录</span></summary><pre className="technical-details">{JSON.stringify(payload.runtime, null, 2)}</pre></details>
    </>}
  </ConsolePageLayout>;
}

function GenericBusinessDetailPage({ kind, endpoint, attemptActions = false }: { kind: string; endpoint: string; attemptActions?: boolean }) {
  const params = useParams();
  const id = params.attemptId || params.runId || params.processInstanceId || "";
  const [payload, setPayload] = useState<Record<string, unknown> | null>(null);
  const [snapshot, setSnapshot] = useState("");
  const [message, setMessage] = useState("");
  useEffect(() => { const controller = new AbortController(); getResource(endpoint.replace(":id", encodeURIComponent(id)), controller.signal).then((response) => { setPayload(response.item); setSnapshot(response.meta.snapshot_at); }).catch((error: unknown) => { if (!controller.signal.aborted) setMessage(error instanceof Error ? error.message : "加载失败"); }); return () => controller.abort(); }, [endpoint, id]);
  const runAction = async (action: string) => { setMessage("操作进行中…"); try { const result = await command(`/api/console/history/${encodeURIComponent(id)}/${action}`); setMessage(result.message); } catch (error) { setMessage(error instanceof Error ? error.message : "操作失败"); } };
  return <ConsolePageLayout title={kind} actions={<><SnapshotBadge timestamp={snapshot} /><Link className="secondary-button" to="/history">返回 History</Link></>}><section className="console-card">{message && <p className="page-state">{message}</p>}{!payload && !message ? <p className="page-state" role="status">正在加载…</p> : payload ? <><div className="task-overview-meta"><StatusBadge value={displayValue(payload.status)} /><span>{displayValue(payload.title)}</span></div><dl className="detail-definition-list">{["input", "decision", "output", "reviewer_feedback", "corrected_reply"].map((key) => <div key={key}><dt>{key}</dt><dd>{displayValue(payload[key])}</dd></div>)}</dl>{attemptActions && <div className="console-page-actions"><button type="button" className="secondary-button" onClick={() => void runAction("rerun")}>重跑</button><button type="button" className="secondary-button" onClick={() => void runAction("feedback")}>提交反馈</button></div>}<details><summary>Runtime details</summary><pre className="technical-details">{JSON.stringify(payload.runtime || {}, null, 2)}</pre></details></> : <div className="page-state page-state-error" role="alert">{message}</div>}</section></ConsolePageLayout>;
}

export function BusinessDetailPage({ kind, endpoint, attemptActions = false }: { kind: string; endpoint: string; attemptActions?: boolean }) {
  return kind === "Meeting Attempt"
    ? <MeetingAttemptPage endpoint={endpoint} />
    : <GenericBusinessDetailPage kind={kind} endpoint={endpoint} attemptActions={attemptActions} />;
}
