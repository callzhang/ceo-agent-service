import { NavLink, useInRouterContext } from "react-router-dom";

const destinations = [
  ["Agent", "/"],
  ["History", "/history"],
  ["Tasks", "/tasks"],
  ["用户反馈", "/user-feedback"],
  ["Settings", "/settings"],
] as const;

export interface GlobalNavProps {
  activePath?: string;
}

function isActivePath(pathname: string, href: string) {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function GlobalNav({ activePath = window.location.pathname || "/" }: GlobalNavProps) {
  const inRouter = useInRouterContext();
  return (
    <nav className="global-nav" aria-label="主导航">
      <div className="global-nav-track">
        {destinations.map(([label, href]) => {
          const active = isActivePath(activePath, href);
          const className = `global-nav-item${active ? " active" : ""}`;
          if (inRouter) return <NavLink className={className} to={href} aria-current={active ? "page" : undefined} key={href}>{label}</NavLink>;
          return <a className={className} href={href} aria-current={active ? "page" : undefined} key={href}>{label}</a>;
        })}
      </div>
    </nav>
  );
}
