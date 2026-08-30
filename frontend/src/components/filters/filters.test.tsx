import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { FilterBar } from "./FilterBar";
import { FilterChip } from "./FilterChip";
import { SearchField } from "./SearchField";
import { SelectField } from "./SelectField";

describe("shared filter controls", () => {
  it("renders a labelled search field with conditional clear and Escape support", async () => {
    const user = userEvent.setup();
    const onClear = vi.fn();
    const { rerender } = render(<SearchField id="search" label="Search tasks" value="" onChange={vi.fn()} onClear={onClear} />);

    expect(screen.getByRole("searchbox", { name: "Search tasks" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Clear Search tasks" })).not.toBeInTheDocument();

    rerender(<SearchField id="search" label="Search tasks" value="client" onChange={vi.fn()} onClear={onClear} />);
    const input = screen.getByRole("searchbox", { name: "Search tasks" });
    expect(screen.getByRole("button", { name: "Clear Search tasks" })).toBeInTheDocument();
    await user.click(input);
    fireEvent.keyDown(input, { key: "Escape" });
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it("renders a native select with a visible label and selected option", () => {
    render(<SelectField id="status" label="Status" value="processing" onChange={vi.fn()} options={[{ value: "", label: "All status" }, { value: "processing", label: "Processing" }]} />);

    expect(screen.getByRole("combobox", { name: "Status" })).toHaveValue("processing");
    expect(screen.getByText("Status")).toBeInTheDocument();
  });

  it("wraps controls in a shared filter bar", () => {
    render(<FilterBar><span>filters</span></FilterBar>);

    expect(screen.getByTestId("filter-bar")).toHaveTextContent("filters");
  });

  it("exposes chip selection semantics and an optional count", async () => {
    const onClick = vi.fn();
    const user = userEvent.setup();
    render(<FilterChip label="Failed" count={4} active onClick={onClick} />);

    const chip = screen.getByRole("button", { name: "Failed 4" });
    expect(chip).toHaveAttribute("aria-pressed", "true");
    await user.click(chip);
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
