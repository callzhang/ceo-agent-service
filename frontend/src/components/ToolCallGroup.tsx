import type { ReactNode } from "react";
import { ChevronRight } from "lucide-react";

export function groupToolCalls<T>(items: T[], isTool: (item: T) => boolean) {
  const groups: { tools: boolean; items: T[] }[] = [];
  for (const item of items) {
    const tools = isTool(item);
    const previous = groups.at(-1);
    if (tools && previous?.tools) previous.items.push(item);
    else groups.push({ tools, items: [item] });
  }
  return groups;
}

export function ToolCallGroup({ count, children }: { count?: number; children: ReactNode }) {
  return <details className="tool-call-group">
    <summary><ChevronRight aria-hidden="true" size={15} />工具调用{count === undefined ? "" : ` · ${count} 次`}</summary>
    <div className="tool-call-group-items">{children}</div>
  </details>;
}
