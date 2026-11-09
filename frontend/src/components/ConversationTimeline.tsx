import { useEffect, useMemo, useRef } from "react";
import { Virtuoso, type VirtuosoHandle } from "react-virtuoso";

import { timelineBlocks } from "../events";
import type { Artifact, Timeline, Turn, WorkbenchEvent } from "../types";
import { NativeAttemptTrajectory } from "./NativeAttemptTrajectory";
import { ArtifactList } from "./ArtifactList";
import { displayText, ExecutionStep } from "./ExecutionStep";
import { MarkdownContent } from "./MarkdownContent";
import { TrajectoryContent } from "./TrajectoryContent";

interface ConversationTimelineProps {
  timeline: Timeline;
  activeTurnId: string | null;
}

export function assistantTurnKey(turn: Turn) {
  return `turn:${turn.id}:assistant`;
}

export function activeTurnScrollIndex(
  turns: Turn[],
  activeTurnId: string | null,
  firstItemIndex: number,
): number | null {
  if (!activeTurnId) return null;
  const position = turns.findIndex((turn) => turn.id === activeTurnId);
  return position < 0 ? null : firstItemIndex + position;
}

export function latestTurnScrollIndex(
  turns: Turn[],
  firstItemIndex: number,
): number | null {
  return turns.length ? firstItemIndex + turns.length - 1 : null;
}

function TurnItem({ turn, events, artifactsById, taskId, active }: {
  turn: Turn;
  events: WorkbenchEvent[];
  artifactsById: Map<string, Artifact>;
  taskId: string;
  active: boolean;
}) {
  const blocks = useMemo(() => timelineBlocks(turn.id, events, turn.status), [events, turn.id, turn.status]);
  const renderedText = blocks.some((block) => block.kind === "markdown");
  const nonterminal = ["queued", "running"].includes(turn.status);
  const nativeTools = !nonterminal && Boolean(turn.runtime_attempts?.length);
  const authoritativeFinalText = !nonterminal && Boolean(turn.final_text);
  return (
    <article className="conversation-turn" data-status={turn.status} data-active={active || undefined}>
      <div className="user-message"><TrajectoryContent text={turn.user_text} label="任务输入" plain /></div>
      <div className="assistant-message">
        {nativeTools && <p className="native-tools-heading">工具执行记录 · 按原生调用分组，组内按发生顺序展示</p>}
        {nativeTools && turn.runtime_attempts?.map((attempt) => <NativeAttemptTrajectory key={attempt.id} id={String(attempt.id)} compact />)}
        {nativeTools && events.length > 0 && <p className="native-service-events">已加载服务事件：{events.filter((event) => event.event_type === "tool_started").length} 次工具开始，{events.filter((event) => event.event_type === "tool_completed").length} 次工具结束。以下为服务记录的输出与产物，不与原生工具混排。</p>}
        {blocks.map((block) => {
          if (block.kind === "markdown") return !authoritativeFinalText ? <MarkdownContent key={block.key} text={block.text ?? ""} /> : null;
          if (block.kind === "thinking") return <details className="thinking-block" key={block.key}><summary>思考摘要</summary><p>{block.text}</p></details>;
          if (block.kind === "tool" && nativeTools) return null;
          if (block.kind === "tool" || block.kind === "file") return <ExecutionStep key={block.key} kind={block.kind} status={block.status} payload={block.payload} startedAt={block.startedAt} completedAt={block.completedAt} />;
          const artifact = artifactsById.get(block.artifactId ?? "");
          return artifact?.turn_id === turn.id ? <ArtifactList key={block.key} taskId={taskId} turnId={turn.id} artifacts={[artifact]} /> : null;
        })}
        {authoritativeFinalText && <TrajectoryContent text={turn.final_text} />}
        {!authoritativeFinalText && !renderedText && turn.final_text && <TrajectoryContent text={turn.final_text} />}
        {turn.status === "queued" && <p className="turn-state" role="status">已排队</p>}
        {turn.status === "running" && <p className="turn-state" role="status">执行中</p>}
        {turn.status === "completed" && <p className="turn-state">已完成</p>}
        {turn.status === "stopped" && <p className="turn-state">已停止</p>}
        {turn.status === "failed" && <p className="turn-error" role="alert">执行失败：{displayText(turn.error_detail || turn.error_code, "未知错误")}</p>}
        {Boolean(turn.runtime_attempts?.length) && <nav className="trajectory-attempt-links" aria-label="本轮原生轨迹">
          {turn.runtime_attempts?.map((attempt) => <a key={attempt.id} href={attempt.url}>
            查看原生轨迹 #{attempt.id} · {attempt.runtime}{attempt.failure_code ? ` · ${attempt.failure_code}` : ""}
          </a>)}
        </nav>}
      </div>
    </article>
  );
}

export function ConversationTimeline({ timeline, activeTurnId }: ConversationTimelineProps) {
  const turns = useMemo(() => [...timeline.turns].reverse(), [timeline.turns]);
  const virtuosoRef = useRef<VirtuosoHandle>(null);
  const eventsByTurn = useMemo(() => {
    const indexed = new Map<string, WorkbenchEvent[]>();
    for (const event of timeline.events) {
      const existing = indexed.get(event.turn_id);
      if (existing) existing.push(event);
      else indexed.set(event.turn_id, [event]);
    }
    return indexed;
  }, [timeline.events]);
  const artifactsById = useMemo(
    () => new Map(timeline.artifacts.map((artifact) => [artifact.id, artifact])),
    [timeline.artifacts],
  );
  const firstItemIndex = useRef(999_900);
  const priorOldestTurnId = useRef<string | null>(null);
  const openedTaskId = useRef<string | null>(null);
  if (turns.length) {
    if (priorOldestTurnId.current) {
      const priorOldestPosition = turns.findIndex((turn) => turn.id === priorOldestTurnId.current);
      if (priorOldestPosition > 0) firstItemIndex.current -= priorOldestPosition;
      if (priorOldestPosition < 0) firstItemIndex.current = 999_900;
    }
    priorOldestTurnId.current = turns[0].id;
  }
  useEffect(() => {
    const index = activeTurnScrollIndex(
      turns,
      activeTurnId,
      firstItemIndex.current,
    );
    if (index === null) return;
    virtuosoRef.current?.scrollToIndex({ index, align: "start", behavior: "auto" });
  }, [activeTurnId, timeline.task.id, turns.length]);
  useEffect(() => {
    if (openedTaskId.current === timeline.task.id) return;
    openedTaskId.current = timeline.task.id;
    const index = latestTurnScrollIndex(turns, firstItemIndex.current);
    if (index === null) return;
    virtuosoRef.current?.scrollToIndex({ index, align: "start", behavior: "auto" });
  }, [timeline.task.id]);
  return (
    <div className="conversation-timeline" data-testid="conversation-timeline">
      <Virtuoso
        ref={virtuosoRef}
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
            artifactsById={artifactsById}
            taskId={timeline.task.id}
            active={turn.id === activeTurnId}
          />
        )}
      />
    </div>
  );
}
