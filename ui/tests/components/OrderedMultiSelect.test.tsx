import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';

import { OrderedMultiSelect } from '@/components/admin/OrderedMultiSelect';

const OPTIONS = [
  { value: 'a', label: 'Alpha' },
  { value: 'b', label: 'Bravo' },
  { value: 'c', label: 'Charlie' },
];

function Harness({ initial }: { initial: string[] }) {
  const [value, setValue] = useState<string[]>(initial);
  return (
    <OrderedMultiSelect
      options={OPTIONS}
      value={value}
      onChange={setValue}
      ariaLabel="Members"
    />
  );
}

describe('OrderedMultiSelect', () => {
  it('shows the empty hint when no items are selected', () => {
    render(<Harness initial={[]} />);
    expect(screen.getByText(/no items added/i)).toBeInTheDocument();
  });

  it('adds items from the dropdown in pick order', async () => {
    const user = userEvent.setup();
    render(<Harness initial={[]} />);

    const select = screen.getByRole('combobox');
    await user.selectOptions(select, 'b');
    expect(screen.getByText('Bravo')).toBeInTheDocument();
    expect(screen.getByText('#1')).toBeInTheDocument();

    await user.selectOptions(select, 'a');
    expect(screen.getByText('Alpha')).toBeInTheDocument();
    expect(screen.getByText('#2')).toBeInTheDocument();
  });

  it('moves an item down with the arrow button', async () => {
    const user = userEvent.setup();
    render(<Harness initial={['a', 'b']} />);

    // Both items present, ordered.
    const items = screen.getAllByRole('listitem');
    expect(items[0]).toHaveTextContent('Alpha');
    expect(items[1]).toHaveTextContent('Bravo');

    // Click the "down" arrow on Alpha (first row).
    const downBtns = screen.getAllByRole('button', { name: /move down/i });
    await user.click(downBtns[0]);

    const after = screen.getAllByRole('listitem');
    expect(after[0]).toHaveTextContent('Bravo');
    expect(after[1]).toHaveTextContent('Alpha');
  });

  it('removes an item with the X button', async () => {
    const user = userEvent.setup();
    render(<Harness initial={['a', 'b']} />);

    // Two list items before removal.
    expect(screen.getAllByRole('listitem')).toHaveLength(2);

    const removeBtns = screen.getAllByRole('button', { name: /^remove$/i });
    await user.click(removeBtns[0]);

    // Alpha is no longer a chip, but the <select> reissues it as an
    // available option — assert on the list, not the document.
    const remaining = screen.getAllByRole('listitem');
    expect(remaining).toHaveLength(1);
    expect(remaining[0]).toHaveTextContent('Bravo');
  });
});
