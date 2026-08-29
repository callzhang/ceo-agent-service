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
    expect(screen.getByText(/已保存的凭据已回填/)).toBeInTheDocument();

    await user.clear(input);
    await user.type(input, "new-token");
    expect(onChange).toHaveBeenCalled();
    const toggle = screen.getByRole("button", { name: "显示 API Token" });
    await user.click(toggle);
    expect(input).toHaveAttribute("type", "text");
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(document.activeElement).toBe(toggle);
  });
});
