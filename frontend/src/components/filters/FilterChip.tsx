export function FilterChip({ label, count, active = false, busy = false, onClick }: { label: string; count?: number; active?: boolean; busy?: boolean; onClick: () => void }) {
  return <button type="button" className={`filter-chip${active ? " active" : ""}${busy ? " busy" : ""}`} aria-label={count !== undefined ? `${label} ${count}` : label} aria-pressed={active} aria-busy={busy} onClick={onClick}>{label}{count !== undefined && <strong aria-hidden="true">{count}</strong>}{busy && <span className="filter-chip-spinner" aria-hidden="true" />}</button>;
}
