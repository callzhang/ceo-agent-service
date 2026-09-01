import { useEffect, useMemo, useState } from "react";
import { Bar, BarChart, Brush, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { HistoryChart } from "../../api/console";

const COLORS = ["#176b50", "#237fc2", "#99610d", "#a43a34", "#7b61a8", "#0f766e", "#c05621", "#64748b"];
const RANGE_OPTIONS = [
  { value: "24h", label: "24 小时", title: "最近 24 小时事件" },
  { value: "1w", label: "1 周", title: "最近 1 周事件" },
  { value: "1m", label: "1 个月", title: "最近 1 个月事件" },
] as const;

interface StackedBarChartProps { chart?: HistoryChart; loading?: boolean; range?: string; onRangeChange?: (range: string) => void; }

function normalizedSeries(series: HistoryChart["series"], length: number) {
  return series.map((item) => ({ ...item, data: Array.from({ length }, (_, index) => Math.max(0, Number(item.data[index] || 0))) }));
}
function rangeTitle(value: string) { return RANGE_OPTIONS.find((option) => option.value === value)?.title || RANGE_OPTIONS[0].title; }
function rangeLabel(value: string) { return RANGE_OPTIONS.find((option) => option.value === value)?.label || RANGE_OPTIONS[0].label; }

export function StackedBarChart({ chart, loading = false, range, onRangeChange }: StackedBarChartProps) {
  const activeRange = range || "24h";
  const labels = chart?.labels || [];
  const series = useMemo(() => normalizedSeries(chart?.series || [], labels.length), [chart?.series, labels.length]);
  const [brushWindow, setBrushWindow] = useState<[number, number]>([0, Math.max(0, labels.length - 1)]);

  useEffect(() => { setBrushWindow([0, Math.max(0, labels.length - 1)]); }, [labels.length, activeRange]);

  const chartData = labels.map((label, index) => {
    const row: Record<string, string | number> = { label };
    for (const item of series) row[item.name] = item.data[index] || 0;
    return row;
  });
  const totalEvents = chart?.total || 0;
  const rangeChange = (nextRange: string) => {
    onRangeChange?.(nextRange);
  };

  return <section className="card history-chart-card" aria-label="Recent 24 hour events">
    <div className="history-chart-head"><h2 className="history-chart-title">{rangeTitle(activeRange)}</h2><div className="history-chart-head-meta">{loading && labels.length > 0 && <span className="history-chart-loading" role="status">正在更新…</span>}<span className="pill">{totalEvents ? `${totalEvents} events` : "- events"}</span></div></div>
    <div className="history-chart-range-tabs" role="tablist" aria-label="事件时间范围">{RANGE_OPTIONS.map((option) => <button key={option.value} type="button" role="tab" aria-selected={activeRange === option.value} className={activeRange === option.value ? "active" : ""} onClick={() => rangeChange(option.value)}>{option.label}</button>)}</div>
    {!labels.length || !series.length ? <div className="history-chart-empty" role={loading ? "status" : undefined}>{loading ? "正在加载…" : "暂无事件"}</div> : <>
      <div className="history-chart-scroll" role="img" aria-label={`${rangeLabel(activeRange)}共 ${totalEvents} 个事件`}><div className="history-chart-recharts" data-testid="history-chart-brush"><ResponsiveContainer width="100%" height={300} minWidth={Math.max(860, labels.length * 18)}><BarChart data={chartData} margin={{ top: 8, right: 18, left: 0, bottom: 26 }}><CartesianGrid strokeDasharray="3 4" vertical={false} stroke="var(--line)" /><XAxis dataKey="label" tick={{ fill: "var(--ink-soft)", fontSize: 11 }} tickLine={false} axisLine={false} minTickGap={18} /><YAxis allowDecimals={false} width={32} tick={{ fill: "var(--ink-soft)", fontSize: 11 }} tickLine={false} axisLine={false} /><Tooltip cursor={{ fill: "rgba(35,127,194,.08)" }} contentStyle={{ borderRadius: 10, border: "1px solid var(--line-strong)", boxShadow: "0 8px 24px rgba(17,24,39,.12)" }} /><Legend verticalAlign="top" align="left" iconType="circle" wrapperStyle={{ paddingBottom: 10, fontSize: 12 }} />{series.map((item, index) => <Bar key={item.name} dataKey={item.name} stackId="events" fill={COLORS[index % COLORS.length]} radius={index === series.length - 1 ? [4, 4, 0, 0] : [0, 0, 0, 0]} />)}<Brush dataKey="label" height={22} travellerWidth={10} startIndex={brushWindow[0]} endIndex={brushWindow[1]} tickFormatter={() => ""} onChange={(next) => { if (next.startIndex !== undefined && next.endIndex !== undefined) setBrushWindow([next.startIndex, next.endIndex]); }} /></BarChart></ResponsiveContainer></div></div>
    </>}
  </section>;
}
