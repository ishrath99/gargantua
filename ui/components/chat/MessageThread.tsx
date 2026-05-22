'use client';

/**
 * Scrolling list of chat messages.
 *
 * Auto-scrolls to the bottom whenever the trailing message updates,
 * unless the user has scrolled up to read history — in which case we
 * stop nailing them to the bottom and respect their scroll position.
 * (Matches the behaviour of every modern chat surface.)
 */

import { useEffect, useRef } from 'react';

import { MessageBubble } from '@/components/chat/MessageBubble';
import type { ChatMessage } from '@/lib/chat/state';

interface Props {
  messages: ChatMessage[];
  emptyState?: React.ReactNode;
}

const STICK_TO_BOTTOM_PX = 80;

export function MessageThread({ messages, emptyState }: Props) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const stickToBottomRef = useRef(true);

  // Track whether the user is currently pinned to the bottom; if they
  // scroll up, stop auto-scrolling so we don't fight them.
  function handleScroll(e: React.UIEvent<HTMLDivElement>) {
    const el = e.currentTarget;
    const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickToBottomRef.current = dist <= STICK_TO_BOTTOM_PX;
  }

  // ``messages`` re-identity (deltas) drives the effect.
  useEffect(() => {
    const el = scrollRef.current;
    if (el === null) return;
    if (!stickToBottomRef.current) return;
    // ``scrollTop = scrollHeight`` is more robust than ``scrollIntoView``
    // for a virtualised list — works even when the inner content
    // isn't a direct child.
    el.scrollTop = el.scrollHeight;
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-neutral-500">
        {emptyState ?? 'Send a message to get started.'}
      </div>
    );
  }

  return (
    <div
      ref={scrollRef}
      onScroll={handleScroll}
      className="flex-1 space-y-4 overflow-y-auto px-4 py-6"
      role="log"
      aria-live="polite"
      aria-label="Chat transcript"
    >
      {messages.map((m) => (
        <MessageBubble key={m.id} message={m} />
      ))}
    </div>
  );
}
