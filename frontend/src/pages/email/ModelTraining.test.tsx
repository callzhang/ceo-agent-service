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
import { initialTrainingSelection } from "./TrainingSetup";

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

it("leaves categories below the cold-start floor out of the default training selection", () => {
  const selection = initialTrainingSelection([
    { source: "agent_auto_label", category: "work", sample_count: 20, unique_trainable_count: 20, provenance: {} },
    { source: "agent_auto_label", category: "personal", sample_count: 2, unique_trainable_count: 2, provenance: {} },
    { source: "agent_auto_label", category: "legal", sample_count: 19, unique_trainable_count: 19, provenance: {} },
  ], []);
  expect(selection.sources).toEqual(["agent_auto_label"]);
  // personal and legal fall under the cold-start floor, so they train as others.
  expect(selection.categories).toEqual(["work", "others"]);
});

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
      micro_f1_min: 0.95,
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
        micro_f1: 0.88,
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
      end_to_end_latency_ms: null,
      head_timing_percentiles_ms: { p50: 12, p95: 82, p99: 100 },
      training: {
        started_at: "2026-09-14T11:00:00Z",
        completed_at: "2026-09-14T12:00:00Z",
        duration_ms: 60000,
        sample_count: 42,
        category_sample_count: 42,
        account_count: 1,
        group_count: 42,
      },
    },
  ],
  active_model_id: null,
  pending_examples: 2,
  training_snapshot: { sample_count: 42 },
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
    "42",
  );
  expect(screen.getByText("候选整体准确率").parentElement).toHaveTextContent(
    "88.0%",
  );
  expect(screen.getByText("候选 P95 延迟").parentElement).toHaveTextContent(
    "82.0 ms",
  );
  expect(screen.getByText("候选 P95 延迟").parentElement).toHaveTextContent(
    "输出头实际测量；端到端未测量",
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

it("uses measurable historical registry models when no staged model is available", () => {
  const historical = {
    model_id: "email-tfidf-lr-history",
    model_family: "tfidf-logistic-regression",
    model_version: "email-tfidf-lr-history",
    status: "previous",
    status_reason: "",
    candidate_reason: "",
    promotion_reason: "",
    rejection_reason: "",
    failure_reason: "",
    superseded_reason: "",
    integrity_status: "verified",
    integrity_error: "",
    lifecycle: [],
    trained_at: "2026-09-13T12:00:00Z",
    training_started_at: "2026-09-13T11:59:00Z",
    training_finished_at: "2026-09-13T12:00:00Z",
    sample_count: 120,
    new_sample_count: 20,
    category_counts: { work: 120 },
    account_counts: { primary: 1 },
    validation_method: "time-ordered-holdout",
    training_dataset_version: "dataset-v1",
    accuracy: 0.8,
    micro_f1: 0.78,
    per_category_metrics: {
      work: { precision: 0.82, recall: 0.76, f1: 0.78 },
    },
    prediction_latency_p50_ms: 0.2,
    prediction_latency_p95_ms: 0.4,
    artifact_sha256: "a".repeat(64),
  };
  render(
    <ModelTraining
      learning={{ ...learning, models: [historical] }}
      configs={[]}
      reload={async () => learning}
      runtimeVerified
      onRuntimeUnverified={vi.fn()}
      onBusy={vi.fn()}
    />,
  );
  expect(screen.queryByText("暂无可测量趋势数据。")).not.toBeInTheDocument();
  expect(screen.getAllByText("email-tfidf-lr-history")).not.toHaveLength(0);
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
  expect(drawer).toHaveTextContent("「是否重要」判断（与分类无关的独立输出头）");
  expect(drawer).toHaveTextContent("整体准确率（全部分类）");
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

it("says why 开始训练 is unavailable instead of doing nothing", async () => {
  const user = userEvent.setup();
  api.previewEmailTraining.mockResolvedValue({
    unique_sample_count: 1238,
    snapshot_id: "snapshot-1",
    snapshot_digest: "digest-1",
    snapshot_version: "v1",
    description_version: "d1",
    training_ready: false,
    training_blockers: ["external_billing:test"],
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
  const start = await screen.findByRole("button", { name: "开始训练" });
  await waitFor(() =>
    expect(document.querySelector(".training-blocked")?.textContent).toContain(
      "external_billing:test",
    ),
  );
  const reason = document.querySelector(".training-blocked")!;
  expect(reason).toHaveTextContent("还不能开始训练");
  expect(start).toBeDisabled();
  expect(start).toHaveAttribute("aria-describedby", reason.id);
  expect(api.requestEmailTraining).not.toHaveBeenCalled();
});

it("shows a training run in progress as a row in the version table, not a banner", async () => {
  const running = {
    ...learning,
    active_run_id: "run-77",
    training_runs_without_model: [
      { run_id: "run-77", status: "running", started_at: "2026-09-17T09:00:00Z", finished_at: "", reason: "", model_families: ["embedding-mlp"] },
    ],
  };
  render(
    <ModelTraining
      learning={running}
      configs={[]}
      reload={async () => running}
      runtimeVerified
      onRuntimeUnverified={vi.fn()}
      onBusy={vi.fn()}
    />,
  );

  const table = screen.getByRole("table", { name: "模型版本" });
  const row = Array.from(table.querySelectorAll("tbody tr")).find((item) => item.querySelector("td")?.getAttribute("title") === "run-77");
  expect(row).toHaveTextContent("训练中");
  expect(row).toHaveTextContent("embedding-mlp");
  expect(screen.queryByText(/训练进行中/)).not.toBeInTheDocument();
});

it("shows category counts in one always-visible table instead of a collapsed source list", async () => {
  const user = userEvent.setup();
  api.previewEmailTraining.mockResolvedValue({
    unique_sample_count: 50,
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

  await user.click(screen.getByRole("button", { name: "新建训练" }));
  const table = await screen.findByRole("table", { name: "类别与数据来源" });
  // work is one row totalling both sources, not one row per source.
  const row = screen.getByRole("checkbox", { name: "work" }).closest("tr")!;
  expect(row).toHaveTextContent("50");
  expect(row).toHaveTextContent("Agent 自动标注 20");
  expect(row).toHaveTextContent("邮件文件夹快照 30");
  expect(table).toBeVisible();
  expect(screen.queryByText("数据来源记录")).not.toBeInTheDocument();
});

it("adds an others row that totals the categories left unselected", async () => {
  const user = userEvent.setup();
  api.previewEmailTraining.mockResolvedValue({
    unique_sample_count: 50,
    snapshot_id: "snapshot-1",
    snapshot_digest: "digest-1",
    snapshot_version: "v1",
    description_version: "d1",
  });
  const sparse = {
    ...learning,
    training_sources: [
      ...learning.training_sources,
      { source: "agent_auto_label", category: "personal", sample_count: 4, provenance: {} },
    ],
  };
  render(
    <ModelTraining
      learning={sparse}
      configs={[]}
      reload={async () => sparse}
      runtimeVerified
      onRuntimeUnverified={vi.fn()}
      onBusy={vi.fn()}
    />,
  );

  await user.click(screen.getByRole("button", { name: "新建训练" }));
  const others = await screen.findByRole("checkbox", { name: "others" });
  expect(others).toBeChecked();
  const row = others.closest("tr")!;
  expect(row).toHaveTextContent("4");
  expect(row).toHaveTextContent("personal");
  // It is a real choice: thin leftover mail can make the split impossible.
  await user.click(others);
  expect(others).not.toBeChecked();
});

it("lists a run that produced no model in the version table with its reason", async () => {
  const failed = {
    ...learning,
    active_run_id: null,
    training_runs_without_model: [
      {
        run_id: "run-88",
        status: "failed",
        started_at: "2026-09-16T08:06:18Z",
        finished_at: "2026-09-16T08:06:21Z",
        reason: "RuntimeError:training selection categories are unavailable",
      },
    ],
  };
  render(
    <ModelTraining
      learning={failed}
      configs={[]}
      reload={async () => failed}
      runtimeVerified
      onRuntimeUnverified={vi.fn()}
      onBusy={vi.fn()}
    />,
  );

  const row = (await screen.findByText("run-88")).closest("tr")!;
  expect(row).toHaveTextContent("训练失败");
  // A run has no model family: that column stays empty rather than inventing one.
  expect(row).toHaveTextContent("—");
  expect(row).toHaveTextContent("training selection categories are unavailable");
});


it("names the categories that go live and leaves the rest with the Agent", () => {
  const perCategory = {
    ...learning,
    promotion_gate: { ...learning.promotion_gate, promotion_eligible: true, promoted_categories: ["work"], important_promoted: false },
  };
  render(
    <ModelTraining
      learning={perCategory}
      configs={[{ category_key: "work", display_name: "工作" } as never]}
      reload={async () => perCategory}
      runtimeVerified
      onRuntimeUnverified={vi.fn()}
      onBusy={vi.fn()}
    />,
  );

  expect(screen.getByLabelText("按分类上线")).toHaveTextContent("可上线的分类：工作。其余分类继续由 Agent 处理，模型不会标记重要邮件。");
});

it("says every email stays with the Agent when no category qualifies", () => {
  const none = { ...learning, promotion_gate: { ...learning.promotion_gate, promoted_categories: [] } };
  render(
    <ModelTraining learning={none} configs={[]} reload={async () => none} runtimeVerified onRuntimeUnverified={vi.fn()} onBusy={vi.fn()} />,
  );

  expect(screen.getByLabelText("按分类上线")).toHaveTextContent("暂无分类达标，所有邮件继续由 Agent 处理。");
});

it("lists model versions and failed runs newest first", () => {
  const mixed = {
    ...learning,
    staged_models: [
      { ...learning.staged_models[0], model_id: "email-embedding-mlp-older", trained_at: "2026-09-14T08:00:00Z" },
      { ...learning.staged_models[0], model_id: "email-embedding-mlp-newest", trained_at: "2026-09-17T08:00:00Z" },
    ],
    training_runs_without_model: [
      { run_id: "run-middle", status: "failed", started_at: "2026-09-16T08:00:00Z", finished_at: "2026-09-16T08:01:00Z", reason: "boom", model_families: ["embedding-mlp", "fasttext"] },
    ],
  };
  render(
    <ModelTraining learning={mixed} configs={[]} reload={async () => mixed} runtimeVerified onRuntimeUnverified={vi.fn()} onBusy={vi.fn()} />,
  );

  const table = screen.getByRole("table", { name: "模型版本" });
  const ids = Array.from(table.querySelectorAll("tbody tr td:first-child")).map((cell) => cell.getAttribute("title"));
  expect(ids).toEqual(["email-embedding-mlp-newest", "run-middle", "email-embedding-mlp-older"]);
  // A failed run shows the families it was asked to train.
  const runRow = Array.from(table.querySelectorAll("tbody tr")).find((row) => row.querySelector("td")?.getAttribute("title") === "run-middle");
  expect(runRow?.querySelectorAll("td")[1]).toHaveTextContent("embedding-mlp、fasttext");
});


it("offers the threshold editor once, from the header", () => {
  render(
    <ModelTraining learning={learning} configs={[]} reload={async () => learning} runtimeVerified onRuntimeUnverified={vi.fn()} onBusy={vi.fn()} />,
  );

  expect(screen.getAllByRole("button", { name: "晋升设置" })).toHaveLength(1);
  expect(screen.queryByRole("button", { name: "查看与编辑门槛" })).not.toBeInTheDocument();
});

it("says which scope each score covers, so 79.5% and 91.4% do not look contradictory", () => {
  const scoped = {
    ...learning,
    staged_models: [{ ...learning.staged_models[0], metrics: { ...learning.staged_models[0].metrics, accuracy: 0.795, micro_f1: 0.795, important: { precision: 0.72, recall: 0.69, f1: 0.706, accepted_precision: 0.65 } } }],
  };
  render(
    <ModelTraining learning={scoped} configs={[]} reload={async () => scoped} runtimeVerified onRuntimeUnverified={vi.fn()} onBusy={vi.fn()} />,
  );

  // The headline number is the whole model, including categories it will not take.
  expect(screen.getByText("候选整体准确率").parentElement).toHaveTextContent("79.5%");
  expect(screen.getByText("候选整体准确率").parentElement).toHaveTextContent("全部分类，含模型不接手的");
  expect(screen.queryByText("候选 Micro F1")).not.toBeInTheDocument();
});
