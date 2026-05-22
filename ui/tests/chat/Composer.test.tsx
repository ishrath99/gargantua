import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { Composer } from '@/components/chat/Composer';

describe('Composer', () => {
  it('submits on Send click and clears the draft', async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<Composer isStreaming={false} onSend={onSend} />);

    const ta = screen.getByLabelText('Message');
    await user.type(ta, 'hello');
    await user.click(screen.getByRole('button', { name: /send message/i }));

    expect(onSend).toHaveBeenCalledWith('hello');
    expect(ta).toHaveValue('');
  });

  it('submits on Enter, but Shift+Enter inserts a newline', async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<Composer isStreaming={false} onSend={onSend} />);

    const ta = screen.getByLabelText('Message');
    await user.type(ta, 'line1');
    await user.keyboard('{Shift>}{Enter}{/Shift}');
    await user.type(ta, 'line2');
    expect(ta).toHaveValue('line1\nline2');

    await user.keyboard('{Enter}');
    expect(onSend).toHaveBeenCalledWith('line1\nline2');
  });

  it('Escape clears the draft without submitting', async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<Composer isStreaming={false} onSend={onSend} />);
    const ta = screen.getByLabelText('Message');
    await user.type(ta, 'discard');
    await user.keyboard('{Escape}');
    expect(ta).toHaveValue('');
    expect(onSend).not.toHaveBeenCalled();
  });

  it('disables the send button on an empty / whitespace draft', () => {
    render(<Composer isStreaming={false} onSend={() => undefined} />);
    expect(
      screen.getByRole('button', { name: /send message/i }),
    ).toBeDisabled();
  });

  it('shows the Stop button while streaming and calls onStop', async () => {
    const user = userEvent.setup();
    const onStop = vi.fn();
    render(
      <Composer isStreaming onSend={() => undefined} onStop={onStop} />,
    );
    expect(
      screen.queryByRole('button', { name: /send message/i }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /stop generating/i }));
    expect(onStop).toHaveBeenCalledOnce();
  });

  it('does not submit a whitespace-only message', async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<Composer isStreaming={false} onSend={onSend} />);
    const ta = screen.getByLabelText('Message');
    await user.type(ta, '   ');
    await user.keyboard('{Enter}');
    expect(onSend).not.toHaveBeenCalled();
  });
});
