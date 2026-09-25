import { act, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const listHistory = vi.hoisted(() => vi.fn());
const getHistoryChart = vi.hoisted(() => vi.fn());
const listHistoryTypes = vi.hoisted(() => vi.fn());
vi.mock("../api/console", () => ({
  listHistory,
  getHistoryChart,
  listHistoryTypes,
  displayValue: (value: unknown) => typeof value === "string" ? value : JSON.stringify(value),
}));

import { HistoryPage, isStatusCode, previewText, splitStatusCode } from "./HistoryPage";

describe("History previews", () => {
  it("names a link instead of spelling out its query string", () => {
    const raw = "日程：ALE质检流程优化 入会：https://shanhui.dingtalk.com/meetingFromCalendar?uniqueId=Wg5T3JEZzY5&corpId=ding8ffc70";

    expect(previewText(raw)).toBe("日程：ALE质检流程优化 入会：[链接]");
  });

  it("keeps the text of a markdown link and drops its target", () => {
    const raw = "见 [会议纪要](https://alidocs.dingtalk.com/i/nodes/abc?corpId=x) 第二节";

    expect(previewText(raw)).toBe("见 会议纪要 第二节");
  });

  it("names a markdown link whose text is itself a URL", () => {
    const raw = "群公告 [dingtalk://dingtalkclient/action/openapp](dingtalk://dingtalkclient/action/openapp?corpId=x)";

    expect(previewText(raw)).toBe("群公告 [链接]");
  });

  it("collapses the whitespace a pasted message carries", () => {
    expect(previewText("已认领，\n\n  等待执行器领取。")).toBe("已认领， 等待执行器领取。");
  });

  it("separates a status code appended to a sentence", () => {
    expect(splitStatusCode("已认领，等待执行器领取。 retry_after_claude_credential_fix")).toEqual({
      copy: "已认领，等待执行器领取。",
      code: "retry_after_claude_credential_fix",
    });
    expect(splitStatusCode("waiting_fast_path_unread_backoff")).toEqual({
      copy: "",
      code: "waiting_fast_path_unread_backoff",
    });
    expect(splitStatusCode("已完成同步")).toEqual({ copy: "已完成同步", code: "" });
  });

  it("tells the service's own status codes from what a person wrote", () => {
    expect(isStatusCode("waiting_fast_path_unread_backoff")).toBe(true);
    expect(isStatusCode("codex_result_invalid; audit retry attempts exhausted")).toBe(true);
    expect(isStatusCode("已认领，等待执行器领取。")).toBe(false);
    expect(isStatusCode("Sent the reply")).toBe(false);
  });
});

describe("HistoryPage", () => {
  beforeEach(() => {
    listHistory.mockReset();
    getHistoryChart.mockReset();
    listHistoryTypes.mockReset();
    listHistoryTypes.mockResolvedValue([
      { value: "queue", label: "队列" },
      { value: "dingtalk", label: "钉钉消息" },
      { value: "calendar", label: "日历邀请" },
      { value: "email_unsubscribe", label: "邮件退订" },
      { value: "email_action", label: "邮件动作" },
      { value: "task", label: "Task" },
      { value: "scheduled_command", label: "定时命令" },
    ]);
    listHistory.mockResolvedValue({
      items: [{
        id: "836",
        occurred_at: "2026-08-29T00:00:00Z",
        title: "客户项目",
        type: "task",
        status: "sent",
        summary: "已经完成客户项目的任务同步。",
        actor: "Task Agent",
        detail_url: "/tasks/836",
        kind: "task",
        input: "请同步客户项目状态",
        output: "已完成同步",
        action: "task_update",
      }],
      meta: { page: 1, page_size: 20, total: 40, next_cursor: "2", has_more: true, snapshot_at: "2026-08-29T00:00:00Z" },
      chart: { labels: ["00:00", "01:00"], series: [{ name: "reply", data: [1, 0] }], total: 1, range: "24h" },
    });
    getHistoryChart.mockResolvedValue({ labels: ["00:00", "01:00"], series: [{ name: "reply", data: [1, 0] }], total: 1, range: "24h" });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("preserves the legacy history workspace hierarchy", async () => {
    render(<MemoryRouter><HistoryPage /></MemoryRouter>);

    expect(await screen.findByRole("region", { name: "History workspace" })).toBeInTheDocument();
    expect(screen.queryByText("CEO AGENT CONSOLE")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "状态：全部状态" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "任务类型：全部类型" })).toBeInTheDocument();
    // Derek 2026-09-25: the quick status chips are gone; the menus do the filtering.
    expect(screen.queryByLabelText("快速状态筛选")).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Recent 24 hour events" })).toBeInTheDocument();
    expect(screen.getByRole("article", { name: /客户项目/ })).toBeInTheDocument();
    expect(screen.getByText("问")).toBeInTheDocument();
    expect(screen.getByText("答")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "分页导航" })).toBeInTheDocument();
  });

  it("changes the event chart time range through the history query", async () => {
    const user = (await import("@testing-library/user-event")).default.setup();
    render(<MemoryRouter><HistoryPage /></MemoryRouter>);

    await user.click(await screen.findByRole("tab", { name: "1 周" }));
    expect(getHistoryChart).toHaveBeenLastCalledWith("1w", expect.anything());
  });

  it("names statuses in Chinese and filters by several at once", async () => {
    const user = (await import("@testing-library/user-event")).default.setup();
    render(<MemoryRouter><HistoryPage /></MemoryRouter>);

    await user.click(await screen.findByRole("button", { name: "状态：全部状态" }));
    const menu = screen.getByRole("group", { name: "状态" });
    expect(menu).toHaveTextContent("需要人工处理");
    expect(menu).not.toHaveTextContent("needs_human");
    await user.click(screen.getByRole("checkbox", { name: "失败" }));
    await user.click(screen.getByRole("checkbox", { name: "需要人工处理" }));

    expect(listHistory).toHaveBeenLastCalledWith(expect.objectContaining({ status: "failed,needs_human" }), expect.anything());
    expect(screen.getByRole("button", { name: "状态：失败、需要人工处理" })).toBeInTheDocument();
  });

  it("offers the service's History types as checkboxes and filters by several", async () => {
    const user = (await import("@testing-library/user-event")).default.setup();
    render(<MemoryRouter><HistoryPage /></MemoryRouter>);

    await screen.findByRole("article", { name: /客户项目/ });
    await user.click(screen.getByRole("button", { name: "任务类型：全部类型" }));
    expect(screen.getByRole("checkbox", { name: "日历邀请" })).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "Reply" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "邮件退订" }));
    await user.click(screen.getByRole("checkbox", { name: "日历邀请" }));

    expect(listHistory).toHaveBeenLastCalledWith(
      expect.objectContaining({ object_type: "email_unsubscribe,calendar" }),
      expect.anything(),
    );
  });

  it("offers no type to tick when the type list cannot be read", async () => {
    const user = (await import("@testing-library/user-event")).default.setup();
    listHistoryTypes.mockRejectedValue(new Error("unavailable"));
    render(<MemoryRouter><HistoryPage /></MemoryRouter>);

    expect(await screen.findByRole("article", { name: /客户项目/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "任务类型：全部类型" }));
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
  });

  it("names each row's type with the service's label", async () => {
    listHistory.mockResolvedValue({
      items: [
        { id: "85117", occurred_at: "2026-09-25 08:09:21", title: "分类新邮件", type: "scheduled_command", status: "failed", summary: "scheduled_task_service_command_failed: imap timeout", actor: "Scheduled task", detail_url: "/scheduled-tasks?id=10", kind: "scheduled_run", input: "", output: "" },
        { id: "3", occurred_at: "2026-09-25 08:09:00", title: "季度复盘", type: "email_action", status: "done", summary: "移动到「工作」", actor: "a@example.com", detail_url: "/email?tab=all&selected=9001", kind: "email_action", input: "", output: "" },
      ],
      meta: { page: 1, page_size: 20, total: 2, next_cursor: "", has_more: false, snapshot_at: "2026-09-25T08:10:00Z" },
    });
    render(<MemoryRouter><HistoryPage /></MemoryRouter>);

    const run = await screen.findByRole("article", { name: "分类新邮件" });
    expect(run).toHaveTextContent("定时命令");
    expect(run).toHaveTextContent("结果");
    expect(screen.getByRole("article", { name: "季度复盘" })).toHaveTextContent("邮件动作");
    expect(screen.getByRole("article", { name: "季度复盘" })).toHaveTextContent("移动到「工作」");
  });

  it("shows 全部 for a retired type in the address", async () => {
    render(<MemoryRouter initialEntries={["/history?object_type=replay"]}><HistoryPage /></MemoryRouter>);

    await screen.findByRole("article", { name: /客户项目/ });
    expect(await screen.findByRole("button", { name: "任务类型：全部类型" })).toBeInTheDocument();
  });

  it("shows an explicit zero when the current failure filter is empty", async () => {
    listHistory.mockResolvedValueOnce({
      items: [],
      meta: { page: 1, page_size: 20, total: 0, next_cursor: "", has_more: false, snapshot_at: "2026-09-09T00:00:00Z" },
    });

    render(<MemoryRouter initialEntries={["/history?status=failed"]}><HistoryPage /></MemoryRouter>);

    expect(await screen.findByText("共 0 条")).toBeInTheDocument();
  });

  it("refreshes visible queue history so completed work does not remain processing", async () => {
    vi.useFakeTimers();
    listHistory
      .mockResolvedValueOnce({
        items: [{ id: "processing-1", occurred_at: "2026-09-07T07:00:00Z", title: "正在执行的任务", type: "queue", status: "processing", summary: "执行中", actor: "Reply task", detail_url: "/workers", kind: "queue" }],
        meta: { page: 1, page_size: 20, total: 1, next_cursor: "", has_more: false, snapshot_at: "2026-09-07T07:00:00Z" },
      })
      .mockResolvedValueOnce({
        items: [{ id: "done-1", occurred_at: "2026-09-07T07:00:10Z", title: "已完成的任务", type: "queue", status: "done", summary: "已完成", actor: "Reply task", detail_url: "/workers", kind: "queue" }],
        meta: { page: 1, page_size: 20, total: 1, next_cursor: "", has_more: false, snapshot_at: "2026-09-07T07:00:10Z" },
    });

    render(<MemoryRouter><HistoryPage /></MemoryRouter>);
    await act(async () => {});
    expect(screen.getByRole("article", { name: "正在执行的任务" })).toBeInTheDocument();

    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });

    expect(screen.getByRole("article", { name: "已完成的任务" })).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: "正在执行的任务" })).not.toBeInTheDocument();
    expect(listHistory).toHaveBeenCalledTimes(2);
  });
});
