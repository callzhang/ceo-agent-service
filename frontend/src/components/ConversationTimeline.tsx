import { memo, useMemo, useRef } from "react";
import ReactMarkdown from "react-markdown";
import { Virtuoso } from "react-virtuoso";
import remarkGfm from "remark-gfm";

import { timelineBlocks } from "../events";
import type { Artifact, Confirmation, Timeline, Turn, WorkbenchEvent } from "../types";
import { ArtifactList } from "./ArtifactList";
import { ConfirmationCard } from "./ConfirmationCard";
import { ExecutionStep, safeDisplayText } from "./ExecutionStep";

interface ConversationTimelineProps {
  timeline: Timeline;
  activeTurnId: string | null;
  onConfirm: (confirmation: Confirmation) => Promise<void>;
  onCancel: (confirmation: Confirmation) => Promise<void>;
}

export function assistantTurnKey(turn: Turn) {
  return `turn:${turn.id}:assistant`;
}

function safeWebHref(href?: string): string | undefined {
  if (!href) return undefined;
  try {
    const parsed = new URL(href);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : undefined;
  } catch {
    return undefined;
  }
}

const MarkdownBlock = memo(function MarkdownBlock({ text }: { text: string }) {
  return (
    <div className="assistant-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        urlTransform={(url) => safeWebHref(url) ?? ""}
        components={{
          a: ({ href, children }) => {
            const safe = safeWebHref(href);
            return safe
              ? <a href={safe} target="_blank" rel="noopener noreferrer">{children}</a>
              : <span>{children}</span>;
          },
          img: ({ alt }) => <span role="note">[图片已阻止：{alt || "未命名"}]</span>,
        }}
      >{text}</ReactMarkdown>
    </div>
  );
});

function TurnItem({ turn, events, confirmationsById, artifactsById, taskId, active, onConfirm, onCancel }: {
  turn: Turn;
  events: WorkbenchEvent[];
  confirmationsById: Map<string, Confirmation>;
  artifactsById: Map<string, Artifact>;
  taskId: string;
  active: boolean;
  onConfirm: ConversationTimelineProps["onConfirm"];
  onCancel: ConversationTimelineProps["onCancel"];
}) {
  const blocks = useMemo(() => timelineBlocks(turn.id, events, turn.status), [events, turn.id, turn.status]);
  const renderedText = blocks.some((block) => block.kind === "markdown");
  const nonterminal = ["queued", "running", "waiting_confirmation"].includes(turn.status);
  const authoritativeFinalText = !nonterminal && Boolean(turn.final_text);
  return (
    <article className="conversation-turn" data-status={turn.status} data-active={active || undefined}>
      <div className="user-message"><p>{turn.user_text}</p></div>
      <div className="assistant-message">
        {blocks.map((block) => {
          if (block.kind === "markdown") return !authoritativeFinalText ? <MarkdownBlock key={block.key} text={block.text ?? ""} /> : null;
          if (block.kind === "thinking") return <details className="thinking-block" key={block.key}><summary>思考摘要</summary><p>{block.text}</p></details>;
          if (block.kind === "tool" || block.kind === "file") return <ExecutionStep key={block.key} kind={block.kind} status={block.status} payload={block.payload} />;
          if (block.kind === "confirmation") {
            const confirmation = confirmationsById.get(block.confirmationId ?? "");
            return confirmation?.turn_id === turn.id ? <ConfirmationCard key={block.key} confirmation={confirmation} onConfirm={onConfirm} onCancel={onCancel} /> : null;
          }
          const artifact = artifactsById.get(block.artifactId ?? "");
          return artifact?.turn_id === turn.id ? <ArtifactList key={block.key} taskId={taskId} turnId={turn.id} artifacts={[artifact]} /> : null;
        })}
        {authoritativeFinalText && <MarkdownBlock text={turn.final_text} />}
        {!authoritativeFinalText && !renderedText && turn.final_text && <MarkdownBlock text={turn.final_text} />}
        {turn.status === "queued" && <p className="turn-state" role="status">已排队</p>}
        {turn.status === "running" && <p className="turn-state" role="status">执行中</p>}
        {turn.status === "waiting_confirmation" && <p className="turn-state" role="status">等待确认</p>}
        {turn.status === "completed" && <p className="turn-state">已完成</p>}
        {turn.status === "stopped" && <p className="turn-state">已停止</p>}
        {turn.status === "failed" && <p className="turn-error" role="alert">执行失败：{safeDisplayText(turn.error_detail || turn.error_code, "未知错误")}</p>}
      </div>
    </article>
  );
}

export function ConversationTimeline({ timeline, activeTurnId, onConfirm, onCancel }: ConversationTimelineProps) {
  const turns = useMemo(() => [...timeline.turns].reverse(), [timeline.turns]);
  const eventsByTurn = useMemo(() => {
    const indexed = new Map<string, WorkbenchEvent[]>();
    for (const event of timeline.events) {
      const existing = indexed.get(event.turn_id);
      if (existing) existing.push(event);
      else indexed.set(event.turn_id, [event]);
    }
    return indexed;
  }, [timeline.events]);
  const confirmationsById = useMemo(
    () => new Map(timeline.confirmations.map((confirmation) => [confirmation.id, confirmation])),
    [timeline.confirmations],
  );
  const artifactsById = useMemo(
    () => new Map(timeline.artifacts.map((artifact) => [artifact.id, artifact])),
    [timeline.artifacts],
  );
  const firstItemIndex = useRef(999_900);
  const priorOldestTurnId = useRef<string | null>(null);
  if (turns.length) {
    if (priorOldestTurnId.current) {
      const priorOldestPosition = turns.findIndex((turn) => turn.id === priorOldestTurnId.current);
      if (priorOldestPosition > 0) firstItemIndex.current -= priorOldestPosition;
      if (priorOldestPosition < 0) firstItemIndex.current = 999_900;
    }
    priorOldestTurnId.current = turns[0].id;
  }
  return (
    <div className="conversation-timeline" data-testid="conversation-timeline">
      <Virtuoso
        className="conversation-virtuoso"
        data={turns}
        firstItemIndex={firstItemIndex.current}
        initialItemCount={Math.min(turns.length, 12)}
        followOutput={activeTurnId ? "smooth" : false}
        computeItemKey={(_index, turn) => assistantTurnKey(turn)}
        itemContent={(_index, turn) => (
          <TurnItem
            turn={turn}
            events={eventsByTurn.get(turn.id) ?? []}
            confirmationsById={confirmationsById}
            artifactsById={artifactsById}
            taskId={timeline.task.id}
            active={turn.id === activeTurnId}
            onConfirm={onConfirm}
            onCancel={onCancel}
          />
        )}
      />
    </div>
  );
}
