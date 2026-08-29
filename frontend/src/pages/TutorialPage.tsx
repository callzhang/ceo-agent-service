import { useEffect, useState } from "react";

import { checkTutorialStep, confirmTutorialStep, displayValue, getTutorial, runTutorialAction } from "../api/console";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { SnapshotBadge } from "../components/status/SnapshotBadge";

export function TutorialPage() {
  const [payload, setPayload] = useState<Record<string, unknown> | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [message, setMessage] = useState("");
  const [snapshot, setSnapshot] = useState("");
  const load = () => getTutorial().then((response) => { setPayload(response.item); setSnapshot(response.meta.snapshot_at); setState("ready"); }).catch((error: unknown) => { setMessage(error instanceof Error ? error.message : "加载失败"); setState("error"); });
  useEffect(() => { void load(); }, []);
  const steps = Array.isArray(payload?.steps) ? payload.steps : [];
  async function run(action: "check" | "run" | "confirm", id: string) {
    setMessage("操作进行中…");
    try { const result = action === "check" ? await checkTutorialStep(id) : action === "run" ? await runTutorialAction(id) : await confirmTutorialStep(id); setMessage(result.message); await load(); } catch (error) { setMessage(error instanceof Error ? error.message : "操作失败"); }
  }
  return <ConsolePageLayout title="Tutorial" actions={<><SnapshotBadge timestamp={snapshot} refreshing={state === "loading"} /><button type="button" className="secondary-button" onClick={() => void load()}>刷新</button></>}>
    <section className="console-card" aria-live="polite"><p className="muted">按步骤检查、执行和确认初始化状态；外部连接仍由后端受保护命令完成。</p>{message && <p className="page-state">{message}</p>}{state === "error" && <p className="page-state page-state-error" role="alert">{message}</p>}{state === "loading" && !payload && <p className="page-state" role="status">正在加载…</p>}{state === "ready" && (steps.length ? <div className="tutorial-step-list">{steps.map((raw, index) => { const step = raw as Record<string, unknown>; const id = displayValue(step.step_id || step.id || index); const actions = Array.isArray(step.available_actions) ? step.available_actions : []; return <article className="console-card tutorial-step" key={id}><div className="tutorial-step-head"><span className="eyebrow">Step {index + 1}</span><strong>{displayValue(step.title)}</strong><span className="status-badge">{displayValue(step.status)}</span></div><p>{displayValue(step.summary || step.description)}</p><div className="console-page-actions">{actions.map((rawAction) => { const action = rawAction as Record<string, unknown>; const actionId = displayValue(action.id); const kind = displayValue(action.kind); return <button type="button" className="secondary-button" key={actionId} onClick={() => void run(kind === "check" ? "check" : kind === "confirm" ? "confirm" : "run", kind === "run" ? actionId : id)}>{displayValue(action.title || actionId)}</button>; })}</div></article>; })}</div> : <p className="muted">初始化已完成或暂无待处理步骤。</p>)}</section>
  </ConsolePageLayout>;
}
