import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { FeedbackItem } from "../api/console";
import { FeedbackDrawer } from "./FeedbackDrawer";

function feedback(feedbackKey: string, summary: string, references: FeedbackItem["references"] = []): FeedbackItem {
  return {
    id: feedbackKey,
    feedback_key: feedbackKey,
    attempt_id: "attempt-1",
    status: "pending",
    processing_status: "pending",
    rating: "",
    comment: "",
    context: "",
    created_at: "2026-08-29T00:00:00Z",
    summary,
    references,
    batch_id: "",
    processing_task_id: "",
  };
}

const baseProps = () => ({
  open: true,
  pending: [feedback("fb-1", "修复任务状态", [{ label: "查看 Attempt", route: "/attempts/attempt-1" }]), feedback("fb-2", "补充测试")],
  loading: false,
  error: "",
  selected: new Set<string>(),
  submitting: false,
  onToggle: vi.fn(),
  onSelectAll: vi.fn(),
  onImport: vi.fn(),
  onClose: vi.fn(),
});

describe("FeedbackDrawer", () => {
  it.each([
    ["loading", { loading: true }, "正在加载反馈…"],
    ["empty", { pending: [] }, "当前没有待处理反馈"],
    ["error", { error: "加载失败" }, "加载失败"],
  ])("renders %s state", (_name, overrides, message) => {
    render(<FeedbackDrawer {...baseProps()} {...overrides} />);
    expect(screen.getByText(message)).toBeInTheDocument();
  });

  it("renders persisted summaries and only API-provided reference links", () => {
    render(<FeedbackDrawer {...baseProps()} />);
    expect(screen.getByText("修复任务状态")).toBeInTheDocument();
    expect(screen.getByText("补充测试")).toBeInTheDocument();
    expect(screen.getAllByText(/评分：未提供/)).toHaveLength(2);
    expect(screen.getAllByText("2026-08-29T00:00:00Z")).toHaveLength(2);
    expect(screen.getByRole("link", { name: "查看 Attempt" })).toHaveAttribute("href", "/attempts/attempt-1");
    expect(screen.queryByRole("link", { name: /反馈|任务|会话/ })).toBeNull();
  });

  it("supports single-select, select-all and deselect", async () => {
    const user = userEvent.setup();
    const props = baseProps();
    const { rerender } = render(<FeedbackDrawer {...props} />);
    await user.click(screen.getByRole("checkbox", { name: "选择反馈 修复任务状态" }));
    expect(props.onToggle).toHaveBeenCalledWith("fb-1");
    await user.click(screen.getByRole("checkbox", { name: "全选反馈" }));
    expect(props.onSelectAll).toHaveBeenCalledOnce();
    rerender(<FeedbackDrawer {...props} selected={new Set(["fb-1", "fb-2"])} />);
    expect(screen.getByRole("checkbox", { name: "全选反馈" })).toBeChecked();
    await user.click(screen.getByRole("checkbox", { name: "选择反馈 修复任务状态" }));
    expect(props.onToggle).toHaveBeenLastCalledWith("fb-1");
  });

  it("disables import until there is a selection and while submitting", () => {
    const props = baseProps();
    render(<FeedbackDrawer {...props} />);
    expect(screen.getByRole("button", { name: "导入并开始 brainstorm" })).toBeDisabled();
    render(<FeedbackDrawer {...props} selected={new Set(["fb-1"])} submitting />);
    expect(screen.getByRole("button", { name: "导入中…" })).toBeDisabled();
  });

  it("ignores selected keys that are not in the pending API rows", () => {
    const props = baseProps();
    render(<FeedbackDrawer {...props} selected={new Set(["stale-key"])} />);
    expect(screen.getByText("已选 0 项")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "全选反馈" })).not.toBePartiallyChecked();
    expect(screen.getByRole("button", { name: "导入并开始 brainstorm" })).toBeDisabled();
  });

  it("keeps hook order stable when opening and closing", () => {
    const props = baseProps();
    const { rerender } = render(<FeedbackDrawer {...props} open={false} />);
    rerender(<FeedbackDrawer {...props} open />);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    rerender(<FeedbackDrawer {...props} open={false} />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
