import { useRef, useState } from "react";
import {
  saveEmailPromotionConfig,
  type EmailCategoryConfig,
  type EmailLearningEvidence,
  type EmailPromotionConfig,
} from "../../api/console";
import { EmailDrawer } from "./EmailDrawer";
import { checkLabel, checkValue, errorMessage, reasonLabel } from "./shared";

const fields = [
  "micro_f1_min",
  "category_precision_min",
  "category_validation_samples_min",
  "p95_latency_max_ms",
] as const;
export function PromotionPanel({
  open,
  gate,
  configs,
  reload,
  disabled,
  onBusy,
  onClose,
}: {
  open: boolean;
  gate: EmailLearningEvidence["promotion_gate"] | undefined;
  configs: EmailCategoryConfig[];
  reload: () => Promise<EmailLearningEvidence | undefined>;
  disabled: boolean;
  onBusy: (value: boolean) => void;
  onClose: () => void;
}) {
  if (!open || !gate) return null;
  return (
    <EmailDrawer title="晋升设置" locked={disabled} onClose={onClose}>
      <div className="email-drawer-content">
        <ThresholdEditor
          config={gate.config}
          reload={reload}
          disabled={disabled}
          onBusy={onBusy}
        />
        <p className="muted">
          每个分类单独判断：精度和样本数都达到门槛的分类才会上线，其余分类继续由 Agent 处理。
        </p>
        <GateChecks checks={gate.checks} configs={configs} />
      </div>
    </EmailDrawer>
  );
}
export function GateChecks({
  checks,
  configs,
  showHeading = true,
  compact = false,
}: {
  checks: EmailLearningEvidence["promotion_gate"]["checks"];
  configs: EmailCategoryConfig[];
  showHeading?: boolean;
  compact?: boolean;
}) {
  const grouped = new Map<string, typeof checks>();
  const independent: typeof checks = [];
  checks.forEach((check) => {
    const match = /^category_(precision|validation_samples):(.+)$/.exec(
      check.key,
    );
    if (!match) {
      independent.push(check);
      return;
    }
    const rows = grouped.get(match[2]) || [];
    rows.push(check);
    grouped.set(match[2], rows);
  });
  return (
    <section className="promotion-checks" aria-label="晋升检查">
      {showHeading && <h3>晋升检查</h3>}
      <p className="muted">
        未测量表示服务器尚未给出独立评测证据；未通过表示已有测量但没有达到门槛。
      </p>
      {independent.map((check) => (
        <CheckRow key={check.key} check={check} configs={configs} />
      ))}
      {compact ? (
        <details className="promotion-category-list">
          <summary>类别检查（{grouped.size} 类）</summary>
          {Array.from(grouped).map(([category, rows]) => (
            <details className="promotion-category" key={category}>
              <summary>
                {categoryLabel(category, configs)} · Precision + 独立验证样本{" "}
                <Status checks={rows} />
              </summary>
              {rows.map((check) => (
                <CheckRow key={check.key} check={check} configs={configs} />
              ))}
            </details>
          ))}
        </details>
      ) : (
        Array.from(grouped).map(([category, rows]) => (
        <details className="promotion-category" key={category}>
          <summary>
            {categoryLabel(category, configs)} · Precision + 独立验证样本{" "}
            <Status checks={rows} />
          </summary>
          {rows.map((check) => (
            <CheckRow key={check.key} check={check} configs={configs} />
          ))}
        </details>
        ))
      )}
    </section>
  );
}
function categoryLabel(category: string, configs: EmailCategoryConfig[]) {
  return (
    configs.find((config) => config.category_key === category)?.display_name ||
    category
  );
}
function CheckRow({
  check,
  configs,
}: {
  check: EmailLearningEvidence["promotion_gate"]["checks"][number];
  configs: EmailCategoryConfig[];
}) {
  return (
    <div className="promotion-check-row">
      <span>{checkLabel(check.key, configs)}</span>
      <strong>
        {checkValue(check.key, check.actual)} / {check.operator}{" "}
        {checkValue(check.key, check.target)}
      </strong>
      <small className={check.reason === "not_measured" ? "muted" : ""}>
        {check.passed
          ? "通过"
          : check.reason === "not_measured"
            ? "未测量"
            : "未通过"}{" "}
        · {reasonLabel(check.reason)}
      </small>
    </div>
  );
}
function Status({
  checks,
}: {
  checks: EmailLearningEvidence["promotion_gate"]["checks"];
}) {
  return (
    <span
      className={
        checks.every((item) => item.passed) ? "status-pass" : "status-hold"
      }
    >
      {checks.every((item) => item.passed) ? "通过" : "待处理"}
    </span>
  );
}
function ThresholdEditor({
  config,
  reload,
  disabled,
  onBusy,
}: {
  config: EmailPromotionConfig;
  reload: () => Promise<EmailLearningEvidence | undefined>;
  disabled: boolean;
  onBusy: (value: boolean) => void;
}) {
  const [values, setValues] = useState(
    () =>
      Object.fromEntries(
        fields.map((key) => [key, String(config[key])]),
      ) as Record<(typeof fields)[number], string>,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const lock = useRef(false);
  async function save() {
    if (lock.current) return;
    const parsed = Object.fromEntries(
      fields.map((key) => [key, Number(values[key])]),
    ) as Record<(typeof fields)[number], number>;
    if (
      fields.some(
        (key) =>
          !values[key].trim() ||
          !Number.isFinite(parsed[key]) ||
          parsed[key] <= 0 ||
          ((key === "micro_f1_min" || key === "category_precision_min") &&
            parsed[key] > 1) ||
          (key === "category_validation_samples_min" &&
            !Number.isInteger(parsed[key])),
      )
    ) {
      setError(
        "请填写有效门槛；比例大于 0 且不超过 1，样本数为正整数，延迟为正数。",
      );
      return;
    }
    lock.current = true;
    setBusy(true);
    onBusy(true);
    setError("");
    try {
      const result = await saveEmailPromotionConfig({
        ...parsed,
        expected_current_version: config.config_version,
      });
      if (!result.ok) throw new Error("门槛保存失败，请重试");
      await reload();
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      lock.current = false;
      setBusy(false);
      onBusy(false);
    }
  }
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <h3>
        晋升门槛 <small>版本 {config.config_version}</small>
      </h3>
      <fieldset disabled={busy || disabled} className="email-threshold-fields">
        <label>
          Micro F1 最低值
          <input
            aria-label="Micro F1 最低值"
            type="number"
            min="0"
            max="1"
            step="any"
            value={values.micro_f1_min}
            onChange={(event) =>
              setValues((current) => ({
                ...current,
                micro_f1_min: event.target.value,
              }))
            }
          />
        </label>
        <label>
          每类别 Precision 最低值
          <input
            aria-label="每类别 Precision 最低值"
            type="number"
            min="0"
            max="1"
            step="any"
            value={values.category_precision_min}
            onChange={(event) =>
              setValues((current) => ({
                ...current,
                category_precision_min: event.target.value,
              }))
            }
          />
        </label>
        <label>
          每类别独立验证样本数
          <input
            aria-label="每类别独立验证样本数"
            type="number"
            min="1"
            step="1"
            value={values.category_validation_samples_min}
            onChange={(event) =>
              setValues((current) => ({
                ...current,
                category_validation_samples_min: event.target.value,
              }))
            }
          />
        </label>
        <label>
          端到端 P95 上限（ms）
          <input
            aria-label="端到端 P95 上限（ms）"
            type="number"
            min="0"
            step="any"
            value={values.p95_latency_max_ms}
            onChange={(event) =>
              setValues((current) => ({
                ...current,
                p95_latency_max_ms: event.target.value,
              }))
            }
          />
        </label>
      </fieldset>
      {error && <p role="alert">{error}</p>}
      <button
        className="primary-button"
        disabled={busy || disabled}
        type="submit"
      >
        {busy ? "正在保存…" : "保存晋升门槛"}
      </button>
    </form>
  );
}
