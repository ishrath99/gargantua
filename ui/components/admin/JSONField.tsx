'use client';

import { useEffect, useState } from 'react';

import { FieldError } from '@/components/ui/FieldError';
import { Textarea } from '@/components/ui/Textarea';
import { cn } from '@/lib/utils';

export interface JSONFieldProps {
  /** The current parsed value (object or array).  We keep state in
   * the parent so the form integration is straightforward. */
  value: unknown;
  onChange: (value: unknown) => void;
  /** Called whenever the validity flips so the parent can disable
   * the submit button.  ``undefined`` means "no error". */
  onValidityChange?: (error: string | undefined) => void;
  id?: string;
  name?: string;
  placeholder?: string;
  rows?: number;
  className?: string;
  disabled?: boolean;
  ariaLabel?: string;
}

/**
 * Plain ``<textarea>`` wrapper that round-trips a JSON value.
 *
 * Why not a fancy editor: this is admin-side; the values are short
 * (``tools_config``, ``agent_config``, ``env_vars`` etc.) and the
 * users editing them are technical.  A textarea with parse-time
 * validation gets us 95% of the value at 1% of the bundle cost.
 *
 * Implementation notes:
 *   * We keep the **string** form in local state so partial edits
 *     (e.g. mid-typing) don't blow up the parent's controlled value.
 *   * We re-sync the local string when the parent's ``value`` changes
 *     from elsewhere (load-from-server, reset, etc.) by comparing
 *     against the current ``parse`` result.
 */
export function JSONField({
  value,
  onChange,
  onValidityChange,
  id,
  name,
  placeholder,
  rows = 6,
  className,
  disabled,
  ariaLabel,
}: JSONFieldProps) {
  const [draft, setDraft] = useState(() => stringify(value));
  const [error, setError] = useState<string | undefined>(undefined);

  // Re-sync local string when the parent's value changes from
  // outside (form reset, server reload, etc.).
  useEffect(() => {
    const expected = stringify(value);
    if (expected !== draft) {
      // Don't trample on the user's mid-typed draft if it parses to the
      // same value the parent is now holding.
      try {
        const parsed = draft.trim() === '' ? null : JSON.parse(draft);
        if (deepEqual(parsed, value)) return;
      } catch {
        // fallthrough — replace draft below
      }
      setDraft(expected);
      setError(undefined);
      onValidityChange?.(undefined);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  function handleChange(next: string) {
    setDraft(next);
    if (next.trim() === '') {
      setError(undefined);
      onValidityChange?.(undefined);
      onChange(null);
      return;
    }
    try {
      const parsed = JSON.parse(next);
      setError(undefined);
      onValidityChange?.(undefined);
      onChange(parsed);
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Invalid JSON';
      setError(msg);
      onValidityChange?.(msg);
    }
  }

  return (
    <div className="flex flex-col">
      <Textarea
        id={id}
        name={name}
        value={draft}
        onChange={(e) => handleChange(e.target.value)}
        placeholder={placeholder}
        rows={rows}
        disabled={disabled}
        aria-invalid={error ? true : undefined}
        aria-label={ariaLabel}
        className={cn(error && 'border-red-500 focus-visible:ring-red-500', className)}
      />
      <FieldError message={error ?? null} />
    </div>
  );
}

function stringify(value: unknown): string {
  if (value === undefined || value === null) return '';
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return '';
  }
}

function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a !== typeof b) return false;
  if (a === null || b === null) return a === b;
  if (typeof a !== 'object') return false;
  try {
    return JSON.stringify(a) === JSON.stringify(b);
  } catch {
    return false;
  }
}
