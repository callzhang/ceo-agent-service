import type { ReactNode } from "react";

export function ConsolePageLayout({ title, eyebrow = "CEO AGENT CONSOLE", children, actions, showHeader = true }: { title: string; eyebrow?: string; children: ReactNode; actions?: ReactNode; showHeader?: boolean }) {
  return (
    <main className="console-page" aria-labelledby="console-page-title">
      {showHeader && <div className="console-page-header">
        <div>
          <p className="eyebrow">{eyebrow}</p>
          <h1 id="console-page-title">{title}</h1>
        </div>
        {actions && <div className="console-page-actions">{actions}</div>}
      </div>}
      {!showHeader && <h1 id="console-page-title" className="sr-only">{title}</h1>}
      {children}
    </main>
  );
}
