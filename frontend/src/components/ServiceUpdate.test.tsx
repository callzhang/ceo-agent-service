import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ServiceUpdate } from "./ServiceUpdate";
import { checkServiceUpdate, getServiceUpdate, getReleaseNotes, startServiceUpdate, type ServiceUpdateStatus } from "../api/serviceUpdate";
vi.mock("../api/serviceUpdate", () => ({ checkServiceUpdate:vi.fn(),getServiceUpdate:vi.fn(),getReleaseNotes:vi.fn(),startServiceUpdate:vi.fn() }));
const snapshot = {status:"update_available",local_commit:"a".repeat(40),remote_commit:"b".repeat(40),commits_behind:25,fingerprint:"reviewed"};
const available: ServiceUpdateStatus = {snapshot,operation:null,progress:null,current_commit:snapshot.local_commit};
const notes = {local_commit:snapshot.local_commit,target_commit:snapshot.remote_commit,summary:"改进队列并增加服务更新入口。",summary_source:"ai",commits:[{sha:"b".repeat(40),subject:"change",body:"完整提交正文"}]};
async function open() { fireEvent.click(await screen.findByRole("button",{name:/更新服务/})); await screen.findByRole("heading",{name:"更新服务"}); }
beforeEach(() => { vi.clearAllMocks(); vi.mocked(getServiceUpdate).mockResolvedValue(available); vi.mocked(checkServiceUpdate).mockResolvedValue({snapshot}); vi.mocked(getReleaseNotes).mockResolvedValue(notes); vi.mocked(startServiceUpdate).mockResolvedValue({operation_id:"accepted",pid:10}); });
afterEach(() => vi.useRealTimers());
describe("ServiceUpdate", () => {
 it("hides the button when the actual installed version is current",async()=>{
  vi.mocked(getServiceUpdate).mockResolvedValue({...available,current_commit:snapshot.remote_commit}); render(<ServiceUpdate/>);
  await waitFor(()=>expect(getServiceUpdate).toHaveBeenCalled()); expect(screen.queryByRole("button",{name:/更新服务/})).toBeNull();
 });
 it("opens all-commit notes without deployment and can cancel",async()=>{
  render(<ServiceUpdate/>); await open(); await screen.findByText(notes.summary);
  fireEvent.click(screen.getByText(/查看.*提交/)); expect(screen.getByText("完整提交正文")).toBeVisible();
  expect(getReleaseNotes).toHaveBeenCalledWith("reviewed",expect.anything()); expect(startServiceUpdate).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button",{name:"取消"})); expect(screen.queryByRole("dialog")).toBeNull();
 });
 it("starts only once with the reviewed fingerprint and waits for tasks",async()=>{
  render(<ServiceUpdate/>); await open(); await screen.findByText(notes.summary);
  fireEvent.click(screen.getByRole("button",{name:"确认更新"})); await waitFor(()=>expect(startServiceUpdate).toHaveBeenCalledTimes(1));
  expect(vi.mocked(startServiceUpdate).mock.calls[0][0]).toBe("reviewed");
  expect(await screen.findByText(/等待当前任务结束/)).toBeVisible(); expect(screen.getByRole("button",{name:"准备更新"})).toBeVisible(); expect(screen.getByRole("button",{name:"确认更新"})).toBeDisabled();
 });
 it("shows AI failure with raw commits and retry",async()=>{
  vi.mocked(getReleaseNotes).mockResolvedValue({...notes,summary:"",summary_error:"更新说明生成失败，请重试"});
  render(<ServiceUpdate/>); await open(); expect(await screen.findByRole("alert")).toHaveTextContent("生成失败");
  fireEvent.click(screen.getByRole("button",{name:"重试生成说明"})); await waitFor(()=>expect(getReleaseNotes).toHaveBeenCalledTimes(2));
 });
 it("shows check failure instead of claiming no update",async()=>{
  vi.mocked(getServiceUpdate).mockRejectedValue(new Error("offline")); render(<ServiceUpdate/>);
  expect(await screen.findByRole("button",{name:/检查更新失败/})).toBeVisible();
 });
 it("refuses confirmation for notes from another target",async()=>{
  vi.mocked(getReleaseNotes).mockResolvedValue({...notes,target_commit:"c".repeat(40)});
  render(<ServiceUpdate/>); await open(); expect(await screen.findByRole("alert")).toHaveTextContent("版本"); expect(screen.getByRole("button",{name:"确认更新"})).toBeDisabled();
 });
 it("shows original single-commit notes without an AI label",async()=>{
  vi.mocked(getReleaseNotes).mockResolvedValue({...notes,summary_source:"commit"});
  render(<ServiceUpdate/>); await open(); await screen.findByText(notes.summary); expect(screen.queryByText("AI 更新说明")).toBeNull();
 });
 it("reconnects and reloads only for the installed successful receipt",async()=>{
  vi.useFakeTimers(); const reload=vi.fn(); let value:ServiceUpdateStatus=available;
  vi.mocked(getServiceUpdate).mockImplementation(async()=>value);
  render(<ServiceUpdate reload={reload}/>); await act(async()=>{});
  fireEvent.click(screen.getByRole("button",{name:/更新服务/})); await act(async()=>{});
  fireEvent.click(screen.getByRole("button",{name:"确认更新"})); await act(async()=>{});
  value={...available,operation:{operation_id:"accepted",fingerprint:"reviewed"},progress:{operation_id:"accepted",status:"restarting",target_commit:snapshot.remote_commit,installed_commit:"",error:""}};
  vi.mocked(getServiceUpdate).mockRejectedValueOnce(new Error("restarting"));
  await act(async()=>{await vi.advanceTimersByTimeAsync(3000);}); expect(screen.getAllByText(/重新连接/).length).toBeGreaterThan(0); expect(reload).not.toHaveBeenCalled();
  value={...value,current_commit:snapshot.local_commit,progress:{...value.progress!,status:"succeeded",installed_commit:snapshot.remote_commit}};
  await act(async()=>{await vi.advanceTimersByTimeAsync(3000);}); expect(reload).not.toHaveBeenCalled(); expect(screen.getByRole("button",{name:"确认更新"})).toBeDisabled();
  value={...value,current_commit:snapshot.remote_commit,progress:{...value.progress!,status:"succeeded",installed_commit:snapshot.remote_commit}};
  await act(async()=>{await vi.advanceTimersByTimeAsync(3000);}); expect(reload).toHaveBeenCalledTimes(1);
 });
});

 it.each(["failed","rolled_back","needs_manual"])("retains persisted %s reason after reload and reopening",async(status)=>{
  vi.mocked(getServiceUpdate).mockResolvedValue({...available,operation:null,progress:{operation_id:"previous",status,target_commit:snapshot.remote_commit,installed_commit:"",error:"在途任务未结束，本次更新已停止"}});
  render(<ServiceUpdate/>); await open(); expect(await screen.findByRole("alert")).toHaveTextContent("在途任务未结束");
  fireEvent.click(screen.getByRole("button",{name:"取消"})); await open(); expect(await screen.findByRole("alert")).toHaveTextContent("在途任务未结束");
 });
 it("does not reload a fresh page already at the successful target before reservation cleanup",async()=>{
  const reload=vi.fn();vi.mocked(getServiceUpdate).mockResolvedValue({...available,current_commit:snapshot.remote_commit,operation:{operation_id:"finished",fingerprint:"reviewed"},progress:{operation_id:"finished",status:"succeeded",target_commit:snapshot.remote_commit,installed_commit:snapshot.remote_commit,error:""}});
  render(<ServiceUpdate reload={reload}/>);await waitFor(()=>expect(getServiceUpdate).toHaveBeenCalled());expect(reload).not.toHaveBeenCalled();
 });
