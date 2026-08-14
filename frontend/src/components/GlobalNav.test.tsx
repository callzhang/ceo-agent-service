import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { GlobalNav } from "./GlobalNav";

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
});
