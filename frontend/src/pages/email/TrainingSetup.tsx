import { useEffect, useRef, useState } from "react";
import type {
  EmailModelFamilyCapability,
  EmailTrainingPreview,
  EmailTrainingSource,
} from "../../api/console";
import { EmailDrawer } from "./EmailDrawer";
import { errorMessage } from "./shared";

export interface TrainingSelection {
  sources: string[];
  categories: string[];
  modelFamilies: string[];
}
type PreviewTraining = (
  payload: { sources: string[]; categories: string[] },
  signal: AbortSignal,
) => Promise<EmailTrainingPreview>;

const DEFAULT_CATEGORY_MINIMUM_SAMPLES = 20;
export const OTHERS_CATEGORY = "others";

export function initialTrainingSelection(
  sources: EmailTrainingSource[],
  families: EmailModelFamilyCapability[],
): TrainingSelection {
  const supported = sources.filter((row) => row.supported !== false);
  const trainableByCategory = new Map<string, number>();
  supported.forEach((row) => {
    const count = row.unique_trainable_count ?? row.sample_count;
    trainableByCategory.set(
      row.category,
      (trainableByCategory.get(row.category) || 0) + count,
    );
  });
  return {
    sources: unique(supported.map((row) => row.source)),
    // A category must meet the agreed cold-start floor before it is selected
    // by default. Keep sparse data visible and selectable for deliberate
    // experiments, while preventing a new training drawer from failing on
    // categories that cannot yet support an independent evaluation.
    categories: unique([
      ...supported
        .filter(
          (row) =>
            (trainableByCategory.get(row.category) || 0) >=
            DEFAULT_CATEGORY_MINIMUM_SAMPLES,
        )
        .map((row) => row.category),
      // Everything left out trains as one others class, so the model can say a
      // message is outside its scope instead of forcing it into the nearest
      // category. Deselect it when that leftover mail is too thin to split.
      OTHERS_CATEGORY,
    ]),
    modelFamilies: unique(
      families
        .filter((row) => row.supported && row.configured)
        .map((row) => row.family),
    ),
  };
}

