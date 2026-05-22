import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { SecretField } from '@/components/admin/SecretField';
import { SECRET_PLACEHOLDER } from '@/lib/api/types';

describe('SecretField', () => {
  it('renders a password-style input by default', () => {
    render(<SecretField defaultValue="my-secret" aria-label="Token" />);
    const input = screen.getByLabelText('Token') as HTMLInputElement;
    expect(input.type).toBe('password');
  });

  it('toggles visibility when the eye button is clicked', async () => {
    const user = userEvent.setup();
    render(<SecretField defaultValue="my-secret" aria-label="Token" />);
    const input = screen.getByLabelText('Token') as HTMLInputElement;
    const toggle = screen.getByRole('button', { name: /show secret/i });

    expect(input.type).toBe('password');
    await user.click(toggle);
    expect(input.type).toBe('text');
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
  });

  it('renders a "redacted" affordance when masked is true', () => {
    render(
      <SecretField
        masked
        onClear={() => undefined}
        aria-label="Token"
        value={SECRET_PLACEHOLDER}
        onChange={() => undefined}
      />,
    );
    expect(screen.getByText(SECRET_PLACEHOLDER)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /replace/i })).toBeInTheDocument();
  });

  it('calls onClear when Replace is clicked', async () => {
    const user = userEvent.setup();
    const onClear = vi.fn();
    render(
      <SecretField
        masked
        onClear={onClear}
        aria-label="Token"
        value={SECRET_PLACEHOLDER}
        onChange={() => undefined}
      />,
    );
    await user.click(screen.getByRole('button', { name: /replace/i }));
    expect(onClear).toHaveBeenCalledOnce();
  });
});
