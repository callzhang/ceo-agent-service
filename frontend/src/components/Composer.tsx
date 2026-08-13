import { Paperclip, RotateCw, Send, Square, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { createTurn, stopTurn, uploadAttachment } from "../api";
import type { Attachment, RuntimeCapabilities, Turn } from "../types";

const maxAttachmentBytes = 20 * 1024 * 1024;
const maxAttachmentFiles = 8;
const maxMessageLength = 100_000;

type PendingStatus = "uploading" | "uploaded" | "failed";

interface PendingFile {
  key: string;
  uploadRequestId: string;
  file: File;
  status: PendingStatus;
  error: string;
  attachment?: Attachment;
}

interface ComposerProps {
  taskId: string | null;
  activeTurn: Turn | null;
  attachments: Attachment[];
  capabilities?: RuntimeCapabilities["capabilities"] | null;
  onTurnCreated: (turn: Turn) => void | Promise<void>;
  onTurnStopped?: (turn: Turn) => void | Promise<void>;
  onAttachmentUploaded?: (attachment: Attachment) => void | Promise<void>;
}

const imageExtensions = new Set(["avif", "bmp", "gif", "heic", "jpeg", "jpg", "png", "tif", "tiff", "webp"]);

function permittedImage(filename: string, mediaType: string) {
  const extension = filename.toLowerCase().split(".").at(-1) ?? "";
  return mediaType.toLowerCase().startsWith("image/") && imageExtensions.has(extension);
}

async function fileAsBase64(file: File, signal: AbortSignal): Promise<string> {
  if (signal.aborted) throw new DOMException("Aborted", "AbortError");
  const buffer = typeof file.arrayBuffer === "function"
    ? await file.arrayBuffer()
    : await new Response(file).arrayBuffer();
  if (signal.aborted) throw new DOMException("Aborted", "AbortError");
  const bytes = new Uint8Array(buffer);
  const chunks: string[] = [];
  const chunkSize = 32_766;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    const chunk = bytes.subarray(offset, offset + chunkSize);
    chunks.push(btoa(String.fromCharCode(...chunk)));
  }
  return chunks.join("");
}

function nonterminal(turn: Turn | null) {
  return Boolean(turn && ["queued", "running", "waiting_confirmation"].includes(turn.status));
}

