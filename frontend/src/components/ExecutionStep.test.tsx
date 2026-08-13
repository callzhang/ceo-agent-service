import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ExecutionStep } from "./ExecutionStep";

describe("ExecutionStep", () => {
  it("exposes its state label as a narrowly scoped status announcement", () => {
    render(<ExecutionStep kind="tool" status="aborted" payload={{ tool: "read", summary: "任务已结束，未收到工具完成事件。" }} />);

    expect(screen.getByRole("status")).toHaveTextContent("已中止");
  });
});
