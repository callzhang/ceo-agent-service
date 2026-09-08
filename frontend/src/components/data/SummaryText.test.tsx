import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SummaryText } from "./SummaryText";

describe("SummaryText", () => {
  beforeEach(() => {
    vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(48);
    vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(48);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("does not offer expansion when the displayed text fits", () => {
    render(<SummaryText value="短文本" lines={3} />);

    expect(screen.queryByRole("button", { name: "展开详情" })).not.toBeInTheDocument();
  });

  it("offers expansion only after the displayed text is clipped", async () => {
    vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(96);
    const user = userEvent.setup();
    render(<SummaryText value="一段很长的文本" lines={3} />);

    await user.click(screen.getByRole("button", { name: "展开详情" }));

    expect(screen.getByRole("button", { name: "收起" })).toHaveAttribute("aria-expanded", "true");
  });
});
