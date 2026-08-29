import { Check, X } from "lucide-react";
import { useEffect, useRef } from "react";

import type { FeedbackItem } from "../api/console";

export interface FeedbackDrawerProps {
  open: boolean;
  pending: readonly FeedbackItem[];
  loading: boolean;
  error: string;
  selected: ReadonlySet<string>;
  submitting: boolean;
  onToggle: (feedbackKey: string) => void;
  onSelectAll: () => void;
  onImport: () => void | Promise<void>;
  onClose: () => void;
}

function feedbackKey(item: FeedbackItem): string {
  return item.feedback_key || item.id;
}

export function FeedbackDrawer({
  open,
  pending,
  loading,
  error,
  selected,
  submitting,
  onToggle,
  onSelectAll,
  onImport,
  onClose,
}: FeedbackDrawerProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const selectAllRef = useRef<HTMLInputElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const visibleKeys = new Set(pending.map(feedbackKey));
  const visibleSelected = new Set([...selected].filter((key) => visibleKeys.has(key)));
  const allSelected = pending.length > 0 && pending.every((item) => visibleSelected.has(feedbackKey(item)));

  useEffect(() => {
    if (!open) return;
    returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const dialog = dialogRef.current;
    if (!dialog) return;
    const focusable = () => Array.from(dialog.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ));
    focusable()[0]?.focus();
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (items.length === 0) {
        event.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      returnFocusRef.current?.focus();
    };
  }, [open]);

  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = visibleSelected.size > 0 && !allSelected;
  }, [allSelected, visibleSelected.size]);

  if (!open) return null;

  return (
    <>
      <div className="drawer-scrim feedback-drawer-scrim" aria-hidden="true" onClick={onClose} />
      <div
        ref={dialogRef}
        className="inspector-panel inspector-drawer feedback-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="feedback-drawer-title"
      >
        <div className="inspector-header inspector-drawer-header">
          <div>
            <p className="eyebrow">FEEDBACK</p>
            <h2 id="feedback-drawer-title">处理反馈</h2>
          </div>
          <button className="drawer-close" type="button" aria-label="关闭反馈" onClick={onClose}>
            <X aria-hidden="true" size={18} />
          </button>
        </div>

        {loading ? (
          <p className="feedback-state" role="status">正在加载反馈…</p>
        ) : error ? (
          <p className="feedback-state feedback-error" role="alert">{error}</p>
        ) : pending.length === 0 ? (
          <p className="feedback-state">当前没有待处理反馈</p>
        ) : (
          <>
            <div className="feedback-toolbar">
              <label className="feedback-select-all">
                <input
                  ref={selectAllRef}
                  type="checkbox"
                  checked={allSelected}
                  onChange={onSelectAll}
                  aria-label="全选反馈"
                />
                <span>全选</span>
              </label>
              <span className="feedback-selection-count" role="status">已选 {visibleSelected.size} 项</span>
            </div>
            <div className="feedback-items">
              {pending.map((item) => {
                const key = feedbackKey(item);
                return (
                  <article className="feedback-item" key={key}>
                    <label className="feedback-item-select">
                      <input
                        type="checkbox"
                        checked={visibleSelected.has(key)}
                        onChange={() => onToggle(key)}
                        aria-label={`选择反馈 ${item.summary}`}
                      />
                      <span className="feedback-item-content">
                        <span className="feedback-item-summary">{item.summary}</span>
                        <span className="feedback-item-meta">评分：{item.rating || "未提供"} · 收到：{item.created_at ? <time dateTime={item.created_at}>{item.created_at}</time> : "未提供"}</span>
                        {item.references.length > 0 && (
                          <span className="feedback-item-references">
                            {item.references.map((reference) => (
                              <a key={`${key}:${reference.route}:${reference.label}`} href={reference.route}>
                                {reference.label}
                              </a>
                            ))}
                          </span>
                        )}
                      </span>
                    </label>
                  </article>
                );
              })}
            </div>
            <div className="feedback-actions">
              <button className="secondary-button" type="button" onClick={onClose} disabled={submitting}>取消</button>
              <button className="primary-button" type="button" onClick={() => void onImport()} disabled={loading || Boolean(error) || submitting || visibleSelected.size === 0}>
                {submitting ? "导入中…" : <><Check aria-hidden="true" size={15} />导入并开始 brainstorm</>}
              </button>
            </div>
          </>
        )}
      </div>
    </>
  );
}
