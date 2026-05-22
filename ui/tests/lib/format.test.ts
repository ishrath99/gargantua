import { describe, expect, it } from 'vitest';

import { formatDateTime, formatRelative, shortId } from '@/lib/format';

describe('formatDateTime', () => {
  it('returns "—" for null / undefined / empty', () => {
    expect(formatDateTime(null)).toBe('—');
    expect(formatDateTime(undefined)).toBe('—');
    expect(formatDateTime('')).toBe('—');
  });

  it('falls back to the raw value when unparseable', () => {
    expect(formatDateTime('not-a-date')).toBe('not-a-date');
  });

  it('renders a localised string for a real ISO timestamp', () => {
    const out = formatDateTime('2024-01-15T12:34:56Z');
    // We can't assume the host timezone, but the output should at
    // least contain the year and a 24h-ish time pattern.
    expect(out).toMatch(/2024/);
  });
});

describe('formatRelative', () => {
  const now = new Date('2024-01-15T12:00:00Z');

  it('returns "just now" for sub-30-second deltas', () => {
    expect(
      formatRelative(new Date(now.getTime() - 10_000).toISOString(), now),
    ).toBe('just now');
  });

  it('uses "Ns ago" within the same minute', () => {
    expect(
      formatRelative(new Date(now.getTime() - 45_000).toISOString(), now),
    ).toMatch(/^45s ago$/);
  });

  it('uses "Nm ago" within the hour', () => {
    expect(
      formatRelative(new Date(now.getTime() - 3 * 60_000).toISOString(), now),
    ).toBe('3m ago');
  });

  it('falls back to a date string for >= 7 days old', () => {
    const out = formatRelative(
      new Date(now.getTime() - 30 * 24 * 60 * 60_000).toISOString(),
      now,
    );
    expect(out).toMatch(/\d{4}/);
  });
});

describe('shortId', () => {
  it('returns "—" for falsy inputs', () => {
    expect(shortId(null)).toBe('—');
    expect(shortId(undefined)).toBe('—');
    expect(shortId('')).toBe('—');
  });

  it('truncates long UUID-like strings', () => {
    expect(shortId('abcdef01-2345-6789-abcd-ef0123456789')).toBe('abcdef01…');
  });

  it('passes short ids through unchanged', () => {
    expect(shortId('abc')).toBe('abc');
  });
});
