import { useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";

export function SummaryText({ value, lines = 3, label = "展开详情" }: { value: ReactNode; lines?: number; label?: string }) {
  const [expanded, setExpanded] = useState(false);
  const [isClipped, setIsClipped] = useState(false);
  const valueRef = useRef<HTMLSpanElement>(null);

  useLayoutEffect(() => {
    setExpanded(false);
  }, [value, lines]);

  useLayoutEffect(() => {
    if (expanded) return;
    const element = valueRef.current;
    if (!element) return;

    const measure = () => {
      setIsClipped(element.scrollHeight > element.clientHeight + 1 || element.scrollWidth > element.clientWidth + 1);
    };

    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [expanded, lines, value]);

  return (
    <span className={`summary-text${expanded ? " is-expanded" : ""}`} style={{ "--summary-lines": lines } as CSSProperties}>
      <span className="summary-text-value" ref={valueRef}>{value || "未提供"}</span>
      {isClipped && <button type="button" className="summary-text-toggle" aria-expanded={expanded} onClick={() => setExpanded((current) => !current)}>
          {expanded ? "收起" : label}
        </button>}
    </span>
  );
}
