import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { HistoryChart } from "../../api/console";
import { StackedBarChart } from "./StackedBarChart";

const chart: HistoryChart = {
  labels: ["00:00", "01:00", "02:00", "03:00"],
  series: [
    { name: "reply", data: [2, 0, 1, 3] },
    { name: "task", data: [1, 2, 0, 1] },
    { name: "failed", data: [0, 1, 2, 0] },
  ],
  total: 13,
  range: "2026-08-29 00:00 — 04:00",
};

describe("StackedBarChart", () => {
  it("renders a coloured stacked plot with a legend for every series", () => {
    render(<StackedBarChart chart={chart} />);

    expect(screen.getByRole("img", { name: /24 小时共 13 个事件/ })).toBeInTheDocument();
    expect(document.querySelector(".history-chart-legend")).not.toBeInTheDocument();
    expect(screen.getByTestId("history-chart-brush")).toBeInTheDocument();
  });

  it("uses a chart component with a time-range picker and horizontal brush", () => {
    render(<StackedBarChart chart={chart} range="24h" onRangeChange={vi.fn()} />);

    expect(screen.getByRole("tablist", { name: "事件时间范围" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "24 小时" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "1 周" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "1 个月" })).toBeInTheDocument();
    expect(screen.getByTestId("history-chart-brush")).toBeInTheDocument();
    expect(screen.queryByText("显示范围")).not.toBeInTheDocument();
    expect(screen.queryByText("起始位置")).not.toBeInTheDocument();
  });

  it("shows an explicit empty state when there is no chart data", () => {
    render(<StackedBarChart chart={{ ...chart, labels: [], series: [], total: 0 }} />);
    expect(screen.getByText("暂无事件")).toBeInTheDocument();
    expect(screen.getByText("- events")).toBeInTheDocument();
    expect(screen.queryByTestId("stacked-bar-segment")).not.toBeInTheDocument();
  });

  it("distinguishes the initial loading state from an empty result", () => {
    render(<StackedBarChart loading />);
    expect(screen.getByRole("status")).toHaveTextContent("正在加载…");
    expect(screen.getByText("- events")).toBeInTheDocument();
  });

  it("shows feedback while refreshing an existing range", () => {
    render(<StackedBarChart chart={chart} loading />);
    expect(screen.getByRole("status")).toHaveTextContent("正在更新…");
  });
});
