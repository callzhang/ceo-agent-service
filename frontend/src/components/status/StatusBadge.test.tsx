import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusBadge } from "./StatusBadge";

describe("StatusBadge", () => {
  it.each([
    ["ready", "就绪", "status-success"],
    ["unavailable", "不可用", "status-danger"],
    ["failed", "失败", "status-danger"],
    ["pending", "待处理", "status-warning"],
    ["warning", "警告", "status-warning"],
    ["completed", "已完成", "status-success"],
    ["not ready", "未就绪", "status-danger"],
    ["Active", "进行中", "status-success"],
    ["Not active", "未启用", "status-neutral"],
    ["sending", "发送中", "status-progress"],
    ["needs_action", "需要处理", "status-warning"],
    ["wechat_window_unavailable", "微信窗口不可用", "status-danger"],
    ["screen_locked", "屏幕已锁定", "status-danger"],
    ["granted", "已允许", "status-success"],
    ["connected", "已连接", "status-success"],
    ["denied", "已拒绝", "status-danger"],
    ["default", "未设置", "status-warning"],
    ["unsupported", "浏览器不支持", "status-danger"],
    ["unknown", "未知", "status-neutral"],
  ])("maps %s to a stable label and semantic tone", (value, label, tone) => {
    render(<StatusBadge value={value} />);

    const badge = screen.getByText(label);
    expect(badge).toHaveClass("status-badge", tone);
  });
});
