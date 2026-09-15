import type { ReactNode } from "react";

export function ConsolePageLayout({ title, eyebrow = "CEO AGENT CONSOLE", description, children, actions, showHeader = true, suppressHiddenTitle = false }: { title: string; eyebrow?: string; description?: string; children: ReactNode; actions?: ReactNode; showHeader?: boolean; suppressHiddenTitle?: boolean }) {
  const shouldShowHeader = showHeader && title !== "History";
  return (
    <main className="console-page" aria-labelledby="console-page-title">
      {!shouldShowHeader && !suppressHiddenTitle && <h1 id="console-page-title" className="sr-only">{title}</h1>}
      {shouldShowHeader && <div className="console-page-header">
        <div>
          {!description && <p className="eyebrow">{eyebrow}</p>}
          <h1 id="console-page-title" className={description ? "sr-only" : undefined}>{title}</h1>
          {description && <p className="muted">{description}</p>}
        </div>
        {actions && <div className="console-page-actions">{actions}</div>}
      </div>}
      {children}
    </main>
  );
}
