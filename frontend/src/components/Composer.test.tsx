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

const imageCapabilities = {
  session_resume: true,
  streamed_text: true,
  structured_tools: true,
  image_input: true,
  model_selection: true,
  mcp_configuration: true,
  stoppable: true,
  recoverable: true,
};

const activeTurn = {
  id: "turn-1", task_id: "task-1", client_request_id: "req", user_text: "run", status: "running" as const,
  stop_requested: false, final_text: "", error_code: "", error_detail: "", started_at: "", completed_at: "", created_at: "", updated_at: "",
};

beforeEach(() => vi.clearAllMocks());

describe("Composer", () => {
  it("sends trimmed text on Enter with a UUID while preserving Shift+Enter and IME composition", async () => {
    const user = userEvent.setup();
    api.createTurn.mockResolvedValue({ ...activeTurn, status: "queued" });
    render(<Composer taskId="task-1" activeTurn={null} attachments={[]} capabilities={imageCapabilities} onTurnCreated={vi.fn()} />);
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
      id: "attachment-1", task_id: "task-1", filename: "notes.png", media_type: "image/png", size_bytes: 5, created_at: "",
    });
    api.createTurn.mockResolvedValue({ ...activeTurn, status: "queued" });
    render(<Composer taskId="task-1" activeTurn={null} attachments={[]} capabilities={imageCapabilities} onTurnCreated={vi.fn()} />);

    fireEvent.change(screen.getByLabelText("添加图片"), { target: { files: [new File(["notes"], "notes.png", { type: "image/png" })] } });
    expect(await screen.findByText("上传失败")).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "发送消息" }), "review");
    expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "重试上传 notes.png" }));
    expect(await screen.findByText("已上传")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(api.uploadAttachment).toHaveBeenCalledTimes(2);
    expect(api.createTurn).toHaveBeenCalledOnce();
  });

  it("reuses the attachment request ID after an ambiguous upload failure", async () => {
    const user = userEvent.setup();
    api.uploadAttachment.mockRejectedValueOnce(new Error("lost response")).mockResolvedValueOnce({
      id: "attachment-retried", task_id: "task-1", filename: "retry.png", media_type: "image/png", size_bytes: 5, created_at: "",
    });
    render(<Composer taskId="task-1" activeTurn={null} attachments={[]} capabilities={imageCapabilities} onTurnCreated={vi.fn()} />);

    fireEvent.change(screen.getByLabelText("添加图片"), { target: { files: [new File(["image"], "retry.png", { type: "image/png" })] } });
    await screen.findByText("上传失败");
    await user.click(screen.getByRole("button", { name: "重试上传 retry.png" }));
    await screen.findByText("已上传");

    expect(api.uploadAttachment).toHaveBeenCalledTimes(2);
    expect(api.uploadAttachment.mock.calls[0][1].client_request_id).toMatch(/^[0-9a-f-]{36}$/);
    expect(api.uploadAttachment.mock.calls[1][1].client_request_id).toBe(api.uploadAttachment.mock.calls[0][1].client_request_id);
  });

  it("rejects unsupported and over-total-limit files before upload", async () => {
    render(<Composer taskId="task-1" activeTurn={null} attachments={[]} capabilities={imageCapabilities} onTurnCreated={vi.fn()} />);
    const input = screen.getByLabelText("添加图片");
    fireEvent.change(input, { target: { files: [new File(["bad"], "script.bin", { type: "application/octet-stream" })] } });
    expect(await screen.findByRole("alert")).toHaveTextContent("仅支持");
    expect(api.uploadAttachment).not.toHaveBeenCalled();
  });

  it("rejects a multi-image selection whose total exceeds 20 MiB", async () => {
    render(<Composer taskId="task-1" activeTurn={null} attachments={[]} capabilities={imageCapabilities} onTurnCreated={vi.fn()} />);
    const first = new File(["a"], "first.png", { type: "image/png" });
    const second = new File(["b"], "second.png", { type: "image/png" });
    Object.defineProperty(first, "size", { value: 11 * 1024 * 1024 });
    Object.defineProperty(second, "size", { value: 10 * 1024 * 1024 });

    fireEvent.change(screen.getByLabelText("添加图片"), { target: { files: [first, second] } });

    expect(await screen.findByRole("alert")).toHaveTextContent("总量均不能超过 20 MiB");
    expect(api.uploadAttachment).not.toHaveBeenCalled();
  });

  it("shows an idempotent stop control for active turns", async () => {
    const user = userEvent.setup();
    let resolveStop!: (value: typeof activeTurn) => void;
    api.stopTurn.mockReturnValue(new Promise((resolve) => { resolveStop = resolve; }));
    render(<Composer taskId="task-1" activeTurn={activeTurn} attachments={[]} capabilities={imageCapabilities} onTurnCreated={vi.fn()} />);

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
        <Composer taskId="task-1" activeTurn={null} attachments={[]} capabilities={imageCapabilities} onTurnCreated={onTurnCreated} />
      </StrictMode>,
    );

    await user.type(screen.getByRole("textbox", { name: "发送消息" }), "strict send");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => expect(onTurnCreated).toHaveBeenCalledWith(expect.objectContaining({ id: "turn-strict" })));
  });

  it("reuses the same client request ID after a lost create response", async () => {
    const user = userEvent.setup();
    api.createTurn.mockRejectedValueOnce(new Error("lost response")).mockResolvedValueOnce({ ...activeTurn, status: "queued" });
    render(<Composer taskId="task-1" activeTurn={null} attachments={[]} capabilities={imageCapabilities} onTurnCreated={vi.fn()} />);

    await user.type(screen.getByRole("textbox", { name: "发送消息" }), "retry safely");
    await user.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("消息发送失败");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(api.createTurn).toHaveBeenCalledTimes(2);
    expect(api.createTurn.mock.calls[1][2]).toBe(api.createTurn.mock.calls[0][2]);
  });

  it("starts a new idempotency intent after the failed draft is edited", async () => {
    const user = userEvent.setup();
    api.createTurn.mockRejectedValueOnce(new Error("lost response")).mockResolvedValueOnce({ ...activeTurn, status: "queued" });
    render(<Composer taskId="task-1" activeTurn={null} attachments={[]} capabilities={imageCapabilities} onTurnCreated={vi.fn()} />);

    const editor = screen.getByRole("textbox", { name: "发送消息" });
    await user.type(editor, "first intent");
    await user.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("消息发送失败");
    await user.type(editor, " revised");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(api.createTurn).toHaveBeenCalledTimes(2);
    expect(api.createTurn.mock.calls[1][2]).not.toBe(api.createTurn.mock.calls[0][2]);
  });

  it("keeps the turn request ID when edits do not change the trimmed payload", async () => {
    const user = userEvent.setup();
    api.createTurn.mockRejectedValueOnce(new Error("lost response")).mockResolvedValueOnce({ ...activeTurn, status: "queued" });
    render(<Composer taskId="task-1" activeTurn={null} attachments={[]} capabilities={imageCapabilities} onTurnCreated={vi.fn()} />);

    const editor = screen.getByRole("textbox", { name: "发送消息" });
    await user.type(editor, "stable payload");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByRole("alert");
    await user.type(editor, "   ");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(api.createTurn.mock.calls[1][2]).toBe(api.createTurn.mock.calls[0][2]);
  });

  it("blocks send while capabilities load or when the runtime is unavailable", async () => {
    const user = userEvent.setup();
    const view = render(<Composer taskId="task-1" activeTurn={null} attachments={[]} capabilities={undefined} onTurnCreated={vi.fn()} />);
    await user.type(screen.getByRole("textbox", { name: "发送消息" }), "do work");

    expect(screen.getByText("正在加载执行器能力")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
    view.rerender(<Composer taskId="task-1" activeTurn={null} attachments={[]} capabilities={null} onTurnCreated={vi.fn()} />);
    expect(screen.getByText("当前执行器不可用")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "发送" }));
    expect(api.createTurn).not.toHaveBeenCalled();
  });

  it("gates image upload and stop controls on runtime capabilities", () => {
    const unsupportedAttachment = {
      id: "attachment-1", task_id: "task-1", filename: "notes.txt", media_type: "text/plain", size_bytes: 5, created_at: "",
    };
    render(
      <Composer
        taskId="task-1"
        activeTurn={activeTurn}
        attachments={[unsupportedAttachment]}
        capabilities={{ ...imageCapabilities, image_input: false, stoppable: false }}
        onTurnCreated={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("添加图片")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "停止执行" })).not.toBeInTheDocument();
    expect(screen.getByText(/不支持安全停止/)).toBeInTheDocument();
    expect(screen.getByText(/现有附件与运行时能力不兼容/)).toBeInTheDocument();
  });
});
