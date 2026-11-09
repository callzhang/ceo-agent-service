import { CheckCircle2, ChevronRight, CircleEllipsis, CircleSlash2, FilePenLine, XCircle } from "lucide-react";

import { executionName, executionStateLabel } from "../presentation";
import { formatWorkbenchDateTime, parseWorkbenchTimestamp } from "../presentation";

const legacySummaries: Record<string, string> = {
  "Tool started": "执行中",
  "Tool completed": "已完成",
  "Tool failed": "执行失败",
};

export function displayText(value: unknown, fallback: string): string {
  if (typeof value !== "string") return fallback;
  const cleaned = Array.from(value).filter((character) => character.charCodeAt(0) >= 32 || character === "\n").join("").trim();
  if (!cleaned) return fallback;
  return cleaned;
}

export interface ExecutionStepProps {
  kind: "tool" | "file";
  status?: string;
  payload?: Record<string, unknown>;
  startedAt?: string;
  completedAt?: string;
}

function exactText(value: unknown, fallback = ""): string {
  if (typeof value !== "string") return fallback;
  return Array.from(value)
    .filter((character) => character.charCodeAt(0) >= 32 || character === "\n" || character === "\t")
    .join("")
    .trim();
}

function detailText(value: unknown): string {
  if (typeof value === "string") return exactText(value);
  const encoded = JSON.stringify(value, null, 2);
  return typeof encoded === "string" ? encoded : String(value);
}

function commandText(value: unknown): string {
  return Array.isArray(value) ? value.map((arg) => JSON.stringify(arg)).join(" ") : exactText(value);
}

function previewText(value: unknown): string {
  let readable = value;
  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value);
      const content = Array.isArray(parsed) ? parsed : parsed?.content;
      if (Array.isArray(content) && content.length && content.every((part) => typeof part?.text === "string")) readable = content.map((part) => part.text).join("\n");
    } catch { /* Preview plain text; expanded evidence remains exact. */ }
  }
  const text = detailText(readable);
  return text.length > 240 ? `${text.slice(0, 120)}\n…\n${text.slice(-120)}` : text;
}

function durationLabel(startedAt?: string, completedAt?: string): string {
  if (!startedAt || !completedAt) return "";
  const start = parseWorkbenchTimestamp(startedAt);
  const end = parseWorkbenchTimestamp(completedAt);
  if (!start || !end || end.getTime() < start.getTime()) return "";
  return `${((end.getTime() - start.getTime()) / 1000).toFixed(1)} 秒`;
}

function TimeDetail({ label, value }: { label: string; value?: string }) {
  if (!value) return null;
  const formatted = formatWorkbenchDateTime(value);
  return <div><dt>{label}</dt><dd>{formatted ? <time dateTime={formatted.dateTime}>{formatted.label}</time> : value}</dd></div>;
}

