import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { displayValue, getCodexSession } from "../api/console";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { ExecutionStep } from "../components/ExecutionStep";
import { TrajectoryContent } from "../components/TrajectoryContent";
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

type RelatedAttempt = { id: number; status: string; role?: string; role_label?: string };

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

type TimelineRow = {
  event: CodexSessionEvent;
  output?: CodexSessionEvent;
};

function callId(event: CodexSessionEvent) {
  return event.trace?.call_id || "";
}

function timelineRows(events: CodexSessionEvent[]): TimelineRow[] {
  const pending = new Map<string, TimelineRow[]>();
  const rows: TimelineRow[] = [];
  for (const event of events) {
    const id = callId(event);
    if (event.kind === "tool_output" && id) {
      const row = pending.get(id)?.shift();
      if (row) { row.output = event; continue; }
    }
    const row: TimelineRow = { event };
    rows.push(row);
    if (event.kind === "tool_call" && id) {
      const calls = pending.get(id) || [];
      calls.push(row);
      pending.set(id, calls);
    }
  }
  return rows;
}

function SessionMarkdown({ event }: { event: CodexSessionEvent }) {
  if (event.kind === "reasoning") return <p className="muted">思考摘要未保留</p>;
  return <TrajectoryContent text={displayValue(event.body || "").trim() || "未记录内容"} collapsed={event.kind === "system_context" || event.kind === "metadata"} />;
}

function SessionTrace({ event, output }: { event: CodexSessionEvent; output?: CodexSessionEvent }) {
  const trace = event.trace || {};
  const result = output?.trace?.output ?? trace.output;
  return <ExecutionStep
    kind="tool"
    startedAt={event.timestamp}
    completedAt={output?.timestamp}
    status={result === undefined ? "running" : "completed"}
    payload={{
      kind: "trace",
      name: trace.name || event.title?.replace(/^Tool call:\s*/, "") || "工具调用",
      tool_call_id: trace.call_id,
      input: trace.input ?? event.body ?? "未记录",
      ...(result !== undefined ? { output: result } : {}),
    }}
  />;
}

function eventCategory(event: CodexSessionEvent) {
  if (["tool", "tool_call", "tool_output"].includes(event.kind || "")) return "tools";
  if (event.kind === "assistant") return "assistant";
  if (event.kind === "user") return "user";
  return "context";
}

function SessionTimeline({ events }: { events: CodexSessionEvent[] }) {
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const rows = useMemo(() => timelineRows(events), [events]);
  const categories = [["all", "全部"], ["assistant", "Agent 输出"], ["tools", "工具调用"], ["user", "任务输入"], ["context", "上下文"]];
  const search = query.trim().toLocaleLowerCase();
  const visible = rows.map((row, index) => ({ ...row, index })).filter(({ event, output }) => {
    if (filter !== "all" && eventCategory(event) !== filter) return false;
    const content = [event.title, event.kind === "reasoning" ? "" : event.body, event.trace?.name, event.trace?.input, event.trace?.output, output?.body, output?.trace?.output].join("\n");
    return !search || content.toLocaleLowerCase().includes(search);
  });
  if (!events.length) return <section className="console-card page-state">本次没有可展示的执行事件。</section>;
  return <section className="console-card codex-session-timeline" aria-label="Agent 执行时间线">
    <div className="codex-session-timeline-header"><div><h2>Agent 执行过程</h2><p>按发生顺序展示已加载的轨迹；工具输入与返回合并阅读，长内容可展开。</p></div><span>{rows.length} 条记录</span></div>
    <div className="trajectory-toolbar">
      <div className="trajectory-filters" role="group" aria-label="轨迹类型">{categories.map(([value, label]) => <button key={value} type="button" aria-pressed={filter === value} onClick={() => setFilter(value)}>{label}<span>{value === "all" ? rows.length : rows.filter(({ event }) => eventCategory(event) === value).length}</span></button>)}</div>
      <div className="trajectory-search"><input type="search" aria-label="搜索轨迹" placeholder="搜索工具、输入或返回内容…" value={query} onChange={(event) => setQuery(event.target.value)} /><span role="status">显示 {visible.length} / {rows.length} 条记录</span></div>
    </div>
    {!visible.length && <p className="page-state">没有匹配的轨迹记录。<button type="button" className="secondary-button" onClick={() => { setFilter("all"); setQuery(""); }}>清除筛选</button></p>}
    <ol>{visible.map(({ event, output, index }) => <li className={`codex-session-event codex-session-event-${event.kind || "system"}`} key={`${event.timestamp}-${index}`}>
      <div className="codex-session-event-rail"><span aria-hidden="true" /><small>#{index + 1}</small><time dateTime={event.timestamp}>{displayEventTime(event.timestamp)}</time></div>
      <article>{event.kind === "tool" || event.kind === "tool_call" ? <SessionTrace event={event} output={output} /> : <><header><span>{event.kind === "tool_output" ? "未配对的工具返回" : eventLabel(event.kind)}</span>{event.kind !== "reasoning" && <small>{event.title || "已记录"}</small>}</header>{event.kind === "tool_output" ? <pre className="trajectory-orphan-output">{event.trace?.output ?? event.body ?? "未记录内容"}</pre> : <SessionMarkdown event={event} />}</>}</article>
    </li>)}</ol>
  </section>;
}

