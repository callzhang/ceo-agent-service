import type { ReactNode } from "react";

export function FilterBar({ children }: { children: ReactNode }) {
  return <div className="filter-bar" data-testid="filter-bar">{children}</div>;
}
