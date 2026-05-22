'use client';

import Link from 'next/link';
import { ChevronRight } from 'lucide-react';
import { type ReactNode } from 'react';

import { cn } from '@/lib/utils';

export interface BreadcrumbItem {
  label: string;
  href?: string;
}

/**
 * Standard page header used on every admin route.  The breadcrumb
 * trail doubles as a no-router-back navigation aid for direct deep
 * links, and the ``actions`` slot is where pages stick their
 * Save / Archive / New buttons so layout stays consistent.
 */
export function PageHeader({
  title,
  description,
  breadcrumbs,
  actions,
  className,
}: {
  title: string;
  description?: ReactNode;
  breadcrumbs?: BreadcrumbItem[];
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn('mb-6 flex flex-col gap-4', className)}>
      {breadcrumbs && breadcrumbs.length > 0 ? (
        <nav
          aria-label="Breadcrumb"
          className="flex flex-wrap items-center gap-1 text-xs text-neutral-500 dark:text-neutral-400"
        >
          {breadcrumbs.map((crumb, idx) => {
            const isLast = idx === breadcrumbs.length - 1;
            return (
              <span key={`${crumb.label}-${idx}`} className="inline-flex items-center gap-1">
                {crumb.href && !isLast ? (
                  <Link
                    href={crumb.href}
                    className="hover:text-neutral-900 dark:hover:text-neutral-100"
                  >
                    {crumb.label}
                  </Link>
                ) : (
                  <span
                    className={
                      isLast
                        ? 'text-neutral-900 dark:text-neutral-100'
                        : undefined
                    }
                  >
                    {crumb.label}
                  </span>
                )}
                {isLast ? null : <ChevronRight className="h-3 w-3" />}
              </span>
            );
          })}
        </nav>
      ) : null}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
          {description ? (
            <p className="mt-1 text-sm text-neutral-500 dark:text-neutral-400">
              {description}
            </p>
          ) : null}
        </div>
        {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
      </div>
    </div>
  );
}
