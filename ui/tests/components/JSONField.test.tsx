import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { JSONField } from '@/components/admin/JSONField';

function Harness({
  initial,
  onChange,
  onValidityChange,
}: {
  initial: unknown;
  onChange?: (v: unknown) => void;
  onValidityChange?: (err: string | undefined) => void;
}) {
  const [value, setValue] = useState<unknown>(initial);
  return (
    <JSONField
      value={value}
      onChange={(v) => {
        setValue(v);
        onChange?.(v);
      }}
      onValidityChange={onValidityChange}
      ariaLabel="JSON"
    />
  );
}

describe('JSONField', () => {
  it('round-trips a valid JSON edit', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Harness initial={{ a: 1 }} onChange={onChange} />);
    const ta = screen.getByLabelText('JSON');
    await user.clear(ta);
    await user.type(ta, '{{"b": 2}');
    // Last call should reflect the final parsed object.
    expect(onChange).toHaveBeenCalledWith({ b: 2 });
  });

  it('emits a validity error for malformed JSON', async () => {
    const user = userEvent.setup();
    const onValidity = vi.fn();
    render(<Harness initial={{}} onValidityChange={onValidity} />);
    const ta = screen.getByLabelText('JSON');
    await user.clear(ta);
    await user.type(ta, '{{not valid');
    // Latest call must carry a non-empty error string.
    const last = onValidity.mock.calls.at(-1)?.[0];
    expect(typeof last).toBe('string');
    expect(last).toMatch(/json/i);
  });

  it('clears the error when the textarea is emptied', async () => {
    const user = userEvent.setup();
    const onValidity = vi.fn();
    render(<Harness initial={{ a: 1 }} onValidityChange={onValidity} />);
    const ta = screen.getByLabelText('JSON');
    await user.clear(ta);
    // Empty input is treated as ``null`` with no error.
    expect(onValidity).toHaveBeenLastCalledWith(undefined);
  });
});
