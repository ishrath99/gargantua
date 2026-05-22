import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { DataTable } from '@/components/admin/DataTable';

interface Row {
  id: string;
  name: string;
}

const ROWS: Row[] = [
  { id: 'a', name: 'Alpha' },
  { id: 'b', name: 'Bravo' },
];

const COLUMNS = [
  { key: 'name', header: 'Name', cell: (r: Row) => r.name },
];

describe('DataTable', () => {
  it('renders rows and headers', () => {
    render(
      <DataTable
        rows={ROWS}
        columns={COLUMNS}
        rowKey={(r) => r.id}
        page={1}
        pageSize={10}
        total={2}
        onPageChange={() => undefined}
      />,
    );
    expect(screen.getByRole('columnheader', { name: 'Name' })).toBeInTheDocument();
    expect(screen.getByText('Alpha')).toBeInTheDocument();
    expect(screen.getByText('Bravo')).toBeInTheDocument();
  });

  it('shows the empty state when rows is empty', () => {
    render(
      <DataTable
        rows={[]}
        columns={COLUMNS}
        rowKey={(r) => r.id}
        page={1}
        pageSize={10}
        total={0}
        onPageChange={() => undefined}
        emptyMessage="Nothing here yet."
      />,
    );
    expect(screen.getByText('Nothing here yet.')).toBeInTheDocument();
  });

  it('shows the loading state when isLoading is true', () => {
    render(
      <DataTable
        rows={undefined}
        columns={COLUMNS}
        rowKey={(r) => r.id}
        isLoading
        page={1}
        pageSize={10}
        total={undefined}
        onPageChange={() => undefined}
      />,
    );
    // LoadingBlock renders a polite live region with the default
    // "Loading…" label.  jsdom doesn't expose ``role=status`` to
    // RTL's role-mapper here, so we assert on the text.
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it('debounces the search input', async () => {
    const user = userEvent.setup();
    const onSearchChange = vi.fn();

    render(
      <DataTable
        rows={ROWS}
        columns={COLUMNS}
        rowKey={(r) => r.id}
        page={1}
        pageSize={10}
        total={2}
        onPageChange={() => undefined}
        search=""
        onSearchChange={onSearchChange}
      />,
    );

    const input = screen.getByRole('searchbox', { name: /search/i });
    await user.type(input, 'alp');

    // Wait for the internal debounce (~250ms) to flush.
    await waitFor(() => {
      expect(onSearchChange).toHaveBeenCalled();
    });
    expect(onSearchChange).toHaveBeenLastCalledWith('alp');
  });

  it('paginates when total > pageSize', async () => {
    const user = userEvent.setup();
    const onPageChange = vi.fn();

    render(
      <DataTable
        rows={ROWS}
        columns={COLUMNS}
        rowKey={(r) => r.id}
        page={1}
        pageSize={2}
        total={4}
        onPageChange={onPageChange}
      />,
    );

    // The table renders "Prev" + "Next" buttons keyed by aria-label.
    const next = screen.getByLabelText('Next page');
    await user.click(next);
    expect(onPageChange).toHaveBeenCalledWith(2);
  });
});
