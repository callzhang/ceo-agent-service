import { useEffect, useRef, type ReactNode } from "react";
export function EmailDrawer({title, onClose, locked = false, returnFocus, children}: {title:string; onClose:()=>void; locked?:boolean; returnFocus?:()=>HTMLElement|null; children:ReactNode}) {
  const ref=useRef<HTMLElement>(null);
  const close=useRef(onClose); close.current=onClose;
  const busy=useRef(locked); busy.current=locked;
  const focus=useRef(returnFocus);focus.current=returnFocus;
  useEffect(()=>{
    const origin=document.activeElement instanceof HTMLElement ? document.activeElement : null;
    ref.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const key=(event:KeyboardEvent)=>{
      if(event.key==="Escape" && !busy.current){event.preventDefault();close.current();}
      if(event.key!=="Tab")return;
      const elements=Array.from(ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href],summary,[tabindex="0"]') || []).filter(element=>{
        if(element.closest('[hidden],[aria-hidden="true"]'))return false;
        let parent=element.parentElement;
        while(parent&&parent!==ref.current){if(parent.tagName==="DETAILS"&&!parent.hasAttribute("open")&&parent.querySelector(":scope > summary")!==element)return false;parent=parent.parentElement;}
        return true;
      });
      const first=elements[0],last=elements.at(-1);
      if(event.shiftKey && document.activeElement===first){event.preventDefault();last?.focus();}
      else if(!event.shiftKey && document.activeElement===last){event.preventDefault();first?.focus();}
    };
    document.addEventListener("keydown",key);
    return ()=>{document.removeEventListener("keydown",key);const target=focus.current?.() || origin;if(target?.isConnected)target.focus();};
  },[]);
  return <aside className="email-detail-drawer" role="dialog" aria-label={title} ref={ref}>
    <header><h2>{title}</h2><button type="button" className="compact-button" disabled={locked} onClick={onClose} aria-label="关闭详情">关闭</button></header>
    {children}
  </aside>;
}
