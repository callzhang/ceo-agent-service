import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { TrajectoryContent } from "./TrajectoryContent";

describe("TrajectoryContent", () => {
  it("preserves raw JSON evidence including large integer IDs and duplicate keys", async () => {
    const source = '{"summary":"已记录", "message_id":9007199254740993,"value":1,"value":2}';
    render(<TrajectoryContent text={source} />);
    await userEvent.click(screen.getByText("查看完整 JSON"));
    expect(screen.getByText(source)).toBeVisible();
  });
  it("previews long input and lets the reader open and close the complete text", async () => {
    const text = `先修复失败的测试。${"上下文".repeat(500)}结束标记`;
    render(<TrajectoryContent text={text} label="任务输入" plain />);
    const toggle = screen.getByText(/展开任务输入/);
    expect(toggle.closest("details")).not.toHaveAttribute("open");
    expect(screen.getByText(/先修复失败的测试。.*…/)).toBeVisible();
    await userEvent.click(toggle);
    expect(toggle.closest("details")).toHaveAttribute("open");
    expect(screen.getByText(text)).toBeVisible();
    await userEvent.click(toggle);
    expect(toggle.closest("details")).not.toHaveAttribute("open");
  });

  it("shows a structured summary and retains every field in expandable JSON", async () => {
    render(<TrajectoryContent text={JSON.stringify({ outcome: "pr_updated", summary: "已修复测试并更新 PR。", remaining_work: ["等待 CI"], custom: { exact: true } }, null, 2)} />);
    expect(screen.getByText("已修复测试并更新 PR。")).toBeVisible();
    await userEvent.click(screen.getByText("查看完整 JSON"));
    expect(screen.getByText(/"remaining_work":/)).toBeVisible();
    expect(screen.getByText(/"exact": true/)).toBeVisible();
  });
});
