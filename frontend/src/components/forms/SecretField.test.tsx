import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SecretField } from "./SecretField";

describe("SecretField", () => {
  it("prefills a saved secret and exposes an accessible toggle", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<SecretField id="token" label="API Token" configured value="saved-token" onChange={onChange} />);

    const input = screen.getByLabelText("API Token");
    expect(input).toHaveValue("saved-token");
    expect(input).toHaveAttribute("type", "password");
    expect(screen.getByRole("button", { name: "显示 API Token" })).toHaveAttribute("aria-pressed", "false");

    await user.clear(input);
    await user.type(input, "new-token");
    expect(onChange).toHaveBeenCalled();
    const toggle = screen.getByRole("button", { name: "显示 API Token" });
    await user.click(toggle);
    expect(input).toHaveAttribute("type", "text");
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(toggle).toHaveAccessibleName("隐藏 API Token");
    expect(toggle).toHaveAttribute("aria-controls", "token");
    expect(document.activeElement).toBe(toggle);
    await user.click(toggle);
    expect(input).toHaveAttribute("type", "password");
    expect(toggle).toHaveAttribute("aria-pressed", "false");
  });
});
