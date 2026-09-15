import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  getEmailModelVersion: vi.fn(),
  previewEmailTraining: vi.fn(),
  requestEmailTraining: vi.fn(),
  saveEmailPromotionConfig: vi.fn(),
  saveEmailRuntimeMode: vi.fn(),
}));
vi.mock("../../api/console", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  ...api,
}));

import { ModelTraining } from "./ModelTraining";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}
beforeEach(() => vi.resetAllMocks());

const learning = {
  runtime: {
    mode: "agent_primary",
    active_model_id: null,
    candidate_model_id: "candidate-full-id",
    candidate_ready: true,
    toggle_enabled: true,
  },
  promotion_gate: {
    config: {
      macro_f1_min: 0.95,
      category_precision_min: 0.9,
      category_validation_samples_min: 20,
      p95_latency_max_ms: 500,
      config_version: "gate-v1",
    },
    promotion_eligible: false,
    checks: [
      {
        key: "category_precision:work",
        actual: 0.91,
        target: 0.9,
        operator: ">=",
        passed: true,
        reason: "passed",
      },
      {
        key: "category_validation_samples:work",
        actual: 24,
        target: 20,
        operator: ">=",
        passed: true,
        reason: "passed",
      },
    ],
  },
  mode_transitions: [],
  staged_models: [
    {
      model_id: "candidate-full-id",
      model_family: "linear",
      status: "candidate",
      trained_at: "2026-09-14T12:00:00Z",
      metrics: {
        accuracy: 0.9,
        macro_f1: 0.88,
        categories: { work: { precision: 0.91, recall: 0.85, f1: 0.88 } },
      },
      evaluation: {
        protocol: "holdout",
        test_digest: "test-a",
        comparability_key: "dataset-a",
      },
      compatibility: {
        enabled_categories: ["work"],
        description_version: "d1",
      },
      split_counts: { train: 10, validation: 4, test: 4 },
      end_to_end_latency_ms: { p50: 12, p95: 82, p99: 100 },
      head_timing_percentiles_ms: null,
    },
  ],
  active_model_id: null,
  pending_examples: 2,
  last_trained_feedback_count: 0,
  last_trained_at: null,
  last_feedback_at: null,
  active_run_id: null,
  models: [],
  registry_issues: [],
  category_thresholds: {},
  training_sources: [
    {
      source: "agent_auto_label",
      category: "work",
      sample_count: 20,
      provenance: {},
    },
    {
      source: "folder_snapshot",
      category: "work",
      sample_count: 30,
      provenance: {},
    },
  ],
  model_families: [
    {
      family: "linear",
      display_name: "Linear",
      supported: true,
      configured: true,
    },
  ],
} as any;

it("keeps the runtime header visible and opens compact setup and promotion dialogs", async () => {
  const user = userEvent.setup();
  api.previewEmailTraining.mockResolvedValue({
    unique_sample_count: 42,
    snapshot_id: "snapshot-1",
    snapshot_digest: "digest-1",
    snapshot_version: "v1",
    description_version: "d1",
  });
  render(
    <ModelTraining
      learning={learning}
      configs={[]}
      reload={async () => learning}
      runtimeVerified
      onRuntimeUnverified={vi.fn()}
      onBusy={vi.fn()}
    />,
  );
  expect(
    screen.getByRole("heading", { name: /Agent 正在分类/ }),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "新建训练" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "晋升设置" })).toBeInTheDocument();
  expect(screen.getByText(/已收集反馈：2/)).toBeInTheDocument();
  expect(screen.getByText("可用训练样本").parentElement).toHaveTextContent(
    "未统计",
  );
  expect(screen.getByText("候选 Macro F1").parentElement).toHaveTextContent(
    "88.0%",
  );
  expect(screen.getByText("候选 P95 延迟").parentElement).toHaveTextContent(
    "82.0 ms",
  );
  expect(screen.getByText("类别检查（1 类）")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "新建训练" }));
  expect(
    await screen.findByRole("dialog", { name: "新建训练" }),
  ).toHaveTextContent("邮件文件夹快照");
  await user.click(screen.getByRole("button", { name: "关闭详情" }));
  await user.click(screen.getByRole("button", { name: "晋升设置" }));
  expect(screen.getByRole("dialog", { name: "晋升设置" })).toHaveTextContent(
    "保存晋升门槛",
  );
});

