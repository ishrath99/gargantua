/**
 * Behavioural tests for ``lib/api/client.ts`` — most of the value
 * sits in the 401-refresh-retry dance, which is hard to get right
 * without a regression test.
 *
 * We stub ``window.fetch`` per case and assert on what the client
 * sends, not on TanStack Query / React state.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, apiFetch } from '@/lib/api/client';
import {
  clearTokens,
  getAccessToken,
  getRefreshToken,
  setTokens,
} from '@/lib/auth/storage';

type FetchMock = ReturnType<typeof vi.fn>;

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

function emptyResponse(status: number): Response {
  return new Response(null, { status });
}

describe('apiFetch', () => {
  let fetchMock: FetchMock;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    clearTokens();
  });

  it('returns parsed JSON on 2xx', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { ok: true }));
    const out = await apiFetch<{ ok: boolean }>('/test');
    expect(out).toEqual({ ok: true });
  });

  it('attaches Authorization when an access token is present', async () => {
    setTokens({
      access_token: 'A',
      refresh_token: 'R',
      token_type: 'bearer',
      expires_in: 900,
    });
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {}));
    await apiFetch('/test');
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    const headers = init.headers as Record<string, string>;
    expect(headers.Authorization).toBe('Bearer A');
  });

  it('throws ApiError with status + body on 4xx', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(404, { detail: 'not found' }));
    await expect(apiFetch('/missing')).rejects.toMatchObject({
      name: 'ApiError',
      status: 404,
      body: { detail: 'not found' },
    });
  });

  it('returns null on 204', async () => {
    fetchMock.mockResolvedValueOnce(emptyResponse(204));
    const out = await apiFetch('/no-content');
    expect(out).toBeNull();
  });

  it('on 401: refreshes once and retries the original request', async () => {
    setTokens({
      access_token: 'expired',
      refresh_token: 'R',
      token_type: 'bearer',
      expires_in: 900,
    });
    // 1. original call -> 401
    // 2. /auth/refresh -> 200 with a new pair
    // 3. retried original call -> 200
    fetchMock
      .mockResolvedValueOnce(jsonResponse(401, { detail: 'expired' }))
      .mockResolvedValueOnce(
        jsonResponse(200, {
          access_token: 'new-A',
          refresh_token: 'new-R',
          token_type: 'bearer',
          expires_in: 900,
        }),
      )
      .mockResolvedValueOnce(jsonResponse(200, { ok: true }));

    const out = await apiFetch<{ ok: boolean }>('/protected');

    expect(out).toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    // The retry must carry the new access token.
    const retryInit = fetchMock.mock.calls[2][1] as RequestInit;
    expect((retryInit.headers as Record<string, string>).Authorization).toBe(
      'Bearer new-A',
    );
    // Tokens are updated.
    expect(getAccessToken()).toBe('new-A');
    expect(getRefreshToken()).toBe('new-R');
  });

  it('on 401 + failed refresh: clears tokens and surfaces the original error', async () => {
    setTokens({
      access_token: 'expired',
      refresh_token: 'bad',
      token_type: 'bearer',
      expires_in: 900,
    });
    fetchMock
      .mockResolvedValueOnce(jsonResponse(401, { detail: 'expired' }))
      .mockResolvedValueOnce(jsonResponse(401, { detail: 'bad refresh' }));

    await expect(apiFetch('/protected')).rejects.toBeInstanceOf(ApiError);
    // No retry of the original after refresh failed.
    expect(fetchMock).toHaveBeenCalledTimes(2);
    // Tokens are cleared.
    expect(getAccessToken()).toBeNull();
    expect(getRefreshToken()).toBeNull();
  });

  it('skipAuthRefresh disables the refresh path (used by /auth/login)', async () => {
    setTokens({
      access_token: 'A',
      refresh_token: 'R',
      token_type: 'bearer',
      expires_in: 900,
    });
    fetchMock.mockResolvedValueOnce(jsonResponse(401, { detail: 'wrong password' }));

    await expect(
      apiFetch('/api/auth/login', {
        method: 'POST',
        body: { username: 'x', password: 'y' },
        skipAuthRefresh: true,
      }),
    ).rejects.toMatchObject({ status: 401 });

    // Only the one call — no refresh attempted.
    expect(fetchMock).toHaveBeenCalledTimes(1);
    // Tokens NOT cleared (caller wasn't authed in the first place
    // for this flow, but if they were, we shouldn't sign them out
    // on a login-form mistype).
    expect(getAccessToken()).toBe('A');
  });

  it('serializes JSON bodies and defaults Content-Type', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {}));
    await apiFetch('/echo', { method: 'POST', body: { a: 1 } });
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.body).toBe(JSON.stringify({ a: 1 }));
    expect((init.headers as Record<string, string>)['Content-Type']).toBe(
      'application/json',
    );
  });

  it('respects an explicit string body without re-serializing', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {}));
    await apiFetch('/raw', { method: 'POST', body: 'already-stringified' });
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.body).toBe('already-stringified');
  });
});
