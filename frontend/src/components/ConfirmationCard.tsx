import { ShieldAlert } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { Confirmation } from "../types";
import { displayText } from "./ExecutionStep";

interface ConfirmationCardProps {
  confirmation: Confirmation;
  onConfirm: (confirmation: Confirmation) => Promise<void>;
  onCancel: (confirmation: Confirmation) => Promise<void>;
}

export function ConfirmationCard({ confirmation, onConfirm, onCancel }: ConfirmationCardProps) {
  const [pending, setPending] = useState<"confirm" | "cancel" | null>(null);
  const [error, setError] = useState("");
  const mounted = useRef(true);
  const inFlight = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const undecided = confirmation.status === "pending";
  const waitingForQuiescence = undecided && Boolean(confirmation.decision_requested) && !confirmation.proposer_quiesced;
  const persistedIntent = Boolean(confirmation.decision_requested);
  const outcomeLabel = confirmation.status === "executed"
    ? "操作已执行"
    : confirmation.status === "cancelled"
      ? "操作已取消"
      : confirmation.status === "failed"
        ? "操作执行失败"
        : confirmation.status === "confirmed"
          ? "正在执行已确认操作"
          : "";

  async function decide(kind: "confirm" | "cancel") {
    if (inFlight.current || pending || !undecided || persistedIntent) return;
    inFlight.current = true;
    setPending(kind);
    setError("");
    try {
      await (kind === "confirm" ? onConfirm(confirmation) : onCancel(confirmation));
    } catch {
      if (mounted.current) {
        setError(kind === "confirm" ? "确认失败，请重试" : "取消失败，请重试");
        setPending(null);
        inFlight.current = false;
      }
    }
  }

  return (
    <section className="confirmation-card" aria-label="需要确认">
      <div className="confirmation-title"><ShieldAlert aria-hidden="true" size={18} /><strong>需要确认</strong></div>
      <dl>
        <div><dt>操作</dt><dd>{displayText(confirmation.canonical_operation || confirmation.action_kind, "未说明")}</dd></div>
        <div><dt>目标</dt><dd>{confirmation.canonical_targets.length ? confirmation.canonical_targets.map((item) => displayText(item, "未说明")).join("、") : displayText(confirmation.target, "未说明")}</dd></div>
        <div><dt>效果</dt><dd>{displayText(confirmation.summary, "未说明")} <small>运行时提供，未验证</small></dd></div>
        <div><dt>风险</dt><dd>{displayText(confirmation.risk, "未说明")} <small>运行时提供，未验证</small></dd></div>
      </dl>
      {waitingForQuiescence && <p className="confirmation-wait" role="status">等待执行器安全停稳</p>}
      {undecided && persistedIntent && confirmation.proposer_quiesced && (
        <p className="confirmation-wait" role="status">
          {confirmation.decision_requested === "cancel" ? "正在取消操作" : "正在执行已确认操作"}
        </p>
      )}
      {outcomeLabel && <p className="confirmation-wait" role="status">{outcomeLabel}</p>}
      {error && <p className="inline-alert" role="alert">{error}</p>}
      <div className="confirmation-actions">
        <button type="button" className="primary-button" disabled={Boolean(pending) || !undecided || persistedIntent} onClick={() => void decide("confirm")}>确认执行</button>
        <button type="button" className="secondary-button" disabled={Boolean(pending) || !undecided || persistedIntent} onClick={() => void decide("cancel")}>取消</button>
      </div>
    </section>
  );
}
