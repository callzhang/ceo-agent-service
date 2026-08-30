import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { TokenEditor } from "./TokenEditor";


describe("TokenEditor", () => {
  it("keeps the highlighted token layer aligned while the textarea scrolls", () => {
    render(<TokenEditor id="template" label="Template" value={`line 1\n${"line\n".repeat(30)}{{principal}}`} onChange={vi.fn()} />);

    const input = screen.getByRole("textbox", { name: "Template" });
    const highlight = document.querySelector<HTMLElement>(".token-editor-highlight");
    expect(highlight).not.toBeNull();

    Object.defineProperty(input, "scrollTop", { configurable: true, value: 180 });
    Object.defineProperty(input, "scrollLeft", { configurable: true, value: 24 });
    fireEvent.scroll(input);

    expect(highlight?.scrollTop).toBe(180);
    expect(highlight?.scrollLeft).toBe(24);
  });

  it("reports the line and column of an incomplete template token", () => {
    render(<TokenEditor id="template" label="Template" value={"first line\nHello {{principal"} onChange={vi.fn()} />);

    expect(screen.getByRole("alert")).toHaveTextContent("第 2 行，第 7 列");
    expect(screen.getByRole("textbox", { name: "Template" })).toHaveAttribute("aria-invalid", "true");
  });
});
