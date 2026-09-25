import { parseWorkbenchTimestamp } from "../presentation";

const clock = new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
const fullFormat = new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });

function dayStart(date: Date) { return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime(); }

/** "刚刚 / 12 分钟前 / 今天 09:30 / 昨天 09:30 / 9月24日 09:30 / 2025/09/24 09:30". Unparsable text is shown as it came. */
export function formatTaskTime(value: string, now = new Date()): string {
  const parsed = parseWorkbenchTimestamp(value);
  if (!parsed) return value;
  const minutes = Math.floor((now.getTime() - parsed.getTime()) / 60_000);
  if (minutes >= 0 && minutes < 1) return "刚刚";
  if (minutes >= 1 && minutes < 60) return `${minutes} 分钟前`;
  const days = Math.round((dayStart(now) - dayStart(parsed)) / 86_400_000);
  if (days === 0) return `今天 ${clock.format(parsed)}`;
  if (days === 1) return `昨天 ${clock.format(parsed)}`;
  return parsed.getFullYear() === now.getFullYear() ? `${parsed.getMonth() + 1}月${parsed.getDate()}日 ${clock.format(parsed)}` : fullFormat.format(parsed);
}

/** A <time> whose visible text is short and whose tooltip is the full local date. */
export function TaskTime({ value }: { value: string }) {
  if (!value) return <span>未提供</span>;
  const parsed = parseWorkbenchTimestamp(value);
  if (!parsed) return <span>{value}</span>;
  return <time dateTime={parsed.toISOString()} title={fullFormat.format(parsed)}>{formatTaskTime(value)}</time>;
}
