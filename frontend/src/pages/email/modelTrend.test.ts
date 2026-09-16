import { expect, it } from "vitest";
import { trendLineSeries, trendPoints } from "./modelTrend";
import type { EmailStagedModel } from "../../api/console";
const model = (id: string, key: string, value: number | null) =>
  ({
    model_id: id,
    model_family: "linear",
    trained_at: "",
    status: "candidate",
    metrics: { accuracy: value, micro_f1: value, categories: {} },
    evaluation: {
      protocol: "holdout",
      test_digest: key,
      comparability_key: key,
    },
    compatibility: { enabled_categories: ["work"], description_version: "d1" },
    end_to_end_latency_ms: null,
    head_timing_percentiles_ms: null,
  }) satisfies EmailStagedModel;
it("breaks different protocols, datasets, missing evidence and category sets", () => {
  const points = trendPoints(
    [
      model("a", "one", 0.8),
      model("b", "one", 0.9),
      model("c", "two", 0.95),
      model("d", "two", null),
      model("e", "two", 0.96),
    ],
    "micro_f1",
    "",
  );
  expect(points[0].segment).toBe(points[1].segment);
  expect(points[2].segment).not.toBe(points[1].segment);
  expect(points[3].value).toBeNull();
  expect(points[4].segment).not.toBe(points[2].segment);
});
it("does not turn null latency into zero or connect absent comparability metadata", () => {
  const points = trendPoints(
    [model("a", "one", null), { ...model("b", "one", 0.9), evaluation: null }],
    "p95",
    "",
  );
  expect(points.every((point) => point.value === null)).toBe(true);
  expect(points[1].reason).toBeTruthy();
});
it("breaks a trend when model family changes even if the dataset evidence matches", () => {
  const points = trendPoints(
    [
      model("linear", "one", 0.8),
      { ...model("mlp", "one", 0.9), model_family: "mlp" },
    ],
    "micro_f1",
    "",
  );
  expect(points[1].segment).not.toBe(points[0].segment);
});
it("keeps points in the same model-family line when families are interleaved", () => {
  const points = trendPoints(
    [
      model("tfidf-a", "one", 0.8),
      { ...model("fasttext-a", "one", 0.7), model_family: "fasttext" },
      model("tfidf-b", "one", 0.9),
    ],
    "micro_f1",
    "",
  );
  const series = trendLineSeries(points);
  expect(series.families).toEqual(["linear", "fasttext"]);
  expect(series.data.map((row) => row["family:linear"])).toEqual([
    0.8,
    null,
    0.9,
  ]);
  expect(series.data.map((row) => row["family:fasttext"])).toEqual([
    null,
    0.7,
    null,
  ]);
});
