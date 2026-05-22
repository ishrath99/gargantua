/**
 * Reducer that folds an agno run-event stream into a renderable thread.
 *
 * The chat UI keeps two layers of state:
 *
 *   * **Messages** — user / assistant turns, in order, that the
 *     :component:`MessageThread` renders.  ``assistant`` messages
 *     accumulate ``content`` deltas from ``RunContentEvent`` and own
 *     the tool-call list that fires within their turn.
 *
 *   * **Tool calls** — keyed by ``tool_call_id`` (or, when the model
 *     forgets to set one, by name + start time), threaded into the
 *     owning assistant message so the renderer can interleave
 *     prose and tool activity in chronological order.
 *
 * The reducer is intentionally pure: it takes the current state +
 * one event and returns a new state, so the chat page can hand it
 * to ``useReducer`` and tests can drive it with arrays of events.
 * Side effects (fetch, abort) live in the chat page.
 */

import type {
  AgnoRunEvent,
  RunCompletedEvent,
  RunContentEvent,
  RunErrorEvent,
  RunStartedEvent,
  ToolCallCompletedEvent,
  ToolCallErrorEvent,
  ToolCallStartedEvent,
  ToolExecution,
} from '@/lib/chat/events';

// ---------------------------------------------------------------------------
// Shape
// ---------------------------------------------------------------------------

export type ChatItemKind = 'message' | 'tool_call';

/** Tool-call state machine; the renderer branches on this. */
export type ToolCallStatus = 'running' | 'completed' | 'error';

export interface ChatToolCall {
  kind: 'tool_call';
  /** Stable client-side identifier; derived from ``tool_call_id`` when
   * available, otherwise a synthesized fallback. */
  id: string;
  /** Index into the parent assistant message's ``items`` list, used
   * to keep the renderer stable across re-renders. */
  status: ToolCallStatus;
  name: string;
  args: Record<string, unknown> | null;
  result: string | null;
  error: string | null;
  /** Wall-clock when the call was first seen — used as a tiebreaker
   * for ordering when several tools start in the same frame. */
  startedAt: number;
}

export interface ChatMessage {
  kind: 'message';
  id: string;
  role: 'user' | 'assistant';
  /** Plain-text content (rendered as markdown for assistant turns). */
  content: string;
  /** Whether the assistant turn is still streaming.  Always ``false``
   * on user turns. */
  streaming: boolean;
  /** Interleaved with the content during render — order is
   * append-only (oldest first). */
  toolCalls: ChatToolCall[];
  /** Set if the run terminated with an error.  The UI renders this
   * as a red banner below the message. */
  error?: string;
  /** Run-id from agno (set after the first ``RunStarted``). */
  runId?: string;
}

export interface ChatThreadState {
  messages: ChatMessage[];
  /** ``true`` when a request is in flight (regardless of whether any
   * delta has arrived yet).  The composer disables itself off this. */
  isStreaming: boolean;
  /** Last-seen ``session_id`` — the chat page persists this so
   * follow-up requests share history. */
  sessionId: string | null;
  /** Top-level error from the transport layer (HTTP non-2xx, aborted
   * connection, malformed framing).  Rendered above the composer. */
  transportError: string | null;
}

export const INITIAL_THREAD_STATE: ChatThreadState = {
  messages: [],
  isStreaming: false,
  sessionId: null,
  transportError: null,
};

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

export type ChatAction =
  | { type: 'send'; userMessageId: string; assistantMessageId: string; input: string }
  | { type: 'event'; event: AgnoRunEvent }
  | { type: 'stream_end' }
  | { type: 'transport_error'; message: string }
  | { type: 'reset' };

// ---------------------------------------------------------------------------
// Reducer
// ---------------------------------------------------------------------------

