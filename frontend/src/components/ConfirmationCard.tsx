import { ShieldAlert } from "lucide-react";

import type { Confirmation } from "../types";
import { displayText } from "./ExecutionStep";

interface ConfirmationCardProps {
  confirmation: Confirmation;
  onConfirm?: (confirmation: Confirmation) => Promise<void>;
  onCancel?: (confirmation: Confirmation) => Promise<void>;
}

export function ConfirmationCard({ confirmation }: ConfirmationCardProps) {
  const outcomeLabel = confirmation.status === "executed"
    ? "操作已执行"
    : confirmation.status === "cancelled"
      ? "操作已取消"
      : confirmation.status === "failed"
        ? "操作执行失败"
        : confirmation.status === "confirmed"
          ? "正在执行已确认操作"
          : "";

  return (
    <section className="confirmation-card" aria-label="历史确认记录">
      <div className="confirmation-title"><ShieldAlert aria-hidden="true" size={18} /><strong>历史确认记录（只读）</strong></div>
      <dl>
        <div><dt>操作</dt><dd>{displayText(confirmation.canonical_operation || confirmation.action_kind, "未说明")}</dd></div>
        <div><dt>目标</dt><dd>{confirmation.canonical_targets.length ? confirmation.canonical_targets.map((item) => displayText(item, "未说明")).join("、") : displayText(confirmation.target, "未说明")}</dd></div>
        <div><dt>效果</dt><dd>{displayText(confirmation.summary, "未说明")} <small>运行时提供，未验证</small></dd></div>
        <div><dt>风险</dt><dd>{displayText(confirmation.risk, "未说明")} <small>运行时提供，未验证</small></dd></div>
      </dl>
      {confirmation.status === "pending" && <p className="confirmation-wait">旧版待确认操作不会再执行</p>}
      {outcomeLabel && <p className="confirmation-wait" role="status">{outcomeLabel}</p>}
    </section>
  );
}
