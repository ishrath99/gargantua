/**
 * Server-Sent Events consumer for ``/v1/agents/{id}/runs?stream=true``.
 *
 * Why a custom reader and not :class:`EventSource`?
 *   * ``EventSource`` only does GET; our run endpoint is POST.
 *   * ``EventSource`` cannot send a ``Authorization: Bearer …``
 *     header in browsers — auth would have to ride a cookie.
 *   * We need ``AbortController`` support so the "stop generating"
 *     button can tear down the stream cleanly.
 *
 * The wire format mirrors what ``gargantua.api.runs._sse_event_stream``
 * emits: one ``data: <json>\n\n`` frame per agno event, terminated
 * by a literal ``data: [DONE]\n\n`` sentinel.  We parse, yield, and
 * stop on the sentinel.
 *
 * The reader is intentionally lenient — agno occasionally bundles
 * multi-byte UTF-8 surrogates across chunk boundaries, and the
 * stream framing splits *between* frames, not inside them.  We buffer
 * partial frames at the line level (``\n\n`` boundary) so a chunk
 * that lands mid-frame is harmless.
 */

import { ApiError } from '@/lib/api/client';
import { getAccessToken, getRefreshToken, setTokens } from '@/lib/auth/storage';
import type { ApiErrorBody, TokenPair } from '@/lib/api/types';

import type { AgnoRunEvent } from '@/lib/chat/events';

/**
 * One delivered SSE event, with the parsed agno payload plus the
 * raw JSON string for debugging / dev tooling.
 */
export interface SSEEvent {
  data: AgnoRunEvent;
  /** Raw JSON string that was on the ``data:`` line.  Useful when
   * something fails to render so the dev console can show what came
   * over the wire. */
  raw: string;
}

export interface StreamRunOptions {
  /** Absolute or app-relative URL of the run endpoint
   * (e.g. ``/v1/agents/{id}/runs``). */
  url: string;
  /** Request body — forwarded verbatim, must include ``stream: true``. */
  body: Record<string, unknown>;
  /** ``new AbortController().signal`` — cancels the in-flight request
   * AND the streaming read loop. */
  signal?: AbortSignal;
}

const apiBaseUrl: string =
  typeof process !== 'undefined' &&
  process.env &&
  typeof process.env.NEXT_PUBLIC_API_BASE_URL === 'string'
    ? process.env.NEXT_PUBLIC_API_BASE_URL
    : '';

function resolveUrl(path: string): string {
  if (path.startsWith('http://') || path.startsWith('https://')) return path;
  const left = apiBaseUrl.endsWith('/') ? apiBaseUrl.slice(0, -1) : apiBaseUrl;
  const right = path.startsWith('/') ? path : `/${path}`;
  return `${left}${right}`;
}

/**
 * Stream events from an agno run endpoint.
 *
 * Yields one :type:`SSEEvent` per parsed agno event.  Returns when
 * the stream sends ``data: [DONE]`` or the server closes the body.
 *
 * Throws :class:`ApiError` on non-2xx (after one refresh attempt on
 * 401), and re-throws ``AbortError`` if the caller aborts mid-stream.
 */
export async function* streamRun(
  opts: StreamRunOptions,
): AsyncGenerator<SSEEvent, void, void> {
  let response = await sendRequest(opts);

  // One-shot refresh-and-retry on 401, matching the JSON client's behavior.
  if (response.status === 401 && getRefreshToken() !== null) {
    const refreshed = await tryRefresh();
    if (refreshed) {
      response = await sendRequest(opts);
    }
  }

  if (!response.ok) {
    await throwForHttpStatus(response);
  }
  if (response.body === null) {
    // No body to stream — the server should never do this for an SSE
    // endpoint, but we surface a useful error rather than hanging.
    throw new ApiError(
      `SSE response with no body (status ${response.status})`,
      response.status,
      null,
    );
  }

  yield* parseSSE(response.body);
}

// ---------------------------------------------------------------------------
// Internals — request building
// ---------------------------------------------------------------------------

async function sendRequest(opts: StreamRunOptions): Promise<Response> {
  const headers: Record<string, string> = {
    Accept: 'text/event-stream',
    'Content-Type': 'application/json',
  };
  const token = getAccessToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  return fetch(resolveUrl(opts.url), {
    method: 'POST',
    headers,
    body: JSON.stringify(opts.body),
    signal: opts.signal,
  });
}

