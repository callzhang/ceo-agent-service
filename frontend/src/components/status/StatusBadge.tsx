const labels: Record<string, string> = {
  active: "进行中",
  completed: "已完成",
  done: "已完成",
  failed: "失败",
  pending: "待处理",
  processing: "处理中",
  running: "运行中",
  queued: "排队中",
  stopped: "已停止",
  waiting_confirmation: "等待确认",
  not_started: "未开始",
  over_due: "已逾期",
};

export function StatusBadge({ value }: { value: string }) {
  const label = labels[value] || value || "未提供";
  return <span className={`status-badge status-${value || "unknown"}`}>{label}</span>;
}
