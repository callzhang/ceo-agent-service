import { formatWorkbenchDateTime } from "../../presentation";

export function SnapshotBadge({ timestamp, refreshing = false }: { timestamp: string; refreshing?: boolean }) {
  const text = timestamp ? formatWorkbenchDateTime(timestamp)?.label ?? timestamp : "暂无快照";
  return <span className="snapshot-badge" role="status">{refreshing ? "刷新中 · " : "更新于 "}{text}</span>;
}
