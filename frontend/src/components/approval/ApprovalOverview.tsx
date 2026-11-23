import type { ApprovalReview } from "../../api/attempts";

export const executionStatusLabels: Record<string, string> = {
  pending: "等待执行", running: "执行中", dispatched: "已提交，等待核验",
  retry: "等待恢复", uncertain: "外部结果待核验", failed: "执行失败",
  verified: "已核验完成", confirmed_no_effect: "已确认未产生外部结果",
  done: "全部动作已核验完成", skipped: "已停止",
};

const scoreLabels = {
  information_completeness: "信息完整度",
  risk: "风险",
  rule_coverage: "规则覆盖率",
  confidence: "判断置信度",
} as const;

const riskLabels: Record<string, string> = { low: "低", medium: "中", high: "高" };

export function ApprovalOverview({ review }: { review: ApprovalReview }) {
  const { decision, scores, summary } = review;
  const metrics = Object.entries(scoreLabels).map(([key, label]) => {
    const value = scores?.[key as keyof ApprovalReview["scores"]];
    return [label, (key === "risk" && value ? riskLabels[value] || value : value)];
  });
  const decisionRows = [
    ["决定", decision?.label],
    ["执行结果", decision ? executionStatusLabels[decision.status] || decision.status : review.status_message],
    ["判断理由", decision?.reason || review.judgment_reason],
    ["审批意见", decision?.remark],
  ];
  return <section className="console-card attempt-detail-section approval-overview" aria-label="审批决定">
    <h2>审批内容摘要</h2>
    {summary.length > 0 ? <ul>{summary.map((fact) => <li key={fact}>{fact}</li>)}</ul> : <p className="muted">未记录审批内容摘要</p>}
    <h2>决策表</h2>
    <table aria-label="决策表">
      <thead><tr><th scope="col">维度</th><th scope="col">本次记录</th></tr></thead>
      <tbody>{[...metrics, ...decisionRows].map(([name, value]) => <tr key={name}>
        <th scope="row">{name}</th><td>{value && value !== "—" ? value : "未记录"}</td>
      </tr>)}</tbody>
    </table>
    {decision?.receipt && <details><summary>审批核验回执</summary><pre>{JSON.stringify(decision.receipt.provider_result, null, 2)}</pre></details>}
  </section>;
}
