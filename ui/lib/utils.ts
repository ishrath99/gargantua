import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/**
 * Combine class names while resolving Tailwind conflicts.
 *
 * ``cn('p-2', condition && 'p-4')`` => ``'p-4'`` if condition truthy,
 * not ``'p-2 p-4'``.  Standard shadcn/ui helper.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
