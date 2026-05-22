'use client';

/**
 * Renders one tool-call inside an assistant turn.
 *
 * Visual states:
 *   * ``running``  — pulsing spinner; result section is collapsed.
 *   * ``completed`` — green checkmark; result section is collapsible.
 *   * ``error``    — red bang; error message is rendered prominently.
 *
 * The args / result blobs are JSON, often noisy.  We pretty-print
 * with a 2-space indent and show the first ~6 lines by default; the
 * user expands to see the whole thing.  We don't use ``<details>``
 * because we need controlled state to remember the expansion across
 * re-renders during streaming.
 */

import { AlertCircle, ChevronRight, Loader2, Wrench, Check } from 'lucide-react';
import { useState } from 'react';

import type { ChatToolCall } from '@/lib/chat/state';
import { cn } from '@/lib/utils';

interface Props {
  tool: ChatToolCall;
}

export function ToolCallCard({ tool }: Props) {
  const [expanded, setExpanded] = useState(false);

  const Icon =
    tool.status === 'completed'
      ? Check
      : tool.status === 'error'
        ? AlertCircle
        : Loader2;
  const iconClass =
    tool.status === 'completed'
      ? 'text-emerald-600 dark:text-emerald-400'
      : tool.status === 'error'
        ? 'text-red-600 dark:text-red-400'
        : 'animate-spin text-neutral-500';

  return (
    <div
      className={cn(
        'my-2 rounded-md border border-neutral-200 bg-neutral-50 text-xs',
        'dark:border-neutral-800 dark:bg-neutral-900/40',
      )}
    >
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
        aria-expanded={expanded}
        aria-label={`Tool call ${tool.name}`}
      >
        <ChevronRight
          className={cn(
            'h-3.5 w-3.5 shrink-0 text-neutral-400 transition-transform',
            expanded && 'rotate-90',
          )}
          aria-hidden
        />
        <Wrench className="h-3.5 w-3.5 shrink-0 text-neutral-500" aria-hidden />
        <span className="font-mono text-neutral-700 dark:text-neutral-300">
          {tool.name}
        </span>
        <Icon className={cn('h-3.5 w-3.5 shrink-0', iconClass)} aria-hidden />
      </button>

      {expanded ? (
        <div className="space-y-2 border-t border-neutral-200 px-3 py-2 dark:border-neutral-800">
          <ToolBlock label="Arguments" value={formatJSON(tool.args)} />
          {tool.error !== null && tool.error !== '' ? (
            <ToolBlock
              label="Error"
              value={tool.error}
              tone="error"
            />
          ) : tool.result !== null ? (
            <ToolBlock label="Result" value={tool.result} />
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function ToolBlock({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: 'error';
}) {
  return (
    <div className="space-y-1">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-neutral-500">
        {label}
      </div>
      <pre
        className={cn(
          'max-h-72 overflow-auto whitespace-pre-wrap break-words rounded',
          'bg-white p-2 font-mono text-[11px] leading-relaxed',
          'dark:bg-neutral-950',
          tone === 'error' &&
            'border border-red-300 bg-red-50 text-red-900 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200',
        )}
      >
        {value}
      </pre>
    </div>
  );
}

function formatJSON(value: unknown): string {
  if (value === null || value === undefined) return 'null';
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}
