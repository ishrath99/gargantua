'use client';

import { cn } from '@/lib/utils';

/**
 * Inline form-error text.  Used under every input that can fail
 * validation.  Returns ``null`` when there's nothing to render so the
 * form layout doesn't shift; this keeps form spacing predictable.
 */
export function FieldError({
  message,
  className,
}: {
  message?: string | null;
  className?: string;
}) {
  if (!message) return null;
  return (
    <p
      role="alert"
      className={cn(
        'mt-1 text-xs text-red-600 dark:text-red-400',
        className,
      )}
    >
      {message}
    </p>
  );
}
