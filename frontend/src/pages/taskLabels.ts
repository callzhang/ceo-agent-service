export const taskStatusLabels: Record<string, string> = { open: "待处理", waiting: "等待中", done: "已完成", cancelled: "已取消", merged: "已合并" };

export const commitmentLabels: Record<string, string> = { none: "承诺待明确", assigned_unaccepted: "已指派，未接受", accepted: "已接受", disputed: "存在争议", completed: "已完成", cancelled: "已取消" };

export const sourceTypeLabels: Record<string, string> = { ai_minutes: "AI 听记", meeting: "会议", dingtalk: "钉钉消息", dingtalk_todo: "钉钉待办" };

export const taskEventLabels: Record<string, string> = {
  created: "已创建", promoted: "升为正式任务", commitment_changed: "承诺状态变化", owner_changed: "负责人变化", deadline_changed: "截止日期变化",
  date_evidence_recorded: "记录了日期依据", status_changed: "状态变化", relevance_changed: "业务相关性变化", details_changed: "详情变化", fields_changed: "字段变化", merged: "已合并",
};

export function labelOf(labels: Record<string, string>, value: string) {
  return labels[value] || value;
}
