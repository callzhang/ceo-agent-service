import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { request, type ConsoleResource } from '../../api/console';

export interface AutomaticRepairStatus {
  status: string;
  summary: string;
  conversation_url: string;
  pr_url: string;
  turn_count: number;
  refresh_url?: string;
  merge_commit?: string;
  merged_at?: string;
}

const labels: Record<string, string> = {
  queued: '等待自动修复', running: 'Agent 正在修复', pr_created: 'PR 已创建',
  awaiting_checks: '等待 CI / review', checks_passed: 'CI / review 已通过',
  feedback_blocked: 'PR 反馈修复受阻', pr_closed: 'PR 已关闭',
  merging: 'Agent 正在判断并合并', merged: '修复已合并到 main',
  review_required: '存在产品取舍，等待评审', merge_blocked: '合并受阻',
  awaiting_pr: '等待 PR 读回', blocked: '修复受阻', failed: '修复未完成', stopped: '修复已停止', no_code_change: '无需代码变更',
};

export function AutomaticRepairCard({ repair }: { repair?: AutomaticRepairStatus | null }) {
  const [current, setCurrent] = useState(repair);
  const [refreshError, setRefreshError] = useState('');
  useEffect(() => { setCurrent(repair); }, [repair]);
  useEffect(() => {
    if (!current?.refresh_url || !['queued', 'running', 'awaiting_pr', 'pr_created', 'awaiting_checks', 'checks_passed', 'feedback_blocked', 'merging', 'review_required', 'merge_blocked'].includes(current.status)) return;
    const controller = new AbortController();
    const refresh = () => {
      request<ConsoleResource<AutomaticRepairStatus | null>>(current.refresh_url!, { signal: controller.signal })
        .then((response) => { if (!controller.signal.aborted) { setCurrent(response.item); setRefreshError(''); } })
        .catch((reason: unknown) => { if (!controller.signal.aborted) setRefreshError(reason instanceof Error ? reason.message : '刷新失败'); });
    };
    const timer = window.setInterval(refresh, 5000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [current?.refresh_url, current?.status]);
  if (!current) return null;
  return <section className="console-card" aria-label="自动修复">
    <h2>自动修复 · {labels[current.status] || current.status}</h2>
    <p>{current.summary || '系统会在修复对话中读取轨迹、修代码、跑测试并提交 PR。'}</p>
    <p>已启动 {current.turn_count} 轮修复。{current.status === 'merged' ? '修复已合并到 main，部署和原业务结果分别保留。' : '确定性修复在检查和 review 通过后由同一 Agent 自动合并。'}</p>
    {current.merge_commit && <p>合并提交：<code>{current.merge_commit}</code></p>}
    {current.conversation_url && <Link className="secondary-button" to={current.conversation_url}>查看修复对话</Link>}{' '}
    {current.pr_url && <a className="secondary-button" href={current.pr_url} target="_blank" rel="noreferrer">查看 PR</a>}
    {refreshError && <p role="status">修复状态刷新失败：{refreshError}</p>}
  </section>;
}
