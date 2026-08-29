import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getSettings = vi.hoisted(() => vi.fn());
const saveSettings = vi.hoisted(() => vi.fn());
const getStatus = vi.hoisted(() => vi.fn());
const listAttention = vi.hoisted(() => vi.fn());

vi.mock("../api/console", () => ({ getSettings, saveSettings, getStatus, listAttention, displayValue: (value: unknown) => typeof value === "string" ? value || "未提供" : JSON.stringify(value) || "未提供" }));

import { SettingsPage } from "./SettingsPage";

function renderSettings(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><Routes><Route path="/settings" element={<SettingsPage />} /></Routes></MemoryRouter>);
}

describe("SettingsPage", () => {
  beforeEach(() => {
    getSettings.mockResolvedValue({
      item: {
        section: "configuration",
        fields: { USER_ALIAS: "磊哥" },
        groups: [{ name: "Runtime & Identity", items: [{ key: "USER_ALIAS", value: "磊哥", description: "用户别名", editable: true }] }],
        compatibility: [],
      },
      meta: { snapshot_at: "2026-08-29T00:00:00Z" },
    });
  });

  it("renders configuration groups instead of flattening the settings DTO", async () => {
    renderSettings("/settings?tab=configuration");

    expect(await screen.findByRole("heading", { name: "Runtime & Identity" })).toBeInTheDocument();
    expect(screen.getByLabelText("USER_ALIAS")).toHaveValue("磊哥");
    expect(screen.getByText("用户别名")).toBeInTheDocument();
  });

  it("keeps prompt and audit editor values visible while highlighting template tokens", async () => {
    getSettings.mockResolvedValueOnce({ item: { section: "prompts", fields: { developer_template: "hello {{principal}}" }, preview: { developer: "hello 磊哥" } }, meta: { snapshot_at: "2026-08-29T00:00:00Z" } });
    renderSettings("/settings?tab=prompts&prompt=developer");

    expect(await screen.findByRole("textbox", { name: "Template" })).toHaveValue("hello {{principal}}");
    expect(screen.getByText("{{principal}}", { selector: "mark" })).toBeInTheDocument();
  });
});
