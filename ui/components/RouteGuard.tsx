'use client';

/**
 * Client-side route gates.
 *
 * Static-exported Next.js has no ``middleware.ts`` to run at request
 * time, so the auth check happens entirely in the browser.  Two
 * shapes:
 *
 *   * :func:`AuthGuard` — wraps protected pages.  Redirects to
 *     ``/login`` if there's no user, optionally checks an
 *     ``admin``-scope requirement.
 *   * :func:`GuestOnly` — wraps the login page.  Redirects to ``/``
 *     if the user is already logged in (no point logging in twice).
 *
 * Both render a tiny spinner while auth state is still loading
 * (the initial ``/auth/me`` round-trip), so we never flash protected
 * content to a logged-out user.
 */

import { useRouter } from 'next/navigation';
import { useEffect } from 'react';
import type { ReactNode } from 'react';

import { useAuth } from '@/lib/auth/context';

/** Small inline spinner shown during the auth boot flicker. */
function AuthBootFallback() {
  return (
    <div className="flex min-h-screen items-center justify-center text-sm text-neutral-500">
      <span aria-live="polite">Loading…</span>
    </div>
  );
}

interface AuthGuardProps {
  children: ReactNode;
  /** If set, the user must have this scope.  Today only ``'admin'``. */
  requireScope?: 'admin';
}

export function AuthGuard({ children, requireScope }: AuthGuardProps) {
  const { user, isLoading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (isLoading) return;
    if (user === null) {
      router.replace('/login/');
      return;
    }
    if (requireScope === 'admin' && user !== undefined && user.role !== 'admin') {
      // Authed but lacks the scope.  Bounce home — the user
      // landing page exists for everyone with SCOPE_USER.
      router.replace('/');
    }
  }, [isLoading, user, requireScope, router]);

  if (isLoading) return <AuthBootFallback />;
  if (user === null || user === undefined) return <AuthBootFallback />;
  if (requireScope === 'admin' && user.role !== 'admin') {
    return <AuthBootFallback />;
  }
  return <>{children}</>;
}

export function GuestOnly({ children }: { children: ReactNode }) {
  const { user, isLoading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (isLoading) return;
    if (user !== null && user !== undefined) {
      router.replace('/');
    }
  }, [isLoading, user, router]);

  if (isLoading) return <AuthBootFallback />;
  if (user !== null && user !== undefined) return <AuthBootFallback />;
  return <>{children}</>;
}
