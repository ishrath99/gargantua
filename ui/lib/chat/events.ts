/**
 * TypeScript shapes for Agno's run-event SSE stream.
 *
 * The backend forwards ``event.to_dict()`` verbatim from agno
 * ``v2.6+`` (see ``gargantua.api.runs._sse_event_stream``), so this
 * file mirrors a *subset* of agno's pydantic event union — only the
 * events the chat UI actually renders.  Unknown events fall through
 * the renderer as ``UnknownEvent`` and are ignored at the UI layer
 * (but still surfaced in dev tooling for debugging).
 *
 * Agno's event-name strings are stable across patch versions
 * (RunStarted, RunContent, ToolCallStarted, …), so this contract is
 * effectively the SSE wire format.
 */

/**
 * Subset of ``agno.run.agent.RunEvent`` / ``agno.run.team.TeamRunEvent``.
 * Both agent + team streams use the same discriminator strings, so a
 * single union type covers both.
 */
export type RunEventName =
  | 'RunStarted'
  | 'RunContent'
  | 'RunIntermediateContent'
  | 'RunContentCompleted'
  | 'RunCompleted'
  | 'RunError'
  | 'RunCancelled'
  | 'ToolCallStarted'
  | 'ToolCallCompleted'
  | 'ToolCallError';

/**
 * Shared envelope on every agno event.  Fields the UI cares about
 * are pulled out here; the long tail of workflow/hook metadata lives
 * in ``UnknownEvent``.
 */
export interface BaseRunEvent {
  event: RunEventName | string;
  /** Unix epoch seconds (agno serializes with ``int(time())``). */
  created_at?: number;
  /** Set on agent runs; absent on team-level events. */
  agent_id?: string;
  agent_name?: string;
  /** Set once the run starts; used to deduplicate retries client-side. */
  run_id?: string | null;
  session_id?: string | null;
}

// ---------------------------------------------------------------------------
// Run lifecycle
// ---------------------------------------------------------------------------

export interface RunStartedEvent extends BaseRunEvent {
  event: 'RunStarted';
  model?: string;
  model_provider?: string;
}

/**
 * The bread-and-butter event: one **delta** of assistant content.
 *
 * ``content`` is the marginal chunk to append to the assistant
 * message — *not* the cumulative buffer.  ``content_type`` is
 * normally ``"str"``; non-string payloads (audio, image, citations)
 * are not rendered in PR 17 and treated as no-ops.
 */
export interface RunContentEvent extends BaseRunEvent {
  event: 'RunContent';
  content?: unknown;
  content_type?: string;
  reasoning_content?: string;
}

export interface RunContentCompletedEvent extends BaseRunEvent {
  event: 'RunContentCompleted';
}

/**
 * Terminal event — carries the *final* assistant content (often a
 * re-emit of everything seen so far, but the source of truth).
 */
export interface RunCompletedEvent extends BaseRunEvent {
  event: 'RunCompleted';
  content?: unknown;
  content_type?: string;
  reasoning_content?: string;
  metadata?: Record<string, unknown>;
}

export interface RunErrorEvent extends BaseRunEvent {
  event: 'RunError';
  content?: string;
  error_type?: string;
  error_id?: string;
  additional_data?: Record<string, unknown>;
}

export interface RunCancelledEvent extends BaseRunEvent {
  event: 'RunCancelled';
  reason?: string;
}

// ---------------------------------------------------------------------------
// Tool calls
// ---------------------------------------------------------------------------

/**
 * One in-flight tool invocation, mirroring ``agno.models.response.ToolExecution``.
 *
 * Lookup key is ``tool_call_id`` (the model assigns it when emitting
 * the call); ``tool_name`` is the function/MCP-tool symbol; ``result``
 * is the stringified return value once the tool returns.
 */
export interface ToolExecution {
  tool_call_id?: string | null;
  tool_name?: string | null;
  tool_args?: Record<string, unknown> | null;
  tool_call_error?: boolean | null;
  result?: string | null;
  /** Set when the call wraps a child agent / team / workflow run. */
  child_run_id?: string | null;
}

export interface ToolCallStartedEvent extends BaseRunEvent {
  event: 'ToolCallStarted';
  tool?: ToolExecution | null;
}

export interface ToolCallCompletedEvent extends BaseRunEvent {
  event: 'ToolCallCompleted';
  tool?: ToolExecution | null;
  content?: unknown;
}

export interface ToolCallErrorEvent extends BaseRunEvent {
  event: 'ToolCallError';
  tool?: ToolExecution | null;
  error?: string;
}

// ---------------------------------------------------------------------------
// Union + fallthrough
// ---------------------------------------------------------------------------

/**
 * Anything we don't render explicitly — workflow events, reasoning
 * deltas, pre/post hooks, memory updates, etc.  Carried for
 * completeness so the SSE consumer never throws on unknown events.
 */
export interface UnknownEvent extends BaseRunEvent {
  // No additional fields — the union is exhaustive against
  // ``RunEventName``; anything else lands here.
  [key: string]: unknown;
}

export type AgnoRunEvent =
  | RunStartedEvent
  | RunContentEvent
  | RunContentCompletedEvent
  | RunCompletedEvent
  | RunErrorEvent
  | RunCancelledEvent
  | ToolCallStartedEvent
  | ToolCallCompletedEvent
  | ToolCallErrorEvent
  | UnknownEvent;

/**
 * Narrow ``AgnoRunEvent`` to the subset we actually render.  Useful
 * in the reducer + tests so we don't have to repeat the literal
 * names.
 */
export function isKnownEvent(
  e: AgnoRunEvent,
): e is Exclude<AgnoRunEvent, UnknownEvent> {
  switch (e.event) {
    case 'RunStarted':
    case 'RunContent':
    case 'RunContentCompleted':
    case 'RunCompleted':
    case 'RunError':
    case 'RunCancelled':
    case 'ToolCallStarted':
    case 'ToolCallCompleted':
    case 'ToolCallError':
      return true;
    default:
      return false;
  }
}
