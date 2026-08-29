import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusBadge } from "./StatusBadge";

describe("StatusBadge", () => {
  it.each([
    ["ready", "就绪", "status-success"],
    ["unavailable", "不可用", "status-danger"],
    ["failed", "失败", "status-danger"],
    ["pending", "待处理", "status-warning"],
    ["completed", "已完成", "status-success"],
    ["not ready", "未就绪", "status-danger"],
    ["Active", "进行中", "status-success"],
    ["Not active", "未启用", "status-neutral"],
    ["sending", "发送中", "status-progress"],
    ["needs_action", "需要处理", "status-warning"],
    ["wechat_window_unavailable", "微信窗口不可用", "status-danger"],
    ["unknown", "未知", "status-neutral"],
  ])("maps %s to a stable label and semantic tone", (value, label, tone) => {
    render(<StatusBadge value={value} />);

    const badge = screen.getByText(label);
    expect(badge).toHaveClass("status-badge", tone);
  });
});
