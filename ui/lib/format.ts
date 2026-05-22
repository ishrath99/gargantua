/**
 * Small formatting helpers used across admin pages.
 *
 * Kept dependency-free (no ``date-fns`` etc.) — the formats we need
 * are simple and stable, and the import cost matters in a static
 * export bundle.
 */

/**
 * Render an ISO timestamp as a compact local-time string.  Falls
 * back to the original string when parsing fails so we never blank
 * out a cell silently.
 */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/**
 * Relative time ("3m ago", "yesterday", "2d ago", "Mar 14").  Falls
 * back to absolute date for anything older than ~6 days.
 */
export function formatRelative(
  iso: string | null | undefined,
  now: Date = new Date(),
): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const ms = now.getTime() - d.getTime();
  const s = Math.round(ms / 1000);
  if (s < 30) return 'just now';
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  const days = Math.round(h / 24);
  if (days < 7) return `${days}d ago`;
  return d.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
  });
}

/**
 * Truncate a UUID for tabular display: ``abcdef01-…`` keeps the
 * leading 8 characters which is enough to disambiguate in practice
 * while saving a lot of horizontal space.
 */
export function shortId(id: string | null | undefined): string {
  if (!id) return '—';
  return id.length > 12 ? `${id.slice(0, 8)}…` : id;
}
