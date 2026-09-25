import { useEffect, useRef, useState } from "react";

export interface MultiSelectOption {
  value: string;
  label: string;
}

export interface MultiSelectFieldProps {
  id: string;
  label: string;
  /** What the closed menu reads when nothing is ticked, e.g. 全部状态. */
  allLabel: string;
  options: MultiSelectOption[];
  values: string[];
  onChange: (values: string[]) => void;
}

/** A filter menu of checkboxes; nothing ticked means no filter. */
export function MultiSelectField({ id, label, allLabel, options, values, onChange }: MultiSelectFieldProps) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  const selected = options.filter((option) => values.includes(option.value));
  const summary = !selected.length
    ? allLabel
    : selected.length <= 2
      ? selected.map((option) => option.label).join("、")
      : `${selected[0].label} 等 ${selected.length} 项`;
  const toggle = (value: string) =>
    onChange(values.includes(value) ? values.filter((item) => item !== value) : [...values, value]);

  return (
    <div className="filter-field filter-select filter-multiselect" ref={root}>
      <span className="filter-control-label" id={`${id}-label`}>{label}</span>
      <span className="filter-control-shell">
        <button
          type="button"
          id={id}
          className="filter-multiselect-trigger"
          aria-haspopup="true"
          aria-expanded={open}
          aria-label={`${label}：${summary}`}
          onClick={() => setOpen((value) => !value)}
        >
          {summary}
        </button>
      </span>
      {open && (
        <div className="filter-multiselect-menu" role="group" aria-label={label}>
          {options.map((option) => (
            <label key={option.value} className="filter-multiselect-option">
              <input type="checkbox" checked={values.includes(option.value)} onChange={() => toggle(option.value)} />
              <span>{option.label}</span>
            </label>
          ))}
          {values.length > 0 && (
            <button type="button" className="filter-multiselect-clear" onClick={() => onChange([])}>
              清除
            </button>
          )}
        </div>
      )}
    </div>
  );
}
