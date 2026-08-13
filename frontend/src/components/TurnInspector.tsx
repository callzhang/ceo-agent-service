import { formatWorkbenchDateTime, parseWorkbenchTimestamp, taskStateLabel } from "../presentation";
import type { RuntimeCapabilities, Task, Timeline, WorkbenchStats } from "../types";

interface TurnInspectorProps {
  task: Task | null;
  timeline: Timeline | null;
  capabilities: RuntimeCapabilities[] | null;
  stats: WorkbenchStats | null;
}

const capabilityLabels: Record<keyof RuntimeCapabilities["capabilities"], string> = {
  session_resume: "会话续接",
  streamed_text: "流式文本",
  structured_tools: "结构化工具",
  image_input: "图片输入",
  model_selection: "模型选择",
  mcp_configuration: "MCP 配置",
  stoppable: "安全停止",
  recoverable: "失败恢复",
};

type Duration = { seconds: number } | "incomplete" | "unknown";

function durationBetween(start: string, end: string): Duration {
  if (!start || !end) return "incomplete";
  const startValue = parseWorkbenchTimestamp(start);
  const endValue = parseWorkbenchTimestamp(end);
  if (!startValue || !endValue || endValue.getTime() < startValue.getTime()) return "unknown";
  return { seconds: (endValue.getTime() - startValue.getTime()) / 1000 };
}

export function TurnInspector({ task, timeline, capabilities, stats }: TurnInspectorProps) {
  if (!task) return <p className="inspector-empty">选择任务后，这里会显示执行信息。</p>;
  const latest = timeline?.turns[0] ?? null;
  const events = timeline?.events ?? [];
  const toolCount = events.filter((event) => event.event_type === "tool_started").length;
  const fileCount = events.filter((event) => event.event_type === "file_changed").length;
  const errorCount = events.filter((event) => event.event_type === "turn_failed" || (event.event_type === "tool_completed" && event.payload.status === "failed")).length;
  const truncated = Boolean(timeline && (timeline.has_more || timeline.events_has_more || timeline.artifacts_has_more || timeline.confirmations_has_more || timeline.attachments_has_more));
  const runtime = capabilities?.find((item) => item.kind === task.runtime_kind);
  const unavailableOptionalRuntimes = capabilities
    ? ["claude", "pi"].filter((kind) => !capabilities.some((item) => item.kind === kind))
    : [];
  const duration = durationBetween(latest?.started_at ?? "", latest?.completed_at ?? "");
  const updatedAt = formatWorkbenchDateTime(task.updated_at);
  return (
    <div data-testid="turn-inspector">
      <dl className="detail-list">
        <div><dt>运行时</dt><dd>{task.runtime_kind}</dd></div>
        <div><dt>最近更新</dt><dd>{updatedAt ? <time dateTime={updatedAt.dateTime}>{updatedAt.label}</time> : "时间未知"}</dd></div>
        <div><dt>状态</dt><dd>{taskStateLabel(latest?.status ?? task.state)}</dd></div>
        <div><dt>耗时</dt><dd>{duration === "incomplete" ? "尚未完成" : duration === "unknown" ? "耗时未知" : `${duration.seconds.toFixed(1)} 秒`}</dd></div>
      </dl>
      <section className="inspector-section">
        <h3>当前已加载页面</h3>
        <p>工具 {toolCount} · 文件 {fileCount} · 产物 {timeline?.artifacts.length ?? 0} · 错误 {errorCount}</p>
        {truncated && <p className="truncation-note">统计可能不完整：仍有较早记录或资源未载入。</p>}
      </section>
      <section className="inspector-section">
        <h3>执行检查</h3>
        <ul className="checklist">
          <li>{latest?.started_at ? "✓ 已开始" : "○ 等待开始"}</li>
          <li>{latest?.completed_at ? "✓ 已结束" : "○ 尚未结束"}</li>
          <li>{latest?.status === "failed" ? "! 存在错误" : "✓ 未记录终止错误"}</li>
        </ul>
      </section>
      <section className="inspector-section">
        <h3>运行时能力</h3>
        {capabilities === null ? <p>运行时能力暂未读取，不能确认当前可用性。</p> : runtime ? (
          <>
            <p>{task.runtime_kind} 已在运行时注册表中启用。</p>
            <ul className="capability-list">
              {Object.entries(runtime.capabilities).map(([name, available]) => (
                <li key={name}>{available ? "✓" : "—"} {capabilityLabels[name as keyof RuntimeCapabilities["capabilities"]]} <small>{name}</small></li>
              ))}
            </ul>
          </>
        ) : <p>{task.runtime_kind === "codex" ? "Codex 运行时当前不可用，请检查服务注册。" : `${task.runtime_kind} 未在运行时注册表中启用。`}</p>}
        {unavailableOptionalRuntimes.length > 0 && <p className="runtime-note">{unavailableOptionalRuntimes.map((kind) => kind === "claude" ? "Claude" : "Pi").join("、")} 未注册，无法创建对应运行时任务。</p>}
      </section>
      {stats && <section className="inspector-section"><h3>工作台总览</h3><p>任务 {stats.tasks.total} · 活跃 {stats.tasks.active} · 产物 {stats.artifacts}</p></section>}
    </div>
  );
}
