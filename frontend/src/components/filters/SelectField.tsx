import { type ChangeEvent, type ReactNode } from "react";

export interface SelectOption {
  value: string;
  label: ReactNode;
}

export interface SelectFieldProps {
  id: string;
  label: string;
  value: string;
  options?: SelectOption[];
  children?: ReactNode;
  ariaLabel?: string;
  onChange: (value: string) => void;
}

export function SelectField({ id, label, value, options = [], children, ariaLabel, onChange }: SelectFieldProps) {
  function handleChange(event: ChangeEvent<HTMLSelectElement>) {
    onChange(event.target.value);
  }

  return (
    <label className="filter-field filter-select" htmlFor={id}>
      <span className="filter-control-label">{label}</span>
      <span className="filter-control-shell">
        <select id={id} value={value} aria-label={ariaLabel || label} onChange={handleChange}>
          {children ?? options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
      </span>
    </label>
  );
}
