import { useEffect, useRef, useState } from "react";
import { createEmailCategory, listEmailCategoryHistory, saveEmailConfig, type EmailCategoryConfig, type EmailCategoryRevision } from "../../api/console";
import { configurableCategories, errorMessage, localTime } from "./shared";

export function EmailConfig({configs,onSaved,onBusy}: {configs:EmailCategoryConfig[];onSaved:(item:EmailCategoryConfig)=>void;onBusy:(busy:boolean)=>void}) {
  const options=configurableCategories(configs);
  const [selected,setSelected]=useState(options[0]?.category_key || "");
  const [creating,setCreating]=useState(false);
  const [busy,setBusy]=useState(false);
  const current=options.find(item=>item.category_key===selected) || options[0];
  return <section className="console-card"><h2>邮件类型配置</h2>
    <p className="muted">描述用于分类和训练。新增类别会创建或精确绑定邮箱文件夹，验证成功后启用。</p>
    <div className="email-config-layout"><nav className="email-config-navigation" aria-label="邮件类型">
      {options.map(item=><button type="button" key={item.category_key} disabled={busy} aria-pressed={!creating&&current?.category_key===item.category_key} onClick={()=>{setSelected(item.category_key);setCreating(false);}}>{item.display_name}{!item.enabled?"（未启用）":""}</button>)}
      <button type="button" disabled={busy} onClick={()=>setCreating(true)}>新增类别</button>
    </nav>
    {creating||current?<CategoryEditor key={creating?"new":current.category_key} config={creating?undefined:current} onBusy={value=>{setBusy(value);onBusy(value);}} onSaved={item=>{onSaved(item);setSelected(item.category_key);setCreating(false);}}/>:<p>暂无业务类别，请新增类别。</p>}
    </div>
    <section aria-label="系统固定规则"><h3>系统固定规则（只读）</h3><p>junk → Trash · important → Star / Flag</p><p className="muted">重要是独立标签。邮箱中新建文件夹不会自动创建业务类别。</p></section>
  </section>;
}
function CategoryEditor({config,onSaved,onBusy}: {config?:EmailCategoryConfig;onSaved:(item:EmailCategoryConfig)=>void;onBusy:(busy:boolean)=>void}) {
  const [core,setCore]=useState(config?.core_description || "");
  const [include,setInclude]=useState(config?.include.join("\n") || "");
  const [exclude,setExclude]=useState(config?.exclude.join("\n") || "");
  const [threshold,setThreshold]=useState(String(config?.threshold ?? .9));
  const [enabled,setEnabled]=useState(config?.enabled ?? true);
  const [name,setName]=useState("");
  const [key,setKey]=useState("");
  const [folder,setFolder]=useState("");
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const [message,setMessage]=useState("");
  const lock=useRef(false);
  async function save() {
    if(lock.current)return;
    const lines=(value:string)=>value.split("\n").map(line=>line.trim()).filter(Boolean);
    if(!core.trim()||!lines(include).length||!lines(exclude).length){setError("请填写核心定义、包括场景和排除场景。");return;}
    if(!threshold.trim()||!Number.isFinite(Number(threshold))||Number(threshold)<0||Number(threshold)>1){setError("阈值必须是 0 到 1 之间的数字。");return;}
    if(!config&&(!name.trim()||!key.trim()||!folder.trim())){setError("请填写类别标识、名称和目标文件夹。");return;}
    lock.current=true;setBusy(true);onBusy(true);setError("");setMessage("");
    const fields={core_description:core.trim(),include:lines(include),exclude:lines(exclude),threshold:Number(threshold),enabled};
    try {
      const result=config
        ? await saveEmailConfig(config.category_key,{...fields,expected_current_version:config.config_version})
        : await createEmailCategory({...fields,category_key:key.trim(),display_name:name.trim(),provider_folder_name:folder.trim()});
      if(!result.ok)throw new Error(result.message || "配置保存失败，请重试");
      onSaved(result.item);setMessage("配置已保存 · 描述版本 "+result.item.description_version+" · 配置版本 "+result.item.config_version);
    }catch(reason){setError(errorMessage(reason));}
    finally{lock.current=false;setBusy(false);onBusy(false);}
  }
  return <form className="email-config-editor" aria-label={config?config.display_name+"配置":"新增类别"} onSubmit={event=>{event.preventDefault();void save();}}>
    <h3>{config?.display_name || "新增类别"}</h3><fieldset disabled={busy} className="email-form-fields">
      {!config&&<><label>类别标识<input value={key} onChange={event=>setKey(event.target.value)}/></label><label>类别名称<input value={name} onChange={event=>setName(event.target.value)}/></label><label>目标文件夹<input value={folder} onChange={event=>setFolder(event.target.value)}/></label></>}
      <label>核心定义<textarea rows={2} value={core} onChange={event=>setCore(event.target.value)}/></label>
      <label>包括场景<textarea aria-label="包括场景" rows={4} value={include} onChange={event=>setInclude(event.target.value)}/><small>每行一个代表性场景</small></label>
      <label>排除场景<textarea aria-label="排除场景" rows={4} value={exclude} onChange={event=>setExclude(event.target.value)}/><small>每行一个反例，可说明应归入的相邻类别</small></label>
      <label>自动处理阈值<input type="number" min="0" max="1" step=".01" value={threshold} onChange={event=>setThreshold(event.target.value)}/></label>
      <label className="email-inline-label"><input type="checkbox" checked={enabled} onChange={event=>setEnabled(event.target.checked)}/>启用此类别</label>
    </fieldset>
    {config&&<><p>描述版本：{config.description_version} · 配置版本：{config.config_version} · 更新时间：{localTime(config.updated_at)}</p>
      <h4>文件夹绑定</h4>{!config.bindings.length?<p>暂无已验证绑定</p>:<div className="responsive-table-wrap"><table className="settings-table"><thead><tr><th>邮箱</th><th>文件夹</th><th>验证状态</th><th>验证时间</th></tr></thead><tbody>{config.bindings.map(binding=><tr key={binding.account_id}><td>{binding.account_id}</td><td>{binding.provider_folder_name}</td><td>{binding.binding_status}</td><td>{localTime(binding.last_verified_at)}</td></tr>)}</tbody></table></div>}
      <details><summary>高置信度固定动作及参数（只读）</summary><pre>{JSON.stringify({actions:config.actions,parameters:config.action_parameters},null,2)}</pre></details>
      <CategoryHistory key={config.config_version} categoryKey={config.category_key}/>
    </>}
    {error&&<p role="alert">{error}</p>}{message&&<p role="status">{message}</p>}
    <button type="submit" className="primary-button" disabled={busy}>{busy?"正在保存…":config?"保存配置":"创建并验证文件夹"}</button>
  </form>;
}
function CategoryHistory({categoryKey}:{categoryKey:string}) {
  const [open,setOpen]=useState(false),[retry,setRetry]=useState(0);
  const [items,setItems]=useState<EmailCategoryRevision[]|null>(null),[error,setError]=useState("");
  useEffect(()=>{
    if(!open)return;
    const controller=new AbortController();setError("");
    listEmailCategoryHistory(categoryKey,controller.signal).then(result=>{if(!controller.signal.aborted)setItems(result.items);}).catch(reason=>{if(!controller.signal.aborted)setError(errorMessage(reason));});
    return ()=>controller.abort();
  },[categoryKey,open,retry]);
  return <section aria-label="描述版本历史"><button type="button" className="compact-button" aria-expanded={open} onClick={()=>setOpen(value=>!value)}>{open?"收起描述历史":"查看描述历史"}</button>
    {open&&(error?<p role="alert">{error} <button type="button" onClick={()=>setRetry(value=>value+1)}>重试历史</button></p>:items===null?<p role="status">正在加载描述历史…</p>:!items.length?<p>暂无描述历史。</p>:<div className="responsive-table-wrap"><table className="settings-table"><thead><tr><th>保存时间</th><th>配置版本</th><th>描述版本</th><th>历史内容（只读）</th></tr></thead><tbody>{items.map(item=><tr key={item.revision_id}><td>{localTime(item.created_at)}</td><td>{item.config_version}</td><td>{item.description_version}</td><td><details><summary>查看历史描述</summary><p>{item.config.core_description}</p><p>包括场景：{item.config.include.join("；")}</p><p>排除场景：{item.config.exclude.join("；")}</p></details></td></tr>)}</tbody></table></div>)}
  </section>;
}