it("keeps structured effect, data coverage, and technical evidence in Chinese detail tabs", async () => {
  const user = userEvent.setup();
  const model = {
    ...learning.staged_models[0],
    compatibility: {
      enabled_categories: ["work"],
      description_version: "d1",
      embedding_revision_reference: "emb-7",
      input_schema_version: "schema-3",
    },
    training: {
      started_at: "2026-09-14T11:00:00Z",
      completed_at: "2026-09-14T12:00:00Z",
      duration_ms: 60000,
      sample_count: 123,
      new_sample_count: 12,
      category_sample_count: 111,
      account_count: 2,
      group_count: 97,
    },
    parameters: { random_seed: 7, solver: "lbfgs" },
    metrics: {
      ...learning.staged_models[0].metrics,
      important: {
        precision: 0.98,
        recall: 0.97,
        f1: 0.97,
        accepted_precision: 0.99,
      },
      categories: {
        work: {
          precision: 0.91,
          recall: 0.85,
          f1: 0.88,
          support: 37,
          accepted_precision: 0.95,
          accepted_hits: 30,
          independent_groups: 25,
          threshold: 0.9,
        },
      },
    },
    artifact_sha256: "a".repeat(64),
  };
  api.getEmailModelVersion.mockResolvedValue({ ok: true, model });
  render(
    <ModelTraining
      learning={{ ...learning, staged_models: [model] }}
      configs={[]}
      reload={async () => learning}
      runtimeVerified
      onRuntimeUnverified={vi.fn()}
      onBusy={vi.fn()}
    />,
  );
  await user.click(
    screen.getByRole("button", { name: "查看 candidate-full-id" }),
  );
  const drawer = await screen.findByRole("dialog", { name: "模型版本详情" });
  expect(drawer).toHaveTextContent("important 独立输出头");
  expect(drawer).toHaveTextContent("30 / 25");
  await user.click(screen.getByRole("tab", { name: "数据与参数" }));
  expect(drawer).toHaveTextContent("训练耗时");
  expect(drawer).toHaveTextContent("2 / 97");
  await user.click(screen.getByRole("tab", { name: "技术信息" }));
  expect(drawer).toHaveTextContent("emb-7");
  expect(drawer).toHaveTextContent("schema-3");
});

it("retries a failed preview and ignores a cancelled selection preview", async () => {
  const user = userEvent.setup();
  const stale = deferred<any>();
  api.previewEmailTraining
    .mockReturnValueOnce(stale.promise)
    .mockRejectedValueOnce(new Error("快照不可用"))
    .mockResolvedValueOnce({
      unique_sample_count: 21,
      snapshot_id: "snapshot-2",
      snapshot_digest: "digest-2",
      snapshot_version: "v2",
      description_version: "d2",
    });
  render(
    <ModelTraining
      learning={learning}
      configs={[]}
      reload={async () => learning}
      runtimeVerified
      onRuntimeUnverified={vi.fn()}
      onBusy={vi.fn()}
    />,
  );
  await user.click(screen.getByRole("button", { name: "新建训练" }));
  await waitFor(() =>
    expect(api.previewEmailTraining).toHaveBeenCalledTimes(1),
  );
  const firstSignal = api.previewEmailTraining.mock.calls[0][1] as AbortSignal;
  await user.click(screen.getByRole("checkbox", { name: "Agent 自动标注" }));
  await waitFor(() => expect(firstSignal.aborted).toBe(true));
  await waitFor(() =>
    expect(api.previewEmailTraining).toHaveBeenCalledTimes(2),
  );
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent("快照不可用"),
  );
  expect(screen.getByRole("button", { name: "开始训练" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "重试" }));
  await waitFor(() =>
    expect(api.previewEmailTraining).toHaveBeenCalledTimes(3),
  );
  stale.resolve({
    unique_sample_count: 99,
    snapshot_id: "stale",
    snapshot_digest: "stale",
    snapshot_version: "old",
    description_version: "old",
  });
  expect(
    await screen.findByText("本次选中样本（含训练与验证）：21"),
  ).toBeInTheDocument();
  expect(
    screen.queryByText("本次选中样本（含训练与验证）：99"),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "开始训练" })).toBeEnabled();
});

it("keeps training disabled when the selected snapshot has no samples", async () => {
  const user = userEvent.setup();
  api.previewEmailTraining.mockResolvedValue({
    unique_sample_count: 0,
    snapshot_id: "empty-snapshot",
    snapshot_digest: "empty-digest",
    snapshot_version: "v3",
    description_version: "d3",
  });
  render(
    <ModelTraining
      learning={learning}
      configs={[]}
      reload={async () => learning}
      runtimeVerified
      onRuntimeUnverified={vi.fn()}
      onBusy={vi.fn()}
    />,
  );
  await user.click(screen.getByRole("button", { name: "新建训练" }));
  expect(
    await screen.findByText("当前选择没有样本，调整来源或类别。"),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "开始训练" })).toBeDisabled();
});

it("does not call a historical registry active model the runtime primary and locks setup choices while submitting", async () => {
  const user = userEvent.setup();
  const submission = deferred<any>();
  api.previewEmailTraining.mockResolvedValue({
    unique_sample_count: 21,
    snapshot_id: "snapshot-2",
    snapshot_digest: "digest-2",
    snapshot_version: "v2",
    description_version: "d2",
  });
  api.requestEmailTraining.mockReturnValue(submission.promise);
  render(
    <ModelTraining
      learning={{
        ...learning,
        models: [
          {
            model_id: "registry-active-id",
            model_family: "linear",
            status: "active",
            trained_at: "2026-09-13T12:00:00Z",
          },
        ],
      }}
      configs={[]}
      reload={async () => learning}
      runtimeVerified
      onRuntimeUnverified={vi.fn()}
      onBusy={vi.fn()}
    />,
  );
  expect(screen.getByText("历史版本 · active")).toBeInTheDocument();
  expect(screen.queryByText("历史版本 · 主模型")).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "新建训练" }));
  await screen.findByText("本次选中样本（含训练与验证）：21");
  await user.click(screen.getByRole("button", { name: "开始训练" }));
  expect(screen.getByRole("group", { name: "训练来源" })).toBeDisabled();
  expect(screen.getByRole("group", { name: "类别" })).toBeDisabled();
  expect(screen.getByRole("group", { name: "模型家族" })).toBeDisabled();
  submission.resolve({ learning: { training_status: "queued" } });
});
