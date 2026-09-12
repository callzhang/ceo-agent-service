import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { displayValue, getCodexSession } from "../api/console";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { ExecutionStep } from "../components/ExecutionStep";
import { MarkdownContent } from "../components/MarkdownContent";
import { SnapshotBadge } from "../components/status/SnapshotBadge";
import { StatusBadge } from "../components/status/StatusBadge";

type CodexSessionEvent = {
  timestamp?: string;
  kind?: string;
  title?: string;
  body?: string;
  trace?: {
    call_id?: string;
    name?: string;
    input?: string;
    output?: string;
  };
};

type CodexSessionPayload = {
  available?: boolean;
  message?: string;
  events?: CodexSessionEvent[];
  related_attempts?: Array<{ id: number; status: string; role?: string; role_label?: string }>;
};

function displayEventTime(value?: string) {
  if (!value) return "时间未记录";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未记录" : date.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function eventLabel(kind?: string) {
  if (kind === "user") return "任务与上下文";
  if (kind === "assistant") return "Agent 输出";
  if (kind === "reasoning") return "思考摘要";
  if (kind === "tool" || kind === "tool_call") return "工具调用";
  return "执行事件";
}

function eventText(event: CodexSessionEvent) {
  if (event.kind === "reasoning") return "思考摘要未保留";
  const body = displayValue(event.body || "").trim();
  if (!body) return "未记录内容";
  try {
    const value = JSON.parse(body) as { summary?: unknown; error_code?: unknown; outcome?: unknown };
    if (value && typeof value === "object") {
      const summary = typeof value.summary === "string" ? value.summary.trim() : "";
      const outcome = typeof value.outcome === "string" ? value.outcome.trim() : "";
      const error = typeof value.error_code === "string" ? value.error_code.trim() : "";
      return summary || [outcome && `结果：${outcome}`, error && `异常：${error}`].filter(Boolean).join(" · ") || "已记录结构化执行结果";
    }
  } catch {
    // The persisted event is ordinary text, so it remains directly readable.
  }
  return body;
}

function traceValue(value?: string): unknown {
  if (!value) return "未记录";
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

type TimelineRow = {
  event: CodexSessionEvent;
  output?: CodexSessionEvent;
};

function callId(event: CodexSessionEvent) {
  return event.trace?.call_id || "";
}

function timelineRows(events: CodexSessionEvent[]): TimelineRow[] {
  const outputsByCallId = new Map<string, CodexSessionEvent>();
  for (const event of events) {
    if (event.kind === "tool_output" && callId(event)) outputsByCallId.set(callId(event), event);
  }
  return events.flatMap((event) => {
    if (event.kind === "tool_output") return [];
    if (event.kind !== "tool_call") return [{ event }];
    return [{ event, output: outputsByCallId.get(callId(event)) }];
  });
}

function SessionMarkdown({ event }: { event: CodexSessionEvent }) {
  const text = eventText(event);
  const shouldCollapse = text.length > 1_200 || event.kind === "system_context";
  if (!shouldCollapse) return <MarkdownContent text={text} />;
  return <details className="codex-session-event-content-collapse"><summary>查看完整内容（{text.length.toLocaleString()} 字）</summary><MarkdownContent text={text} /></details>;
}

function SessionTrace({ event, output }: { event: CodexSessionEvent; output?: CodexSessionEvent }) {
  const trace = event.trace || {};
  const result = output?.trace?.output ?? trace.output;
  return <ExecutionStep
    kind="tool"
    status={result === undefined ? "running" : "completed"}
    payload={{
      kind: "trace",
      name: trace.name || event.title?.replace(/^Tool call:\s*/, "") || "工具调用",
      tool_call_id: trace.call_id,
      input: traceValue(trace.input ?? event.body),
      ...(result !== undefined ? { output: traceValue(result) } : {}),
    }}
  />;
}

function SessionTimeline({ events }: { events: CodexSessionEvent[] }) {
  if (!events.length) return <section className="console-card page-state">本次没有可展示的执行事件。</section>;
  const rows = timelineRows(events);
  return <section className="console-card codex-session-timeline" aria-label="Agent 执行时间线">
    <div className="codex-session-timeline-header"><div><h2>Agent 执行过程</h2><p>按发生顺序记录任务输入、Agent 输出与可见工具调用；展开工具卡可查看输入与输出。</p></div><span>{rows.length} 条记录</span></div>
    <ol>{rows.map(({ event, output }, index) => <li className={`codex-session-event codex-session-event-${event.kind || "system"}`} key={`${event.timestamp}-${index}`}>
      <div className="codex-session-event-rail"><span aria-hidden="true" /><time>{displayEventTime(event.timestamp)}</time></div>
      <article>{event.kind === "tool" || event.kind === "tool_call" ? <SessionTrace event={event} output={output} /> : <><header><span>{eventLabel(event.kind)}</span>{event.kind !== "reasoning" && <small>{event.title || "已记录"}</small>}</header><SessionMarkdown event={event} /></>}</article>
    </li>)}</ol>
  </section>;
}

function RelatedAttempts({ attempts }: { attempts: Array<{ id: number; status: string; role?: string; role_label?: string }> }) {
  if (!attempts.length) return null;
  return <section className="console-card codex-related-attempts"><div><h2>关联事项</h2><p>这些业务记录使用了本次 Agent 执行。</p></div><ul>{attempts.map((attempt) => <li key={`${attempt.id}-${attempt.role || ""}`}><Link to={`/attempts/${attempt.id}`}>Attempt #{attempt.id}</Link>{attempt.role_label && <span className="codex-related-role">{attempt.role_label}</span>}<StatusBadge value={attempt.status || "unknown"} /></li>)}</ul></section>;
}

export function CodexSessionDetailPage() {
  const { sessionId = "" } = useParams();
  const [payload, setPayload] = useState<CodexSessionPayload | null>(null);
  const [error, setError] = useState("");
  const [snapshot, setSnapshot] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    getCodexSession(sessionId, controller.signal).then((response) => {
      setPayload(response.item);
      setSnapshot(response.meta.snapshot_at);
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "加载失败");
    });
    return () => controller.abort();
  }, [sessionId]);
  const events = useMemo(() => payload?.events || [], [payload?.events]);
  const relatedAttempts = payload?.related_attempts || [];

  return <ConsolePageLayout title="Agent 执行过程" actions={<><SnapshotBadge timestamp={snapshot} /><Link className="secondary-button" to="/codex">返回会话列表</Link></>}>
    {error ? <section className="console-card page-state page-state-error" role="alert">{error}</section> : !payload ? <section className="console-card page-state" role="status">正在加载…</section> : payload.available ? <><SessionTimeline events={events} /><RelatedAttempts attempts={relatedAttempts} /></> : <><section className="console-card codex-session-unavailable"><div><h2>本机执行记录不可用</h2><p>{payload.message === "本机执行记录不可用" ? "本机的 session 文件已被清理或当前不可读取。" : displayValue(payload.message || "本机 transcript 文件已不可用。")}</p><p>业务处理结果仍保留在关联事项中；可从那里查看最终回复、状态和审计结论。</p></div><StatusBadge value="unavailable" /></section><RelatedAttempts attempts={relatedAttempts} /></>}
  </ConsolePageLayout>;
}
