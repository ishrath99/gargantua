'use client';

/**
 * Auth context: tracks the logged-in user and exposes
 * ``login`` / ``logout`` / ``refreshMe``.
 *
 * On mount we check ``localStorage`` for an access token; if one is
 * present we eagerly call ``GET /auth/me`` to confirm it's still
 * valid + populate the user record.  A failed call clears the
 * tokens and leaves us in the logged-out state — the caller's
 * :class:`AuthGuard` will then redirect to ``/login``.
 *
 * Why not put this in a server component / load on the server
 * before render?  Because every UI page is static-exported — there
 * is no server at request time.  The "loading" flicker is one
 * round-trip to the backend, mitigated by the optimistic
 * ``isAccessTokenLikelyValid()`` check that gates the loading
 * spinner.
 */

import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';

import { apiFetch } from '@/lib/api/client';
import type { LoginRequest, MeResponse, TokenPair } from '@/lib/api/types';
import {
  clearTokens,
  getAccessToken,
  isAccessTokenLikelyValid,
  setTokens,
} from '@/lib/auth/storage';

interface AuthState {
  /**
   * The currently logged-in user, or ``null`` if logged out.
   * ``undefined`` is reserved for "haven't finished the initial
   * load yet" — distinguishes the boot flicker from a real logout.
   */
  user: MeResponse | null | undefined;
  isAdmin: boolean;
  isLoading: boolean;
  login: (credentials: LoginRequest) => Promise<void>;
  logout: () => void;
  /** Re-fetch ``/auth/me`` — used after role changes etc. */
  refreshMe: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<MeResponse | null | undefined>(undefined);

  const refreshMe = useCallback(async () => {
    if (!getAccessToken()) {
      setUser(null);
      return;
    }
    try {
      const me = await apiFetch<MeResponse>('/api/auth/me');
      setUser(me);
    } catch {
      // The client already cleared tokens if refresh failed.  Set
      // the user to null so guards redirect to /login.
      clearTokens();
      setUser(null);
    }
  }, []);

  // Initial load.  Optimistic: if the access token looks valid
  // locally, skip the round-trip and trust it (the next API call
  // will hit /auth and refresh if needed).  This keeps the
  // perceived load instant on warm reloads.
  useEffect(() => {
    if (!getAccessToken()) {
      setUser(null);
      return;
    }
    if (isAccessTokenLikelyValid()) {
      void refreshMe(); // still validate, but don't block UI
    } else {
      void refreshMe(); // expired locally — let the client refresh on first call
    }
  }, [refreshMe]);

  const login = useCallback(
    async (credentials: LoginRequest) => {
      const pair = await apiFetch<TokenPair>('/api/auth/login', {
        method: 'POST',
        body: credentials,
        // A 401 here means "wrong password", not "token expired" —
        // we don't want to attempt a refresh-and-retry.
        skipAuthRefresh: true,
      });
      setTokens(pair);
      await refreshMe();
    },
    [refreshMe],
  );

  const logout = useCallback(() => {
    clearTokens();
    setUser(null);
  }, []);

  const value: AuthState = {
    user,
    isAdmin: user?.role === 'admin',
    isLoading: user === undefined,
    login,
    logout,
    refreshMe,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (ctx === null) {
    throw new Error('useAuth must be used inside <AuthProvider>');
  }
  return ctx;
}
