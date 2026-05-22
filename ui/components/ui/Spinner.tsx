'use client';

import { Loader2 } from 'lucide-react';

import { cn } from '@/lib/utils';

export function Spinner({ className }: { className?: string }) {
  return (
    <Loader2
      className={cn('h-4 w-4 animate-spin text-neutral-500', className)}
      aria-hidden
    />
  );
}

export function LoadingBlock({ label = 'Loading…' }: { label?: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-center justify-center gap-2 py-12 text-sm text-neutral-500"
    >
      <Spinner />
      <span>{label}</span>
    </div>
  );
}