export function TrainingSetup({
  open,
  sources,
  families,
  selection,
  onSelectionChange,
  onClose,
  onSubmit,
  previewTraining,
  busy,
  status,
}: {
  open: boolean;
  sources: EmailTrainingSource[];
  families: EmailModelFamilyCapability[];
  selection: TrainingSelection;
  onSelectionChange: (value: TrainingSelection) => void;
  onClose: () => void;
  onSubmit: () => void;
  previewTraining: PreviewTraining;
  busy: boolean;
  status: string;
}) {
  const [preview, setPreview] = useState<EmailTrainingPreview | null>(null);
  const [previewKey, setPreviewKey] = useState("");
  const [previewError, setPreviewError] = useState("");
  const [previewing, setPreviewing] = useState(false);
  const [retry, setRetry] = useState(0);
  const valid = selection.sources.length > 0 && selection.categories.length > 0;
  const selectionKey = JSON.stringify({
    sources: [...selection.sources].sort(),
    categories: [...selection.categories].sort(),
  });

  useEffect(() => {
    if (!open || !valid) {
      setPreview(null);
      setPreviewKey("");
      setPreviewError("");
      setPreviewing(false);
      return;
    }
    setPreview(null);
    setPreviewKey("");
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setPreviewing(true);
      setPreview(null);
      setPreviewError("");
      previewTraining(
        { sources: selection.sources, categories: selection.categories },
        controller.signal,
      )
        .then((result) => {
          if (!controller.signal.aborted) {
            setPreview(result);
            setPreviewKey(selectionKey);
          }
        })
        .catch((reason) => {
          if (!controller.signal.aborted) setPreviewError(errorMessage(reason));
        })
        .finally(() => {
          if (!controller.signal.aborted) setPreviewing(false);
        });
    }, 250);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [open, selectionKey, retry, previewTraining]);

  if (!open) return null;
  const sourceKeys = unique(sources.map((row) => row.source));
  const pickedCategories = unique(sources.map((row) => row.category)).filter(
    (category) => selection.categories.includes(category),
  );
  const othersRows = sources.filter(
    (row) => !pickedCategories.includes(row.category),
  );
  const categoryRows = unique(sources.map((row) => row.category)).map(
    (category) => {
      const rows = sources.filter((row) => row.category === category);
      return {
        category,
        supported: rows.some((row) => row.supported !== false),
        records: rows.reduce(
          (total, row) => total + (row.record_count ?? row.sample_count),
          0,
        ),
        trainable: rows.reduce(
          (total, row) => total + (row.unique_trainable_count ?? row.sample_count),
          0,
        ),
        bySource: rows
          .map(
            (row) =>
              `${sourceLabel(row.source)} ${row.unique_trainable_count ?? row.sample_count}`,
          )
          .join(" · "),
      };
    },
  );
  categoryRows.push({
    category: OTHERS_CATEGORY,
    supported: true,
    records: othersRows.reduce(
      (total, row) => total + (row.record_count ?? row.sample_count),
      0,
    ),
    trainable: othersRows.reduce(
      (total, row) => total + (row.unique_trainable_count ?? row.sample_count),
      0,
    ),
    bySource: othersRows.length
      ? unique(othersRows.map((row) => row.category)).join("、")
      : "未勾选的类别",
  });
  const blocked = !valid
    ? "来源和类别各要至少选择一项"
    : previewing || previewKey !== selectionKey
      ? "正在按当前选择核对样本"
      : previewError
        ? previewError
        : preview === null
          ? "样本核对结果尚未返回"
          : preview.unique_sample_count === 0
            ? "当前选择没有可训练的样本"
            : preview.training_ready === false
              ? `${(preview.training_blockers || []).join("、")} 的邮件不够分成训练、验证和测试三份，请取消这些类别或补充邮件`
              : !selection.modelFamilies.length
                ? "至少选择一个模型家族"
                : "";
  const toggle = (
    field: keyof TrainingSelection,
    value: string,
    checked: boolean,
  ) =>
    onSelectionChange({
      ...selection,
      [field]: checked
        ? unique([...selection[field], value])
        : selection[field].filter((item) => item !== value),
    });

  return (
    <EmailDrawer title="新建训练" locked={busy} onClose={onClose}>
      <div className="email-drawer-content training-setup">
        <p className="muted">
          选择来源、类别和模型家族。记录数不等于去重后的可训练数；默认只勾选每类至少 20 封的类别。
        </p>
        <fieldset disabled={busy}>
          <legend>训练来源</legend>
          {sourceKeys.map((source) => {
            const rows = sources.filter((row) => row.source === source);
            const supported = rows.some((row) => row.supported !== false);
            return (
              <label key={source}>
                <input
                  type="checkbox"
                  checked={selection.sources.includes(source)}
                  disabled={!supported}
                  onChange={(event) =>
                    toggle("sources", source, event.target.checked)
                  }
                />
                {sourceLabel(source)} {!supported && "（当前不可用）"}
              </label>
            );
          })}
        </fieldset>
        <fieldset disabled={busy}>
          <legend>类别</legend>
          {categoryRows.length ? (
            <div className="responsive-table-wrap">
              <table
                className="settings-table training-category-table"
                aria-label="类别与数据来源"
              >
                <thead>
                  <tr>
                    <th>类别</th>
                    <th>记录数</th>
                    <th>去重后可训练数</th>
                    <th>来源</th>
                  </tr>
                </thead>
                <tbody>
                  {categoryRows.map((row) => (
                    <tr key={row.category}>
                      <td data-label="类别">
                        <label className="training-category-pick">
                          <input
                            type="checkbox"
                            checked={selection.categories.includes(row.category)}
                            disabled={!row.supported}
                            onChange={(event) =>
                              toggle(
                                "categories",
                                row.category,
                                event.target.checked,
                              )
                            }
                          />
                          {row.category}
                          {!row.supported && "（当前不可选）"}
                        </label>
                      </td>
                      <td data-label="记录数">{row.records}</td>
                      <td data-label="去重后可训练数">{row.trainable}</td>
                      <td data-label="来源">{row.bySource}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p>暂无训练来源记录。</p>
          )}
        </fieldset>
        <fieldset disabled={busy}>
          <legend>模型家族</legend>
          {families.map((family) => (
            <label key={family.family}>
              <input
                type="checkbox"
                checked={selection.modelFamilies.includes(family.family)}
                disabled={!family.supported || !family.configured}
                onChange={(event) =>
                  toggle("modelFamilies", family.family, event.target.checked)
                }
              />
              {family.display_name}
              {(!family.supported || !family.configured) && "（当前不可用）"}
            </label>
          ))}
        </fieldset>
        <PreviewReadback
          valid={valid}
          preview={preview}
          previewing={previewing}
          error={previewError}
          onRetry={() => setRetry((value) => value + 1)}
        />
        <div className="email-drawer-footer">
          <p aria-live="polite">{status}</p>
          {blocked && !busy && (
            <p className="training-blocked" id="training-blocked" role="status">
              还不能开始训练：{blocked}
            </p>
          )}
          <button
            type="button"
            className="primary-button"
            disabled={busy || Boolean(blocked)}
            aria-describedby={blocked && !busy ? "training-blocked" : undefined}
            onClick={onSubmit}
          >
            {busy ? "正在提交…" : "开始训练"}
          </button>
        </div>
      </div>
    </EmailDrawer>
  );
}

function PreviewReadback({
  valid,
  preview,
  previewing,
  error,
  onRetry,
}: {
  valid: boolean;
  preview: EmailTrainingPreview | null;
  previewing: boolean;
  error: string;
  onRetry: () => void;
}) {
  if (!valid) return <p role="status">请选择至少一个来源和类别。</p>;
  if (previewing) return <p role="status">正在核对本次可训练样本…</p>;
  if (error)
    return (
      <p role="alert">
        样本核对失败：{error}{" "}
        <button type="button" className="compact-button" onClick={onRetry}>
          重试
        </button>
      </p>
    );
  if (!preview) return <p role="status">等待样本核对…</p>;
  const hasSamples = preview.unique_sample_count > 0;
  const trainingReady = preview.training_ready !== false;
  return (
    <section className="training-preview" aria-label="本次训练样本">
      <strong>
        本次选中样本（含训练与验证）：{preview.unique_sample_count}
      </strong>
      {!hasSamples && (
        <p role="status">当前选择没有样本，调整来源或类别。</p>
      )}
      {!trainingReady && (
        <p role="status">
          当前选择不能形成独立的训练、验证和测试集：
          {(preview.training_blockers || []).join("、")}。请取消这些类别或补充邮件。
        </p>
      )}
      <small>
        快照：{preview.snapshot_id} · {preview.snapshot_digest}
      </small>
      <small>
        版本：{preview.snapshot_version} · 描述：{preview.description_version}
      </small>
      <p className="muted">提交时重新校验各模型训练条件。</p>
    </section>
  );
}

function sourceLabel(value: string) {
  return value === "agent_auto_label"
    ? "Agent 自动标注"
    : value === "user_feedback"
      ? "用户反馈"
      : value === "folder_snapshot"
        ? "邮件文件夹快照"
        : value;
}
function unique(values: string[]) {
  return Array.from(new Set(values));
}
export function useStableTrainingSelection(
  sources: EmailTrainingSource[],
  families: EmailModelFamilyCapability[],
) {
  const initialized = useRef(false);
  const [selection, setSelection] = useState(() =>
    initialTrainingSelection(sources, families),
  );
  useEffect(() => {
    if (!initialized.current) {
      initialized.current = true;
      setSelection(initialTrainingSelection(sources, families));
    }
  }, [sources, families]);
  return [selection, setSelection] as const;
}
