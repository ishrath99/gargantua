'use client';

/**
 * Reusable chat surface: header + transcript + composer + SSE wiring.
 *
 * Both ``/chat/agent`` and ``/chat/team`` reduce to this component
 * with a different ``runUrl`` and ``title``.  Everything stateful
 * (message list, session id, abort controller) lives here so the
 * route pages stay declarative.
 *
 * State machine
 *   * idle      — composer enabled, send button visible.
 *   * streaming — composer disabled, stop button visible, abort wired.
 *
 * Session continuity
 *   The first ``send`` mints a UUID and sticks it on every subsequent
 *   request as ``session_id`` so agno threads turns into one
 *   conversation.  Reloading the page starts a fresh session — that's
 *   intentional for PR 17; multi-session history lands later.
 */

import Link from 'next/link';
import { ChevronLeft } from 'lucide-react';
import { useCallback, useEffect, useReducer, useRef, useState } from 'react';

import { Composer } from '@/components/chat/Composer';
import { MessageThread } from '@/components/chat/MessageThread';
import { streamRun } from '@/lib/chat/sse';
import {
  INITIAL_THREAD_STATE,
  chatReducer,
} from '@/lib/chat/state';
import { cn } from '@/lib/utils';

interface Props {
  /** Run endpoint to POST to (e.g. ``/v1/agents/{id}/runs``). */
  runUrl: string;
  /** Title shown in the header (agent / team name). */
  title: string;
  /** Optional descriptive subtitle (model id, role, etc.). */
  subtitle?: string;
}

export function ChatSurface({ runUrl, title, subtitle }: Props) {
  const [state, dispatch] = useReducer(chatReducer, INITIAL_THREAD_STATE);
  const abortRef = useRef<AbortController | null>(null);
  // Stable per-tab session id — minted lazily on first send so we don't
  // burn an id when the user navigates away without chatting.
  const [sessionId, setSessionId] = useState<string | null>(null);

  // Cancel any in-flight stream when the component unmounts (e.g. the
  // user navigates back to the picker mid-run).  The backend's SSE
  // generator releases its leases on disconnect via the ``finally``
  // block in :func:`_sse_event_stream`.
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const handleSend = useCallback(
    async (input: string) => {
      // Mint a session id on first send and reuse it for the lifetime
      // of this surface so follow-up turns share history.
      let sid = sessionId;
      if (sid === null) {
        sid = mintSessionId();
        setSessionId(sid);
      }

      // Cancel any straggler from a previous send (defensive — the
      // composer is disabled while streaming, but a race could exist).
      abortRef.current?.abort();
      const ac = new AbortController();
      abortRef.current = ac;

      const userId = `u-${Date.now()}`;
      const assistantId = `a-${Date.now()}`;
      dispatch({
        type: 'send',
        userMessageId: userId,
        assistantMessageId: assistantId,
        input,
      });

      try {
        for await (const evt of streamRun({
          url: runUrl,
          body: { input, stream: true, session_id: sid },
          signal: ac.signal,
        })) {
          dispatch({ type: 'event', event: evt.data });
        }
        dispatch({ type: 'stream_end' });
      } catch (err) {
        if (isAbortError(err)) {
          // The user pressed Stop — surface it as a cancel, not a crash.
          dispatch({
            type: 'event',
            event: { event: 'RunCancelled', reason: 'Stopped by user' },
          });
          dispatch({ type: 'stream_end' });
          return;
        }
        dispatch({
          type: 'transport_error',
          message:
            err instanceof Error
              ? err.message
              : 'Request failed — check the network tab.',
        });
      }
    },
    [runUrl, sessionId],
  );

  const handleStop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  return (
    <div className="flex h-screen w-full flex-col bg-white dark:bg-neutral-950">
      <Header title={title} subtitle={subtitle} />

      {state.transportError !== null ? (
        <div
          role="alert"
          className="mx-4 mt-3 flex items-start gap-2 rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-900 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200"
        >
          <span className="font-medium">Request failed:</span>
          <span>{state.transportError}</span>
        </div>
      ) : null}

      <MessageThread
        messages={state.messages}
        emptyState={
          <div className="max-w-md text-center">
            <p className="text-base font-medium text-neutral-700 dark:text-neutral-300">
              Ready when you are.
            </p>
            <p className="mt-1 text-xs text-neutral-500">
              {subtitle ?? title} can run tools from the attached MCP servers.
              Ask anything.
            </p>
          </div>
        }
      />

      <Composer
        isStreaming={state.isStreaming}
        onSend={handleSend}
        onStop={handleStop}
      />
    </div>
  );
}

function Header({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <header
      className={cn(
        'flex shrink-0 items-center justify-between gap-4 border-b border-neutral-200 px-4 py-3',
        'dark:border-neutral-800',
      )}
    >
      <div className="flex items-center gap-3 min-w-0">
        <Link
          href="/chat/"
          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-neutral-600 hover:bg-neutral-100 dark:text-neutral-300 dark:hover:bg-neutral-900"
          aria-label="Back to picker"
        >
          <ChevronLeft className="h-3.5 w-3.5" aria-hidden />
          Back
        </Link>
        <div className="min-w-0">
          <h1 className="truncate text-sm font-semibold text-neutral-900 dark:text-neutral-50">
            {title}
          </h1>
          {subtitle !== undefined ? (
            <p className="truncate text-xs text-neutral-500">{subtitle}</p>
          ) : null}
        </div>
      </div>
    </header>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function mintSessionId(): string {
  // ``crypto.randomUUID`` is available on every modern browser and on
  // Node 19+, which covers our build + dev surfaces.  Fall back to a
  // timestamp-based id if (somehow) it's missing — old Safari that
  // still ships in production.
  if (
    typeof crypto !== 'undefined' &&
    typeof crypto.randomUUID === 'function'
  ) {
    return crypto.randomUUID();
  }
  return `sess-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function isAbortError(err: unknown): boolean {
  if (err === null || typeof err !== 'object') return false;
  const name = (err as { name?: unknown }).name;
  return name === 'AbortError';
}
