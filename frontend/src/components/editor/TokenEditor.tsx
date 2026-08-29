import { useMemo } from "react";

function escapeHtml(value: string) {
  return value.replace(/[&<>\"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;" }[character] || character));
}

function highlight(value: string) {
  return escapeHtml(value).replace(/\{\{[^{}]+\}\}/g, (token) => `<mark>${token}</mark>`).replace(/\n/g, "<br />");
}

function templateError(value: string) {
  const opening = (value.match(/\{\{/g) || []).length;
  const closing = (value.match(/\}\}/g) || []).length;
  if (opening !== closing) return "模板变量括号不完整，请检查 {{...}}。";
  const invalid = value.match(/\{\{\s*\}\}/);
  return invalid ? "模板变量不能为空。" : "";
}

export function TokenEditor({ id, label, value, onChange, rows = 18 }: { id: string; label: string; value: string; onChange: (value: string) => void; rows?: number }) {
  const error = useMemo(() => templateError(value), [value]);
  return <div className="token-editor-field">
    <label htmlFor={id}>{label}</label>
    <div className="token-editor" data-has-error={Boolean(error)}>
      <div className="token-editor-highlight" aria-hidden="true" dangerouslySetInnerHTML={{ __html: highlight(value) || "&nbsp;" }} />
      <textarea id={id} className="token-editor-input" rows={rows} value={value} onChange={(event) => onChange(event.target.value)} spellCheck={false} aria-invalid={Boolean(error)} aria-describedby={error ? `${id}-error` : undefined} />
    </div>
    {error && <p id={`${id}-error`} className="field-error" role="alert">{error}</p>}
  </div>;
}
