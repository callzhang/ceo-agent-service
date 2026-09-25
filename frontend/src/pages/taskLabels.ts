import type { AttentionCategory } from "../api/console";

export const taskStatusLabels: Record<string, string> = { open: "待处理", waiting: "等待中", done: "已完成", cancelled: "已取消", merged: "已合并" };

export const commitmentLabels: Record<string, string> = { none: "承诺待明确", assigned_unaccepted: "已指派，未接受", accepted: "已接受", disputed: "存在争议", completed: "已完成", cancelled: "已取消" };

export const sourceTypeLabels: Record<string, string> = { ai_minutes: "AI 听记", meeting: "会议", dingtalk: "钉钉消息", dingtalk_todo: "钉钉待办" };

export const evidenceRoleLabels: Record<string, string> = {
  discovery: "发现", commitment: "承诺", assignment: "指派", acceptance: "接受", completion: "完成", correction: "更正", merge_identity: "合并依据", relevance: "业务相关性", resolution: "收口",
};

export const dateTypeLabels: Record<string, string> = {
  assigned_at: "指派时间", requested_deadline_at: "对方要求的截止", external_deadline_at: "外部截止", committed_deadline_at: "承诺的截止", estimated_deadline_at: "预估截止", next_check_at: "下次检查",
};

export const taskEventLabels: Record<string, string> = {
  created: "已创建", promoted: "升为正式任务", commitment_changed: "承诺状态变化", owner_changed: "负责人变化", deadline_changed: "截止日期变化",
  date_evidence_recorded: "记录了日期依据", status_changed: "状态变化", relevance_changed: "业务相关性变化", details_changed: "详情变化", fields_changed: "字段变化", merged: "已合并",
};

export const attentionEventLabels: Record<string, string> = { opened: "开始关注", updated: "内容更新", category_changed: "类别变化", resolved: "已解除", reopened: "重新关注" };

export const relationTypeLabels: Record<string, string> = { depends_on: "依赖", blocks: "阻塞", supports: "支撑", supersedes: "取代", related_to: "相关" };

export const categoryLabels: Record<AttentionCategory, string> = { fyi: "仅需知晓", watch: "持续观察", decision: "需要决策", push: "需要推动" };
export const categoryOrder: AttentionCategory[] = ["decision", "push", "watch", "fyi"];

export function labelOf(labels: Record<string, string>, value: string) {
  return labels[value] || value;
}

/** Status and commitment can both end in "已完成"/"已取消"; say it once. */
export function commitmentText(status: string, commitment: string) {
  if (commitment === "none") return "";
  const text = labelOf(commitmentLabels, commitment);
  return text === labelOf(taskStatusLabels, status) ? "" : text;
}
