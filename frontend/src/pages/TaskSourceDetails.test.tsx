import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import type { BusinessTaskSummary } from "../api/console";

const api = vi.hoisted(() => ({ task: vi.fn(), attention: vi.fn(), original: vi.fn() }));
vi.mock("../api/console", async (importOriginal) => ({ ...(await importOriginal<typeof import("../api/console")>()), getBusinessTaskDetail: api.task, getBusinessAttentionDetail: api.attention, getBusinessTaskSignalDetail: api.original }));
import { TaskDetailPage, SourceOriginalRecord } from "./TaskDetailPage";
import { TaskAttentionDetailPage } from "./TaskAttentionDetailPage";

beforeEach(() => vi.clearAllMocks());
const references = [31, 32].map((id) => ({ id, source_type: "meeting", source_ref: `meeting:${id}`, source_time: "2026-10-30", context_json: "{}" }));
const task: BusinessTaskSummary = { id: "42", title: "核实验收", origin: "source", stage: "formal", status: "open", commitment_status: "accepted", owner: "张三", deadline_at: "", deadline_type: "", business_relevance: "relevant", anchor_labels: [], updated_at: "now", detail_url: "/tasks/item/42", suggested_owner: "", suggestion_reason: "" };
function mockDetails() {
  api.task.mockResolvedValue({ item: { summary: task, suggestion: null, description: "验收事实", evidence: references.map((signal) => ({ role: "assignment", signal })), date_evidence: [], events: [], relations: [], clusters: [], anchors: [], official_projects: [], follow_ups: [], dingtalk_todos: [] }, meta: { snapshot_at: "now" } });
  api.attention.mockResolvedValue({ item: { summary: { id: "7", category: "watch", title: "关注验收", business_area: "交付", why_attention: "待验收", current_state: "核实中", ceo_action: "继续关注", anchor_label: "", linked_task_count: 1, updated_at: "now", detail_url: "/tasks/attention/7" }, assessment: { inference: "等待核实", evidence: [{ signal_id: 31, source_ref: "meeting:31", source_excerpt: "已保存逐字引文", source_time: "now", source_link: "" }] }, linked_tasks: [task], evidence_signals: references, events: [] }, meta: { snapshot_at: "now" } });
}

it.each(["task", "attention"])("%s facts appear before a slow selected original and only that original is read", async (kind) => {
  mockDetails();
  let finish: (value: unknown) => void = () => {};
  api.original.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  render(<MemoryRouter>{kind === "task" ? <TaskDetailPage taskId="42" /> : <TaskAttentionDetailPage attentionId="7" />}</MemoryRouter>);
  await screen.findByRole("heading", { name: kind === "task" ? "核实验收" : "关注验收" });
  expect(api.original).not.toHaveBeenCalled();
  expect(screen.queryByText("原文不可用")).not.toBeInTheDocument();
  if (kind === "attention") await userEvent.click(screen.getByText("来源证据").closest("summary")!);
  await userEvent.click(screen.getByRole("button", { name: "读取来源 31 原文" }));
  expect(screen.getByRole("button", { name: "读取来源 31 原文", hidden: true })).toBeDisabled();
  expect(screen.getByRole("heading", { name: kind === "task" ? "核实验收" : "关注验收" })).toBeInTheDocument();
  finish({ item: { ...references[0], evidence_text: "所选来源完整原文", source_available: true, source_unavailable_reason: null }, meta: { snapshot_at: "now" } });
  expect(await screen.findByText("所选来源完整原文")).toBeInTheDocument();
  expect(api.original).toHaveBeenCalledTimes(1);
  expect(api.original.mock.calls[0][0]).toBe("31");
  expect(screen.getByRole("button", { name: "读取来源 32 原文", hidden: true })).toBeEnabled();
  if (kind === "attention") expect(screen.getByText("已保存逐字引文")).toBeInTheDocument();
});


it("cancels a source read on a different detail identity and ignores its late result", async () => {
  let finish: (value: unknown) => void = () => {};
  api.original.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  const { rerender } = render(<SourceOriginalRecord key="task:1:31" reference={references[0]} />);
  await userEvent.click(screen.getByRole("button", { name: "读取来源 31 原文" }));
  const signal = api.original.mock.calls[0][1] as AbortSignal;
  rerender(<SourceOriginalRecord key="attention:2:32" reference={references[1]} />);
  expect(signal.aborted).toBe(true);
  await act(async () => finish({ item: { ...references[0], evidence_text: "旧详情的迟到原文", source_available: true, source_unavailable_reason: null }, meta: { snapshot_at: "now" } }));
  expect(screen.queryByText("旧详情的迟到原文")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "读取来源 32 原文" })).toBeEnabled();
  expect(api.original).toHaveBeenCalledTimes(1);
});
