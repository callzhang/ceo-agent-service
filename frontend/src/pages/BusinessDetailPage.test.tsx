import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getResource = vi.hoisted(() => vi.fn());
const command = vi.hoisted(() => vi.fn());
vi.mock("../api/console", () => ({
  command,
  displayValue: (value: unknown) => typeof value === "string" ? value || "未提供" : JSON.stringify(value),
  getResource,
}));

import { BusinessDetailPage } from "./BusinessDetailPage";

function renderDetail(kind: string, endpoint: string, path: string, initialEntry: string, attemptActions = false) {
  render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path={path} element={<BusinessDetailPage kind={kind} endpoint={endpoint} attemptActions={attemptActions} />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("BusinessDetailPage", () => {
  beforeEach(() => {
    getResource.mockResolvedValue({
      item: { status: "done", title: "Business result", runtime: {} },
      meta: { snapshot_at: "2026-08-30T08:00:00Z" },
    });
    command.mockReset();
  });

  it("shows Attempt commands on an Attempt detail", async () => {
    renderDetail("Attempt", "/api/console/history/:id", "/attempts/:attemptId", "/attempts/8337", true);

    expect(await screen.findByText("Business result")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重跑" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "提交反馈" })).toBeInTheDocument();
  });

  it.each([
    ["Meeting Attempt", "/api/console/meeting-attempts/:id", "/meeting-attempts/:runId", "/meeting-attempts/1957"],
    ["OA Approval", "/api/console/oa-approvals/:id", "/oa-approvals/:processInstanceId", "/oa-approvals/process-1"],
  ])("does not expose Attempt commands on %s details", async (kind, endpoint, path, initialEntry) => {
    renderDetail(kind, endpoint, path, initialEntry);

    expect(await screen.findByText("Business result")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重跑" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "提交反馈" })).not.toBeInTheDocument();
  });
});
