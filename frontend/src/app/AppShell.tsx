import { useLocation } from "react-router-dom";
import type { ReactNode } from "react";

import { GlobalNav } from "../components/GlobalNav";

export function AppShell({ children }: { children: ReactNode }) {
  const location = useLocation();
  return <div className="console-root"><GlobalNav activePath={location.pathname} />{children}</div>;
}
