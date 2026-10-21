import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { getStatus, type WorkerStatus } from "../api/console";
import {
  getDashboardQueue,
  type DashboardQueue,
  type DashboardQueueItem,
  type QueueState,
} from "../api/dashboard";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { StatusBadge } from "../components/status/StatusBadge";
import { SnapshotBadge } from "../components/status/SnapshotBadge";
import { parseWorkbenchTimestamp } from "../presentation";
import { AttentionPanel } from "./AttentionPage";
import { HistoryPage } from "./HistoryPage";
import { TaskTime } from "./TaskTime";
import "./dashboard.css";

type Tab = "failed" | QueueState;
const tabs: { id: Tab; label: string }[] = [
  { id: "failed", label: "Failed" },
  { id: "active", label: "Active tasks" },
  { id: "queued", label: "Queued" },
];
function duration(value: string, snapshot: string) {
  const start = parseWorkbenchTimestamp(value),
    end = parseWorkbenchTimestamp(snapshot);
  if (!start || !end) return "未提供";
  const seconds = Math.max(
    0,
    Math.floor((end.getTime() - start.getTime()) / 1000),
  );
  return seconds < 60
    ? `${seconds}s`
    : seconds < 3600
      ? `${Math.floor(seconds / 60)}m ${seconds % 60}s`
      : `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}
export function retryLabel(row: DashboardQueueItem, state: QueueState) {
  if (row.id.startsWith("scheduled_task_runs:")) return "本次触发";
  if (row.retry_number === null) return "重试次数未提供";
  if (row.retry_number === 0) return "首次执行";
  return state === "active"
    ? `第 ${row.retry_number} 次 retry 中`
    : `即将第 ${row.retry_number} 次 retry`;
}
function QueueRows({
  queue,
  state,
  page,
  setPage,
}: {
  queue: DashboardQueue;
  state: QueueState;
  page: number;
  setPage: (page: number) => void;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  return (
    <>
      <div className="dashboard-queue-caption">
        <span>
          {queue.meta.total} 个
          {state === "active" ? "任务正在执行" : "任务等待执行"}
        </span>
        <span>
          {state === "active" ? "包含正在执行的重试" : "包含首次执行和等待重试"}
        </span>
      </div>
      {!queue.items.length ? (
        <div className="page-state">
          {state === "active" ? "当前没有运行中的任务" : "当前没有排队任务"}
        </div>
      ) : (
        <div className={`dashboard-queue-list dashboard-queue-${state}`}>
          <div className="dashboard-queue-columns" aria-hidden="true">
            <span>{state === "active" ? "Agent / 模型" : "任务来源"}</span>
            <span>任务 / 当前状态</span>
            <span>{state === "active" ? "运行时长" : "可执行时间"}</span>
            <span>执行轮次</span>
          </div>
          {queue.items.map((row) => (
            <article className="dashboard-task" key={row.id}>
              <div className="dashboard-task-row">
                <div>
                  <strong>
                    {state === "active" ? row.agent || row.source : row.source}
                  </strong>
                  <small>
                    {state === "active"
                      ? [row.runtime_kind, row.model]
                          .filter(Boolean)
                          .join(" · ")
                      : ""}
                  </small>
                </div>
                <div>
                  <button
                    className="dashboard-task-title"
                    type="button"
                    aria-expanded={expanded === row.id}
                    onClick={() =>
                      setExpanded(expanded === row.id ? null : row.id)
                    }
                  >
                    {row.title}
                  </button>
                  <small>
                    {state === "active"
                      ? row.source
                      : row.error
                        ? "失败后等待重试"
                        : "等待调度"}
                  </small>
                  {state === "active" && (
                    <small>
                      <StatusBadge
                        value={row.agent === "audit" ? "audit" : row.stage}
                      />
                    </small>
                  )}
                </div>
                <div>
                  {state === "active" ? (
                    <>
                      <strong>
                        {duration(row.started_at, queue.meta.snapshot_at)}
                      </strong>
                      <small>
                        最近更新 <TaskTime value={row.updated_at} />
                      </small>
                    </>
                  ) : (
                    <>
                      {row.available_at ? (
                        <TaskTime value={row.available_at} />
                      ) : (
                        <span>尚未安排时间</span>
                      )}
                      <small>
                        已等待{" "}
                        {duration(row.updated_at, queue.meta.snapshot_at)}
                      </small>
                    </>
                  )}
                </div>
                <div>
                  <span className={row.retry_number ? "dashboard-retry" : ""}>
                    {retryLabel(row, state)}
                  </span>
                </div>
              </div>
              {expanded === row.id && (
                <div className="dashboard-task-detail">
                  <dl>
                    <dt>任务</dt>
                    <dd>{row.id}</dd>
                    <dt>原始状态</dt>
                    <dd>{row.status}</dd>
                    <dt>最近更新</dt>
                    <dd>
                      <TaskTime value={row.updated_at} />
                    </dd>
                    <dt>执行轮次</dt>
                    <dd>{retryLabel(row, state)}</dd>
                    {row.error && (
                      <>
                        <dt>上次错误</dt>
                        <dd>{row.error}</dd>
                      </>
                    )}
                  </dl>
                  {row.detail_url && (
                    <Link to={row.detail_url}>查看关联执行记录 →</Link>
                  )}
                </div>
              )}
            </article>
          ))}
        </div>
      )}
      {queue.meta.total > queue.meta.page_size && (
        <nav className="dashboard-pagination" aria-label={`${state}队列分页`}>
          <span>
            {page} / {Math.ceil(queue.meta.total / queue.meta.page_size)}
          </span>
          <button
            type="button"
            className="secondary-button"
            disabled={page === 1}
            onClick={() => setPage(page - 1)}
          >
            上一页
          </button>
          <button
            type="button"
            className="secondary-button"
            disabled={!queue.meta.has_more}
            onClick={() => setPage(page + 1)}
          >
            下一页
          </button>
        </nav>
      )}
    </>
  );
}
export function DashboardPage() {
  const [selected, setSelected] = useState<Tab>("failed");
  const [failedCount, setFailedCount] = useState<number | null>(null);
  const [epoch, setEpoch] = useState(0);
  const [service, setService] = useState<WorkerStatus | null>(null);
  const [snapshot, setSnapshot] = useState("");
  const [serviceError, setServiceError] = useState("");
  const [serviceDetails, setServiceDetails] = useState(false);
  const [queue, setQueue] = useState<DashboardQueue>();
  const [queueError, setQueueError] = useState("");
  const [loading, setLoading] = useState(true);
  const [pages, setPages] = useState({ active: 1, queued: 1 });
  const userSelected = useRef(false),
    initialSelection = useRef(false);
  const queueState: QueueState = selected === "queued" ? "queued" : "active";
  const previousQueueState = useRef(queueState);
  const countChanged = useCallback((count: number) => {
    setFailedCount(count);
    if (!initialSelection.current) {
      initialSelection.current = true;
      if (!userSelected.current) setSelected(count > 0 ? "failed" : "active");
    }
  }, []);
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible")
        setEpoch((value) => value + 1);
    }, 10_000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    getStatus(controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) {
          setService(result.item);
          setSnapshot(result.meta.snapshot_at);
          setServiceError("");
        }
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted)
          setServiceError(
            error instanceof Error ? error.message : "Service 状态读取失败",
          );
      });
    return () => controller.abort();
  }, [epoch]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setQueueError("");
    if (previousQueueState.current !== queueState) {
      setQueue(undefined);
      previousQueueState.current = queueState;
    }
    getDashboardQueue(queueState, pages[queueState], controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) {
          const lastPage = Math.max(
            1,
            Math.ceil(result.meta.total / result.meta.page_size),
          );
          if (pages[queueState] > lastPage) {
            setPages((current) => ({ ...current, [queueState]: lastPage }));
            return;
          }
          setQueue(result);
          setLoading(false);
        }
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setQueueError(
            error instanceof Error ? error.message : "队列读取失败",
          );
          setLoading(false);
        }
      });
    return () => controller.abort();
  }, [queueState, pages, epoch]);
  const select = (tab: Tab) => {
    userSelected.current = true;
    setSelected(tab);
  };
  return (
    <ConsolePageLayout title="Dashboard" showHeader={false}>
      <div className="dashboard-page">
        <section
          className="console-card dashboard-service"
          aria-label="Service 运行状态"
        >
          <div className="dashboard-service-line">
            <strong
              className={service?.service.ok ? "dashboard-service-up" : ""}
            >
              {service
                ? `Service ${service.service.ok ? "运行中" : service.service.state}`
                : "Service 加载中…"}
            </strong>
            {service && (
              <span className="dashboard-health">
                <StatusBadge value={service.system_health.state} />
              </span>
            )}
            <span className="dashboard-service-snapshot">
              <SnapshotBadge timestamp={snapshot} />
            </span>
            <div className="dashboard-service-actions">
              <button
                type="button"
                className="details-toggle"
                aria-expanded={serviceDetails}
                onClick={() => setServiceDetails(!serviceDetails)}
              >
                系统详情
              </button>
              <button
                type="button"
                className="secondary-button"
                aria-label="刷新 Dashboard"
                onClick={() => setEpoch((value) => value + 1)}
              >
                刷新
              </button>
            </div>
          </div>
          {serviceError && (
            <p className="inline-alert" role="alert">
              Service 状态读取失败：{serviceError}
              {service && "（显示上次快照）"}
            </p>
          )}
          {serviceDetails && service && (
            <div className="dashboard-service-detail">
              <span>
                PID {service.service.pid} · {service.service.detail}
              </span>
              <span>{service.system_health.detail}</span>
              <div>
                {Object.entries(service.connectors).map(([name, value]) => (
                  <span key={name}>
                    {name} <StatusBadge value={value.state} />
                  </span>
                ))}
              </div>
            </div>
          )}
        </section>
        <section
          className="console-card dashboard-queues"
          aria-label="任务与问题队列"
        >
          <div className="dashboard-tabs" role="tablist" aria-label="队列分类">
            {tabs.map((tab) => (
              <button
                type="button"
                role="tab"
                id={`dashboard-tab-${tab.id}`}
                aria-controls={`dashboard-panel-${tab.id}`}
                aria-selected={selected === tab.id}
                key={tab.id}
                onClick={() => select(tab.id)}
                onKeyDown={(event) => {
                  if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
                    event.preventDefault();
                    const index = tabs.findIndex(
                      (value) => value.id === tab.id,
                    );
                    const next =
                      tabs[(index + (event.key === "ArrowRight" ? 1 : 2)) % 3]
                        .id;
                    select(next);
                    document.getElementById(`dashboard-tab-${next}`)?.focus();
                  }
                }}
              >
                {tab.label}{" "}
                <span
                  className={
                    tab.id === "failed" && failedCount
                      ? "dashboard-count-failed"
                      : "dashboard-count"
                  }
                >
                  {tab.id === "failed"
                    ? (failedCount ?? "—")
                    : (queue?.counts[tab.id] ?? "—")}
                </span>
              </button>
            ))}
          </div>
          <div
            role="tabpanel"
            id="dashboard-panel-failed"
            aria-labelledby="dashboard-tab-failed"
            hidden={selected !== "failed"}
          >
            <AttentionPanel
              compact
              refreshKey={epoch}
              onCountChange={countChanged}
            />
          </div>
          {(["active", "queued"] as const).map((tab) => (
            <div
              role="tabpanel"
              id={`dashboard-panel-${tab}`}
              aria-labelledby={`dashboard-tab-${tab}`}
              hidden={selected !== tab}
              key={tab}
            >
              {selected === tab && (
                <>
                  {queueError ? (
                    <div className="page-state page-state-error" role="alert">
                      {queueError}
                    </div>
                  ) : !queue && loading ? (
                    <div className="page-state" role="status">
                      正在加载队列…
                    </div>
                  ) : (
                    queue && (
                      <QueueRows
                        queue={queue}
                        state={tab}
                        page={pages[tab]}
                        setPage={(page) =>
                          setPages((current) => ({ ...current, [tab]: page }))
                        }
                      />
                    )
                  )}
                </>
              )}
            </div>
          ))}
        </section>
        <HistoryPage embedded />
      </div>
    </ConsolePageLayout>
  );
}
