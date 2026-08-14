const destinations = [
  ["Agent", "/"],
  ["History", "/history"],
  ["Tasks", "/tasks"],
  ["用户反馈", "/user-feedback"],
  ["服务修复", "/service-bugfix-candidates"],
  ["Settings", "/settings"],
] as const;

export function GlobalNav() {
  return (
    <nav className="global-nav" aria-label="主导航">
      <div className="global-nav-track">
        {destinations.map(([label, href], index) => (
          <a
            className={`global-nav-item${index === 0 ? " active" : ""}`}
            href={href}
            aria-current={index === 0 ? "page" : undefined}
            key={href}
          >
            {label}
          </a>
        ))}
      </div>
    </nav>
  );
}
