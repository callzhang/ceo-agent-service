import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { SecretField } from "./SecretField";

describe("SecretField", () => {
  it("does not render a saved secret and exposes an accessible toggle for new input", async () => {
    const user = userEvent.setup();
    render(<SecretField id="token" label="API Token" configured />);

    const input = screen.getByLabelText("API Token");
    expect(input).toHaveValue("");
    expect(screen.getByText("已保存的凭据不会回填")).toBeInTheDocument();

    await user.type(input, "new-token");
    const toggle = screen.getByRole("button", { name: "显示 API Token" });
    await user.click(toggle);
    expect(input).toHaveAttribute("type", "text");
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(document.activeElement).toBe(toggle);
  });
});
