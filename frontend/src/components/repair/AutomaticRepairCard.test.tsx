import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it } from 'vitest';
import { AutomaticRepairCard } from './AutomaticRepairCard';

afterEach(cleanup);
it('shows real conversation and PR links without claiming deployed', () => {
  render(<MemoryRouter><AutomaticRepairCard repair={{ status: 'pr_created', summary: 'PR 已创建', conversation_url: '/?task=12', pr_url: 'https://github.com/example/service/pull/7', turn_count: 2 }} /></MemoryRouter>);
  expect(screen.getByRole('link', { name: '查看修复对话' })).toHaveAttribute('href', '/?task=12');
  expect(screen.getByRole('link', { name: '查看 PR' })).toHaveAttribute('href', 'https://github.com/example/service/pull/7');
  expect(screen.getByText(/PR 创建不代表已合并/)).toBeInTheDocument();
});
it('shows queued and exhausted repair states', () => {
  const view = render(<MemoryRouter><AutomaticRepairCard repair={{ status: 'queued', summary: '', conversation_url: '', pr_url: '', turn_count: 0 }} /></MemoryRouter>);
  expect(screen.getByRole('heading', { name: '自动修复 · 等待自动修复' })).toBeInTheDocument();
  expect(screen.queryByRole('link')).not.toBeInTheDocument();
  view.rerender(<MemoryRouter><AutomaticRepairCard repair={{ status: 'failed', summary: '续接次数已耗尽', conversation_url: '/?task=12', pr_url: '', turn_count: 4 }} /></MemoryRouter>);
  expect(screen.getByText('续接次数已耗尽')).toBeInTheDocument();
  expect(screen.queryByRole('link', { name: '查看 PR' })).not.toBeInTheDocument();
});
it('does not invent a repair for historical errors', () => {
  const { container } = render(<AutomaticRepairCard repair={null} />);
  expect(container).toBeEmptyDOMElement();
});
