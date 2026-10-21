import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DashboardPage } from "./DashboardPage";
import * as api from "../api/console";
import { getDashboardQueue } from "../api/dashboard";

vi.mock("../api/console", async () => ({
  ...(await vi.importActual("../api/console")),
  getStatus: vi.fn(),
  listAttention: vi.fn(),
}));
vi.mock("../api/dashboard", () => ({ getDashboardQueue: vi.fn() }));
vi.mock("./HistoryPage", () => ({
  HistoryPage: () => (
    <section aria-label="保留执行历史">History chart and filters</section>
  ),
}));
const meta = {
  page: 1,
  page_size: 20,
  total: 0,
  next_cursor: "",
  has_more: false,
  snapshot_at: "2026-10-20T17:00:00Z",
};
const attention = {
  statuses: ["failed"],
  id: "issue",
  category: "Service error",
  root_cause: "read_failed",
  context: "Meeting",
  severity: "error",
  count: 1,
  summary: "结论未写入",
  detail_label: "错误",
  detail: "依赖连接失败",
  error: "read_failed",
  updated_at: meta.snapshot_at,
  links: [],
};
const row = {
  id: "reply_tasks:1",
  source: "消息 / 审批",
  title: "处理申请",
  status: "pending",
  retry_number: 2,
  agent: "",
  runtime_kind: "",
  model: "",
  stage: "pending",
  started_at: "",
  updated_at: meta.snapshot_at,
  created_at: meta.snapshot_at,
  available_at: "2026-10-20T17:05:00Z",
  error: "连接超时",
  detail_url: "",
};
function show() {
  return render(
    <MemoryRouter>
      <DashboardPage />
    </MemoryRouter>,
  );
}
beforeEach(() => {
  vi.mocked(api.getStatus).mockResolvedValue({
    item: {
      service: { ok: true, state: "running", pid: "123", detail: "loaded" },
      system_health: { state: "healthy", detail: "健康" },
      connectors: { dingtalk: { state: "ready" } },
    },
    meta: { snapshot_at: meta.snapshot_at },
  } as never);
  vi.mocked(api.listAttention).mockResolvedValue({ items: [attention], meta });
  vi.mocked(getDashboardQueue).mockResolvedValue({
    items: [row],
    counts: { active: 0, queued: 1 },
    meta: { ...meta, total: 1 },
  });
});
afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
});
describe("Dashboard", () => {
  it("shows Service first, defaults to Failed and retains History without a visible page heading", async () => {
    show();
    expect(
      await screen.findByRole("tab", { name: "Failed 1" }),
    ).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Service 运行中")).toBeInTheDocument();
    expect(screen.getByLabelText("保留执行历史")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Dashboard" })).toHaveClass(
      "sr-only",
    );
    expect(screen.getByText("结论未写入")).toBeInTheDocument();
  });
  it("selects Active only after a successful empty Attention read", async () => {
    vi.mocked(api.listAttention).mockResolvedValue({ items: [], meta });
    show();
    expect(
      await screen.findByRole("tab", { name: "Failed 0" }),
    ).toHaveAttribute("aria-selected", "false");
    expect(screen.getByRole("tab", { name: "Active tasks 0" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });
  it("retains an explicit Queued selection on refresh, shows retry ordinal and opens metadata details", async () => {
    const user = userEvent.setup();
    show();
    await screen.findByRole("tab", { name: "Failed 1" });
    await user.click(screen.getByRole("tab", { name: "Queued 1" }));
    expect(await screen.findByText("即将第 2 次 retry")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "处理申请" }));
    expect(screen.getByText("连接超时")).toBeInTheDocument();
    vi.mocked(api.listAttention).mockResolvedValue({ items: [], meta });
    await user.click(screen.getByRole("button", { name: "刷新 Dashboard" }));
    expect(
      await screen.findByRole("tab", { name: "Failed 0" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Queued 1" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });
  it("keeps read errors visible instead of treating unavailable Attention as zero", async () => {
    vi.mocked(api.listAttention).mockRejectedValue(
      new Error("Attention unavailable"),
    );
    show();
    expect(
      await screen.findByText("Attention unavailable"),
    ).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Failed —" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });
  it("makes queue failure visible without stale rows looking refreshed", async () => {
    vi.mocked(api.listAttention).mockResolvedValue({ items: [], meta });
    vi.mocked(getDashboardQueue).mockRejectedValue(
      new Error("Queue unavailable"),
    );
    show();
    expect(await screen.findByText("Queue unavailable")).toBeInTheDocument();
  });
});

it("preserves an open queue detail across a successful refresh", async () => {
  const user = userEvent.setup();
  vi.mocked(api.listAttention).mockResolvedValue({ items: [], meta });
  show();
  await screen.findByRole("tab", { name: "Active tasks 0" });
  await user.click(screen.getByRole("button", { name: "处理申请" }));
  expect(screen.getByText("连接超时")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "刷新 Dashboard" }));
  await screen.findByRole("tab", { name: "Active tasks 0" });
  expect(screen.getByText("连接超时")).toBeInTheDocument();
});

it("returns to a valid page when a queued page disappears after refresh", async () => {
  const user = userEvent.setup();
  vi.mocked(api.listAttention).mockResolvedValue({ items: [], meta });
  let total = 21;
  vi.mocked(getDashboardQueue).mockImplementation(async (state, page = 1) => ({
    items:
      page === 1
        ? [row]
        : total === 21
          ? [{ ...row, id: "reply_tasks:21", title: "末页任务" }]
          : [],
    counts: { active: 0, queued: total },
    meta: { ...meta, page, total, has_more: page * 20 < total },
  }));
  show();
  await screen.findByRole("tab", { name: "Active tasks 0" });
  await user.click(screen.getByRole("tab", { name: "Queued 21" }));
  await user.click(screen.getByRole("button", { name: "下一页" }));
  await screen.findByRole("button", { name: "末页任务" });
  total = 1;
  await user.click(screen.getByRole("button", { name: "刷新 Dashboard" }));
  expect(
    await screen.findByRole("button", { name: "处理申请" }),
  ).toBeInTheDocument();
  expect(screen.queryByText("当前没有排队任务")).not.toBeInTheDocument();
});

it("does not present creation time as an unrecorded eligible time", async () => {
  vi.mocked(getDashboardQueue).mockResolvedValue({
    items: [{ ...row, available_at: "" }],
    counts: { active: 0, queued: 1 },
    meta: { ...meta, total: 1 },
  });
  const user = userEvent.setup();
  show();
  await screen.findByRole("tab", { name: "Failed 1" });
  await user.click(screen.getByRole("tab", { name: "Queued 1" }));
  expect(await screen.findByText("尚未安排时间")).toBeInTheDocument();
  expect(screen.queryByText(row.id)).not.toBeInTheDocument();
});

 it("identifies scheduled dispatch without missing Agent retry placeholders", async () => {
    vi.mocked(api.listAttention).mockResolvedValue({ items: [], meta });
    vi.mocked(getDashboardQueue).mockResolvedValue({items:[{...row,
      id:"scheduled_task_runs:12", title:"处理新的日历邀请", status:"running", stage:"dispatching",
      agent:"调度器", runtime_kind:"内置命令", retry_number:null,
      started_at:"2026-10-20T16:58:00Z", updated_at:"2026-10-20T16:59:55Z",
      detail_url:"/scheduled-tasks?id=9"}],counts:{active:1,queued:0},meta:{...meta,total:1}});
    show();
    expect(await screen.findByText("处理新的日历邀请")).toBeInTheDocument();
    expect(screen.getByText("调度器")).toBeInTheDocument();
    expect(screen.getByText("调度中")).toHaveClass("status-progress");
    expect(screen.getByText("本次触发")).toBeInTheDocument();
    expect(screen.getByText("2m 0s")).toBeInTheDocument();
    expect(screen.queryByText("重试次数未提供")).not.toBeInTheDocument();
  });
