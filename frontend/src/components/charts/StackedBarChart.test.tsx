import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

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

    expect(screen.getByRole("img", { name: /最近 24 小时共 13 个事件/ })).toBeInTheDocument();
    expect(screen.getByText("reply")).toBeInTheDocument();
    expect(screen.getByText("task")).toBeInTheDocument();
    expect(screen.getByText("failed")).toBeInTheDocument();
    expect(screen.getAllByTestId("stacked-bar-segment")).toHaveLength(8);
  });

  it("zooms the visible range without changing the source chart", async () => {
    render(<StackedBarChart chart={chart} minWindow={2} />);

    const zoom = screen.getByRole("slider", { name: "图表显示小时数" });
    expect(zoom).toHaveValue("4");
    fireEvent.change(zoom, { target: { value: "2" } });
    expect(screen.getByText("显示 2 小时")).toBeInTheDocument();
    expect(screen.getAllByTestId("stacked-bar-column")).toHaveLength(2);
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
});
