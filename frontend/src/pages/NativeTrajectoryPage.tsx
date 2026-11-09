import { Link, useParams } from "react-router-dom";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { NativeAttemptTrajectory } from "../components/NativeAttemptTrajectory";

export function NativeTrajectoryPage() {
  const { runtimeAttemptId = "" } = useParams();
  return <ConsolePageLayout title="原生 Agent 轨迹" actions={<Link className="secondary-button" to="/history">返回 History</Link>}>
    <NativeAttemptTrajectory key={runtimeAttemptId} id={runtimeAttemptId} />
  </ConsolePageLayout>;
}
