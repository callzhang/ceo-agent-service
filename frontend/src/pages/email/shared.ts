import type { EmailCategoryConfig } from "../../api/console";
export function configurableCategories(items: EmailCategoryConfig[]) {
  return items.filter(item => !["junk", "important", "subscription", "other"].includes(item.category_key));
}
export function localTime(value: string | null | undefined) {
  if (!value) return "未提供";
  const parsed = new Date(value.includes("T") || value.includes(",") ? value : value.replace(" ", "T") + "Z");
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("zh-CN", {hour12:false});
}
export function measured(value: unknown, unit = "%") {
  if (typeof value !== "number" || !Number.isFinite(value)) return "未测量";
  return unit === "%" ? (value * 100).toFixed(1) + "%" : value.toFixed(1) + unit;
}
export function errorMessage(reason: unknown) {return reason instanceof Error ? reason.message : "请求失败，请重试";}
export function sourceLabel(value:string) {
  return ({agent:"Agent 分类",model:"模型分类",user:"人工确认",provider:"邮箱观察"} as Record<string,string>)[value] || value || "来源未提供";
}
export function statusLabel(value:string) {
  return ({processed:"已处理",pending_feedback:"待反馈",pending_classification:"待分类",pending:"待执行",processing:"处理中",succeeded:"成功",done:"完成",failed:"失败",skipped:"已跳过",candidate:"候选",active:"主模型",previous:"前一版本",superseded:"已替换",rejected:"已拒绝",archived:"已归档",applied:"已生效"} as Record<string,string>)[value] || value || "未提供";
}
export function modeLabel(value:string) {return value==="agent_primary"?"Agent 主分类":value==="model_primary"?"模型主分类":"模式未提供";}
export function checkLabel(key:string,configs:EmailCategoryConfig[]) {
  const [kind,category]=key.split(":");
  const name=configs.find(item=>item.category_key===category)?.display_name || category || "各类别";
  if(kind==="category_precision")return name+" · Precision";
  if(kind==="category_validation_samples")return name+" · 独立验证样本";
  return ({macro_f1:"整体 Macro F1",p95_latency:"端到端 P95 延迟",system_integrity:"模型与配置完整性"} as Record<string,string>)[key] || "其他晋升检查";
}
export function reasonLabel(reason:string) {
  return ({passed:"已满足条件",not_measured:"未测量，请等待独立评测",threshold_not_met:"尚未达到门槛",model_evidence_or_configuration_not_ready:"模型证据或当前配置尚未就绪，请检查版本、评测和完整性",user_enabled_primary_model:"用户启用主模型",user_disabled_primary_model:"用户恢复 Agent 主分类"} as Record<string,string>)[reason] || (reason?"请查看原始证据":"—");
}
export function checkValue(key:string,value:unknown) {
  if(value==null)return "未测量";
  if(typeof value==="boolean")return value?"满足":"不满足";
  if(key==="macro_f1"||key.startsWith("category_precision:"))return measured(value);
  if(key==="p95_latency")return measured(value," ms");
  return typeof value==="number"?String(value):"请查看原始证据";
}
