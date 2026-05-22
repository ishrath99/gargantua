'use client';

/**
 * One chat turn — either a user prompt or an assistant response.
 *
 * Assistant content is rendered as Markdown (gfm) because gargantua
 * frequently uses bullet lists, tables, and fenced code blocks in
 * its replies.  Tool calls are interleaved at the end of the bubble;
 * the agno event stream currently doesn't expose where in the prose
 * they fired, so a trailing block is the honest representation
 * (and matches what ChatGPT, Claude, etc. do).
 *
 * Errors are rendered inline below the message body.
 */

import { AlertTriangle, Bot, User } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { ToolCallCard } from '@/components/chat/ToolCallCard';
import type { ChatMessage } from '@/lib/chat/state';
import { cn } from '@/lib/utils';

interface Props {
  message: ChatMessage;
}

export function MessageBubble({ message }: Props) {
  const isUser = message.role === 'user';

  return (
    <div
      className={cn(
        'flex w-full gap-3',
        isUser ? 'justify-end' : 'justify-start',
      )}
      data-role={message.role}
    >
      {!isUser ? (
        <div
          aria-hidden
          className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300"
        >
          <Bot className="h-4 w-4" />
        </div>
      ) : null}

      <div
        className={cn(
          'min-w-0 max-w-[90%] rounded-lg px-4 py-2.5 text-sm leading-relaxed',
          isUser
            ? 'bg-neutral-900 text-neutral-50 dark:bg-neutral-100 dark:text-neutral-900'
            : 'bg-neutral-50 text-neutral-900 dark:bg-neutral-900 dark:text-neutral-100',
        )}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap break-words">{message.content}</p>
        ) : (
          <AssistantBody message={message} />
        )}

        {message.error !== undefined ? (
          <div
            role="alert"
            className="mt-2 flex items-start gap-2 rounded-md border border-red-300 bg-red-50 p-2 text-xs text-red-900 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200"
          >
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
            <span>{message.error}</span>
          </div>
        ) : null}
      </div>

      {isUser ? (
        <div
          aria-hidden
          className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300"
        >
          <User className="h-4 w-4" />
        </div>
      ) : null}
    </div>
  );
}

function AssistantBody({ message }: { message: ChatMessage }) {
  const hasContent = message.content.length > 0;
  const hasTools = message.toolCalls.length > 0;
  const showTypingDot = message.streaming && !hasContent && !hasTools;

  return (
    <div>
      {hasContent ? (
        <div className="prose prose-sm max-w-none dark:prose-invert">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {message.content}
          </ReactMarkdown>
        </div>
      ) : null}

      {hasTools ? (
        <div className="mt-2 space-y-1">
          {message.toolCalls.map((t) => (
            <ToolCallCard key={t.id} tool={t} />
          ))}
        </div>
      ) : null}

      {showTypingDot ? (
        <span
          aria-label="Agent is thinking"
          className="inline-flex items-center gap-1 text-neutral-500"
        >
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-neutral-400" />
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-neutral-400 [animation-delay:120ms]" />
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-neutral-400 [animation-delay:240ms]" />
        </span>
      ) : null}
    </div>
  );
}
