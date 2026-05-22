'use client';

import { AlertTriangle } from 'lucide-react';

import { ApiError } from '@/lib/api/client';
import { cn } from '@/lib/utils';

/**
 * Render an error in a way that's useful for an admin: surface the
 * status code, the backend's ``detail`` field, and the action verb if
 * we know it.  Network/CORS errors land as plain message strings.
 */
export function ErrorBlock({
  error,
  title = 'Something went wrong.',
  className,
}: {
  error: unknown;
  title?: string;
  className?: string;
}) {
  const detail = explain(error);
  return (
    <div
      role="alert"
      className={cn(
        'rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900',
        'dark:border-red-900/50 dark:bg-red-950/30 dark:text-red-200',
        className,
      )}
    >
      <div className="flex items-center gap-2 font-medium">
        <AlertTriangle className="h-4 w-4" aria-hidden />
        {title}
      </div>
      <p className="mt-1 break-words font-mono text-xs">{detail}</p>
    </div>
  );
}

export function explain(error: unknown): string {
  if (error instanceof ApiError) {
    const status = error.status ? `${error.status} ` : '';
    if (error.body && typeof error.body === 'object') {
      const body = error.body as { detail?: unknown };
      if (typeof body.detail === 'string') return `${status}${body.detail}`;
      if (body.detail) return `${status}${JSON.stringify(body.detail)}`;
    }
    return `${status}${error.message}`;
  }
  if (error instanceof Error) return error.message;
  return String(error);
}
