'use client';

import { Check, ChevronDown, X } from 'lucide-react';
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from 'react';

import { Badge } from '@/components/ui/Badge';
import { Input } from '@/components/ui/Input';
import { cn } from '@/lib/utils';

export interface MultiSelectOption {
  value: string;
  label: string;
  /** Hidden chip text — searched but not displayed. */
  description?: string;
}

export interface MultiSelectProps {
  options: MultiSelectOption[];
  /** Selected option values (unordered semantics). */
  value: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  disabled?: boolean;
  /** Label announced to screen readers when no text label is rendered. */
  ariaLabel?: string;
}

/**
 * Multi-select with chips + filterable dropdown.  Selection order is
 * **not** preserved — callers that care about order should use
 * :class:`OrderedMultiSelect` instead.
 *
 * Roll-your-own rationale: the headless libraries that do this well
 * (`react-select`, `downshift`) pull in 30-50KB; this implementation
 * is ~3KB and covers our needs (keyboard nav, click-outside, focus
 * trap inside the popover, click-to-toggle, type-to-filter).
 */
export function MultiSelect({
  options,
  value,
  onChange,
  placeholder = 'Select…',
  disabled,
  ariaLabel,
}: MultiSelectProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Click-outside to close.
  useEffect(() => {
    if (!open) return;
    function onClick(e: MouseEvent) {
      if (!rootRef.current?.contains(e.target as Node)) {
        setOpen(false);
        setQuery('');
      }
    }
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, [open]);

  // Focus the filter input when the popover opens.
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options.filter(
      (o) =>
        o.label.toLowerCase().includes(q) ||
        o.value.toLowerCase().includes(q) ||
        o.description?.toLowerCase().includes(q),
    );
  }, [options, query]);

  const valueSet = useMemo(() => new Set(value), [value]);

  const toggle = useCallback(
    (v: string) => {
      if (valueSet.has(v)) {
        onChange(value.filter((x) => x !== v));
      } else {
        onChange([...value, v]);
      }
    },
    [value, valueSet, onChange],
  );

  const selectedChips = useMemo(() => {
    const byVal = new Map(options.map((o) => [o.value, o]));
    return value.map((v) => byVal.get(v) ?? { value: v, label: v });
  }, [options, value]);

  function handleKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (disabled) return;
    if (!open && (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown')) {
      e.preventDefault();
      setOpen(true);
    } else if (open && e.key === 'Escape') {
      e.preventDefault();
      setOpen(false);
      setQuery('');
    }
  }

  return (
    <div
      ref={rootRef}
      className="relative"
      onKeyDown={handleKeyDown}
      aria-disabled={disabled}
    >
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
        className={cn(
          'flex min-h-9 w-full items-center justify-between gap-2 rounded-md border border-neutral-200 bg-white px-2 py-1 text-left text-sm shadow-sm',
          'focus:outline-none focus:ring-2 focus:ring-neutral-900 focus:ring-offset-1',
          'disabled:cursor-not-allowed disabled:opacity-50',
          'dark:border-neutral-800 dark:bg-neutral-950 dark:focus:ring-neutral-100',
        )}
      >
        <span className="flex flex-1 flex-wrap items-center gap-1">
          {selectedChips.length === 0 ? (
            <span className="text-neutral-400">{placeholder}</span>
          ) : (
            selectedChips.map((c) => (
              <Badge
                key={c.value}
                variant="secondary"
                className="gap-1 pr-1 text-xs"
              >
                <span>{c.label}</span>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    toggle(c.value);
                  }}
                  className="rounded-sm hover:bg-neutral-300 dark:hover:bg-neutral-700"
                  aria-label={`Remove ${c.label}`}
                >
                  <X className="h-3 w-3" />
                </button>
              </Badge>
            ))
          )}
        </span>
        <ChevronDown
          className="h-4 w-4 shrink-0 text-neutral-400"
          aria-hidden
        />
      </button>

      {open ? (
        <div
          role="listbox"
          aria-multiselectable
          className={cn(
            'absolute z-30 mt-1 w-full min-w-[240px] rounded-md border border-neutral-200 bg-white p-1 shadow-lg',
            'dark:border-neutral-800 dark:bg-neutral-950',
          )}
        >
          <div className="p-1">
            <Input
              ref={inputRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter…"
              className="h-8"
            />
          </div>
          <ul className="max-h-60 overflow-auto py-1">
            {filtered.length === 0 ? (
              <li className="px-3 py-2 text-xs text-neutral-500">
                No matches.
              </li>
            ) : (
              filtered.map((o) => {
                const selected = valueSet.has(o.value);
                return (
                  <li key={o.value}>
                    <button
                      type="button"
                      role="option"
                      aria-selected={selected}
                      onClick={() => toggle(o.value)}
                      className={cn(
                        'flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-sm',
                        'hover:bg-neutral-100 dark:hover:bg-neutral-900',
                        selected && 'font-medium',
                      )}
                    >
                      <span
                        className={cn(
                          'flex h-4 w-4 items-center justify-center rounded border',
                          selected
                            ? 'border-neutral-900 bg-neutral-900 text-white dark:border-neutral-100 dark:bg-neutral-100 dark:text-neutral-900'
                            : 'border-neutral-300 dark:border-neutral-700',
                        )}
                      >
                        {selected ? <Check className="h-3 w-3" /> : null}
                      </span>
                      <span className="flex-1 truncate">{o.label}</span>
                      {o.description ? (
                        <span className="truncate text-xs text-neutral-500">
                          {o.description}
                        </span>
                      ) : null}
                    </button>
                  </li>
                );
              })
            )}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
