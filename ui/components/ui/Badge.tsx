'use client';

import { type HTMLAttributes } from 'react';

import { cn } from '@/lib/utils';

export type BadgeVariant =
  | 'default'
  | 'secondary'
  | 'outline'
  | 'success'
  | 'warning'
  | 'destructive';

const variantClasses: Record<BadgeVariant, string> = {
  default:
    'bg-neutral-900 text-neutral-50 dark:bg-neutral-100 dark:text-neutral-900',
  secondary:
    'bg-neutral-100 text-neutral-900 dark:bg-neutral-800 dark:text-neutral-100',
  outline:
    'border border-neutral-200 text-neutral-900 dark:border-neutral-800 dark:text-neutral-100',
  success:
    'bg-emerald-100 text-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300',
  warning:
    'bg-amber-100 text-amber-900 dark:bg-amber-950/40 dark:text-amber-300',
  destructive:
    'bg-red-100 text-red-900 dark:bg-red-950/40 dark:text-red-300',
};

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
}

export function Badge({ className, variant = 'default', ...rest }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium',
        variantClasses[variant],
        className,
      )}
      {...rest}
    />
  );
}
