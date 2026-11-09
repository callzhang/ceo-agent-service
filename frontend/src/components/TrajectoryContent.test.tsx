import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { TrajectoryContent } from "./TrajectoryContent";

describe("TrajectoryContent", () => {
  it("renders long Markdown, empty collections and exact exponent values without toggles", () => {
    render(<TrajectoryContent text={'{"summary":"**检查结果**\\n\\n[查看 PR](https://github.com/example/repo/pull/1)","work":[],"details":{},"value":1e400,"nullable":null}'} collapsed />);
    expect(screen.getByText("检查结果").tagName).toBe("STRONG");
    expect(screen.getByRole("link", { name: "查看 PR" })).toHaveAttribute("href", "https://github.com/example/repo/pull/1");
    for (const text of ["[]", "{}", "1e400", "null"]) expect(screen.getByText(text)).toBeVisible();
    expect(document.querySelector("details")).toBeNull();
  });
  it("preserves exact numeric display and original JSON including duplicate keys", () => {
    const source = '{"summary":"已记录", "message_id":9007199254740993,"value":1,"value":2}';
    render(<TrajectoryContent text={source} />);
    expect(screen.getByText("9007199254740993")).toBeVisible();
    expect(screen.getAllByText("value")).toHaveLength(2);
    expect(screen.getByText("1")).toBeVisible();
    expect(screen.getByText("2")).toBeVisible();
    expect(decodeURIComponent(screen.getByRole("link", { name: "下载原始 JSON" }).getAttribute("href")!.split(",").slice(1).join(","))).toBe(source);
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

  it("renders every field and nested list without collapsed JSON", () => {
    render(<TrajectoryContent text={JSON.stringify({ outcome: "pr_updated", summary: "已修复测试并更新 PR。", remaining_work: ["等待 CI"], custom: { exact: true } }, null, 2)} />);
    expect(screen.getByText("已修复测试并更新 PR。")).toBeVisible();
    expect(screen.getByText("remaining_work")).toBeVisible();
    expect(screen.getByText("等待 CI")).toBeVisible();
    expect(screen.getByText("exact")).toBeVisible();
    expect(screen.getByText("true")).toBeVisible();
    expect(document.querySelector("details")).toBeNull();
  });
});