export function chatReducer(
  state: ChatThreadState,
  action: ChatAction,
): ChatThreadState {
  switch (action.type) {
    case 'send':
      return sendAction(state, action);
    case 'event':
      return applyEvent(state, action.event);
    case 'stream_end':
      return endStream(state);
    case 'transport_error':
      return {
        ...endStream(state),
        transportError: action.message,
      };
    case 'reset':
      return INITIAL_THREAD_STATE;
    default: {
      // Exhaustiveness — TS will error on unhandled action types.
      const _exhaustive: never = action;
      void _exhaustive;
      return state;
    }
  }
}

function sendAction(
  state: ChatThreadState,
  action: Extract<ChatAction, { type: 'send' }>,
): ChatThreadState {
  const user: ChatMessage = {
    kind: 'message',
    id: action.userMessageId,
    role: 'user',
    content: action.input,
    streaming: false,
    toolCalls: [],
  };
  const assistant: ChatMessage = {
    kind: 'message',
    id: action.assistantMessageId,
    role: 'assistant',
    content: '',
    streaming: true,
    toolCalls: [],
  };
  return {
    ...state,
    messages: [...state.messages, user, assistant],
    isStreaming: true,
    transportError: null,
  };
}

// ---------------------------------------------------------------------------
// Event application
// ---------------------------------------------------------------------------

function applyEvent(
  state: ChatThreadState,
  event: AgnoRunEvent,
): ChatThreadState {
  // We always thread events into the trailing assistant message —
  // that's the one currently streaming.  If for some reason there
  // isn't one (a bug, or events after stream_end), we drop them.
  const last = state.messages.at(-1);
  if (last === undefined || last.role !== 'assistant') return state;

  switch (event.event) {
    case 'RunStarted':
      return updateLastMessage(state, (m) => ({
        ...m,
        runId: (event as RunStartedEvent).run_id ?? m.runId,
      }), event.session_id ?? state.sessionId);

    case 'RunContent': {
      const delta = extractContentString((event as RunContentEvent).content);
      if (delta === '') return rememberSession(state, event);
      return updateLastMessage(state, (m) => ({
        ...m,
        content: m.content + delta,
      }), event.session_id ?? state.sessionId);
    }

    case 'RunCompleted': {
      // ``RunCompleted`` carries the final, canonical content.  Some
      // agno backends emit the whole answer here (cumulative) and
      // some only emit it via ``RunContent`` deltas — to handle both,
      // we *only* overwrite the buffer when the final content is
      // longer than what we accumulated.  That handles "deltas
      // missed during reconnects" without double-printing the
      // common case where deltas already covered it.
      const final = extractContentString((event as RunCompletedEvent).content);
      return updateLastMessage(state, (m) => ({
        ...m,
        content: final.length > m.content.length ? final : m.content,
        streaming: false,
      }), event.session_id ?? state.sessionId);
    }

    case 'RunError': {
      const err = event as RunErrorEvent;
      return updateLastMessage(state, (m) => ({
        ...m,
        streaming: false,
        error: err.content ?? err.error_type ?? 'Run failed.',
      }), event.session_id ?? state.sessionId);
    }

    case 'RunCancelled':
      return updateLastMessage(state, (m) => ({
        ...m,
        streaming: false,
        error: 'Run cancelled.',
      }), event.session_id ?? state.sessionId);

    case 'ToolCallStarted':
      return upsertToolCall(state, (event as ToolCallStartedEvent).tool, 'running');

    case 'ToolCallCompleted':
      return upsertToolCall(state, (event as ToolCallCompletedEvent).tool, 'completed');

    case 'ToolCallError': {
      const err = event as ToolCallErrorEvent;
      return upsertToolCall(state, err.tool, 'error', err.error ?? null);
    }

    default:
      // Unknown event — keep the session_id refresh, but otherwise
      // ignore (workflow events, reasoning, memory, …).
      return rememberSession(state, event);
  }
}

