import { useState, type CSSProperties, type ReactNode } from "react";

export function SummaryText({ value, lines = 3, label = "展开详情" }: { value: ReactNode; lines?: number; label?: string }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <span className={`summary-text${expanded ? " is-expanded" : ""}`} style={{ "--summary-lines": lines } as CSSProperties}>
      <span className="summary-text-value">{value || "未提供"}</span>
      <button type="button" className="summary-text-toggle" aria-expanded={expanded} onClick={() => setExpanded((current) => !current)}>
        {expanded ? "收起" : label}
      </button>
    </span>
  );
}
