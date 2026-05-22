import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { ToolCallCard } from '@/components/chat/ToolCallCard';
import type { ChatToolCall } from '@/lib/chat/state';

function makeTool(overrides: Partial<ChatToolCall> = {}): ChatToolCall {
  return {
    kind: 'tool_call',
    id: 'tc-1',
    name: 'github_search',
    status: 'completed',
    args: { q: 'is:open' },
    result: '[1, 2]',
    error: null,
    startedAt: 0,
    ...overrides,
  };
}

describe('ToolCallCard', () => {
  it('renders the tool name and is collapsed by default', () => {
    render(<ToolCallCard tool={makeTool()} />);
    expect(screen.getByText('github_search')).toBeInTheDocument();
    // Args / Result labels only appear when expanded.
    expect(screen.queryByText('Arguments')).not.toBeInTheDocument();
  });

  it('expands the args + result on click', async () => {
    const user = userEvent.setup();
    render(<ToolCallCard tool={makeTool()} />);
    await user.click(screen.getByRole('button', { name: /tool call/i }));
    expect(screen.getByText('Arguments')).toBeInTheDocument();
    expect(screen.getByText('Result')).toBeInTheDocument();
    expect(screen.getByText(/is:open/)).toBeInTheDocument();
  });

  it('shows the error block instead of Result when status is error', async () => {
    const user = userEvent.setup();
    render(
      <ToolCallCard
        tool={makeTool({ status: 'error', error: 'permission denied', result: null })}
      />,
    );
    await user.click(screen.getByRole('button', { name: /tool call/i }));
    expect(screen.getByText('Error')).toBeInTheDocument();
    expect(screen.getByText('permission denied')).toBeInTheDocument();
    expect(screen.queryByText('Result')).not.toBeInTheDocument();
  });

  it('shows the typing spinner while status is running', () => {
    const { container } = render(
      <ToolCallCard tool={makeTool({ status: 'running' })} />,
    );
    // The Loader2 icon has ``animate-spin`` — we sniff for that class
    // since the icon itself is rendered via lucide.
    expect(container.querySelector('.animate-spin')).not.toBeNull();
  });
});
