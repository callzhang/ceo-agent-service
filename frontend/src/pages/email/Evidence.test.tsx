import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, it } from "vitest";
import type { EmailObservabilityEvent } from "../../api/console";
import { actionResultLabel, ObservabilityDetails } from "./Evidence";

const receipt = (outcome?: string): EmailObservabilityEvent => ({
  kind: "unsubscribe", operation: "unsubscribe", status: "done", outcome, attempt_ids: [9203],
});

it.each([
  ["needs_human", "退订需要人工处理"],
  ["needs_feedback", "退订等待反馈"],
  ["skipped", "退订已跳过"],
  ["unrecognized", "退订状态未知"],
])("does not show %s as queued", (status, label) => {
  expect(actionResultLabel({...receipt(), status})).toBe(label);
});

it.each([
  ["done", "退订成功"],
  ["already_unsubscribed", "该来源已退订"],
  ["skipped_no_reliable_entry", "本次未完成退订"],
  ["skipped_login_required", "本次未完成退订"],
  ["failed_browser", "退订执行失败"],
  ["failed_provider_auth", "退订执行失败"],
  [undefined, "退订任务已结束，结果未知"],
])("uses outcome %s rather than completed task status", (outcome, label) => {
  expect(actionResultLabel(receipt(outcome))).toBe(label);
});

it("keeps Attempt accessible when the entry URL is unavailable", () => {
  render(<MemoryRouter><ObservabilityDetails events={[receipt("skipped_no_reliable_entry")]} classificationId="mail-1" entry={{available:false,reason:"entry_unavailable"}}/></MemoryRouter>);
  expect(screen.getByRole("link", {name:/Attempt #9203/})).toHaveAttribute("href", "/attempts/9203");
  expect(screen.queryByRole("button", {name:"复制地址"})).not.toBeInTheDocument();
  expect(screen.getByText("没有保存退订地址：当前记录没有可验证的退订地址。")).toBeInTheDocument();
});
