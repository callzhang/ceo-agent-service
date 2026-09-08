import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getCodexSession = vi.hoisted(() => vi.fn());

vi.mock("../api/console", () => ({
  displayValue: (value: unknown) => String(value || ""),
  getCodexSession,
}));

import { CodexSessionDetailPage } from "./CodexPages";

describe("CodexSessionDetailPage", () => {
  beforeEach(() => {
    getCodexSession.mockResolvedValue({
      item: {
        available: false,
        message: "本机执行记录不可用",
        events: [],
        related_attempts: [{ id: 8840, status: "needs_human" }],
      },
      meta: { snapshot_at: "2026-09-08T23:00:00Z" },
    });
  });

  it("shows related attempts instead of an empty runtime detail panel when the transcript is unavailable", async () => {
    render(<MemoryRouter initialEntries={["/codex/session-1"]}><Routes><Route path="/codex/:sessionId" element={<CodexSessionDetailPage />} /></Routes></MemoryRouter>);

    expect(await screen.findByRole("heading", { name: "执行记录不可用" })).toBeInTheDocument();
    expect(screen.getByText("本机执行记录不可用")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Attempt #8840" })).toHaveAttribute("href", "/attempts/8840");
    expect(screen.queryByText("Runtime details")).not.toBeInTheDocument();
  });
});
