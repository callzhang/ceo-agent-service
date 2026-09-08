import { useLocation } from "react-router-dom";
import type { ReactNode } from "react";

import { GlobalNav } from "../components/GlobalNav";

export function AppShell({ children }: { children: ReactNode }) {
  const location = useLocation();
  const className = location.pathname === "/scheduled-tasks"
    ? "console-root scheduled-tasks-route"
    : "console-root";
  return <div className={className}><GlobalNav activePath={location.pathname} />{children}</div>;
}
