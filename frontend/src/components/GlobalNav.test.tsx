import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import workbenchStyles from "../styles.css?raw";
import { GlobalNav } from "./GlobalNav";

function styleFor(selector: string) {
  const style = document.createElement("style");
  style.textContent = workbenchStyles;
  document.head.append(style);
  const rule = Array.from(style.sheet?.cssRules ?? [])
    .filter((candidate): candidate is CSSStyleRule => "selectorText" in candidate)
    .find((candidate) => candidate.selectorText === selector);
  style.remove();
  expect(rule, `missing CSS rule for ${selector}`).toBeDefined();
  return rule!.style;
}

describe("GlobalNav", () => {
  it("exposes Agent and every existing console destination", () => {
    render(<GlobalNav />);

    const expected = [
      ["Agent", "/"],
      ["History", "/history"],
      ["Tasks", "/tasks"],
      ["用户反馈", "/user-feedback"],
      ["服务修复", "/service-bugfix-candidates"],
      ["Settings", "/settings"],
    ] as const;
    for (const [name, href] of expected) {
      expect(screen.getByRole("link", { name })).toHaveAttribute("href", href);
    }
    expect(screen.getByRole("link", { name: "Agent" })).toHaveAttribute("aria-current", "page");
  });

  it("centers a consistently sized tab group using the workbench accent", () => {
    const nav = styleFor(".global-nav");
    expect(nav.width).toBe("100%");
    expect(nav.minWidth).toBe("0px");

    const track = styleFor(".global-nav-track");
    expect(track.justifyContent).toBe("center");

    const item = styleFor(".global-nav-item");
    expect(item.justifyContent).toBe("center");
    expect(item.minWidth).toBe("84px");

    const activeItem = styleFor(".global-nav-item.active");
    expect(activeItem.background).toBe("var(--accent)");
    expect(activeItem.borderColor).toBe("var(--accent)");
  });
});
