import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import {
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  getEmailModelVersion,
  previewEmailTraining,
  requestEmailTraining,
  saveEmailRuntimeMode,
  type EmailCategoryConfig,
  type EmailLearningEvidence,
  type EmailModelScanProgress,
  type EmailPromotionConfig,
  type EmailRuntime,
  type EmailRuntimeRate,
  type EmailStagedModel,
} from "../../api/console";
import { EmailDrawer } from "./EmailDrawer";
import { PromotionPanel, GateChecks } from "./PromotionPanel";
import { TrainingSetup, useStableTrainingSelection } from "./TrainingSetup";
import {
  checkLabel,
  errorMessage,
  localTime,
  measured,
  modeLabel,
  reasonLabel,
  statusLabel,
} from "./shared";
import {
  modelMetric,
  trendLineSeries,
  trendPoints,
  type TrendMetric,
} from "./modelTrend";
import "./training.css";

type RefreshLearning = () => Promise<EmailLearningEvidence | undefined>;
// A family is stored under its key ("embedding-mlp"); the catalog says what it
// is called. Showing the key told the owner nothing, and this one no longer
// describes the model. A run carries several keys joined with "、".
const FamilyNames = createContext<Record<string, string>>({});

function useFamilyLabel() {
  const names = useContext(FamilyNames);
  return (value: string) =>
    value
      .split("、")
      .map((key) => names[key] || key)
      .join("、");
}

