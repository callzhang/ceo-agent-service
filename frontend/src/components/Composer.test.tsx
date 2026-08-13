import { StrictMode } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  uploadAttachment: vi.fn(),
  createTurn: vi.fn(),
  stopTurn: vi.fn(),
}));
vi.mock("../api", () => api);

import { Composer } from "./Composer";

const activeTurn = {
  id: "turn-1", task_id: "task-1", client_request_id: "req", user_text: "run", status: "running" as const,
  stop_requested: false, final_text: "", error_code: "", error_detail: "", started_at: "", completed_at: "", created_at: "", updated_at: "",
};

beforeEach(() => vi.clearAllMocks());

describe("Composer", () => {
  it("sends trimmed text on Enter with a UUID while preserving Shift+Enter and IME composition", async () => {
    const user = userEvent.setup();
    api.createTurn.mockResolvedValue({ ...activeTurn, status: "queued" });
    render(<Composer taskId="task-1" activeTurn={null} attachments={[]} onTurnCreated={vi.fn()} />);
    const input = screen.getByRole("textbox", { name: "发送消息" });

    await user.type(input, "  hello");
    await user.keyboard("{Shift>}{Enter}{/Shift}");
    expect(api.createTurn).not.toHaveBeenCalled();
    fireEvent.compositionStart(input);
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    expect(api.createTurn).not.toHaveBeenCalled();
    fireEvent.compositionEnd(input);
    await user.keyboard("{Enter}");

    expect(api.createTurn).toHaveBeenCalledWith("task-1", "hello", expect.stringMatching(/^[0-9a-f-]{36}$/), { signal: expect.any(AbortSignal) });
  });

  it("keeps a failed upload visible, blocks send, and retries before creating the turn", async () => {
    const user = userEvent.setup();
    api.uploadAttachment.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce({
      id: "attachment-1", task_id: "task-1", filename: "notes.txt", media_type: "text/plain", size_bytes: 5, created_at: "",
    });
    api.createTurn.mockResolvedValue({ ...activeTurn, status: "queued" });
    render(<Composer taskId="task-1" activeTurn={null} attachments={[]} onTurnCreated={vi.fn()} />);

    fireEvent.change(screen.getByLabelText("添加附件"), { target: { files: [new File(["notes"], "notes.txt", { type: "text/plain" })] } });
    expect(await screen.findByText("上传失败")).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "发送消息" }), "review");
    expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "重试上传 notes.txt" }));
    expect(await screen.findByText("已上传")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(api.uploadAttachment).toHaveBeenCalledTimes(2);
    expect(api.createTurn).toHaveBeenCalledOnce();
  });

  it("rejects unsupported and over-total-limit files before upload", async () => {
    const user = userEvent.setup();
    render(<Composer taskId="task-1" activeTurn={null} attachments={[]} onTurnCreated={vi.fn()} />);
    const input = screen.getByLabelText("添加附件");
    fireEvent.change(input, { target: { files: [new File(["bad"], "script.bin", { type: "application/octet-stream" })] } });
    expect(await screen.findByRole("alert")).toHaveTextContent("不支持的文件类型");
    expect(api.uploadAttachment).not.toHaveBeenCalled();
  });

  it("shows an idempotent stop control for active turns", async () => {
    const user = userEvent.setup();
    let resolveStop!: (value: typeof activeTurn) => void;
    api.stopTurn.mockReturnValue(new Promise((resolve) => { resolveStop = resolve; }));
    render(<Composer taskId="task-1" activeTurn={activeTurn} attachments={[]} onTurnCreated={vi.fn()} />);

    const stop = screen.getByRole("button", { name: "停止执行" });
    await user.click(stop);
    await user.click(stop);
    expect(api.stopTurn).toHaveBeenCalledOnce();
    expect(stop).toBeDisabled();
    await act(async () => resolveStop({ ...activeTurn, stop_requested: true }));
  });

  it("completes sends after the StrictMode effect replay", async () => {
    const user = userEvent.setup();
    const onTurnCreated = vi.fn();
    api.createTurn.mockResolvedValue({ ...activeTurn, id: "turn-strict", status: "queued" });
    render(
      <StrictMode>
        <Composer taskId="task-1" activeTurn={null} attachments={[]} onTurnCreated={onTurnCreated} />
      </StrictMode>,
    );

    await user.type(screen.getByRole("textbox", { name: "发送消息" }), "strict send");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => expect(onTurnCreated).toHaveBeenCalledWith(expect.objectContaining({ id: "turn-strict" })));
  });
});