function RelatedAttemptList({ attempts }: { attempts: RelatedAttempt[] }) {
  return <ul>{attempts.map((attempt) => <li key={`${attempt.id}-${attempt.role || ""}`}><Link to={`/attempts/${attempt.id}`}>Attempt #{attempt.id}</Link>{attempt.role_label && <span className="codex-related-role">{attempt.role_label}</span>}<StatusBadge value={attempt.status || "unknown"} /></li>)}</ul>;
}

function RelatedAttempts({ attempts }: { attempts: RelatedAttempt[] }) {
  if (!attempts.length) return null;
  const visibleAttempts = attempts.slice(0, 5);
  const olderAttempts = attempts.slice(5);
  return <section className="console-card codex-related-attempts">
    <div>
      <h2>关联事项</h2>
      <p>同一个 Agent 会话可能被多个处理轮次复用；这些记录不等于重复发送。</p>
      <p className="codex-related-summary">这个会话关联了 {attempts.length} 条处理记录；它们不等于 {attempts.length} 次发送。</p>
    </div>
    <div className="codex-related-latest">
      <h3>最新关联事项</h3>
      <RelatedAttemptList attempts={visibleAttempts} />
    </div>
    {olderAttempts.length > 0 && <details className="codex-related-history">
      <summary>查看其余 {olderAttempts.length} 条关联事项</summary>
      <RelatedAttemptList attempts={olderAttempts} />
    </details>}
  </section>;
}

export function CodexSessionDetailPage() {
  const { sessionId = "" } = useParams();
  return <CodexSessionDetailContent key={sessionId} sessionId={sessionId} />;
}

function CodexSessionDetailContent({ sessionId }: { sessionId: string }) {
  const [payload, setPayload] = useState<CodexSessionPayload | null>(null);
  const [error, setError] = useState("");
  const [snapshot, setSnapshot] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    getCodexSession(sessionId, controller.signal).then((response) => {
      if (controller.signal.aborted) return;
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
    {error ? <section className="console-card page-state page-state-error" role="alert">{error}</section> : !payload ? <section className="console-card page-state" role="status">正在加载…</section> : payload.available ? <><SessionTimeline events={events} /><RelatedAttempts attempts={relatedAttempts} /></> : <><section className="console-card codex-session-unavailable"><div><h2>Agent 记录不可用</h2><p>{payload.message === "本机执行记录不可用" ? "原始 session 文件已被清理或当前不可读取。" : displayValue(payload.message || "本机执行记录不可用。")}</p><p>这不会删除业务结果；请从下方关联事项查看最终状态和审计结论。关联事项是索引，不代表每条记录都执行过外部动作。</p></div><StatusBadge value="unavailable" /></section><RelatedAttempts attempts={relatedAttempts} /></>}
  </ConsolePageLayout>;
}
