import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ResponsiveDataList } from "./ResponsiveDataList";

describe("ResponsiveDataList", () => {
  it("shows all primary fields in a mobile-friendly record card", () => {
    render(
      <ResponsiveDataList
        ariaLabel="任务列表"
        columns={[
          { key: "project", label: "Project" },
          { key: "owner", label: "Owner" },
          { key: "progress", label: "Progress" },
        ]}
        rows={[{ id: "1", project: "客户项目", owner: "Shawn", progress: "3/5" }]}
        renderCell={(row, key) => String(row[key])}
      />,
    );

    expect(screen.getByRole("table", { name: "任务列表" })).toBeInTheDocument();
    expect(screen.getByText("客户项目")).toBeInTheDocument();
    expect(screen.getByText("Owner")).toBeInTheDocument();
    expect(screen.getByText("Shawn")).toBeInTheDocument();
  });

  it("renders loading, empty and error states explicitly", () => {
    const props = {
      ariaLabel: "数据",
      columns: [{ key: "value", label: "Value" }],
      rows: [],
      renderCell: (row: { value: string }) => row.value,
    };

    const { rerender } = render(<ResponsiveDataList {...props} state="loading" />);
    expect(screen.getByRole("status")).toHaveTextContent("正在加载");

    rerender(<ResponsiveDataList {...props} state="empty" emptyMessage="暂无数据" />);
    expect(screen.getByText("暂无数据")).toBeInTheDocument();

    rerender(<ResponsiveDataList {...props} state="error" errorMessage="加载失败" />);
    expect(screen.getByRole("alert")).toHaveTextContent("加载失败");
  });

  it("reveals long content only for the selected row", async () => {
    const user = userEvent.setup();
    render(
      <ResponsiveDataList
        ariaLabel="事实"
        columns={[{ key: "description", label: "Description" }]}
        rows={[{ id: "a", description: "第一条很长的事实内容" }, { id: "b", description: "第二条很长的事实内容" }]}
        renderCell={(row) => <span>{row.description}</span>}
        expandable
      />,
    );

    const buttons = screen.getAllByRole("button", { name: "展开详情" });
    expect(buttons).toHaveLength(2);
    await user.click(buttons[0]);
    expect(buttons[0]).toHaveAttribute("aria-expanded", "true");
    expect(buttons[1]).toHaveAttribute("aria-expanded", "false");
  });
});
