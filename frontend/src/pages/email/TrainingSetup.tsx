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

export function initialTrainingSelection(
  sources: EmailTrainingSource[],
  families: EmailModelFamilyCapability[],
): TrainingSelection {
  const supported = sources.filter((row) => row.supported !== false);
  return {
    sources: unique(supported.map((row) => row.source)),
    categories: unique(supported.map((row) => row.category)),
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
  const categoryKeys = unique(
    sources.filter((row) => row.supported !== false).map((row) => row.category),
  );
  const previewReady =
    valid &&
    preview !== null &&
    preview.unique_sample_count > 0 &&
    previewKey === selectionKey &&
    !previewing &&
    !previewError;
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
          选择来源、类别和模型家族。记录数不等于去重后的可训练数。
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
          {categoryKeys.map((category) => (
            <label key={category}>
              <input
                type="checkbox"
                checked={selection.categories.includes(category)}
                onChange={(event) =>
                  toggle("categories", category, event.target.checked)
                }
              />
              {category}
            </label>
          ))}
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
        <SourceEvidence sources={sources} />
        <div className="email-drawer-footer">
          <p aria-live="polite">{status}</p>
          <button
            type="button"
            className="primary-button"
            disabled={busy || !previewReady || !selection.modelFamilies.length}
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
  return (
    <section className="training-preview" aria-label="本次训练样本">
      <strong>
        本次选中样本（含训练与验证）：{preview.unique_sample_count}
      </strong>
      {!hasSamples && (
        <p role="status">当前选择没有样本，调整来源或类别。</p>
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

function SourceEvidence({ sources }: { sources: EmailTrainingSource[] }) {
  return sources.length ? (
    <details>
      <summary>数据来源记录</summary>
      <div className="responsive-table-wrap">
        <table className="settings-table" aria-label="训练数据来源明细">
          <thead>
            <tr>
              <th>来源</th>
              <th>类别</th>
              <th>记录数</th>
              <th>可选状态</th>
              <th>数据版本 / 摘要</th>
            </tr>
          </thead>
          <tbody>
            {sources.map((row) => (
              <tr key={row.source + row.category}>
                <td>{sourceLabel(row.source)}</td>
                <td>{row.category}</td>
                <td>{row.sample_count}</td>
                <td>{row.supported === false ? "当前不可选" : "可选"}</td>
                <td>
                  {String(
                    row.provenance.snapshot_id ||
                      row.provenance.classification_source ||
                      "未提供",
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  ) : (
    <p>暂无训练来源记录。</p>
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
