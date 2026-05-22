/**
 * Smoke test for ``lib/auth/storage.ts``.  The functions are
 * mechanical, but the SSR-safety guard (no-op when ``window`` is
 * absent) has bitten Next.js apps before, so we lock in the
 * happy-path round-trip here.
 */

import { describe, expect, it } from 'vitest';

import {
  clearTokens,
  getAccessExpiryMs,
  getAccessToken,
  getRefreshToken,
  isAccessTokenLikelyValid,
  setTokens,
} from '@/lib/auth/storage';

describe('token storage', () => {
  it('stores and reads a token pair', () => {
    setTokens({
      access_token: 'A',
      refresh_token: 'R',
      token_type: 'bearer',
      expires_in: 900,
    });
    expect(getAccessToken()).toBe('A');
    expect(getRefreshToken()).toBe('R');
    const expiry = getAccessExpiryMs();
    expect(expiry).not.toBeNull();
    // 900s in the future, with a few seconds of test-runtime slack.
    expect(expiry! - Date.now()).toBeGreaterThan(890_000);
    expect(expiry! - Date.now()).toBeLessThanOrEqual(900_000);
  });

  it('clearTokens wipes all three keys', () => {
    setTokens({
      access_token: 'A',
      refresh_token: 'R',
      token_type: 'bearer',
      expires_in: 900,
    });
    clearTokens();
    expect(getAccessToken()).toBeNull();
    expect(getRefreshToken()).toBeNull();
    expect(getAccessExpiryMs()).toBeNull();
  });

  it('isAccessTokenLikelyValid: false when no token', () => {
    expect(isAccessTokenLikelyValid()).toBe(false);
  });

  it('isAccessTokenLikelyValid: true within the validity window', () => {
    setTokens({
      access_token: 'A',
      refresh_token: 'R',
      token_type: 'bearer',
      expires_in: 900,
    });
    expect(isAccessTokenLikelyValid()).toBe(true);
  });

  it('isAccessTokenLikelyValid: false when locally expired', () => {
    setTokens({
      access_token: 'A',
      refresh_token: 'R',
      token_type: 'bearer',
      expires_in: -10, // already expired
    });
    expect(isAccessTokenLikelyValid()).toBe(false);
  });
});