export function ExecutionStep({ kind, status = "running", payload = {}, startedAt, completedAt }: ExecutionStepProps) {
  const failed = status === "failed" || status === "error";
  const completed = status === "completed" || status === "success";
  const aborted = status === "aborted";
  const Icon = kind === "file" ? FilePenLine : failed ? XCircle : completed ? CheckCircle2 : aborted ? CircleSlash2 : CircleEllipsis;
  const sessionTrace = kind === "tool" && payload.kind === "trace";
  const whiteBox = kind === "tool" && (payload.kind === "command" || payload.kind === "mcp" || sessionTrace);
  const name = kind === "file"
    ? displayText(payload.filename, "文件变更")
    : sessionTrace
      ? exactText(payload.name, "工具调用")
      : whiteBox
      ? (payload.kind === "command" ? commandText(payload.command) : exactText(payload.name)) || exactText(payload.name, "工具调用")
      : executionName(payload.tool);
  const rawSummary = payload.summary ?? payload.change;
  const summary = displayText(
    typeof rawSummary === "string" && Object.prototype.hasOwnProperty.call(legacySummaries, rawSummary)
      ? legacySummaries[rawSummary]
      : rawSummary,
    "未提供可显示的摘要",
  );
  const stateLabel = executionStateLabel(status);
  const duration = durationLabel(startedAt, completedAt);
  const input = payload.input ?? payload.arguments;
  let previewInput = input;
  if (typeof input === "string") {
    try { previewInput = JSON.parse(input); } catch { /* Raw command text. */ }
  }
  const inputRecord = previewInput && typeof previewInput === "object" ? previewInput as Record<string, unknown> : null;
  const previewValue = inputRecord?.cmd ?? inputRecord?.command ?? inputRecord?.path ?? input;
  const preview = payload.kind === "command" || previewValue === undefined ? "" : previewText(previewValue);
  const result = payload.output ?? payload.result;
  const resultPreview = result === undefined ? "" : previewText(result);
  return (
    <details className={`execution-step execution-${failed ? "failed" : completed ? "completed" : aborted ? "aborted" : "running"}`}>
      <summary>
        <Icon aria-hidden="true" size={16} />
        <span className="execution-name" title={name}>{name}{preview && <small className="execution-preview" title={preview}>{preview}</small>}{resultPreview && <small className="execution-result-preview"><span>返回</span>{resultPreview}</small>}</span>
        <span className="execution-state" role="status">{stateLabel}{duration && <small className="execution-duration">{duration}</small>}</span>
        <ChevronRight className="execution-chevron" aria-hidden="true" size={15} />
      </summary>
      {kind === "file" ? <p>{summary}</p> : whiteBox ? (
        <div className="execution-details">
          {sessionTrace && payload.input !== undefined && <section><h4>输入</h4><pre>{detailText(payload.input)}</pre></section>}
          {payload.arguments !== undefined && <section><h4>参数</h4><pre>{detailText(payload.arguments)}</pre></section>}
          {payload.output !== undefined && <section><h4>输出</h4><pre>{detailText(payload.output)}</pre></section>}
          {payload.result !== undefined && <section><h4>结果</h4><pre>{detailText(payload.result)}</pre></section>}
          <details className="execution-technical"><summary>调用详情{payload.provider_item !== undefined ? "与原始事件" : ""}</summary><dl className="execution-metadata">
            {sessionTrace ? (
              <>
                <div><dt>调用名称</dt><dd><code>{exactText(payload.name, "未提供")}</code></dd></div>
              </>
            ) : payload.kind === "command" ? (
              <>
                <div><dt>命令</dt><dd><code>{commandText(payload.command) || "未提供"}</code></dd></div>
                {typeof payload.cwd === "string" && <div><dt>工作目录</dt><dd><code>{exactText(payload.cwd)}</code></dd></div>}
                {typeof payload.exit_code === "number" && <div><dt>退出码</dt><dd>{payload.exit_code}</dd></div>}
              </>
            ) : (
              <>
                <div><dt>MCP 服务</dt><dd><code>{exactText(payload.server, "未提供")}</code></dd></div>
                <div><dt>工具</dt><dd><code>{exactText(payload.tool, "未提供")}</code></dd></div>
              </>
            )}
            {typeof payload.tool_call_id === "string" && <div><dt>调用 ID</dt><dd><code>{exactText(payload.tool_call_id)}</code></dd></div>}
            {typeof payload.native_id === "string" && <div><dt>Provider 项目 ID</dt><dd><code>{exactText(payload.native_id)}</code></dd></div>}
            <TimeDetail label="开始时间" value={startedAt} />
            <TimeDetail label="完成时间" value={completedAt} />
          </dl>
          {payload.provider_item !== undefined && <section><h4>原始工具事件</h4><pre>{detailText(payload.provider_item)}</pre></section>}
          </details>
          {status === "aborted" && <p className="execution-aborted-note">{summary}</p>}
          {failed && <p className="execution-failure-note">{summary}</p>}
        </div>
      ) : (
        <div className="execution-legacy-detail">
          <strong>历史事件未记录命令详情</strong>
          <p>{summary}</p>
        </div>
      )}
    </details>
  );
}
