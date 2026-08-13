import { Download } from "lucide-react";

import type { Artifact } from "../types";
import { safeDisplayText } from "./ExecutionStep";

interface ArtifactListProps {
  taskId: string;
  turnId: string;
  artifacts: Artifact[];
}

function safeLabel(value: string) {
  return safeDisplayText(value, "下载产物").slice(0, 180);
}

export function ArtifactList({ taskId, turnId, artifacts }: ArtifactListProps) {
  if (!artifacts.length) return null;
  return (
    <ul className="artifact-list" aria-label="产物">
      {artifacts.map((artifact) => {
        const href = `/api/workbench/tasks/${encodeURIComponent(taskId)}/turns/${encodeURIComponent(turnId)}/artifacts/${encodeURIComponent(artifact.id)}/download`;
        return (
          <li key={artifact.id}>
            <a href={href} target="_blank" rel="noopener noreferrer">
              <Download aria-hidden="true" size={16} />
              <span>{safeLabel(artifact.label)}</span>
              <small>{safeLabel(artifact.media_type)}</small>
            </a>
          </li>
        );
      })}
    </ul>
  );
}
