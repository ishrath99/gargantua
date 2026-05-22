import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { MessageBubble } from '@/components/chat/MessageBubble';
import type { ChatMessage } from '@/lib/chat/state';

function userMessage(content: string): ChatMessage {
  return {
    kind: 'message',
    id: 'u1',
    role: 'user',
    content,
    streaming: false,
    toolCalls: [],
  };
}

function assistantMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    kind: 'message',
    id: 'a1',
    role: 'assistant',
    content: '',
    streaming: false,
    toolCalls: [],
    ...overrides,
  };
}

describe('MessageBubble', () => {
  it('renders user content as plain text', () => {
    render(<MessageBubble message={userMessage('hello *world*')} />);
    // User turns must NOT be parsed as markdown — the asterisks
    // should be visible literally.
    expect(screen.getByText('hello *world*')).toBeInTheDocument();
  });

  it('renders assistant content as markdown (gfm)', () => {
    render(
      <MessageBubble
        message={assistantMessage({ content: '**bold** and `code`' })}
      />,
    );
    // ``react-markdown`` wraps these in <strong> / <code>.
    const strong = screen.getByText('bold');
    expect(strong.tagName.toLowerCase()).toBe('strong');
    expect(screen.getByText('code').tagName.toLowerCase()).toBe('code');
  });

  it('shows a typing indicator while the assistant streams an empty turn', () => {
    render(
      <MessageBubble
        message={assistantMessage({ content: '', streaming: true })}
      />,
    );
    expect(
      screen.getByLabelText(/agent is thinking/i),
    ).toBeInTheDocument();
  });

  it('renders the error banner when message.error is set', () => {
    render(
      <MessageBubble
        message={assistantMessage({ error: 'rate limited', streaming: false })}
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent(/rate limited/);
  });

  it('threads tool calls below the assistant content', () => {
    render(
      <MessageBubble
        message={assistantMessage({
          content: 'searching the repo…',
          toolCalls: [
            {
              kind: 'tool_call',
              id: 'tc-1',
              name: 'github_search',
              status: 'completed',
              args: null,
              result: '[]',
              error: null,
              startedAt: 0,
            },
          ],
        })}
      />,
    );
    expect(screen.getByText('searching the repo…')).toBeInTheDocument();
    expect(screen.getByText('github_search')).toBeInTheDocument();
  });
});
