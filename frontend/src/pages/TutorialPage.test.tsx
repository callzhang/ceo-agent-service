import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getTutorial = vi.hoisted(() => vi.fn());
const runTutorialAction = vi.hoisted(() => vi.fn());
const checkTutorialStep = vi.hoisted(() => vi.fn());
const confirmTutorialStep = vi.hoisted(() => vi.fn());

vi.mock("../api/console", () => ({
  getTutorial,
  runTutorialAction,
  checkTutorialStep,
  confirmTutorialStep,
  displayValue: (value: unknown) => String(value || "未提供"),
}));

import { TutorialPage } from "./TutorialPage";

const wechatStep = {
  step_id: "wechat_connection",
  title: "Connect WeChat",
  status: "not_started",
  summary: "Connect the local personal account and check database access.",
  available_actions: [
    {
      id: "check_wechat_connection",
      label: "Check",
      kind: "check",
    },
    {
      id: "connect_wechat",
      label: "Connect WeChat",
      kind: "run",
    },
  ],
};

describe("TutorialPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getTutorial
      .mockResolvedValueOnce({
        item: { steps: [wechatStep] },
        meta: { snapshot_at: "2026-09-05T00:00:00Z" },
      })
      .mockResolvedValue({
        item: {
          steps: [{
            ...wechatStep,
            status: "needs_action",
            summary: (
              "Enable CEO WeChat Reader in Full Disk Access, then click " +
              "Connect WeChat again."
            ),
          }],
        },
        meta: { snapshot_at: "2026-09-05T00:00:01Z" },
      });
    runTutorialAction.mockResolvedValue({
      ok: true,
      item: {
        next_step_status: "needs_action",
        evidence: { full_disk_access_prompted: true },
      },
      message: (
        "Enable CEO WeChat Reader in Full Disk Access, then click " +
        "Connect WeChat again."
      ),
      meta: { updated_at: "2026-09-05T00:00:01Z" },
    });
  });

  it("runs the two-action WeChat permission flow and refreshes persisted guidance", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><TutorialPage /></MemoryRouter>);

    expect(await screen.findByRole("button", { name: "Check" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Connect WeChat" }));

    expect(runTutorialAction).toHaveBeenCalledWith("connect_wechat");
    expect(screen.queryByRole("button", { name: "Verify" })).not.toBeInTheDocument();
    expect(screen.getAllByText(/Enable CEO WeChat Reader in Full Disk Access/).length).toBeGreaterThan(0);
    await waitFor(() => expect(getTutorial).toHaveBeenCalledTimes(2));
  });
});
