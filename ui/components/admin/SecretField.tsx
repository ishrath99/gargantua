'use client';

import { Eye, EyeOff } from 'lucide-react';
import { forwardRef, useState, type InputHTMLAttributes } from 'react';

import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { SECRET_PLACEHOLDER } from '@/lib/api/types';
import { cn } from '@/lib/utils';

export interface SecretFieldProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  /**
   * When ``true`` the field renders an "unchanged" hint instead of the
   * real value.  Used on edit forms where the backend has masked the
   * existing secret with :data:`SECRET_PLACEHOLDER` and the user
   * doesn't necessarily want to overwrite it.
   *
   * The consumer must wire ``onClear`` to actually re-arm the input
   * for editing (we don't auto-mutate ``value``).
   */
  masked?: boolean;
  /** Called when the user clicks "Replace" on a masked value. */
  onClear?: () => void;
}

/**
 * Password-style input with a show/hide toggle and a "value is masked
 * server-side" affordance.
 *
 * The visibility toggle is opt-in: leaving the value invisible is the
 * default since these inputs typically carry tokens, keys, and
 * passwords that shouldn't be visible to anyone who walks up to a
 * laptop.  The toggle is keyboard-accessible and announces its state.
 */
export const SecretField = forwardRef<HTMLInputElement, SecretFieldProps>(
  function SecretField({ masked, onClear, className, value, ...rest }, ref) {
    const [reveal, setReveal] = useState(false);

    if (masked) {
      return (
        <div
          className={cn(
            'flex h-9 items-center justify-between gap-2 rounded-md border border-dashed border-neutral-300 bg-neutral-50 px-3 text-sm text-neutral-500',
            'dark:border-neutral-700 dark:bg-neutral-900/40 dark:text-neutral-400',
            className,
          )}
        >
          <span className="font-mono text-xs">{SECRET_PLACEHOLDER}</span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onClear}
            className="h-7 px-2 text-xs"
          >
            Replace
          </Button>
        </div>
      );
    }

    return (
      <div className="relative">
        <Input
          ref={ref}
          type={reveal ? 'text' : 'password'}
          autoComplete="new-password"
          spellCheck={false}
          value={value}
          className={cn('pr-9', className)}
          {...rest}
        />
        <button
          type="button"
          onClick={() => setReveal((v) => !v)}
          aria-label={reveal ? 'Hide secret' : 'Show secret'}
          aria-pressed={reveal}
          className={cn(
            'absolute right-1.5 top-1/2 inline-flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded-sm',
            'text-neutral-500 hover:bg-neutral-100 hover:text-neutral-900',
            'focus:outline-none focus:ring-2 focus:ring-neutral-900',
            'dark:hover:bg-neutral-800 dark:hover:text-neutral-100 dark:focus:ring-neutral-100',
          )}
        >
          {reveal ? (
            <EyeOff className="h-3.5 w-3.5" />
          ) : (
            <Eye className="h-3.5 w-3.5" />
          )}
        </button>
      </div>
    );
  },
);
