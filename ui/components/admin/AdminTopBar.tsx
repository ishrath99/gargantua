'use client';

import { LogOut } from 'lucide-react';

import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { useAuth } from '@/lib/auth/context';

/**
 * Slim top bar that surfaces who's signed in and gives them a single
 * obvious logout entry-point.  We don't put navigation here — the
 * sidebar owns that — so the top bar can stay context-free across
 * every admin route.
 */
export function AdminTopBar() {
  const { user, logout } = useAuth();
  if (!user) return null;
  return (
    <header
      className={
        'sticky top-0 z-20 flex h-12 items-center justify-end gap-3 border-b border-neutral-200 bg-white/85 px-4 backdrop-blur ' +
        'dark:border-neutral-800 dark:bg-neutral-950/85'
      }
    >
      <Badge variant={user.role === 'admin' ? 'default' : 'secondary'}>
        {user.role}
      </Badge>
      <span className="font-mono text-sm">{user.username}</span>
      <Button
        size="sm"
        variant="outline"
        onClick={logout}
        aria-label="Log out"
      >
        <LogOut className="h-3.5 w-3.5" />
        Log out
      </Button>
    </header>
  );
}