export function ModelTraining({
  learning,
  configs,
  reload,
  runtimeVerified,
  onRuntimeUnverified,
  onBusy,
}: {
  learning: EmailLearningEvidence;
  configs: EmailCategoryConfig[];
  reload: RefreshLearning;
  runtimeVerified: boolean;
  onRuntimeUnverified: () => void;
  onBusy: (busy: boolean) => void;
}) {
  const runtime = learning.runtime;
  const models = learning.staged_models || [];
  // Which version the question is about: an id to start running, or null to
  // go back to the Agent. Without it the switch only knew how to toggle, so
  // starting a newer version while one was running read as "revert".
  const [confirm, setConfirm] = useState<
    (EmailRuntime & { target: string | null }) | null
  >(null);
  const [switchError, setSwitchError] = useState("");
  const [switching, setSwitching] = useState(false);
  const [setupOpen, setSetupOpen] = useState(false);
  const [promotionOpen, setPromotionOpen] = useState(false);
  const [setupBusy, setSetupBusy] = useState(false);
  const [trainingStatus, setTrainingStatus] = useState("");
  const [selection, setSelection] = useStableTrainingSelection(
    learning.training_sources || [],
    learning.model_families || [],
  );
  const [selected, setSelected] = useState("");
  const [legacy, setLegacy] = useState<
    EmailLearningEvidence["models"][number] | null
  >(null);
  const [detail, setDetail] = useState<EmailStagedModel | null>(null);
  const [detailError, setDetailError] = useState("");
  const [retry, setRetry] = useState(0);
  const [familyFilter, setFamilyFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const lock = useRef(false);
  const requestId = useRef("");
  const poll = useRef<number | undefined>(undefined);
  const candidate =
    models.find((model) => model.model_id === runtime?.candidate_model_id) ||
    null;
  const allVersions = useMemo(
    () => [
      ...models.map((model) => ({ kind: "staged" as const, model })),
      ...(learning.models || [])
        .filter(
          (model) => !models.some((item) => item.model_id === model.model_id),
        )
        .map((model) => ({ kind: "historical" as const, model })),
      // A run that produced no model still belongs here: otherwise a training
      // attempt that failed leaves no trace of having happened at all.
      ...(learning.training_runs_without_model || []).map((run) => ({
        kind: "run" as const,
        model: {
          model_id: run.run_id,
          // The families the run was asked to train; its status column says
          // what happened to it.
          model_family: (run.model_families || []).join("、"),
          status: run.status,
          trained_at: run.started_at,
        },
        reason: run.reason,
      })),
      // Newest first, across models and failed runs alike: the three sources
      // arrive in different orders and read as scrambled when concatenated.
    ].sort((a, b) => (b.model.trained_at || "").localeCompare(a.model.trained_at || "")),
    [models, learning.models, learning.training_runs_without_model],
  );
  // Runs carry no family and no model status, so they must not offer filter
  // values that would only ever hide the models the filters exist for.
  const modelVersions = allVersions.filter((item) => item.kind !== "run");
  const families = Array.from(
    new Set(modelVersions.map((item) => item.model.model_family || "未提供")),
  );
  const statuses = Array.from(
    new Set(modelVersions.map((item) => item.model.status || "未提供")),
  );
  const visibleVersions = allVersions.filter(
    (item) =>
      (familyFilter === "all" ||
        (item.model.model_family || "未提供") === familyFilter) &&
      (statusFilter === "all" || item.model.status === statusFilter),
  );
  const trainingLock = useRef(false);
  const polling = useRef(false);
  function stopPolling() {
    if (poll.current !== undefined) {
      window.clearInterval(poll.current);
      poll.current = undefined;
    }
  }
  async function pollTraining(runId: string) {
    if (polling.current) return;
    polling.current = true;
    try {
      const current = await reload();
      if (!current?.active_run_id || current.active_run_id !== runId)
        stopPolling();
    } catch (reason) {
      setTrainingStatus(`训练状态读取失败：${errorMessage(reason)}`);
      stopPolling();
    } finally {
      polling.current = false;
    }
  }
  function beginPolling(runId: string) {
    stopPolling();
    void pollTraining(runId);
    poll.current = window.setInterval(() => void pollTraining(runId), 5000);
  }
  useEffect(() => () => stopPolling(), []);
  useEffect(() => {
    // A run started before this page was opened is still worth following: the
    // status line used to appear only for whoever submitted it in this tab.
    if (learning.active_run_id) beginPolling(learning.active_run_id);
    else stopPolling();
  }, [learning.active_run_id]);
  useEffect(() => {
    setDetail(null);
    setDetailError("");
    if (!selected) return;
    const controller = new AbortController();
    getEmailModelVersion(selected, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setDetail(result.model);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setDetailError(errorMessage(reason));
      });
    return () => controller.abort();
  }, [selected, retry]);
  async function startTraining() {
    if (trainingLock.current || switching) return;
    trainingLock.current = true;
    setSetupBusy(true);
    onBusy(true);
    setTrainingStatus("");
    try {
      const result = await requestEmailTraining({
        sources: selection.sources,
        categories: selection.categories,
        model_families: selection.modelFamilies,
      });
      const runId = result.learning.training_run_id;
      setTrainingStatus(
        result.learning.training_status
          ? `训练状态：${result.learning.training_status}`
          : "训练请求已提交。",
      );
      await reload();
      setSetupOpen(false);
      if (runId) beginPolling(runId);
    } catch (reason) {
      setTrainingStatus(errorMessage(reason));
    } finally {
      trainingLock.current = false;
      setSetupBusy(false);
      onBusy(false);
    }
  }
  async function switchMode() {
    if (lock.current || !confirm) return;
    lock.current = true;
    setSwitching(true);
    setSwitchError("");
    onBusy(true);
    try {
      const mode = confirm.target ? "model_primary" : "agent_primary";
      const result = await saveEmailRuntimeMode({
        mode,
        model_id: confirm.target,
        request_id: requestId.current,
        expected_mode: confirm.mode,
        expected_model_id: confirm.active_model_id,
      });
      if (!result.ok) throw new Error("切换失败，请刷新后重试");
      await reload();
      setConfirm(null);
    } catch (reason) {
      const message = errorMessage(reason);
      onRuntimeUnverified();
      try {
        const current = await reload();
        if (!current) throw new Error("服务器读取已取消");
        if (
          current.runtime.mode !== confirm.mode ||
          current.runtime.active_model_id !== confirm.active_model_id
        )
          setConfirm(null);
        setSwitchError(message + "；已重新读取服务器状态。");
      } catch (readError) {
        setConfirm(null);
        setSwitchError(
          message + "；当前运行模式未确认：" + errorMessage(readError),
        );
      }
    } finally {
      lock.current = false;
      setSwitching(false);
      onBusy(false);
    }
  }
  async function rereadMode() {
    if (lock.current) return;
    lock.current = true;
    setSwitching(true);
    onBusy(true);
    try {
      await reload();
      setSwitchError("");
    } catch (reason) {
      setSwitchError("当前运行模式未确认：" + errorMessage(reason));
    } finally {
      lock.current = false;
      setSwitching(false);
      onBusy(false);
    }
  }
  const familyNames = Object.fromEntries(
    (learning.model_families || []).map((row) => [row.family, row.display_name || row.family]),
  );
  const familyLabel = (value: string) =>
    value
      .split("、")
      .map((key) => familyNames[key] || key)
      .join("、");
  return (
    <FamilyNames.Provider value={familyNames}>
    <section className="email-training training-shell">
      <header className="training-runtime-banner">
        <div className="training-runtime-copy">
          <span className="training-mode-chip">
            {runtime?.mode === "model_primary" ? "模型主分类" : "影子模式"}
          </span>
          <div>
            <h2>
              {!runtimeVerified
                ? "运行模式未确认"
                : runtime?.mode === "model_primary"
                  ? "模型正在分类"
                  : runtime?.candidate_ready
                    ? "Agent 正在分类"
                    : "Agent 正在分类"}
              {runtimeVerified && runtime?.candidate_ready && (
                <span className="email-ready-dot" aria-label="候选模型已达标" />
              )}
            </h2>
            <p>
              {!runtimeVerified
                ? "无法验证当前分类方式，请重新读取服务器状态。"
                : runtime?.mode === "model_primary"
                  ? "新邮件由当前运行模型分类；拒判、超时和不可用仍由 Classifier Agent 处理。"
                  : "模型用于阶段性训练与离线验证，当前新邮件仍由 Classifier Agent 分类。"}
            </p>
            <small>
              运行主模型：
              <span title={runtime?.active_model_id || "无"}>
                {shortModelId(runtime?.active_model_id || "无")}
              </span>
              {" · 候选："}
              <span title={runtime?.candidate_model_id || "无"}>
                {shortModelId(runtime?.candidate_model_id || "无")}
              </span>
              {" · 已收集反馈："}
              {learning.pending_examples}（不等于可用训练样本）
            </small>
          </div>
        </div>
        <div className="training-runtime-actions">
          <div>
            <button
              type="button"
              className="secondary-button"
              onClick={() => setPromotionOpen(true)}
              disabled={!learning.promotion_gate || switching}
            >
              晋升设置
            </button>
            <button
              type="button"
              className="primary-button"
              onClick={() => setSetupOpen(true)}
              disabled={switching}
            >
              新建训练
            </button>
          </div>
          {runtimeVerified ? null : (
            <button
              className="compact-button"
              disabled={switching}
              onClick={() => void rereadMode()}
            >
              重新读取运行模式
            </button>
          )}
        </div>
      </header>
      <p id="email-mode-help" className="muted training-mode-help">
        {!runtimeVerified
          ? "当前状态未确认，已暂停显示上线按钮。"
          : runtime?.mode === "model_primary"
            ? "模型正在判新邮件；改回 Agent 后它只做影子判断。"
            : runtime?.toggle_enabled
              ? "下面的模型版本表里，达标的那一版有上线按钮。"
              : "还没有达标的版本，晋升检查里写着差哪一项。"}
      </p>
      {trainingStatus && (
        <p className="training-request-status" role="status">
          {trainingStatus}
        </p>
      )}
      {switchError && <p role="alert">{switchError}</p>}
      <section className="training-stats" aria-label="候选模型摘要">
        <Stat
          label="可用训练样本"
          value={String(
            candidate?.training?.sample_count ??
              learning.training_snapshot?.sample_count ??
              "未统计",
          )}
          note={
            candidate?.training?.sample_count != null
              ? "当前候选训练样本"
              : "去重后的可训练数"
          }
        />
        <Stat
          label="候选整体准确率"
          value={measured(candidate?.metrics?.micro_f1)}
          note={candidate ? "全部分类，含模型不接手的" : "当前候选无评测"}
        />
        <Stat
          label="实时处理速度"
          value={
            learning.runtime_rate?.latency_ms
              ? `${learning.runtime_rate.latency_ms.mean} ms/封`
              : "暂无"
          }
          note={liveRateNote(learning.runtime, learning.runtime_rate)}
        />
      </section>
      <ScanProgress
        progress={learning.model_scan_progress}
        perMinute={learning.runtime_rate?.per_minute ?? 0}
      />
      <div className="training-middle">
        <ModelTrend
          models={allVersions
            .filter((item) => item.kind !== "run")
            .map((item) =>
              item.kind === "staged"
                ? item.model
                : legacyModelForTrend(item.model),
            )}
          config={learning.promotion_gate?.config}
        />
        <aside className="training-gate-summary">
          <h3>
            晋升检查{" "}
            <span
              className={
                learning.promotion_gate?.promotion_eligible
                  ? "status-pass"
                  : "status-hold"
              }
              title="指最新候选版本能不能上线；正在线上跑的模型不受影响"
            >
              {gateVerdict(learning.promotion_gate, configs)}
            </span>
          </h3>
          {learning.promotion_gate && (
            <PromotedCategories gate={learning.promotion_gate} configs={configs} />
          )}
          {learning.promotion_gate ? (
            <GateChecks
              checks={learning.promotion_gate.checks}
              configs={configs}
              showHeading={false}
              compact
            />
          ) : (
            <p role="alert">晋升配置暂不可用，请刷新重试。</p>
          )}
        </aside>
      </div>
      {!!learning.registry_issues?.length && (
        <p role="alert">
          模型 Registry 完整性异常：
          {learning.registry_issues
            .map(
              (issue) => issue.model_id + "（" + issue.integrity_error + "）",
            )
            .join("；")}
        </p>
      )}
      <section className="console-card training-versions">
        <header>
          <div>
            <h3>模型版本</h3>
            <p className="muted">
              完整 ID 可追溯。历史登记为 active 不代表当前运行主模型。
            </p>
          </div>
          <div className="training-filters">
            <label>
              家族{" "}
              <select
                aria-label="模型家族筛选"
                value={familyFilter}
                onChange={(event) => setFamilyFilter(event.target.value)}
              >
                <option value="all">全部家族</option>
                {families.map((value) => (
                  <option key={value} value={value}>
                    {familyLabel(value)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              状态{" "}
              <select
                aria-label="模型状态筛选"
                value={statusFilter}
                onChange={(event) => setStatusFilter(event.target.value)}
              >
                <option value="all">全部状态</option>
                {statuses.map((value) => (
                  <option key={value} value={value}>
                    {statusLabel(value)}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </header>
        {visibleVersions.length ? (
          <div className="responsive-table-wrap">
            <table
              className="settings-table email-model-table"
              aria-label="模型版本"
            >
              <thead>
                <tr>
                  <th>完整模型 ID</th>
                  <th>家族</th>
                  <th>状态</th>
                  <th>训练时间</th>
                  <th>样本数</th>
                  <th>整体准确率</th>
                  <th>P95</th>
                  <th>上线</th>
                  <th>详情</th>
                </tr>
              </thead>
              <tbody>
                {visibleVersions.map((item) => (
                  <tr key={item.model.model_id}>
                    <td title={item.model.model_id}>
                      {shortModelId(item.model.model_id)}
                    </td>
                    <td>
                      {familyLabel(
                        item.kind === "run"
                          ? item.model.model_family || "—"
                          : item.model.model_family || "未提供",
                      )}
                    </td>
                    <td>
                      {item.kind === "run"
                        ? item.model.status === "failed"
                          ? "训练失败"
                          : ["queued", "launching", "running"].includes(item.model.status)
                            ? "训练中"
                            : `训练${item.model.status || "未提供"}`
                        : item.kind === "historical"
                        ? `历史版本 · ${item.model.status || "未提供"}`
                        : `${statusLabel(item.model.status)}${runtime?.active_model_id === item.model.model_id ? " · 当前运行主模型" : ""}`}
                    </td>
                    <td>{localTime(item.model.trained_at)}</td>
                    <td>
                      {item.kind === "run"
                        ? "未产出"
                        : item.kind === "staged"
                          ? (item.model.training?.sample_count ?? "未测量")
                          : (item.model.sample_count ?? "未测量")}
                    </td>
                    <td>
                      {item.kind === "run"
                        ? "未产出"
                        : measured(
                            item.kind === "staged"
                              ? item.model.metrics?.micro_f1
                              : item.model.micro_f1,
                          )}
                    </td>
                    <td>
                      {item.kind === "run"
                        ? "未产出"
                        : measured(
                            item.kind === "staged"
                              ? item.model.end_to_end_latency_ms?.p95
                              : item.model.prediction_latency_p95_ms,
                            " ms",
                          )}
                    </td>
                    <td>
                      {item.kind !== "staged" || !runtimeVerified ? null : runtime
                          ?.active_model_id === item.model.model_id ? (
                        <button
                          type="button"
                          className="danger-button compact-button"
                          aria-label={"改回 Agent " + item.model.model_id}
                          disabled={switching}
                          onClick={() => {
                            requestId.current = crypto.randomUUID();
                            setConfirm({ ...runtime, target: null });
                            setSwitchError("");
                          }}
                        >
                          改回 Agent
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="primary-button compact-button"
                          aria-label={"让这一版上线 " + item.model.model_id}
                          title={
                            runtime?.candidate_model_id !== item.model.model_id
                              ? "只有最新达标的版本能上线"
                              : runtime?.toggle_enabled
                                ? "点了还要确认一次"
                                : "这一版还没达标，看下面的晋升检查"
                          }
                          disabled={
                            switching ||
                            !runtime?.toggle_enabled ||
                            runtime?.candidate_model_id !== item.model.model_id
                          }
                          onClick={() => {
                            requestId.current = crypto.randomUUID();
                            setConfirm({ ...runtime, target: item.model.model_id });
                            setSwitchError("");
                          }}
                        >
                          {switching ? "正在上线…" : "上线"}
                        </button>
                      )}
                    </td>
                    <td>
                      {item.kind === "run" ? (
                        <span className="training-run-reason">
                          {item.reason || "未记录原因"}
                        </span>
                      ) : (
                      <button
                        className="compact-button"
                        aria-label={
                          (item.kind === "historical" ? "历史证据 " : "查看 ") +
                          item.model.model_id
                        }
                        onClick={() => {
                          if (item.kind === "historical") {
                            setSelected("");
                            setLegacy(item.model);
                          } else {
                            setLegacy(null);
                            setSelected(item.model.model_id);
                          }
                        }}
                      >
                        查看
                      </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p>没有符合当前筛选条件的模型版本。</p>
        )}
        <details className="training-mode-history">
          <summary>运行模式切换记录</summary>
          {learning.mode_transitions?.length ? (
            <div className="responsive-table-wrap">
              <table className="settings-table" aria-label="运行模式切换记录">
                <thead>
                  <tr>
                    <th>时间 / 操作者</th>
                    <th>原模式</th>
                    <th>目标模式</th>
                    <th>模型变化</th>
                    <th>结果</th>
                  </tr>
                </thead>
                <tbody>
                  {learning.mode_transitions.map((event, index) => (
                    <tr key={String(event.request_id || index)}>
                      <td>
                        {localTime(
                          typeof event.created_at === "string"
                            ? event.created_at
                            : null,
                        )}{" "}
                        ·{" "}
                        {event.actor === "console-user"
                          ? "控制台用户"
                          : String(event.actor || "未提供")}
                      </td>
                      <td>{modeLabel(String(event.from_mode))}</td>
                      <td>{modeLabel(String(event.to_mode))}</td>
                      <td>
                        {String(event.from_model_id || "无")} →{" "}
                        {String(event.target_model_id || "无")}
                      </td>
                      <td>
                        {statusLabel(String(event.status || ""))} ·{" "}
                        {reasonLabel(String(event.reason || ""))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p>暂无切换记录。</p>
          )}
        </details>
      </section>
      <TrainingSetup
        open={setupOpen}
        sources={learning.training_sources || []}
        families={learning.model_families || []}
        selection={selection}
        onSelectionChange={setSelection}
        onClose={() => setSetupOpen(false)}
        onSubmit={() => void startTraining()}
        previewTraining={previewEmailTraining}
        busy={setupBusy}
        status={trainingStatus}
      />
      <PromotionPanel
        open={promotionOpen}
        gate={learning.promotion_gate}
        configs={configs}
        reload={reload}
        disabled={switching}
        onBusy={onBusy}
        onClose={() => setPromotionOpen(false)}
      />
      {confirm && (
        // A drawer is for reading a thing; this is one question with two
        // answers, so it asks in place.
        <div
          className="training-confirm"
          role="alertdialog"
          aria-modal="true"
          aria-label="确认运行模式"
          aria-describedby="training-confirm-text"
        >
          <p id="training-confirm-text">
            {confirm.target
              ? "让 " +
                shortModelId(confirm.target) +
                " 开始判新邮件，它没把握的仍然交给 Agent。"
              : "改回 Agent 判新邮件，模型退回影子模式。"}
          </p>
          <div>
            <button
              type="button"
              className={confirm.target ? "primary-button" : "danger-button"}
              disabled={switching}
              onClick={() => void switchMode()}
            >
              {switching ? "正在切换…" : "确认切换"}
            </button>
            <button
              type="button"
              className="secondary-button"
              disabled={switching}
              onClick={() => setConfirm(null)}
            >
              取消
            </button>
          </div>
        </div>
      )}
      {selected && (
        <EmailDrawer title="模型版本详情" onClose={() => setSelected("")}>
          {detailError ? (
            <p role="alert">
              {detailError}{" "}
              <button onClick={() => setRetry((value) => value + 1)}>
                重试
              </button>
            </p>
          ) : detail ? (
            <ModelDetails model={detail} />
          ) : (
            <p role="status">正在加载模型证据…</p>
          )}
        </EmailDrawer>
      )}
      {legacy && (
        <LegacyDetails model={legacy} onClose={() => setLegacy(null)} />
      )}
    </section>
    </FamilyNames.Provider>
  );
}
// What the badge beside "晋升检查" says. It is about the newest candidate, not
// the model that is live, and when it is not ready it names what is missing
// instead of a bare "pending".
function gateVerdict(
  gate: EmailLearningEvidence["promotion_gate"] | undefined,
  configs: EmailCategoryConfig[],
) {
  if (!gate) return "暂无数据";
  if (gate.promotion_eligible) return "候选已达标，可上线";
  const missing = gate.checks
    .filter((check) => !check.passed && !check.key.startsWith("category_"))
    .map((check) => checkLabel(check.key, configs));
  return missing.length
    ? `候选未达标 · 差 ${missing.join("、")}`
    : "候选未达标 · 没有类别达到上线门槛";
}

function liveRateNote(
  runtime: EmailRuntime | undefined,
  rate: EmailRuntimeRate | null | undefined,
) {
  if (!rate) return "服务未提供实时数据";
  if (runtime?.mode !== "model_primary") return "模型未上线，没有实时处理";
  const minutes = Math.round(rate.window_seconds / 60);
  if (!rate.latency_ms) return `最近 ${minutes} 分钟没有邮件进来`;
  if (!rate.evaluated) {
    // The model's speed is unchanged by a quiet spell, so the last batch's time
    // is shown, with when that was.
    return `最近 ${minutes} 分钟没有邮件进来 · 上一批${rate.latest_at ? `（${localTime(rate.latest_at)}）` : ""}平均 ${rate.latency_ms.mean} ms · 只算模型耗时`;
  }
  // Only what the model itself spends. Moving or deleting the mail afterwards
  // happens in the mailbox and is timed on the Email page.
  return `最近 ${minutes} 分钟 ${rate.evaluated} 封 · 平均 ${rate.latency_ms.mean} ms · 只算模型耗时，不含邮箱动作`;
}

function waitLabel(minutes: number) {
  if (minutes < 1) return "不到 1 分钟";
  if (minutes < 60) return `约 ${Math.ceil(minutes)} 分钟`;
  const hours = minutes / 60;
  return hours < 24 ? `约 ${hours.toFixed(1)} 小时` : `约 ${Math.ceil(hours / 24)} 天`;
}

function ScanProgress({
  progress,
  perMinute,
}: {
  progress: EmailModelScanProgress | null | undefined;
  perMinute: number;
}) {
  if (!progress || progress.total <= 0) return null;
  const percent = Math.min(100, Math.round((progress.done / progress.total) * 100));
  const finished = progress.remaining === 0;
  return (
    <section className="training-progress" aria-label="模型处理进度">
      <div className="training-progress-head">
        <span>模型处理进度</span>
        <strong>
          {progress.done} / {progress.total} 封 · {percent}%
        </strong>
      </div>
      <div
        className="training-progress-bar"
        role="progressbar"
        aria-label="模型处理进度"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
      >
        <i style={{ width: `${percent}%` }} />
      </div>
      <small>
        {finished
          ? "窗口内的邮件已全部处理，之后只跟进新邮件。"
          : perMinute > 0
            ? `还剩 ${progress.remaining} 封，${waitLabel(progress.remaining / perMinute)}处理完`
            : `还剩 ${progress.remaining} 封，当前没有处理速度，无法估算时间`}
      </small>
    </section>
  );
}

function Stat({
  label,
  value,
  note,
}: {
  label: string;
  value: string;
  note: string;
}) {
  return (
    <article className="training-stat">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{note}</small>
    </article>
  );
}
function Hint({ text }: { text: string }) {
  return (
    <button type="button" className="metric-hint" title={text} aria-label={text}>
      ?
    </button>
  );
}

function shortModelId(modelId: string) {
  return modelId.length <= 26
    ? modelId
    : `${modelId.slice(0, 14)}…${modelId.slice(-8)}`;
}
function legacyModelForTrend(
  model: EmailLearningEvidence["models"][number],
): EmailStagedModel {
  const categories = Object.fromEntries(
    Object.entries(model.per_category_metrics || {}).map(([category, metrics]) => [
      category,
      {
        precision: numberOrNull(metrics.precision),
        recall: numberOrNull(metrics.recall),
        f1: numberOrNull(metrics.f1),
      },
    ]),
  );
  const datasetVersion = model.training_dataset_version || "";
  const validationMethod = model.validation_method || "";
  return {
    model_id: model.model_id,
    model_family: model.model_family,
    status: model.status,
    trained_at: model.trained_at,
    metrics: {
      accuracy: numberOrNull(model.accuracy),
      micro_f1: numberOrNull(model.micro_f1),
      categories,
    },
    evaluation:
      datasetVersion && validationMethod
        ? {
            protocol: `legacy:${validationMethod}`,
            test_digest: datasetVersion,
            comparability_key: datasetVersion,
          }
        : null,
    head_timing_percentiles_ms: null,
    end_to_end_latency_ms: {
      p50: numberOrNull(model.prediction_latency_p50_ms),
      p95: numberOrNull(model.prediction_latency_p95_ms),
      p99: null,
    },
    compatibility: {
      enabled_categories: Object.keys(categories).sort(),
      description_version: model.training_dataset_version || "legacy",
    },
  };
}
function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
function ModelTrend({
  models,
  config,
}: {
  models: EmailStagedModel[];
  config?: EmailPromotionConfig;
}) {
  const familyLabel = useFamilyLabel();
  const [metric, setMetric] = useState<TrendMetric>("micro_f1");
  const [category, setCategory] = useState("");
  const categories = Array.from(
    new Set(
      models.flatMap((model) => Object.keys(model.metrics?.categories || {})),
    ),
  );
  const activeCategory = category || categories[0] || "";
  const points = trendPoints(
    [...models].sort((a, b) => a.trained_at.localeCompare(b.trained_at)),
    metric,
    activeCategory,
  );
  const latency = ["p50", "p95", "p99"].includes(metric);
  const target =
    metric === "micro_f1"
      ? config?.micro_f1_min
      : metric === "precision"
        ? config?.category_precision_min
        : metric === "p95"
          ? config?.p95_latency_max_ms
          : undefined;
  const lineSeries = trendLineSeries(points);
  const data = lineSeries.data.map((row, index) => ({
    ...row,
    description: `${points[index].model.model_id} · ${localTime(points[index].model.trained_at)} · ${points[index].reason || points[index].model.evaluation?.test_digest || ""}`,
  }));
  const trendFamilies = lineSeries.families;
  const values = points
    .map((point) => point.value)
    .filter((value): value is number => value !== null);
  const domain = values.length
    ? latency
      ? [Math.min(...values), Math.max(...values)]
      : [Math.min(...values, 0), Math.max(...values, 1)]
    : undefined;
  return (
    <section className="console-card training-trend" aria-label="模型能力趋势">
      <header>
        <div>
          <h3>能力趋势</h3>
          <p className="muted">仅连接家族、评测和类别一致的版本。</p>
        </div>
        <label>
          指标{" "}
          <select
            value={metric}
            onChange={(event) => setMetric(event.target.value as TrendMetric)}
          >
            {[
              "micro_f1",
              "accuracy",
              "precision",
              "recall",
              "f1",
              "p50",
              "p95",
              "p99",
            ].map((key) => (
              <option key={key} value={key}>
                {key}
              </option>
            ))}
          </select>
        </label>
      </header>
      {["precision", "recall", "f1"].includes(metric) && (
        <label>
          类别{" "}
          <select
            value={activeCategory}
            onChange={(event) => setCategory(event.target.value)}
          >
            {categories.map((key) => (
              <option key={key}>{key}</option>
            ))}
          </select>
        </label>
      )}
      {values.length ? (
        <div className="email-trend-chart">
          <ResponsiveContainer width="100%" height={250}>
            <LineChart data={data} accessibilityLayer>
              <XAxis
                dataKey="name"
                tickFormatter={(name) => String(name).slice(-10)}
                interval="preserveStartEnd"
                tick={{ fontSize: 11 }}
              />
              <YAxis domain={domain} />
              <Tooltip
                labelFormatter={(_label, items) =>
                  items[0]?.payload.description
                }
                formatter={(value) => measured(value, latency ? " ms" : "%")}
              />
              {target !== undefined && (
                <ReferenceLine
                  y={target}
                  stroke="var(--ink-soft)"
                  strokeDasharray="4 4"
                  label="晋升门槛"
                />
              )}
              {trendFamilies.map((family) => (
                <Line
                  key={family}
                  dataKey={`family:${family}`}
                  name={familyLabel(family)}
                  type="linear"
                  stroke={familyColor(family)}
                  connectNulls
                  dot
                  isAnimationActive={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <p>暂无可测量趋势数据。</p>
      )}
      {trendFamilies.length > 0 && (
        <div className="training-trend-legend" aria-label="模型家族图例">
          {trendFamilies.map((family) => (
            <span key={family}>
              <i style={{ background: familyColor(family) }} />
              {familyLabel(family)}
            </span>
          ))}
        </div>
      )}
      <details>
        <summary>趋势文字数据</summary>
        <table className="settings-table">
          <thead>
            <tr>
              <th>模型 ID</th>
              <th>指标</th>
              <th>评测 / 断点原因</th>
            </tr>
          </thead>
          <tbody>
            {points.map((point) => (
              <tr key={point.model.model_id}>
                <td>{point.model.model_id}</td>
                <td>
                  {measured(
                    modelMetric(point.model, metric, activeCategory),
                    latency ? " ms" : "%",
                  )}
                </td>
                <td>
                  {point.reason ||
                    point.model.evaluation?.test_digest ||
                    "未提供"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </section>
  );
}
function familyColor(family: string) {
  const colors = ["#0f766e", "#4f46e5", "#c26b09", "#b42366", "#2f855a"];
  let hash = 0;
  for (const character of family)
    hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
  return colors[hash % colors.length];
}
function ModelDetails({ model }: { model: EmailStagedModel }) {
  const [tab, setTab] = useState("effect");
  return (
    <div className="email-drawer-content">
      <h3>{model.model_id}</h3>
      <p>
        状态：{statusLabel(model.status)} · 训练时间：
        {localTime(model.trained_at)}
      </p>
      <div
        role="tablist"
        className="settings-pill-row"
        aria-label="模型详情分区"
      >
        <button
          role="tab"
          aria-selected={tab === "effect"}
          onClick={() => setTab("effect")}
        >
          效果
        </button>
        <button
          role="tab"
          aria-selected={tab === "data"}
          onClick={() => setTab("data")}
        >
          数据与参数
        </button>
        <button
          role="tab"
          aria-selected={tab === "technical"}
          onClick={() => setTab("technical")}
        >
          技术信息
        </button>
      </div>
      {tab === "effect" && <EffectDetails model={model} />}{" "}
      {tab === "data" && <DataDetails model={model} />}{" "}
      {tab === "technical" && <TechnicalDetails model={model} />}
      <details>
        <summary>原始版本证据</summary>
        <pre>{JSON.stringify(model, null, 2)}</pre>
      </details>
    </div>
  );
}
function EffectDetails({ model }: { model: EmailStagedModel }) {
  return (
    <section aria-label="效果">
      <p>
        整体准确率（全部分类）{measured(model.metrics?.accuracy)}
      </p>
      <p className="muted">
        晋升检查里的 Micro F1 只统计将要上线的分类，所以会高于这个数。
      </p>
      <CategoryMetrics model={model} />
      <h4>「是否重要」判断（与分类无关的独立输出头）</h4>
      {["precision", "recall", "f1", "accepted_precision"].map((key) => (
        <p key={key}>
          {key}：{measured(model.metrics?.important?.[key])}
        </p>
      ))}
      <h4>延迟</h4>
      {(["p50", "p95", "p99"] as const).map((key) => (
        <p key={key}>
          {key} · 端到端 {measured(model.end_to_end_latency_ms?.[key], " ms")} ·
          输出头 {measured(model.head_timing_percentiles_ms?.[key], " ms")}
        </p>
      ))}
      <p>首次调用 / 模型加载耗时：未测量（当前未提供测量证据）</p>
    </section>
  );
}
function DataDetails({ model }: { model: EmailStagedModel }) {
  return (
    <section aria-label="训练记录">
      <h4>训练记录</h4>
      <dl className="detail-definition-list">
        <Detail
          label="开始时间"
          value={localTime(model.training?.started_at)}
        />
        <Detail
          label="完成时间"
          value={localTime(model.training?.completed_at)}
        />
        <Detail
          label="训练耗时"
          value={measured(model.training?.duration_ms, " ms")}
        />
        <Detail
          label="总样本 / 新增样本"
          value={`${model.training?.sample_count ?? "未测量"} / ${model.training?.new_sample_count ?? "未测量"}`}
        />
        <Detail
          label="分类样本"
          value={model.training?.category_sample_count ?? "未测量"}
        />
        <Detail
          label="邮箱账户 / 事项组覆盖"
          value={`${model.training?.account_count ?? "未测量"} / ${model.training?.group_count ?? "未测量"}`}
        />
        <Detail
          label="训练 / 验证 / 测试样本"
          value={`${model.split_counts?.train ?? "未测量"} / ${model.split_counts?.validation ?? "未测量"} / ${model.split_counts?.test ?? "未测量"}`}
        />
        <Detail
          label="训练数据版本"
          value={model.training_snapshot_id ?? "未提供"}
        />
      </dl>
      <h4>训练参数</h4>
      <dl className="detail-definition-list">
        {[
          ["随机种子", model.parameters?.random_seed],
          ["求解器", model.parameters?.solver],
          ["最大迭代次数", model.parameters?.max_iter],
          ["正则化系数", model.parameters?.regularization_alpha],
          ["隐藏层", model.parameters?.hidden_layer_sizes],
          ["描述权重 α", model.parameters?.alpha],
          ["模型权重 β", model.parameters?.beta],
        ].map(([label, value]) => (
          <Detail
            key={String(label)}
            label={String(label)}
            value={
              value == null
                ? "未测量"
                : Array.isArray(value)
                  ? value.join("、")
                  : value
            }
          />
        ))}
      </dl>
    </section>
  );
}
function TechnicalDetails({ model }: { model: EmailStagedModel }) {
  const familyLabel = useFamilyLabel();
  return (
    <section aria-label="技术信息">
      <h4>模型版本与评测</h4>
      <dl className="detail-definition-list">
        <Detail label="完整模型 ID" value={model.model_id} />
        <Detail
          label="模型家族"
          value={
            familyLabel(
              String(
                model.model_family || model.compatibility?.head_format || "未提供",
              ),
            )
          }
        />
        <Detail
          label="Embedding 版本"
          value={model.compatibility?.embedding_revision_reference ?? "未提供"}
        />
        <Detail
          label="描述版本"
          value={model.compatibility?.description_version ?? "未提供"}
        />
        <Detail
          label="输入 Schema"
          value={model.compatibility?.input_schema_version ?? "未提供"}
        />
        <Detail
          label="评测方法"
          value={model.evaluation?.protocol ?? "未提供"}
        />
        <Detail
          label="测试集摘要"
          value={model.evaluation?.test_digest ?? "未提供"}
        />
        <Detail
          label="Artifact SHA-256"
          value={model.artifact_sha256 ?? "未提供"}
        />
      </dl>
    </section>
  );
}
function Detail({ label, value }: { label: string; value: unknown }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{String(value)}</dd>
    </div>
  );
}
function CategoryMetrics({ model }: { model: EmailStagedModel }) {
  return (
    <div className="responsive-table-wrap">
      <table className="settings-table email-metric-table">
        <thead>
          <tr>
            <th>类别</th>
            <th>
              Precision
              <Hint text="逼它对每封邮件都表态时，它说属于这一类的邮件里有多少判对。" />
            </th>
            <th>
              Recall
              <Hint text="这一类真实的邮件里，它认出了多少。认不出的不是判错，是它没把握、退给 Agent。" />
            </th>
            <th>
              F1
              <Hint text="Precision 和 Recall 的调和平均。它把「没把握而退回」也算成扣分，所以会明显低于接受准确率。" />
            </th>
            <th>
              support
              <Hint text="这一类在这次评估里一共有多少封邮件。" />
            </th>
            <th>
              接受准确率
              <Hint text="英文 accepted precision，也就是「带弃权的预测」里的选择性精度：只算它有把握、自己拍板的那些邮件，其中有多少判对；没把握的退给 Agent，不进这个分母。所以一个分类可以「大部分不会，但会的都很准」——legal 的 F1 只有 59%（80 封里它认出不到一半），拍板的 25 件却有 92.6% 是对的。上线看的就是这个数。" />
            </th>
            <th>
              接受数量 / 独立事项组
              <Hint text="它拍板了多少件事。同一条邮件线程只算一件——十封来回讨论同一份合同是一个证据，不是十个。" />
            </th>
            <th>
              阈值
              <Hint text="把握超过这个分数才自己拍板，低于就退给 Agent。阈值是训练时挑出来的：在保证接受准确率达标的前提下，尽量多接。" />
            </th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(model.metrics?.categories || {}).map(
            ([key, value]) => (
              <tr key={key}>
                <td data-label="类别">{key}</td>
                <td data-label="Precision">{measured(value.precision)}</td>
                <td data-label="Recall">{measured(value.recall)}</td>
                <td data-label="F1">{measured(value.f1)}</td>
                <td data-label="support">{value.support ?? "未测量"}</td>
                <td data-label="接受准确率">{measured(value.accepted_precision)}</td>
                <td data-label="接受数量 / 独立事项组">
                  {value.accepted_hits ?? "未测量"} /{" "}
                  {value.independent_groups ?? "未测量"}
                </td>
                <td data-label="阈值">{measured(value.threshold)}</td>
              </tr>
            ),
          )}
        </tbody>
      </table>
    </div>
  );
}
function LegacyDetails({
  model,
  onClose,
}: {
  model: EmailLearningEvidence["models"][number];
  onClose: () => void;
}) {
  const familyLabel = useFamilyLabel();
  return (
    <EmailDrawer title="历史模型证据" onClose={onClose}>
      <div className="email-drawer-content">
        <h3>{model.model_id}</h3>
        <p>
          模型家族：{familyLabel(model.model_family || "未提供")} · 历史登记状态：
          {model.status}
        </p>
        <p>
          整体准确率（全部分类）{measured(model.accuracy)} · P95{" "}
          {measured(model.prediction_latency_p95_ms, " ms")}
        </p>
        <p className="muted">
          历史登记状态不代表当前新邮件的主模型；当前运行状态只来自
          runtime.active_model_id。
        </p>
        <details>
          <summary>原始模型证据</summary>
          <pre>{JSON.stringify(model, null, 2)}</pre>
        </details>
      </div>
    </EmailDrawer>
  );
}


function PromotedCategories({
  gate,
  configs,
}: {
  gate: EmailLearningEvidence["promotion_gate"];
  configs: EmailCategoryConfig[];
}) {
  const promoted = gate.promoted_categories || [];
  const name = (key: string) =>
    configs.find((item) => item.category_key === key)?.display_name || key;
  return (
    <p className="training-promoted" aria-label="按分类上线">
      {promoted.length
        ? `可上线的分类：${promoted.map(name).join("、")}。其余分类继续由 Agent 处理${
            gate.important_promoted === false ? "，模型不会标记重要邮件" : ""
          }。`
        : "暂无分类达标，所有邮件继续由 Agent 处理。"}
    </p>
  );
}
