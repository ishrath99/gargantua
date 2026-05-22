'use client';

import { RefreshCw, Trash2 } from 'lucide-react';
import { useState } from 'react';

import { ConfirmDialog } from '@/components/admin/ConfirmDialog';
import { DataTable, type ColumnDef } from '@/components/admin/DataTable';
import { ErrorBlock } from '@/components/admin/ErrorBlock';
import { PageHeader } from '@/components/admin/PageHeader';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import {
  useMcpCacheEvict,
  useMcpCacheList,
} from '@/lib/api/hooks/useMcpCache';
import { formatRelative, shortId } from '@/lib/format';
import type { MCPCacheEntryOut, UUID } from '@/lib/api/types';

/**
 * Read-mostly operations page: lists every warm MCP tool handle held
 * by this server process, with a manual evict for stuck entries.
 *
 * Polling cadence (5s) lives in :func:`useMcpCacheList` so this
 * component just renders.
 */
export default function McpCachePage() {
  const list = useMcpCacheList();
  const evict = useMcpCacheEvict();

  const [confirming, setConfirming] = useState<UUID | null>(null);

  const columns: ColumnDef<MCPCacheEntryOut>[] = [
    {
      key: 'server_id',
      header: 'Server',
      cell: (r) => (
        <span className="font-mono text-xs" title={r.server_id}>
          {shortId(r.server_id)}
        </span>
      ),
    },
    {
      key: 'children',
      header: 'Children',
      cell: (r) =>
        r.child_resource_ids.length === 0 ? (
          <span className="text-neutral-400">—</span>
        ) : (
          <span
            className="font-mono text-xs"
            title={r.child_resource_ids.join('\n')}
          >
            {r.child_resource_ids.length}
          </span>
        ),
      className: 'tabular-nums',
    },
    {
      key: 'version',
      header: 'Version',
      cell: (r) => <span className="tabular-nums">{r.version}</span>,
      className: 'tabular-nums',
    },
    {
      key: 'ref_count',
      header: 'Refs',
      cell: (r) => <span className="tabular-nums">{r.ref_count}</span>,
      className: 'tabular-nums',
    },
    {
      key: 'status',
      header: 'Status',
      cell: (r) =>
        r.is_orphan ? (
          <Badge variant="warning">orphan</Badge>
        ) : (
          <Badge variant="success">live</Badge>
        ),
    },
    {
      key: 'last_used',
      header: 'Last used',
      cell: (r) => (
        <span className="text-xs text-neutral-500">
          {formatRelative(r.last_used)}
        </span>
      ),
    },
    {
      key: 'actions',
      header: '',
      cell: (r) => (
        <Button
          size="sm"
          variant="outline"
          onClick={() => setConfirming(r.server_id)}
        >
          <Trash2 className="h-3.5 w-3.5" />
          Evict
        </Button>
      ),
      className: 'text-right',
    },
  ];

  return (
    <>
      <PageHeader
        title="MCP cache"
        description="Live snapshot of warm MCP tool handles held by this server process.  Orphans appear after a version bump while at least one caller still holds the old handle."
        breadcrumbs={[
          { label: 'Admin', href: '/admin' },
          { label: 'MCP cache' },
        ]}
        actions={
          <Button
            variant="outline"
            size="sm"
            onClick={() => list.refetch()}
            disabled={list.isFetching}
          >
            <RefreshCw
              className={list.isFetching ? 'h-3.5 w-3.5 animate-spin' : 'h-3.5 w-3.5'}
            />
            Refresh
          </Button>
        }
      />

      {list.error ? <ErrorBlock error={list.error} className="mb-4" /> : null}

      <DataTable
        rows={list.data?.items}
        columns={columns}
        rowKey={(r) => `${r.server_id}:${r.child_resource_ids.join(',')}`}
        isLoading={list.isLoading}
        page={1}
        pageSize={list.data?.total ?? 0}
        total={list.data?.total}
        onPageChange={() => undefined}
        emptyMessage="No warm cache entries.  The cache populates lazily on the first run for a given (server, child-resource set) pair."
      />

      <ConfirmDialog
        open={!!confirming}
        onOpenChange={(o) => !o && setConfirming(null)}
        title="Evict MCP cache entry?"
        description="The next run for this server (or any agent that uses it) will re-build a fresh tool handle.  In-flight runs that still hold the old handle are unaffected — they'll continue to use it until they finish."
        confirmLabel="Evict"
        onConfirm={async () => {
          if (!confirming) return;
          await evict.mutateAsync(confirming);
        }}
      />
    </>
  );
}
