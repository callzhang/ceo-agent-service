import { useState } from "react";

import { MarkdownContent } from "./MarkdownContent";

/** Keep the complete source available while making large trajectory messages scannable. */
export function TrajectoryContent({ text, label = "完整内容", plain = false, collapsed = false }: {
  text: string; label?: string; plain?: boolean; collapsed?: boolean;
}) {
  const [open, setOpen] = useState(false);
  let structured: unknown;
  if (!plain) {
    try { structured = JSON.parse(text); } catch { /* Ordinary Markdown. */ }
  }
  if (structured !== null && typeof structured === "object") {
    const summary = "summary" in structured && typeof structured.summary === "string" ? structured.summary : "";
    return <div className="trajectory-structured">
      {summary && <MarkdownContent text={summary} />}
      <details><summary>查看完整 JSON</summary><pre>{text}</pre></details>
    </div>;
  }
  const content = plain ? <p className="trajectory-plain">{text}</p> : <MarkdownContent text={text} />;
  if (!collapsed && text.length <= 1_200) return content;
  return <div className="trajectory-content">
    {!open && <p className="trajectory-preview">{text.slice(0, 180).trim()}{text.length > 180 ? "…" : ""}</p>}
    <details open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>{open ? "收起" : "展开"}{label}（{text.length.toLocaleString()} 字）</summary>
      {content}
    </details>
  </div>;
}