function rememberSession(
  state: ChatThreadState,
  event: AgnoRunEvent,
): ChatThreadState {
  if (event.session_id === undefined || event.session_id === null) return state;
  if (event.session_id === state.sessionId) return state;
  return { ...state, sessionId: event.session_id };
}

function updateLastMessage(
  state: ChatThreadState,
  mutate: (m: ChatMessage) => ChatMessage,
  sessionId: string | null,
): ChatThreadState {
  const idx = state.messages.length - 1;
  const last = state.messages[idx];
  if (last === undefined) return state;
  const next = [...state.messages];
  next[idx] = mutate(last);
  return {
    ...state,
    messages: next,
    sessionId: sessionId ?? state.sessionId,
  };
}

function upsertToolCall(
  state: ChatThreadState,
  tool: ToolExecution | null | undefined,
  status: ToolCallStatus,
  errorOverride: string | null = null,
): ChatThreadState {
  if (tool === null || tool === undefined) return state;
  const id = toolCallId(tool);

  return updateLastMessage(state, (m) => {
    const existingIdx = m.toolCalls.findIndex((c) => c.id === id);
    if (existingIdx === -1) {
      // First time we've seen this call — append.
      const next: ChatToolCall = {
        kind: 'tool_call',
        id,
        status,
        name: tool.tool_name ?? '(anonymous tool)',
        args: tool.tool_args ?? null,
        result: tool.result ?? null,
        error: errorOverride,
        startedAt: Date.now(),
      };
      return { ...m, toolCalls: [...m.toolCalls, next] };
    }
    // Subsequent update — merge new fields without clobbering an
    // earlier ``startedAt`` or non-null ``args``.
    const prev = m.toolCalls[existingIdx];
    const merged: ChatToolCall = {
      ...prev,
      status,
      name: tool.tool_name ?? prev.name,
      args: tool.tool_args ?? prev.args,
      result: tool.result ?? prev.result,
      error: errorOverride ?? prev.error,
    };
    const nextCalls = [...m.toolCalls];
    nextCalls[existingIdx] = merged;
    return { ...m, toolCalls: nextCalls };
  }, state.sessionId);
}

function endStream(state: ChatThreadState): ChatThreadState {
  const idx = state.messages.length - 1;
  if (idx < 0 || state.messages[idx].role !== 'assistant') {
    return { ...state, isStreaming: false };
  }
  // Mark the trailing assistant as no longer streaming if no
  // RunCompleted/Error already flipped it.
  const last = state.messages[idx];
  if (!last.streaming) return { ...state, isStreaming: false };
  const next = [...state.messages];
  next[idx] = { ...last, streaming: false };
  return { ...state, messages: next, isStreaming: false };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function toolCallId(tool: ToolExecution): string {
  if (tool.tool_call_id !== null && tool.tool_call_id !== undefined && tool.tool_call_id !== '') {
    return tool.tool_call_id;
  }
  // Fallback: name + Date.now() bucket.  We round to 50ms so a
  // ToolCallStarted + ToolCallCompleted pair without an id still
  // round-trips to the same key (the second event lands shortly
  // after the first).  Realistically the model always sets an id.
  const bucket = Math.floor(Date.now() / 50);
  return `${tool.tool_name ?? 'anonymous'}-${bucket}`;
}

/**
 * Best-effort extraction of a string delta from an agno event's
 * ``content`` field.  Non-string payloads (audio, image references,
 * citations) get coerced to ``""`` so we never crash, but they also
 * don't appear in the message body — those need dedicated renderers
 * which are out-of-scope for PR 17.
 */
function extractContentString(content: unknown): string {
  if (content === null || content === undefined) return '';
  if (typeof content === 'string') return content;
  // Some models return ``{ text: "..." }`` for structured outputs;
  // we pick up the obvious case.
  if (typeof content === 'object' && 'text' in (content as Record<string, unknown>)) {
    const t = (content as { text: unknown }).text;
    return typeof t === 'string' ? t : '';
  }
  return '';
}
