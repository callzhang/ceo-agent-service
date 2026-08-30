import { Search, X } from "lucide-react";
import { type ChangeEvent, type KeyboardEvent } from "react";

export interface SearchFieldProps {
  id: string;
  label: string;
  value: string;
  placeholder?: string;
  describedBy?: string;
  onChange: (value: string) => void;
  onClear?: () => void;
}

export function SearchField({ id, label, value, placeholder = "搜索", describedBy, onChange, onClear }: SearchFieldProps) {
  function handleChange(event: ChangeEvent<HTMLInputElement>) {
    onChange(event.target.value);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape" && value && onClear) {
      event.preventDefault();
      onClear();
    }
  }

  return (
    <label className="filter-field filter-search" htmlFor={id}>
      <span className="filter-control-label">{label}</span>
      <span className="filter-control-shell">
        <Search className="filter-search-icon" size={17} aria-hidden="true" />
        <input id={id} type="search" value={value} placeholder={placeholder} aria-label={label} aria-describedby={describedBy} onChange={handleChange} onKeyDown={handleKeyDown} />
        {value && onClear && <button type="button" className="filter-clear" aria-label={`Clear ${label}`} onClick={onClear}><X size={15} aria-hidden="true" /></button>}
      </span>
    </label>
  );
}
