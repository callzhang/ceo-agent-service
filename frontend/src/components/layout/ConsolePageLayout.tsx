import type { ReactNode } from "react";

export function ConsolePageLayout({ title, eyebrow = "CEO AGENT CONSOLE", children, actions }: { title: string; eyebrow?: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <main className="console-page" aria-labelledby="console-page-title">
      <div className="console-page-header">
        <div>
          <p className="eyebrow">{eyebrow}</p>
          <h1 id="console-page-title">{title}</h1>
        </div>
        {actions && <div className="console-page-actions">{actions}</div>}
      </div>
      {children}
    </main>
  );
}
