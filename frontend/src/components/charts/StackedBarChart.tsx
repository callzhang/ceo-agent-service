import { useMemo, useState } from "react";

import type { HistoryChart } from "../../api/console";

const COLORS = ["#176b50", "#237fc2", "#99610d", "#a43a34", "#7b61a8", "#0f766e", "#c05621", "#64748b"];

interface ChartSeries {
  name: string;
  data: number[];
}

interface StackedBarChartProps {
  chart?: HistoryChart;
  minWindow?: number;
  loading?: boolean;
}

function normalizedSeries(series: ChartSeries[], length: number) {
  return series.map((item) => ({
    ...item,
    data: Array.from({ length }, (_, index) => Math.max(0, Number(item.data[index] || 0))),
  }));
}

function formatRange(labels: string[], start: number, end: number) {
  if (!labels.length) return "暂无快照";
  return `${labels[start]} — ${labels[end - 1]}`;
}

export function StackedBarChart({ chart, minWindow = 6, loading = false }: StackedBarChartProps) {
  const labels = chart?.labels || [];
  const series = useMemo(() => normalizedSeries(chart?.series || [], labels.length), [chart?.series, labels.length]);
  const safeMinWindow = Math.max(1, Math.min(minWindow, labels.length || minWindow));
  const [windowSize, setWindowSize] = useState(Math.max(safeMinWindow, labels.length));
  const [startIndex, setStartIndex] = useState(0);
  const clampedWindow = labels.length ? Math.min(labels.length, Math.max(safeMinWindow, windowSize)) : 0;
  const maxStart = Math.max(0, labels.length - clampedWindow);
  const clampedStart = Math.min(startIndex, maxStart);
  const visibleLabels = labels.slice(clampedStart, clampedStart + clampedWindow);
  const visibleSeries = series.map((item) => ({ ...item, data: item.data.slice(clampedStart, clampedStart + clampedWindow) }));
  const totals = visibleLabels.map((_label, index) => visibleSeries.reduce((sum, item) => sum + item.data[index], 0));
  const maxTotal = Math.max(1, ...totals);
  const chartWidth = Math.max(620, visibleLabels.length * 42);
  const chartHeight = 220;
  const plotTop = 14;
  const plotBottom = 34;
  const plotHeight = chartHeight - plotTop - plotBottom;

  function changeWindow(next: number) {
    const value = Math.max(safeMinWindow, Math.min(labels.length, next));
    setWindowSize(value);
    setStartIndex((current) => Math.min(current, Math.max(0, labels.length - value)));
  }

  if (!labels.length || !series.length) {
    return (
      <section className="card history-chart-card" aria-label="Recent 24 hour events">
        <div className="history-chart-head"><div><h2 className="history-chart-title">最近 24 小时事件</h2><div className="history-chart-subtitle">{chart?.range || "暂无快照"}</div></div><span className="pill">- events</span></div>
        <div className="history-chart-empty" role={loading ? "status" : undefined}>{loading ? "正在加载…" : "暂无事件"}</div>
      </section>
    );
  }

  const totalEvents = chart?.total || 0;

  return (
    <section className="card history-chart-card" aria-label="Recent 24 hour events">
      <div className="history-chart-head">
        <div><h2 className="history-chart-title">最近 24 小时事件</h2><div className="history-chart-subtitle">{formatRange(labels, clampedStart, clampedStart + clampedWindow)}</div></div>
        <span className="pill">{totalEvents ? `${totalEvents} events` : "- events"}</span>
      </div>
      <div className="history-chart-toolbar" aria-label="图表范围控制">
        <label className="history-chart-range-control"><span>显示范围</span><input type="range" min={safeMinWindow} max={labels.length} value={clampedWindow} aria-label="图表显示小时数" onChange={(event) => changeWindow(Number(event.target.value))} /><strong>显示 {clampedWindow} 小时</strong></label>
        <label className="history-chart-range-control"><span>起始位置</span><input type="range" min={0} max={maxStart} value={clampedStart} aria-label="图表起始位置" disabled={maxStart === 0} onChange={(event) => setStartIndex(Number(event.target.value))} /><strong>{labels[clampedStart]}</strong></label>
      </div>
      <div className="history-chart-legend" aria-label="事件类型图例">{visibleSeries.map((item, index) => <span className="history-chart-legend-item" key={item.name}><i style={{ backgroundColor: COLORS[index % COLORS.length] }} aria-hidden="true" />{item.name}</span>)}</div>
      <div className="history-chart-scroll" role="img" aria-label={`最近 24 小时共 ${totalEvents} 个事件，当前显示 ${clampedWindow} 小时`}>
        <svg className="history-chart-svg" viewBox={`0 0 ${chartWidth} ${chartHeight}`} width={chartWidth} height={chartHeight} preserveAspectRatio="none">
          {[0, 0.25, 0.5, 0.75, 1].map((ratio) => <line key={ratio} x1="0" x2={chartWidth} y1={plotTop + plotHeight * ratio} y2={plotTop + plotHeight * ratio} className="history-chart-gridline" />)}
          {visibleLabels.map((label, index) => {
            const total = totals[index];
            const columnWidth = chartWidth / visibleLabels.length;
            const barWidth = Math.max(8, columnWidth * 0.66);
            const x = index * columnWidth + (columnWidth - barWidth) / 2;
            let offset = 0;
            return <g data-testid="stacked-bar-column" key={`${label}-${index}`}>
              {visibleSeries.map((item, seriesIndex) => {
                const value = item.data[index];
                const height = total ? value / maxTotal * plotHeight : 0;
                const y = plotTop + plotHeight - offset - height;
                offset += height;
                return value > 0 ? <rect data-testid="stacked-bar-segment" key={item.name} x={x} y={y} width={barWidth} height={height} rx="3" fill={COLORS[seriesIndex % COLORS.length]}><title>{`${label} · ${item.name}: ${value}`}</title></rect> : null;
              })}
              <text x={index * columnWidth + columnWidth / 2} y={chartHeight - 10} textAnchor="middle" className="history-chart-axis-label">{label}</text>
            </g>;
          })}
        </svg>
      </div>
    </section>
  );
}
