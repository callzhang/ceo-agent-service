import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ detail: vi.fn<typeof import("../api/console").getBusinessTaskDetail>(), decide: vi.fn() }));
vi.mock("../api/console", async (importOriginal) => ({ ...(await importOriginal<typeof import("../api/console")>()), getBusinessTaskDetail: api.detail, decideCandidateTask: api.decide }));
import { TaskDetailPage } from "./TaskDetailPage";

type Response = Awaited<ReturnType<typeof api.detail>>;
function detail(id: string, status = "open"): Response {
  return { item: {
    summary: { id, title: `任务 ${id}`, origin: "source", suggested_owner: "", suggestion_reason: "", stage: "candidate", status, commitment_status: "none", owner: `负责人 ${id}`, deadline_at: "", deadline_type: "", business_relevance: "relevant", anchor_labels: [], updated_at: "now", detail_url: `/tasks/item/${id}` },
    description: `事实 ${id}`, suggestion: null, evidence: [], date_evidence: [], events: [], relations: [], clusters: [], anchors: [], official_projects: [], follow_ups: [], dingtalk_todos: [],
  }, meta: { snapshot_at: "now" } };
}
function deferred() {
  let resolve!: (value: Response) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<Response>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const page = (id: string) => <MemoryRouter><TaskDetailPage taskId={id} /></MemoryRouter>;
beforeEach(() => { vi.resetAllMocks(); api.decide.mockResolvedValue({ ok: true, message: "已忽略", meta: { updated_at: "now" } }); });

it("hides the refreshed Task and its controls while a different Task loads", async () => {
  const refresh = deferred();
  const next = deferred();
  api.detail.mockResolvedValueOnce(detail("1")).mockReturnValueOnce(refresh.promise).mockReturnValueOnce(next.promise);
  const view = render(page("1"));
  fireEvent.click(await screen.findByRole("button", { name: "忽略 任务 1" }));
  await waitFor(() => expect(api.detail).toHaveBeenCalledTimes(2));
  // Refreshing the same identity keeps the saved facts visible.
  expect(screen.getByText("事实 1")).toBeInTheDocument();
  await act(async () => refresh.resolve(detail("1", "cancelled")));
  expect(screen.getByRole("button", { name: "恢复 任务 1" })).toBeInTheDocument();
  view.rerender(page("2"));
  expect(screen.queryByText("事实 1")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "恢复 任务 1" })).not.toBeInTheDocument();
  await act(async () => next.resolve(detail("2")));
  expect(screen.getByRole("heading", { name: "任务 2" })).toBeInTheDocument();
  expect(screen.getByText("负责人 2")).toBeInTheDocument();
  expect(api.decide).toHaveBeenCalledExactlyOnceWith("1", "ignore");
});

it("aborts a pending old refresh and ignores it even after the new Task fails", async () => {
  const refresh = deferred();
  const next = deferred();
  api.detail.mockResolvedValueOnce(detail("1")).mockReturnValueOnce(refresh.promise).mockReturnValueOnce(next.promise);
  const view = render(page("1"));
  fireEvent.click(await screen.findByRole("button", { name: "忽略 任务 1" }));
  await waitFor(() => expect(api.detail).toHaveBeenCalledTimes(2));
  const oldSignal = api.detail.mock.calls[1][1] as AbortSignal;
  view.rerender(page("2"));
  expect(oldSignal.aborted).toBe(true);
  expect(screen.queryByText("事实 1")).not.toBeInTheDocument();
  await act(async () => next.reject(new Error("任务 2 读取失败")));
  expect(screen.getByRole("alert")).toHaveTextContent("任务 2 读取失败");
  await act(async () => refresh.resolve(detail("1", "cancelled")));
  expect(screen.getByRole("alert")).toHaveTextContent("任务 2 读取失败");
  expect(screen.queryByText("事实 1")).not.toBeInTheDocument();
});