export function Composer({ taskId, activeTurn, attachments, capabilities, onTurnCreated, onTurnStopped, onAttachmentUploaded }: ComposerProps) {
  const [text, setText] = useState("");
  const [pendingFiles, setPendingFiles] = useState<PendingFile[]>([]);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const [stopping, setStopping] = useState(false);
  const mounted = useRef(true);
  const taskIdRef = useRef(taskId);
  taskIdRef.current = taskId;
  const composing = useRef(false);
  const uploadControllers = useRef(new Map<string, AbortController>());
  const sendController = useRef<AbortController | null>(null);
  const stopController = useRef<AbortController | null>(null);
  const submissionIdentity = useRef<{ signature: string; clientRequestId: string } | null>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      for (const controller of uploadControllers.current.values()) controller.abort();
      sendController.current?.abort();
      stopController.current?.abort();
    };
  }, []);

  useEffect(() => {
    for (const controller of uploadControllers.current.values()) controller.abort();
    uploadControllers.current.clear();
    sendController.current?.abort();
    stopController.current?.abort();
    setPendingFiles([]);
    setText("");
    setError("");
    setSending(false);
    setStopping(false);
    submissionIdentity.current = null;
  }, [taskId]);

  function invalidateSubmissionIdentity() {
    submissionIdentity.current = null;
  }

  function updatePending(key: string, update: (item: PendingFile) => PendingFile) {
    setPendingFiles((current) => current.map((item) => item.key === key ? update(item) : item));
  }

  async function beginUpload(item: PendingFile) {
    const selectedTask = taskIdRef.current;
    if (!selectedTask) return;
    uploadControllers.current.get(item.key)?.abort();
    const controller = new AbortController();
    uploadControllers.current.set(item.key, controller);
    updatePending(item.key, (current) => ({ ...current, status: "uploading", error: "" }));
    try {
      const content = await fileAsBase64(item.file, controller.signal);
      const uploaded = await uploadAttachment(selectedTask, {
        client_request_id: item.uploadRequestId,
        filename: item.file.name,
        media_type: item.file.type,
        content_base64: content,
      }, { signal: controller.signal });
      if (!mounted.current || taskIdRef.current !== selectedTask || uploadControllers.current.get(item.key) !== controller) return;
      updatePending(item.key, (current) => ({ ...current, status: "uploaded", attachment: uploaded, error: "" }));
      try {
        void Promise.resolve(onAttachmentUploaded?.(uploaded)).catch(() => undefined);
      } catch {
        // The upload is already persisted; supplementary UI callbacks cannot undo it.
      }
    } catch (uploadError) {
      if (!mounted.current || taskIdRef.current !== selectedTask || controller.signal.aborted) return;
      updatePending(item.key, (current) => ({ ...current, status: "failed", error: "上传失败" }));
    } finally {
      if (uploadControllers.current.get(item.key) === controller) uploadControllers.current.delete(item.key);
    }
  }

  function chooseFiles(files: FileList | null) {
    if (!files || !taskId) return;
    const selected = Array.from(files);
    const existingBytes = pendingFiles.reduce((total, item) => total + item.file.size, 0);
    if (!capabilities?.image_input) {
      setError("当前运行时未声明图片输入能力");
      return;
    }
    if (selected.some((file) => !permittedImage(file.name, file.type))) {
      setError("仅支持带有效图片扩展名的 image/* 文件");
      return;
    }
    if (pendingFiles.length + selected.length > maxAttachmentFiles) {
      setError(`一次最多选择 ${maxAttachmentFiles} 个图片文件`);
      return;
    }
    if (selected.some((file) => file.size > maxAttachmentBytes) || existingBytes + selected.reduce((total, file) => total + file.size, 0) > maxAttachmentBytes) {
      setError("附件单个及本次选择总量均不能超过 20 MiB");
      return;
    }
    setError("");
    invalidateSubmissionIdentity();
    const additions = selected.map((file) => ({ key: crypto.randomUUID(), uploadRequestId: crypto.randomUUID(), file, status: "uploading" as const, error: "" }));
    setPendingFiles((current) => [...current, ...additions]);
    for (const item of additions) void beginUpload(item);
  }

  function removeFile(key: string) {
    uploadControllers.current.get(key)?.abort();
    uploadControllers.current.delete(key);
    setPendingFiles((current) => current.filter((item) => item.key !== key));
    invalidateSubmissionIdentity();
  }

  async function send() {
    const selectedTask = taskIdRef.current;
    const message = text.trim();
    const unsupportedAttachments = [
      ...attachments,
      ...pendingFiles.flatMap((item) => item.attachment ? [item.attachment] : []),
    ].some((attachment) => capabilities !== undefined && (!capabilities?.image_input || !permittedImage(attachment.filename, attachment.media_type)));
    if (!selectedTask || !capabilities?.streamed_text || !message || message.length > maxMessageLength || sendController.current || sending || nonterminal(activeTurn) || unsupportedAttachments || pendingFiles.some((item) => item.status !== "uploaded")) return;
    const attachmentIds = Array.from(new Set([
      ...attachments.map((attachment) => attachment.id),
      ...pendingFiles.flatMap((item) => item.attachment ? [item.attachment.id] : []),
    ]));
    const signature = JSON.stringify([selectedTask, message, attachmentIds]);
    if (!submissionIdentity.current || submissionIdentity.current.signature !== signature) {
      submissionIdentity.current = { signature, clientRequestId: crypto.randomUUID() };
    }
    const identity = submissionIdentity.current;
    const controller = new AbortController();
    sendController.current = controller;
    setSending(true);
    setError("");
    try {
      const turn = await createTurn(selectedTask, message, identity.clientRequestId, { signal: controller.signal });
      if (!mounted.current || taskIdRef.current !== selectedTask || sendController.current !== controller) return;
      setText("");
      setPendingFiles([]);
      submissionIdentity.current = null;
      await onTurnCreated(turn);
    } catch (sendError) {
      if (mounted.current && taskIdRef.current === selectedTask && !controller.signal.aborted) setError("消息发送失败，请重试");
    } finally {
      if (sendController.current === controller) sendController.current = null;
      if (mounted.current && taskIdRef.current === selectedTask) setSending(false);
    }
  }

  async function stop() {
    const selectedTask = taskIdRef.current;
    if (!selectedTask || !activeTurn || stopController.current || stopping || activeTurn.stop_requested) return;
    const controller = new AbortController();
    stopController.current = controller;
    setStopping(true);
    setError("");
    try {
      const turn = await stopTurn(selectedTask, activeTurn.id, { signal: controller.signal });
      if (!mounted.current || taskIdRef.current !== selectedTask || stopController.current !== controller) return;
      await onTurnStopped?.(turn);
    } catch (stopError) {
      if (mounted.current && taskIdRef.current === selectedTask && !controller.signal.aborted) {
        setError("停止请求失败，请重试");
        setStopping(false);
      }
    } finally {
      if (stopController.current === controller) stopController.current = null;
    }
  }

  const uploadBlocked = pendingFiles.some((item) => item.status !== "uploaded");
  const unsupportedAttachments = [
    ...attachments,
    ...pendingFiles.flatMap((item) => item.attachment ? [item.attachment] : []),
  ].some((attachment) => capabilities !== undefined && (!capabilities?.image_input || !permittedImage(attachment.filename, attachment.media_type)));
  const sendDisabled = !taskId || !capabilities?.streamed_text || !text.trim() || text.trim().length > maxMessageLength || sending || nonterminal(activeTurn) || uploadBlocked || unsupportedAttachments;
  return (
    <section className="composer" aria-label="消息编辑器">
      {attachments.length > 0 && <p className="existing-attachments">任务已有 {attachments.length} 个附件</p>}
      {unsupportedAttachments && <p className="inline-alert" role="alert">现有附件与运行时能力不兼容，无法开始新回合。</p>}
      {capabilities === undefined && <p className="runtime-control-note">正在加载执行器能力</p>}
      {capabilities === null && <p className="runtime-control-note">当前执行器不可用</p>}
      {capabilities && !capabilities.image_input && <p className="runtime-control-note">当前运行时未声明图片输入能力，图片上传已禁用。</p>}
      {nonterminal(activeTurn) && !capabilities?.stoppable && <p className="runtime-control-note">当前运行时不支持安全停止，请等待回合结束。</p>}
      {pendingFiles.length > 0 && (
        <ul className="pending-files">
          {pendingFiles.map((item) => (
            <li key={item.key}>
              <span>{item.file.name}</span>
              <small>{item.status === "uploading" ? "上传中" : item.status === "uploaded" ? "已上传" : "上传失败"}</small>
              {item.status === "failed" && <button type="button" aria-label={`重试上传 ${item.file.name}`} onClick={() => void beginUpload(item)}><RotateCw aria-hidden="true" size={15} /></button>}
              {item.status !== "uploaded" && <button type="button" aria-label={`移除附件 ${item.file.name}`} onClick={() => removeFile(item.key)}><Trash2 aria-hidden="true" size={15} /></button>}
            </li>
          ))}
        </ul>
      )}
      {error && <p className="inline-alert" role="alert">{error}</p>}
      <div className="composer-row">
        <label className="attachment-button">
          <Paperclip aria-hidden="true" size={18} />
          <input aria-label="添加图片" type="file" multiple accept="image/*" disabled={!taskId || !capabilities?.image_input || sending || nonterminal(activeTurn)} onChange={(event) => { chooseFiles(event.currentTarget.files); event.currentTarget.value = ""; }} />
        </label>
        <textarea
          aria-label="发送消息"
          maxLength={maxMessageLength}
          placeholder={taskId ? (nonterminal(activeTurn) ? "当前回合结束后可继续发送" : "输入消息，Enter 发送") : "请先选择或创建任务"}
          value={text}
          disabled={!taskId}
          onChange={(event) => setText(event.target.value)}
          onCompositionStart={() => { composing.current = true; }}
          onCompositionEnd={() => { composing.current = false; }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !composing.current && !event.nativeEvent.isComposing) {
              event.preventDefault();
              void send();
            }
          }}
        />
        {nonterminal(activeTurn) && capabilities?.stoppable ? (
          <button type="button" className="stop-button" aria-label="停止执行" disabled={stopping || Boolean(activeTurn?.stop_requested)} onClick={() => void stop()}><Square aria-hidden="true" size={16} />停止</button>
        ) : !nonterminal(activeTurn) ? (
          <button type="button" className="send-button" aria-label="发送" disabled={sendDisabled} onClick={() => void send()}><Send aria-hidden="true" size={18} /></button>
        ) : <span aria-hidden="true" />}
      </div>
    </section>
  );
}
