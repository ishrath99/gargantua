'use client';

import { forwardRef, type SelectHTMLAttributes } from 'react';

import { cn } from '@/lib/utils';

/**
 * Native <select> wrapper.
 *
 * We deliberately avoid Radix Select here — the native element has
 * great mobile UX, full a11y by default, and forms-as-data interop
 * (works with react-hook-form's ``register()`` out of the box).
 * Reach for Radix only when we need rich option markup (icons,
 * descriptions, etc.); none of the admin pages do.
 */
export const Select = forwardRef<
  HTMLSelectElement,
  SelectHTMLAttributes<HTMLSelectElement>
>(function Select({ className, children, ...rest }, ref) {
  return (
    <select
      ref={ref}
      className={cn(
        'flex h-9 w-full rounded-md border border-neutral-200 bg-white px-3 py-1 text-sm shadow-sm',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-neutral-900 focus-visible:ring-offset-1',
        'disabled:cursor-not-allowed disabled:opacity-50',
        'dark:border-neutral-800 dark:bg-neutral-950 dark:text-neutral-50 dark:focus-visible:ring-neutral-100',
        className,
      )}
      {...rest}
    >
      {children}
    </select>
  );
});
