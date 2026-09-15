import { useLayoutEffect, useMemo, useRef } from "react";

function escapeHtml(value: string) {
  return value.replace(/[&<>\"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;" }[character] || character));
}

function highlight(value: string) {
  return `${escapeHtml(value).replace(/\{\{[^{}]+\}\}/g, (token) => `<mark>${token}</mark>`).replace(/\n/g, "<br />")}<br />`;
}

function positionLabel(value: string, index: number) {
  const before = value.slice(0, index);
  const line = before.split("\n").length;
  const lastBreak = before.lastIndexOf("\n");
  const column = index - lastBreak;
  return `第 ${line} 行，第 ${column} 列`;
}

function templateError(value: string) {
  const openings: number[] = [];
  for (let index = 0; index < value.length - 1; index += 1) {
    const pair = value.slice(index, index + 2);
    if (pair === "{{") {
      openings.push(index);
      index += 1;
    } else if (pair === "}}") {
      if (openings.length === 0) return `${positionLabel(value, index)}出现了没有对应开括号的 }}。`;
      openings.pop();
      index += 1;
    }
  }
  if (openings.length > 0) return `${positionLabel(value, openings[0])}的模板变量括号不完整，请检查 {{...}}。`;
  const invalid = value.match(/\{\{\s*\}\}/);
  return invalid?.index === undefined ? "" : `${positionLabel(value, invalid.index)}的模板变量不能为空。`;
}

export function TokenEditor({ id, label, value, onChange, rows = 18, autoResize = false }: { id: string; label: string; value: string; onChange: (value: string) => void; rows?: number; autoResize?: boolean }) {
  const error = useMemo(() => templateError(value), [value]);
  const editorRef = useRef<HTMLDivElement>(null);
  const highlightRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    if (!autoResize || !editorRef.current || !inputRef.current) return;
    const input = inputRef.current;
    input.style.height = "auto";
    const height = Math.max(160, input.scrollHeight);
    input.style.height = `${height}px`;
    editorRef.current.style.height = `${height}px`;
  }, [autoResize, value]);

  return <div className="token-editor-field">
    <label htmlFor={id}>{label}</label>
    <div ref={editorRef} className="token-editor" data-has-error={Boolean(error)} data-auto-resize={autoResize}>
      <div ref={highlightRef} className="token-editor-highlight" aria-hidden="true" dangerouslySetInnerHTML={{ __html: highlight(value) || "&nbsp;" }} />
      <textarea ref={inputRef} id={id} className="token-editor-input" rows={rows} value={value} onChange={(event) => onChange(event.target.value)} onScroll={(event) => { if (highlightRef.current) { highlightRef.current.scrollTop = event.currentTarget.scrollTop; highlightRef.current.scrollLeft = event.currentTarget.scrollLeft; } }} spellCheck={false} aria-invalid={Boolean(error)} aria-describedby={error ? `${id}-error` : undefined} />
    </div>
    {error && <p id={`${id}-error`} className="field-error" role="alert">{error}</p>}
  </div>;
}
