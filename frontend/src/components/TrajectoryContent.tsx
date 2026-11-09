import { useState } from "react";
import { parseTree, type Node, type ParseError } from "jsonc-parser";

import { MarkdownContent } from "./MarkdownContent";

function JsonValue({ node, source }: { node: Node; source: string }) {
  if (node.type === "string") return node.value ? <MarkdownContent text={node.value} /> : <code>""</code>;
  if (node.type === "array") return node.children?.length ? <ul>{node.children.map((item) => <li key={item.offset}><JsonValue node={item} source={source} /></li>)}</ul> : <code>[]</code>;
  if (node.type === "object") return node.children?.length ? <dl className="trajectory-json-fields">{node.children.map((property) => <div key={property.offset}><dt>{property.children![0].value}</dt><dd><JsonValue node={property.children![1]} source={source} /></dd></div>)}</dl> : <code>{"{}"}</code>;
  return <code>{source.slice(node.offset, node.offset + node.length)}</code>;
}

/** Keep the complete source available while making large trajectory messages scannable. */
export function TrajectoryContent({ text, label = "完整内容", plain = false, collapsed = false }: {
  text: string; label?: string; plain?: boolean; collapsed?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const errors: ParseError[] = [];
  const structured = plain ? undefined : parseTree(text, errors, { disallowComments: true, allowTrailingComma: false });
  if (structured && !errors.length) {
    return <div className="trajectory-structured">
      <JsonValue node={structured} source={text} />
      <a className="trajectory-json-source" href={`data:application/json;charset=utf-8,${encodeURIComponent(text)}`} download="agent-result.json">下载原始 JSON</a>
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
