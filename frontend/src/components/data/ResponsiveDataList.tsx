import { Fragment, useState, type ReactNode } from "react";

export interface DataColumn<T extends { id: string }> {
  key: keyof T & string;
  label: string;
}

export type DataListState = "loading" | "empty" | "error" | "ready";

export interface ResponsiveDataListProps<T extends { id: string }> {
  ariaLabel: string;
  columns: DataColumn<T>[];
  rows: T[];
  renderCell: (row: T, key: keyof T & string) => ReactNode;
  state?: DataListState;
  emptyMessage?: string;
  errorMessage?: string;
  expandable?: boolean;
  renderExpanded?: (row: T) => ReactNode;
}

export function ResponsiveDataList<T extends { id: string }>({
  ariaLabel,
  columns,
  rows,
  renderCell,
  state = "ready",
  emptyMessage = "暂无数据",
  errorMessage = "加载失败",
  expandable = false,
  renderExpanded,
}: ResponsiveDataListProps<T>) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  if (state === "loading") return <div className="page-state" role="status">正在加载…</div>;
  if (state === "error") return <div className="page-state page-state-error" role="alert">{errorMessage}</div>;
  if (state === "empty" || rows.length === 0) return <div className="page-state">{emptyMessage}</div>;

  return (
    <section className="responsive-data-list" aria-label={ariaLabel}>
      <div className="responsive-table-wrap">
        <table aria-label={ariaLabel}>
          <thead>
            <tr>
              {columns.map((column) => <th key={column.key}>{column.label}</th>)}
              {expandable && <th aria-label="详情" />}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const expanded = expandedId === row.id;
              return (
                <Fragment key={row.id}>
                  <tr>
                    {columns.map((column) => <td data-label={column.label} key={column.key}>{renderCell(row, column.key)}</td>)}
                    {expandable && (
                      <td data-label="详情">
                        <button
                          type="button"
                          className="details-toggle"
                          aria-expanded={expanded}
                          onClick={() => setExpandedId(expanded ? null : row.id)}
                        >
                          {expanded ? "收起详情" : "展开详情"}
                        </button>
                      </td>
                    )}
                  </tr>
                  {expanded && renderExpanded && (
                    <tr className="responsive-expanded-row">
                      <td colSpan={columns.length + 1}>
                        <div className="responsive-expanded">{renderExpanded(row)}</div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
