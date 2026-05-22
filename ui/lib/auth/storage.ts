/**
 * Token storage shim.
 *
 * Tokens live in ``localStorage``.  This is the simplest possible
 * choice and matches the backend's current JSON token response;
 * the security trade-off (XSS could read tokens) is acknowledged in
 * the plan and is mitigated by:
 *   * never rendering user-supplied HTML (no ``dangerouslySetInnerHTML``)
 *   * a strict CSP added in PR 18 via FastAPI middleware
 *
 * If we later need httpOnly cookies, the only file that changes is
 * this one — the rest of the codebase talks to ``getAccessToken`` /
 * ``setTokens`` / ``clearTokens``.
 */

import type { TokenPair } from '@/lib/api/types';

const ACCESS_KEY = 'gargantua.access_token';
const REFRESH_KEY = 'gargantua.refresh_token';
const EXPIRY_KEY = 'gargantua.access_expires_at_ms';

/**
 * Test whether we're running in the browser.  Pages rendered at
 * build time (static export prerender) execute on the Node side and
 * have no ``localStorage`` — every accessor here has to no-op then.
 */
const hasStorage = (): boolean =>
  typeof window !== 'undefined' && typeof window.localStorage !== 'undefined';

export function getAccessToken(): string | null {
  if (!hasStorage()) return null;
  return window.localStorage.getItem(ACCESS_KEY);
}

export function getRefreshToken(): string | null {
  if (!hasStorage()) return null;
  return window.localStorage.getItem(REFRESH_KEY);
}

/**
 * Approximate expiry timestamp (ms since epoch) for the *access*
 * token.  We record ``now + expires_in*1000`` rather than parsing
 * the JWT — saves us a base64 / JSON.parse hop and a dependency.
 *
 * The number is *advisory*: the source of truth is what the backend
 * returns when verifying.  We only consult it to decide whether to
 * proactively refresh before firing a doomed request.
 */
export function getAccessExpiryMs(): number | null {
  if (!hasStorage()) return null;
  const raw = window.localStorage.getItem(EXPIRY_KEY);
  if (raw === null) return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

export function setTokens(pair: TokenPair): void {
  if (!hasStorage()) return;
  window.localStorage.setItem(ACCESS_KEY, pair.access_token);
  window.localStorage.setItem(REFRESH_KEY, pair.refresh_token);
  window.localStorage.setItem(
    EXPIRY_KEY,
    String(Date.now() + pair.expires_in * 1000),
  );
}

export function clearTokens(): void {
  if (!hasStorage()) return;
  window.localStorage.removeItem(ACCESS_KEY);
  window.localStorage.removeItem(REFRESH_KEY);
  window.localStorage.removeItem(EXPIRY_KEY);
}

/** True iff we have an access token *and* it hasn't expired locally. */
export function isAccessTokenLikelyValid(): boolean {
  const token = getAccessToken();
  if (!token) return false;
  const expiresAt = getAccessExpiryMs();
  if (expiresAt === null) return true; // unknown expiry — let the server decide
  // 5s buffer so we don't fire a request that lands after expiry.
  return Date.now() < expiresAt - 5000;
}
