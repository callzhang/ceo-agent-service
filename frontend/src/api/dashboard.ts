import { parseConsoleList, request, type ConsoleList } from "./console";

export type QueueState = "active" | "queued";
export interface DashboardQueueItem {
  id: string;
  source: string;
  title: string;
  status: string;
  retry_number: number | null;
  agent: string;
  runtime_kind: string;
  model: string;
  stage: string;
  started_at: string;
  updated_at: string;
  created_at: string;
  available_at: string;
  error: string;
  detail_url: string;
}
export interface DashboardQueue extends ConsoleList<DashboardQueueItem> {
  counts: { active: number; queued: number };
}
export async function getDashboardQueue(
  state: QueueState,
  page = 1,
  signal?: AbortSignal,
): Promise<DashboardQueue> {
  const value = await request<DashboardQueue>(
    `/api/console/dashboard/queues?state=${state}&page=${page}&page_size=20`,
    { signal },
  );
  const result = parseConsoleList<DashboardQueueItem>(value);
  if (
    !value.counts ||
    !Number.isInteger(value.counts.active) ||
    value.counts.active < 0 ||
    !Number.isInteger(value.counts.queued) ||
    value.counts.queued < 0 ||
    result.items.some(
      (row) =>
        !row ||
        typeof row.id !== "string" ||
        typeof row.title !== "string" ||
        (row.retry_number !== null &&
          (!Number.isInteger(row.retry_number) || row.retry_number < 0)),
    )
  ) {
    throw new Error("invalid Dashboard queue response");
  }
  return { ...result, counts: value.counts };
}
