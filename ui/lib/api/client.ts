/**
 * Typed fetch wrapper for the gargantua backend.
 *
 * Responsibilities:
 *   * Resolve the base URL (``NEXT_PUBLIC_API_BASE_URL`` or relative).
 *   * Attach ``Authorization: Bearer <access_token>`` when present.
 *   * Parse JSON bodies; throw :class:`ApiError` on non-2xx.
 *   * On 401, transparently try one refresh-and-retry; if the
 *     refresh fails, clear tokens and surface a sentinel error the
 *     auth layer can use to redirect to ``/login``.
 *
 * Deliberately *not* in this module:
 *   * SSE / ``ReadableStream`` consumption — that lives in
 *     ``lib/chat/sse.ts`` (PR 17) because the shape is different.
 *   * Caching / dedupe — TanStack Query owns that one layer up.
 */

import {
  clearTokens,
  getAccessToken,
  getRefreshToken,
  setTokens,
} from '@/lib/auth/storage';
import type { ApiErrorBody, TokenPair } from '@/lib/api/types';

// ---------------------------------------------------------------------------
// Public surface
// ---------------------------------------------------------------------------

/**
 * Thrown by every non-2xx response.  Carries enough information that
 * UI code can branch on status without re-parsing.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly body: ApiErrorBody | null;

  constructor(message: string, status: number, body: ApiErrorBody | null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }

  /**
   * True for errors that mean "you're not authenticated"; the auth
   * layer uses this to redirect to ``/login``.  Distinct from a 403
   * (you ARE authed but lack the scope).
   */
  get isAuthRequired(): boolean {
    return this.status === 401;
  }
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  /** Body — pre-serialized JSON-able object.  Leave undefined for GET. */
  body?: unknown;
  /** Additional headers (auth + content-type are injected). */
  headers?: Record<string, string>;
  /**
   * If true, do *not* attempt the 401-refresh-retry dance.  Used by
   * the refresh call itself to avoid recursion, and by the login
   * call where a 401 means "wrong password".
   */
  skipAuthRefresh?: boolean;
  /** Pass through to fetch — e.g. ``new AbortController().signal``. */
  signal?: AbortSignal;
}

/**
 * Send a request, return the parsed JSON body.
 *
 * For 204 / empty-body responses returns ``null``.  Throws
 * :class:`ApiError` on any non-2xx, with a one-shot refresh attempt
 * on 401 (unless ``skipAuthRefresh`` is set).
 */
export async function apiFetch<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  return executeWithRefresh<T>(path, options);
}

// ---------------------------------------------------------------------------
// Internals
// ---------------------------------------------------------------------------

const apiBaseUrl: string =
  typeof process !== 'undefined' &&
  process.env &&
  typeof process.env.NEXT_PUBLIC_API_BASE_URL === 'string'
    ? process.env.NEXT_PUBLIC_API_BASE_URL
    : '';

function resolveUrl(path: string): string {
  if (path.startsWith('http://') || path.startsWith('https://')) return path;
  // Ensure a single ``/`` between base and path.
  const left = apiBaseUrl.endsWith('/') ? apiBaseUrl.slice(0, -1) : apiBaseUrl;
  const right = path.startsWith('/') ? path : `/${path}`;
  return `${left}${right}`;
}

function buildHeaders(options: RequestOptions): HeadersInit {
  const headers: Record<string, string> = {
    Accept: 'application/json',
    ...(options.headers ?? {}),
  };
  if (options.body !== undefined && !('Content-Type' in headers)) {
    headers['Content-Type'] = 'application/json';
  }
  const token = getAccessToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

async function executeWithRefresh<T>(
  path: string,
  options: RequestOptions,
): Promise<T> {
  const first = await executeOnce(path, options);

  // 401 with a refresh token + caller didn't opt out → try one refresh.
  if (
    first.status === 401 &&
    !options.skipAuthRefresh &&
    getRefreshToken() !== null
  ) {
    const refreshed = await tryRefresh();
    if (refreshed) {
      return parseOrThrow<T>(await executeOnce(path, options));
    }
    // Refresh failed — clear local state.  The auth layer's
    // ``AuthProvider`` watches storage events / its own context and
    // will redirect to /login when ``getAccessToken()`` returns null.
    clearTokens();
  }

  return parseOrThrow<T>(first);
}

async function executeOnce(
  path: string,
  options: RequestOptions,
): Promise<Response> {
  const init: RequestInit = {
    method: options.method ?? (options.body === undefined ? 'GET' : 'POST'),
    headers: buildHeaders(options),
    signal: options.signal,
  };
  if (options.body !== undefined) {
    init.body =
      typeof options.body === 'string'
        ? options.body
        : JSON.stringify(options.body);
  }
  return fetch(resolveUrl(path), init);
}

async function parseOrThrow<T>(response: Response): Promise<T> {
  if (response.status === 204) {
    return null as T;
  }

  // Some endpoints (e.g. SSE) don't return JSON.  We don't route
  // those through this client, but a defensive content-type check
  // keeps the error path useful if a caller misuses us.
  const ct = response.headers.get('content-type') ?? '';
  let body: unknown = null;
  if (ct.includes('application/json')) {
    body = await response.json().catch(() => null);
  } else if (!response.ok) {
    body = { detail: await response.text().catch(() => '') };
  }

  if (!response.ok) {
    const detail =
      typeof body === 'object' &&
      body !== null &&
      'detail' in body &&
      typeof (body as { detail?: unknown }).detail === 'string'
        ? (body as { detail: string }).detail
        : response.statusText;
    throw new ApiError(
      `${response.status} ${detail || response.statusText}`,
      response.status,
      body as ApiErrorBody | null,
    );
  }
  return body as T;
}

/**
 * One-shot refresh attempt.  Returns ``true`` on success (tokens are
 * updated as a side effect), ``false`` on any failure.  Never throws.
 */
async function tryRefresh(): Promise<boolean> {
  const refresh = getRefreshToken();
  if (refresh === null) return false;
  try {
    const pair = await apiFetch<TokenPair>('/auth/refresh', {
      method: 'POST',
      body: { refresh_token: refresh },
      skipAuthRefresh: true, // avoid recursion
    });
    setTokens(pair);
    return true;
  } catch {
    return false;
  }
}
