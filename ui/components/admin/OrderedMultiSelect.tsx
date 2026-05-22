'use client';

import { ArrowDown, ArrowUp, X } from 'lucide-react';
import { useMemo } from 'react';

import { Button } from '@/components/ui/Button';
import { Select } from '@/components/ui/Select';
import { cn } from '@/lib/utils';

import type { MultiSelectOption } from './MultiSelect';

export interface OrderedMultiSelectProps {
  options: MultiSelectOption[];
  /** Selected option values **in display order**. */
  value: string[];
  onChange: (next: string[]) => void;
  /** Label for the "add" select; defaults to a generic prompt. */
  addPlaceholder?: string;
  disabled?: boolean;
  ariaLabel?: string;
}

/**
 * List + add-from-dropdown control where selection order is part of
 * the value (e.g. team ``member_agent_ids`` — the first member is
 * often the "lead").
 *
 * UI shape: a vertical stack of "chips" with up/down/remove buttons,
 * plus a single ``<select>`` at the bottom that only shows the
 * not-yet-picked options.
 */
export function OrderedMultiSelect({
  options,
  value,
  onChange,
  addPlaceholder = 'Add member…',
  disabled,
  ariaLabel,
}: OrderedMultiSelectProps) {
  const byVal = useMemo(
    () => new Map(options.map((o) => [o.value, o])),
    [options],
  );
  const available = useMemo(
    () => options.filter((o) => !value.includes(o.value)),
    [options, value],
  );

  function move(index: number, delta: -1 | 1) {
    const next = [...value];
    const j = index + delta;
    if (j < 0 || j >= next.length) return;
    const [moved] = next.splice(index, 1);
    next.splice(j, 0, moved);
    onChange(next);
  }
  function remove(index: number) {
    const next = [...value];
    next.splice(index, 1);
    onChange(next);
  }
  function add(v: string) {
    if (!v || value.includes(v)) return;
    onChange([...value, v]);
  }

  return (
    <div
      aria-label={ariaLabel}
      className={cn(
        'flex flex-col gap-2 rounded-md border border-neutral-200 p-2',
        'dark:border-neutral-800',
        disabled && 'opacity-60',
      )}
    >
      {value.length === 0 ? (
        <p className="px-1 py-2 text-xs text-neutral-500">
          No items added yet.
        </p>
      ) : (
        <ul className="flex flex-col gap-1">
          {value.map((v, i) => {
            const opt = byVal.get(v) ?? { value: v, label: v };
            return (
              <li
                key={v}
                className={cn(
                  'flex items-center gap-2 rounded-md border border-neutral-200 bg-white px-2 py-1.5 text-sm',
                  'dark:border-neutral-800 dark:bg-neutral-950',
                )}
              >
                <span className="w-6 shrink-0 text-xs tabular-nums text-neutral-400">
                  #{i + 1}
                </span>
                <span className="flex-1 truncate">{opt.label}</span>
                <div className="flex items-center gap-1">
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7"
                    onClick={() => move(i, -1)}
                    disabled={disabled || i === 0}
                    aria-label="Move up"
                  >
                    <ArrowUp className="h-3.5 w-3.5" />
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7"
                    onClick={() => move(i, 1)}
                    disabled={disabled || i === value.length - 1}
                    aria-label="Move down"
                  >
                    <ArrowDown className="h-3.5 w-3.5" />
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 text-red-600 dark:text-red-400"
                    onClick={() => remove(i)}
                    disabled={disabled}
                    aria-label="Remove"
                  >
                    <X className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      <Select
        value=""
        disabled={disabled || available.length === 0}
        onChange={(e) => {
          add(e.target.value);
          e.currentTarget.value = '';
        }}
      >
        <option value="">{addPlaceholder}</option>
        {available.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </Select>
    </div>
  );
}
