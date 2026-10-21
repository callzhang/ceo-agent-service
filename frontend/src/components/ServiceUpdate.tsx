import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ArrowUpCircle, LoaderCircle, X } from "lucide-react";
import { checkServiceUpdate, getReleaseNotes, getServiceUpdate, startServiceUpdate, type ReleaseNotes, type ServiceUpdateStatus, type UpdateSnapshot } from "../api/serviceUpdate";

const terminal = new Set(["succeeded","failed","rolled_back","needs_manual"]);
const phases: Record<string,string> = {preparing:"准备更新 · 等待当前任务结束",updating:"正在安装更新",verifying:"正在检查更新",restarting:"等待服务恢复",succeeded:"等待服务版本确认"};
const navPhases: Record<string,string> = {preparing:"准备更新",updating:"正在安装更新",verifying:"正在检查更新",restarting:"等待服务恢复",succeeded:"等待版本确认"};
const reloadPage = () => window.location.reload();
const message = (error: unknown) => error instanceof Error ? error.message : "请求失败，请重试";

export function ServiceUpdate({ reload = reloadPage }: {reload?:()=>void}) {
  const [state,setState] = useState<ServiceUpdateStatus|null>(null);
  const [connectionLost,setConnectionLost] = useState(false);
  const [open,setOpen] = useState(false);
  const [review,setReview] = useState<UpdateSnapshot|null>(null);
  const [notes,setNotes] = useState<ReleaseNotes|null>(null);
  const [loading,setLoading] = useState(false);
  const [starting,setStarting] = useState(false);
  const [submitted,setSubmitted] = useState(false);
  const [error,setError] = useState("");
  const pending = useRef<string|null>(null);
  const reloaded = useRef(false);
  const initialCommit = useRef<string|null>(null);
  const mounted = useRef(true);
  const refreshing = useRef(false);
  const controller = useRef<AbortController|null>(null);
  const dialog = useRef<HTMLElement>(null);
  const progress = state?.progress;
  const busy = starting || submitted || Boolean(progress && !terminal.has(progress.status)) || Boolean(state?.operation && (!progress || progress.operation_id !== state.operation.operation_id || !terminal.has(progress.status)));

  const refresh = useCallback(async()=>{
    if (refreshing.current) return;
    refreshing.current=true;
    try {
      const value=await getServiceUpdate();
      if (!mounted.current) return;
      initialCommit.current ??= value.current_commit;
      setState(value); setConnectionLost(false);
      if (value.operation && !pending.current) pending.current=value.operation.operation_id;
      const receipt=value.progress;
      if (receipt && receipt.operation_id===pending.current && terminal.has(receipt.status)) {
        if (receipt.status==="succeeded" && receipt.installed_commit===receipt.target_commit && value.current_commit===receipt.installed_commit) {
          setSubmitted(false);
          if (!reloaded.current && initialCommit.current !== receipt.installed_commit) {reloaded.current=true;reload();}
        } else if (receipt.status!=="succeeded") {
          setSubmitted(false);
        }
      }
    } catch {
      if (mounted.current) setConnectionLost(true);
    } finally { refreshing.current=false; }
  },[reload]);
  useEffect(()=>{
    mounted.current=true; void refresh();
    return ()=>{mounted.current=false;controller.current?.abort();};
  },[refresh]);
  useEffect(()=>{
    const timer=window.setInterval(()=>void refresh(),busy?3000:60000);
    return ()=>window.clearInterval(timer);
  },[busy,refresh]);

  function close() { controller.current?.abort(); setOpen(false); }
  useEffect(()=>{
    if (!open) return;
    const previous=document.activeElement instanceof HTMLElement?document.activeElement:null;
    const before=document.body.style.overflow;
    document.body.style.overflow="hidden";
    const elements=()=>Array.from(dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), summary, a[href], [tabindex="0"]')??[]);
    elements()[0]?.focus();
    const keydown=(event:KeyboardEvent)=>{
      if (event.key==="Escape") { event.preventDefault(); close(); }
      if (event.key==="Tab") {
        const items=elements(),first=items[0],last=items[items.length-1];
        if (event.shiftKey && document.activeElement===first) {event.preventDefault();last?.focus();}
        else if (!event.shiftKey && document.activeElement===last) {event.preventDefault();first?.focus();}
      }
    };
    document.addEventListener("keydown",keydown);
    return ()=>{document.removeEventListener("keydown",keydown);document.body.style.overflow=before;previous?.focus();};
  },[open]);

  async function openUpdate() {
    setOpen(true);
    if (busy) return;
    controller.current?.abort();
    const abort=new AbortController(); controller.current=abort;
    setLoading(true);setError("");setNotes(null);setReview(null);
    try {
      const {snapshot}=await checkServiceUpdate(abort.signal);
      if (abort.signal.aborted) return;
      setReview(snapshot);setState(value=>value?{...value,snapshot}:value);
      if (snapshot.status!=="update_available") throw new Error(snapshot.status==="current"?"当前已是最新版本":"仓库状态暂不支持更新，请检查上游或本地修改");
      const result=await getReleaseNotes(snapshot.fingerprint,abort.signal);
      if (abort.signal.aborted) return;
      if (result.target_commit!==snapshot.remote_commit || result.local_commit!==snapshot.local_commit) throw new Error("更新版本已变化，请重新检查");
      setNotes(result);
    } catch (reason) { if (!abort.signal.aborted) setError(message(reason)); }
    finally { if (!abort.signal.aborted) setLoading(false); }
  }
  async function confirm() {
    if (!review || !notes || busy) return;
    setStarting(true);setError("");
    try {
      const result=await startServiceUpdate(review.fingerprint,crypto.randomUUID());
      pending.current=result.operation_id;setSubmitted(true);
    } catch (reason) { setError(message(reason)); }
    finally { setStarting(false); }
  }
  const available=state?.snapshot?.status==="update_available" && state.snapshot.remote_commit!==state.current_commit;
  const blocked=state?.snapshot && ["check_failed","diverged","local_changes"].includes(state.snapshot.status);
  const failed=Boolean(progress && ["failed","rolled_back","needs_manual"].includes(progress.status));
  const visibleError=error || (!busy && failed ? progress?.error || "更新未完成，请查看原因后重试" : "") || notes?.summary_error;
  if (!available && !busy && !connectionLost && !blocked && !failed && !open) return null;
  const phase=connectionLost&&busy?"连接中断，正在重新连接…":phases[progress?.status??""]??"准备更新 · 等待当前任务结束";
  const label=busy?(connectionLost?"正在重新连接…":navPhases[progress?.status??""]??"准备更新"):connectionLost||blocked?"检查更新失败":available?`更新服务 · ${state?.snapshot?.commits_behind}`:"更新未完成";
  return <>
    <div className="service-update-control">
      <button type="button" className="service-update-button" onClick={()=>void openUpdate()} disabled={starting} aria-label={label}>
        {busy?<LoaderCircle size={15} className="service-update-spinner" aria-hidden="true"/>:<ArrowUpCircle size={15} aria-hidden="true"/>}{label}
      </button>
    </div>
    {open && createPortal(<div className="service-update-scrim" onMouseDown={event=>{if(event.target===event.currentTarget)close();}}>
      <section ref={dialog} className="service-update-dialog" role="dialog" aria-modal="true" aria-labelledby="service-update-title" aria-describedby="service-update-explanation">
        <header><div><p className="eyebrow">SERVICE UPDATE</p><h2 id="service-update-title">更新服务</h2></div><button className="service-update-close" onClick={close} aria-label="关闭更新说明"><X size={20}/></button></header>
        <div className="service-update-body">
          {review && <p className="service-update-version"><code>{review.local_commit.slice(0,8)}</code><span>→</span><code>{review.remote_commit.slice(0,8)}</code><span>{review.commits_behind} 个提交</span></p>}
          {busy && <p className="service-update-progress" role="status"><LoaderCircle size={17} className="service-update-spinner"/>{phase}</p>}
          {loading && !busy && <p role="status">正在读取更新内容并生成说明…</p>}
          {visibleError && <p role="alert" className="service-update-error">{visibleError}</p>}
          {notes?.summary && <div className="service-update-summary"><h3>{notes.summary_source==="ai"?"AI 更新说明":"更新说明"}</h3><p>{notes.summary}</p></div>}
          {notes?.commits.length ? <details className="service-update-commits"><summary>查看 {notes.commits.length} 条提交</summary><ol>{notes.commits.map(commit=><li key={commit.sha}><code>{commit.sha.slice(0,8)}</code><strong>{commit.subject}</strong>{commit.body && <pre>{commit.body}</pre>}</li>)}</ol></details>:null}
          {visibleError && !busy && <button type="button" onClick={()=>void openUpdate()}>{notes?.summary_error?"重试生成说明":"重新检查"}</button>}
          <p id="service-update-explanation" className="service-update-explanation">更新期间暂停领取新任务，当前任务继续完成。新消息、Queued 和延迟 retry 会保留，服务恢复后继续处理。</p>
        </div>
        <footer><button type="button" onClick={close}>{busy?"关闭":"取消"}</button><button type="button" className="service-update-confirm" onClick={()=>void confirm()} disabled={loading||busy||!notes}>确认更新</button></footer>
      </section>
    </div>,document.body)}
  </>;
}
