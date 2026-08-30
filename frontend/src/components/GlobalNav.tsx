import { useEffect, useRef } from "react";
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
  const activeLink = useRef<HTMLAnchorElement>(null);
  const pageLabel = destinations.find(([, href]) => isActivePath(activePath, href))?.[0] || "CEO Agent";
  useEffect(() => {
    if (typeof activeLink.current?.scrollIntoView === "function") {
      activeLink.current.scrollIntoView({ behavior: "auto", block: "nearest", inline: "center" });
    }
  }, [activePath]);
  return (
    <nav className="global-nav" aria-label="主导航">
      <a className="global-brand" href="/history">
        <span className="global-brand-mark" aria-hidden="true" />
        <span><strong>{pageLabel}</strong><small>Local audit console</small></span>
      </a>
      <div className="global-nav-track">
        {destinations.map(([label, href]) => {
          const active = isActivePath(activePath, href);
          const className = `global-nav-item${active ? " active" : ""}`;
          if (inRouter) return <NavLink ref={active ? activeLink : undefined} className={className} to={href} aria-current={active ? "page" : undefined} key={href}>{label}</NavLink>;
          return <a ref={active ? activeLink : undefined} className={className} href={href} aria-current={active ? "page" : undefined} key={href}>{label}</a>;
        })}
      </div>
    </nav>
  );
}
