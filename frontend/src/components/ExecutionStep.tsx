import { CheckCircle2, ChevronRight, CircleEllipsis, FilePenLine, XCircle } from "lucide-react";

import { executionName, executionStateLabel } from "../presentation";

const legacySummaries: Record<string, string> = {
  "Tool started": "执行中",
  "Tool completed": "已完成",
  "Tool failed": "执行失败",
};

export function safeDisplayText(value: unknown, fallback: string): string {
  if (typeof value !== "string") return fallback;
  const cleaned = Array.from(value).filter((character) => character.charCodeAt(0) >= 32 || character === "\n").join("").trim();
  if (!cleaned) return fallback;
  const redacted = cleaned
    .replace(/(^|[\s=:'"`[\]{}(),;<>|])(?:file:\/\/|\/|~\/|[A-Za-z]:[\\/]|\\\\)\S*/g, "$1[已隐藏本地路径]")
    .replace(/\bBearer\s+\S+/gi, "Bearer [已隐藏凭据]")
    .replace(/\b(api[_-]?key|token|secret|password)\s*[:=]\s*\S+/gi, "$1=[已隐藏凭据]")
    .replace(/\bsk-[A-Za-z0-9_-]{8,}/g, "[已隐藏凭据]");
  return redacted.slice(0, 240);
}

export interface ExecutionStepProps {
  kind: "tool" | "file";
  status?: string;
  payload?: Record<string, unknown>;
}

export function ExecutionStep({ kind, status = "running", payload = {} }: ExecutionStepProps) {
  const failed = status === "failed" || status === "error";
  const completed = status === "completed" || status === "success";
  const Icon = kind === "file" ? FilePenLine : failed ? XCircle : completed ? CheckCircle2 : CircleEllipsis;
  const name = kind === "file"
    ? safeDisplayText(payload.filename, "文件变更")
    : executionName(payload.tool);
  const rawSummary = payload.summary ?? payload.change;
  const summary = safeDisplayText(
    typeof rawSummary === "string" && Object.prototype.hasOwnProperty.call(legacySummaries, rawSummary)
      ? legacySummaries[rawSummary]
      : rawSummary,
    "未提供可显示的摘要",
  );
  const stateLabel = executionStateLabel(status);
  return (
    <details className={`execution-step execution-${failed ? "failed" : completed ? "completed" : "running"}`}>
      <summary>
        <Icon aria-hidden="true" size={16} />
        <span className="execution-name">{name}</span>
        <span className="execution-state">{stateLabel}</span>
        <ChevronRight className="execution-chevron" aria-hidden="true" size={15} />
      </summary>
      <p>{summary}</p>
    </details>
  );
}
