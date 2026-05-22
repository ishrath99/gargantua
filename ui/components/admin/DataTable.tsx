'use client';

import { ChevronLeft, ChevronRight, Search } from 'lucide-react';
import {
  useEffect,
  useState,
  type ReactNode,
} from 'react';

import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { LoadingBlock } from '@/components/ui/Spinner';
import { ErrorBlock } from '@/components/admin/ErrorBlock';
import { cn } from '@/lib/utils';

export interface ColumnDef<T> {
  /** Unique key for the column.  Used as React key + as the dataset selector. */
  key: string;
  header: ReactNode;
  /** Cell renderer; receives the row and returns the displayed node. */
  cell: (row: T) => ReactNode;
  /** Tailwind classes appended to the <td>.  Use for column alignment / sizing. */
  className?: string;
  /** Same for the <th>; defaults to ``className`` when unset. */
  headerClassName?: string;
}

export interface DataTableProps<T> {
  rows: T[] | undefined;
  columns: ColumnDef<T>[];
  rowKey: (row: T) => string;
  isLoading?: boolean;
  error?: unknown;
  /** Stable empty-state message; shown when ``rows`` is empty and not loading. */
  emptyMessage?: ReactNode;

  // --- Pagination (controlled) ---
  page: number;
  pageSize: number;
  total: number | undefined;
  onPageChange: (page: number) => void;

  // --- Search (controlled; debounced internally) ---
  search?: string;
  onSearchChange?: (value: string) => void;
  searchPlaceholder?: string;

  /** Optional click handler for an entire row.  Cursor becomes pointer when set. */
  onRowClick?: (row: T) => void;

  /** Slot to the right of the search box (toggle filters etc.). */
  toolbar?: ReactNode;
}

const DEBOUNCE_MS = 250;

/**
 * Server-paginated, optionally-searchable table.  All paging /
 * sorting / filtering state is **controlled** — the page is in charge
 * of the query state and just passes the current values in.  This
 * keeps the table dumb and lets the page decide how to encode state
 * in the URL (TanStack Query keys, etc).
 *
 * The search input is debounced internally so consumers can connect
 * directly to a TanStack Query key without re-firing per keystroke.
 */
export function DataTable<T>({
  rows,
  columns,
  rowKey,
  isLoading,
  error,
  emptyMessage = 'No results.',
  page,
  pageSize,
  total,
  onPageChange,
  search,
  onSearchChange,
  searchPlaceholder = 'Search…',
  onRowClick,
  toolbar,
}: DataTableProps<T>) {
  const [searchInput, setSearchInput] = useState(search ?? '');

  // Keep the local input in sync when the parent resets it
  // (e.g. when the user navigates).
  useEffect(() => {
    setSearchInput(search ?? '');
  }, [search]);

  // Debounce the search emit.
  useEffect(() => {
    if (!onSearchChange) return;
    if (searchInput === (search ?? '')) return;
    const handle = setTimeout(() => {
      onSearchChange(searchInput);
    }, DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [searchInput, onSearchChange, search]);

  const totalPages =
    total === undefined ? undefined : Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="flex flex-col gap-3">
      {(onSearchChange || toolbar) && (
        <div className="flex flex-wrap items-center gap-2">
          {onSearchChange ? (
            <div className="relative max-w-sm flex-1">
              <Search
                className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400"
                aria-hidden
              />
              <Input
                type="search"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                placeholder={searchPlaceholder}
                className="pl-8"
                aria-label="Search"
              />
            </div>
          ) : null}
          {toolbar ? (
            <div className="ml-auto flex flex-wrap items-center gap-2">
              {toolbar}
            </div>
          ) : null}
        </div>
      )}

      {error ? (
        <ErrorBlock error={error} />
      ) : (
        <div className="overflow-hidden rounded-lg border border-neutral-200 dark:border-neutral-800">
          <table className="w-full border-collapse text-sm">
            <thead className="bg-neutral-50 dark:bg-neutral-900">
              <tr>
                {columns.map((c) => (
                  <th
                    key={c.key}
                    scope="col"
                    className={cn(
                      'px-4 py-2 text-left font-medium text-neutral-700 dark:text-neutral-300',
                      c.headerClassName ?? c.className,
                    )}
                  >
                    {c.header}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                <tr>
                  <td colSpan={columns.length} className="px-4">
                    <LoadingBlock />
                  </td>
                </tr>
              ) : rows && rows.length > 0 ? (
                rows.map((row) => (
                  <tr
                    key={rowKey(row)}
                    onClick={onRowClick ? () => onRowClick(row) : undefined}
                    className={cn(
                      'border-t border-neutral-200 dark:border-neutral-800',
                      'hover:bg-neutral-50/60 dark:hover:bg-neutral-900/40',
                      onRowClick && 'cursor-pointer',
                    )}
                  >
                    {columns.map((c) => (
                      <td
                        key={c.key}
                        className={cn(
                          'px-4 py-2 align-middle text-neutral-900 dark:text-neutral-100',
                          c.className,
                        )}
                      >
                        {c.cell(row)}
                      </td>
                    ))}
                  </tr>
                ))
              ) : (
                <tr>
                  <td
                    colSpan={columns.length}
                    className="px-4 py-8 text-center text-neutral-500"
                  >
                    {emptyMessage}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      <DataTableFooter
        page={page}
        totalPages={totalPages}
        total={total}
        pageSize={pageSize}
        rowsThisPage={rows?.length ?? 0}
        onPageChange={onPageChange}
      />
    </div>
  );
}

function DataTableFooter({
  page,
  totalPages,
  total,
  pageSize,
  rowsThisPage,
  onPageChange,
}: {
  page: number;
  totalPages: number | undefined;
  total: number | undefined;
  pageSize: number;
  rowsThisPage: number;
  onPageChange: (p: number) => void;
}) {
  if (total === undefined || total === 0) {
    return null;
  }
  const from = (page - 1) * pageSize + 1;
  const to = (page - 1) * pageSize + rowsThisPage;
  const canPrev = page > 1;
  const canNext = totalPages === undefined ? false : page < totalPages;

  return (
    <div className="flex items-center justify-between text-xs text-neutral-500">
      <span>
        Showing {from}–{to} of {total}
      </span>
      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={!canPrev}
          onClick={() => onPageChange(page - 1)}
          aria-label="Previous page"
        >
          <ChevronLeft className="h-3.5 w-3.5" />
          Prev
        </Button>
        <span className="px-2">
          Page {page}
          {totalPages ? ` / ${totalPages}` : null}
        </span>
        <Button
          variant="outline"
          size="sm"
          disabled={!canNext}
          onClick={() => onPageChange(page + 1)}
          aria-label="Next page"
        >
          Next
          <ChevronRight className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  );
}
