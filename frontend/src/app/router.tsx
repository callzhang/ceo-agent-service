import { BrowserRouter, Navigate, Route, Routes, useParams, useSearchParams } from "react-router-dom";
import type { ReactNode } from "react";

import { App } from "../app";
import { AppShell } from "./AppShell";
import { TaskDetailPage } from "../pages/TaskDetailPage";
import { TasksPage } from "../pages/TasksPage";
import { AttentionPage } from "../pages/AttentionPage";
import { HistoryPage } from "../pages/HistoryPage";
import { StatusPage } from "../pages/StatusPage";
import { FeedbackPage } from "../pages/FeedbackPage";
import { SettingsPage } from "../pages/SettingsPage";
import { DomainListPage } from "../pages/DomainListPage";
import { TutorialPage } from "../pages/TutorialPage";
import { CodexSessionDetailPage } from "../pages/CodexPages";
import { BusinessDetailPage } from "../pages/BusinessDetailPage";

function PlaceholderPage({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <main className="console-page" aria-labelledby="console-page-title">
      <div className="console-page-card">
        <p className="eyebrow">CEO AGENT CONSOLE</p>
        <h1 id="console-page-title">{title}</h1>
        {children}
      </div>
    </main>
  );
}

function AgentRoute() {
  return <App showGlobalNav={false} />;
}

function SettingsRoute() {
  return <SettingsPage />;
}

function TaskDetailRoute() {
  const { projectId } = useParams();
  return <TaskDetailPage projectId={projectId || ""} />;
}

function CodexDetailRoute() {
  return <CodexSessionDetailPage />;
}

function ConsoleRoutes() {
  return (
    <Routes>
      <Route path="/" element={<AgentRoute />} />
      <Route path="/history" element={<HistoryPage />} />
      <Route path="/tasks" element={<TasksPage />} />
      <Route path="/tasks/:projectId" element={<TaskDetailRoute />} />
      <Route path="/settings" element={<SettingsRoute />} />
      <Route path="/user-feedback" element={<FeedbackPage />} />
      <Route path="/tutorial" element={<TutorialPage />} />
      <Route path="/notifications" element={<DomainListPage title="Notifications" endpoint="/api/console/notifications" />} />
      <Route path="/codex" element={<DomainListPage title="Codex Sessions" endpoint="/api/console/codex/sessions" kind="codex" />} />
      <Route path="/codex/:sessionId" element={<CodexDetailRoute />} />
      <Route path="/wechat/review" element={<DomainListPage title="WeChat 待发审核" endpoint="/api/console/wechat/review" kind="wechat" />} />
      <Route path="/wechat/memory-review" element={<DomainListPage title="WeChat Memory Review" endpoint="/api/console/wechat/memory-review" kind="wechat" />} />
      <Route path="/wechat/deliveries" element={<DomainListPage title="WeChat Deliveries" endpoint="/api/console/wechat/deliveries" kind="wechat" />} />
      <Route path="/wechat/conversations" element={<DomainListPage title="WeChat Conversations" endpoint="/api/console/wechat/conversations" kind="wechat" />} />
      <Route path="/attempts/:attemptId" element={<BusinessDetailPage kind="Attempt" endpoint="/api/console/history/:id" />} />
      <Route path="/attempts/:attemptId/execution/:role" element={<BusinessDetailPage kind="Execution details" endpoint="/api/console/history/:id" />} />
      <Route path="/meeting-attempts/:runId" element={<BusinessDetailPage kind="Meeting Attempt" endpoint="/api/console/meeting-attempts/:id" />} />
      <Route path="/oa-approvals/:processInstanceId" element={<BusinessDetailPage kind="OA Approval" endpoint="/api/console/oa-approvals/:id" />} />
      <Route path="/status" element={<StatusPage />} />
      <Route path="/workers" element={<StatusPage />} />
      <Route path="/attention" element={<AttentionPage />} />
      <Route path="/config" element={<Navigate to="/settings?tab=configuration" replace />} />
      <Route path="/developer-prompt" element={<Navigate to="/settings?tab=prompts" replace />} />
      <Route path="/logs" element={<Navigate to="/history" replace />} />
      <Route path="/errors" element={<Navigate to="/history" replace />} />
      <Route path="*" element={<PlaceholderPage title="页面不存在"><p>请检查地址，或从顶部导航选择一个业务页面。</p></PlaceholderPage>} />
    </Routes>
  );
}

export function ConsoleRouter() {
  return (
    <BrowserRouter>
      <AppShell>
        <ConsoleRoutes />
      </AppShell>
    </BrowserRouter>
  );
}
