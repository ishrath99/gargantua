'use client';

/**
 * Multi-line composer for the chat surface.
 *
 * Keybindings:
 *   * ``Enter``        — send.
 *   * ``Shift+Enter``  — newline.
 *   * ``Esc``          — clear the draft.
 *
 * The send button is also a stop button while a run is streaming —
 * the parent component swaps the action by passing ``onStop`` and
 * ``isStreaming``.  We don't show two buttons because that's twice
 * the cognitive load for an interaction with one obvious state.
 */

import { Send, Square } from 'lucide-react';
import { useEffect, useRef, useState, type KeyboardEvent } from 'react';

import { Button } from '@/components/ui/Button';
import { Textarea } from '@/components/ui/Textarea';

interface Props {
  isStreaming: boolean;
  onSend: (input: string) => void;
  onStop?: () => void;
  disabled?: boolean;
  placeholder?: string;
}

export function Composer({
  isStreaming,
  onSend,
  onStop,
  disabled,
  placeholder = 'Send a message…',
}: Props) {
  const [draft, setDraft] = useState('');
  const ref = useRef<HTMLTextAreaElement | null>(null);

  // Autosize: grow up to ~8 lines, then scroll inside.
  useEffect(() => {
    const el = ref.current;
    if (el === null) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [draft]);

  function submit() {
    const trimmed = draft.trim();
    if (trimmed.length === 0 || isStreaming || disabled) return;
    onSend(trimmed);
    setDraft('');
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
      return;
    }
    if (e.key === 'Escape') {
      setDraft('');
    }
  }

  return (
    <div className="border-t border-neutral-200 bg-white px-4 py-3 dark:border-neutral-800 dark:bg-neutral-950">
      <div className="flex items-end gap-2">
        <Textarea
          ref={ref}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          rows={1}
          disabled={disabled}
          aria-label="Message"
          className="min-h-[44px] resize-none font-sans"
        />
        {isStreaming && onStop !== undefined ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onStop}
            aria-label="Stop generating"
          >
            <Square className="h-4 w-4" aria-hidden />
            Stop
          </Button>
        ) : (
          <Button
            type="button"
            onClick={submit}
            size="sm"
            disabled={disabled || isStreaming || draft.trim().length === 0}
            aria-label="Send message"
          >
            <Send className="h-4 w-4" aria-hidden />
            Send
          </Button>
        )}
      </div>
      <p className="mt-1.5 text-[10px] text-neutral-400">
        <kbd className="rounded border border-neutral-300 bg-neutral-50 px-1 dark:border-neutral-700 dark:bg-neutral-900">
          Enter
        </kbd>{' '}
        to send,{' '}
        <kbd className="rounded border border-neutral-300 bg-neutral-50 px-1 dark:border-neutral-700 dark:bg-neutral-900">
          Shift+Enter
        </kbd>{' '}
        for newline.
      </p>
    </div>
  );
}