async function throwForHttpStatus(response: Response): Promise<never> {
  // Servers can answer 4xx/5xx on the run endpoint with a JSON body
  // (FastAPI's HTTPException) *or* with text (proxy errors).  Sniff
  // by content-type so the error surface is consistent with apiFetch.
  const ct = response.headers.get('content-type') ?? '';
  let body: ApiErrorBody | null = null;
  let detail = response.statusText;
  if (ct.includes('application/json')) {
    const parsed = (await response.json().catch(() => null)) as unknown;
    if (parsed && typeof parsed === 'object') {
      body = parsed as ApiErrorBody;
      const d = (parsed as { detail?: unknown }).detail;
      if (typeof d === 'string') detail = d;
    }
  } else {
    const text = await response.text().catch(() => '');
    if (text) detail = text;
  }
  throw new ApiError(`${response.status} ${detail}`, response.status, body);
}

/**
 * One-shot refresh attempt — duplicated from the JSON client to keep
 * the chat module from depending on its internals (the client's
 * ``tryRefresh`` isn't exported).  Both call sites would converge if
 * we ever ship a shared "session" helper.
 */
async function tryRefresh(): Promise<boolean> {
  const refresh = getRefreshToken();
  if (refresh === null) return false;
  try {
    const r = await fetch(resolveUrl('/auth/refresh'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refresh }),
    });
    if (!r.ok) return false;
    const pair = (await r.json()) as TokenPair;
    setTokens(pair);
    return true;
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Internals — SSE framing
// ---------------------------------------------------------------------------

const DONE_SENTINEL = '[DONE]';

/**
 * Parse a ``ReadableStream<Uint8Array>`` of SSE bytes into agno events.
 *
 * Supports:
 *   * Multi-byte UTF-8 split across chunks (``TextDecoder({stream:true})``).
 *   * Multiple frames per chunk.
 *   * One frame split across multiple chunks (buffered on ``\n\n``).
 *   * Multi-line ``data:`` fields (joined with ``\n`` per SSE spec).
 *   * Stray ``\r`` from servers that emit CRLF (we normalize).
 *
 * Stops cleanly on ``data: [DONE]`` and on stream EOF.  Malformed
 * JSON inside a frame is logged to ``console.warn`` and skipped — one
 * bad event shouldn't kill the whole stream.
 */
export async function* parseSSE(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<SSEEvent, void, void> {
  const reader = body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        // Flush whatever's left.  In practice this is empty because
        // the server's terminator already triggered a final ``\n\n``.
        if (buffer.trim().length > 0) {
          const event = parseFrame(buffer);
          if (event !== null && event !== 'DONE') yield event;
        }
        return;
      }
      buffer += decoder.decode(value, { stream: true }).replace(/\r/g, '');

      // Drain every complete frame currently in the buffer.
      let separator = buffer.indexOf('\n\n');
      while (separator !== -1) {
        const frame = buffer.slice(0, separator);
        buffer = buffer.slice(separator + 2);

        const event = parseFrame(frame);
        if (event === 'DONE') {
          return;
        }
        if (event !== null) {
          yield event;
        }
        separator = buffer.indexOf('\n\n');
      }
    }
  } finally {
    // Always release the lock so the underlying stream can be GC'd /
    // its leases released even if the consumer ``break``s early.
    reader.releaseLock();
  }
}

/**
 * Parse one SSE frame.  Returns:
 *   * ``'DONE'`` for the terminating ``data: [DONE]`` sentinel,
 *   * an :type:`SSEEvent` for a parsable ``data: <json>`` line,
 *   * ``null`` for empty / comment-only / malformed frames.
 */
function parseFrame(frame: string): SSEEvent | 'DONE' | null {
  const dataLines: string[] = [];
  for (const line of frame.split('\n')) {
    if (line.startsWith(':')) continue; // SSE comment
    if (!line.startsWith('data:')) continue;
    // Per the SSE spec, the value starts after ``data:`` and
    // optionally one space.  We strip that single space if present.
    const value = line.slice(5).replace(/^ /, '');
    dataLines.push(value);
  }
  if (dataLines.length === 0) return null;

  const raw = dataLines.join('\n');
  if (raw === DONE_SENTINEL) return 'DONE';

  try {
    const parsed = JSON.parse(raw) as AgnoRunEvent;
    return { data: parsed, raw };
  } catch (e) {
    // One bad event mustn't kill the stream.  Surface in dev tools
    // so the user can pinpoint what the backend sent.
    if (typeof console !== 'undefined') {
      // eslint-disable-next-line no-console
      console.warn('[sse] failed to parse event', { raw, error: e });
    }
    return null;
  }
}
