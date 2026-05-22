'use client';

/**
 * Root landing page.  Wrapped in :component:`AuthGuard` so anyone
 * who hits ``/`` while logged out gets bounced to ``/login``.
 *
 * Two entry points are surfaced based on the caller's role:
 *   * ``/chat`` is for everyone — the user-facing chat picker.
 *   * ``/admin`` is admin-only — gated by ``requireScope='admin'``.
 */

import Link from 'next/link';

import { useAuth } from '@/lib/auth/context';
import { AuthGuard } from '@/components/RouteGuard';

function HomeContent() {
  const { user, logout } = useAuth();
  const isAdmin = user?.role === 'admin';

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col gap-6 p-8">
      <header className="flex items-center justify-between border-b border-neutral-200 pb-4 dark:border-neutral-800">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">gargantua</h1>
          <p className="text-sm text-neutral-500">
            Welcome, <span className="font-mono">{user?.username ?? '…'}</span> —
            role{' '}
            <span className="rounded bg-neutral-100 px-1.5 py-0.5 font-mono text-xs dark:bg-neutral-800">
              {user?.role ?? '…'}
            </span>
          </p>
        </div>
        <button
          type="button"
          onClick={logout}
          className="rounded border border-neutral-300 px-3 py-1.5 text-sm font-medium hover:bg-neutral-50 dark:border-neutral-700 dark:hover:bg-neutral-900"
        >
          Log out
        </button>
      </header>

      <section className="space-y-3">
        <h2 className="text-lg font-medium">Chat</h2>
        <p className="text-sm text-neutral-600 dark:text-neutral-300">
          Talk to any agent or team you have access to — streaming
          responses with tool calls inline.
        </p>
        <Link
          href="/chat/"
          className="inline-flex w-fit items-center gap-2 rounded-md bg-neutral-900 px-4 py-2 text-sm font-medium text-white hover:bg-neutral-800 dark:bg-neutral-100 dark:text-neutral-900 dark:hover:bg-white"
        >
          Open chat →
        </Link>
      </section>

      {isAdmin ? (
        <section className="space-y-3">
          <h2 className="text-lg font-medium">Admin</h2>
          <p className="text-sm text-neutral-600 dark:text-neutral-300">
            Configure the catalog, MCP servers, agents, teams, and users.
          </p>
          <Link
            href="/admin/"
            className="inline-flex w-fit items-center gap-2 rounded-md border border-neutral-300 px-4 py-2 text-sm font-medium hover:bg-neutral-50 dark:border-neutral-700 dark:hover:bg-neutral-900"
          >
            Open admin console →
          </Link>
        </section>
      ) : null}
    </main>
  );
}

export default function HomePage() {
  return (
    <AuthGuard>
      <HomeContent />
    </AuthGuard>
  );
}
