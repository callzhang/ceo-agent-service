export function FilterChip({ label, count, active = false, onClick }: { label: string; count?: number; active?: boolean; onClick: () => void }) {
  return <button type="button" className={`filter-chip${active ? " active" : ""}`} aria-label={count !== undefined ? `${label} ${count}` : label} aria-pressed={active} onClick={onClick}>{label}{count !== undefined && <strong aria-hidden="true">{count}</strong>}</button>;
}
