import { useEffect, useState } from "react";
import { listHistory, type HistoryItem } from "../api/console";

const REFRESH_MS = 60_000;

/**
 * Everything waiting on Derek, on the page he opens first.
 *
 * Derek, 2026-09-25: four OA approvals were waiting on him while the landing
 * page showed only "处理反馈 · 0" (message feedback, a different thing), so
 * from where he looks nothing needed him. Each row names the item and the
 * one-line reason, and opens its attempt page.
 */
export function NeedsDecisionPanel() {
  const [items, setItems] = useState<HistoryItem[]>([]);
  const [total, setTotal] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let controller = new AbortController();
    const load = () => {
      controller.abort();
      controller = new AbortController();
      listHistory({ status: "needs_human", page_size: 20, include_chart: "false" }, controller.signal)
        .then((page) => {
          if (cancelled) return;
          setItems(page.items.filter((item) => String(item.status).toLowerCase() === "needs_human"));
          setTotal(page.meta.total);
        })
        .catch(() => {
          // The panel is a pointer, not the source of truth; History still lists these.
        });
    };
    load();
    const timer = window.setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      controller.abort();
      window.clearInterval(timer);
    };
  }, []);

  if (total === 0) return null;

  return (
    <section className="needs-decision" aria-labelledby="needs-decision-title">
      <h2 id="needs-decision-title" className="needs-decision-title">
        需要你决定 · {total}
      </h2>
      <ul className="needs-decision-list">
        {items.map((item) => (
          <li key={item.id}>
            <a className="needs-decision-item" href={item.detail_url || `/attempts/${item.id}`}>
              <span className="needs-decision-name">{item.title}</span>
              {typeof item.summary === "string" && item.summary && (
                <span className="needs-decision-reason">{item.summary}</span>
              )}
            </a>
          </li>
        ))}
      </ul>
      {total > items.length && (
        <a className="needs-decision-more" href="/history?status=needs_human">
          查看全部 {total} 条
        </a>
      )}
    </section>
  );
}
