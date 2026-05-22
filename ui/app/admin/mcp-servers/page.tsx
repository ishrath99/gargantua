'use client';

import { Plus } from 'lucide-react';
import Link from 'next/link';
import { useMemo, useState } from 'react';

import { ConfirmDialog } from '@/components/admin/ConfirmDialog';
import { DataTable, type ColumnDef } from '@/components/admin/DataTable';
import { ErrorBlock } from '@/components/admin/ErrorBlock';
import { PageHeader } from '@/components/admin/PageHeader';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Label } from '@/components/ui/Label';
import { Select } from '@/components/ui/Select';
import { useCatalogList } from '@/lib/api/hooks/useCatalog';
import {
  useServerArchive,
  useServerUnarchive,
  useServersList,
} from '@/lib/api/hooks/useServers';
import type {
  MCPServerListQuery,
  MCPServerOut,
  UUID,
} from '@/lib/api/types';
import { formatDateTime } from '@/lib/format';

const PAGE_SIZE = 25;

export default function McpServersPage() {
  const [params, setParams] = useState<MCPServerListQuery>({
    page: 1,
    page_size: PAGE_SIZE,
    include_archived: false,
  });
  const [pending, setPending] = useState<{
    row: MCPServerOut;
    action: 'archive' | 'unarchive';
  } | null>(null);

  const list = useServersList(params);
  // Fetch the catalog to label rows by parent-type slug.  100 should
  // be more than enough; if not, the dropdown still works on UUID.
  const types = useCatalogList({
    page: 1,
    page_size: 100,
    include_archived: true,
  });

  const typeBySlug = useMemo(() => {
    const m = new Map<UUID, { slug: string; name: string }>();
    for (const t of types.data?.items ?? []) {
      m.set(t.id, { slug: t.slug, name: t.name });
    }
    return m;
  }, [types.data]);

  const columns: ColumnDef<MCPServerOut>[] = [
    {
      key: 'name',
      header: 'Name',
      cell: (r) => <span className="font-medium">{r.name}</span>,
    },
    {
      key: 'type',
      header: 'Type',
      cell: (r) => {
        const t = typeBySlug.get(r.type_id);
        return (
          <span className="font-mono text-xs" title={r.type_id}>
            {t?.slug ?? r.type_id.slice(0, 8)}
          </span>
        );
      },
    },
    {
      key: 'env_tag',
      header: 'Environment',
      cell: (r) => <Badge variant="secondary">{r.env_tag}</Badge>,
    },
    {
      key: 'status',
      header: 'Status',
      cell: (r) =>
        r.archived_at ? (
          <Badge variant="warning">archived</Badge>
        ) : (
          <Badge variant="success">active</Badge>
        ),
    },
    {
      key: 'updated_at',
      header: 'Updated',
      cell: (r) => (
        <span className="text-xs text-neutral-500">
          {formatDateTime(r.updated_at)}
        </span>
      ),
    },
    {
      key: 'actions',
      header: '',
      cell: (r) => (
        <div className="flex items-center justify-end gap-2">
          <Link href={`/admin/mcp-servers/edit?id=${encodeURIComponent(r.id)}`}>
            <Button size="sm" variant="outline">
              Edit
            </Button>
          </Link>
          {r.archived_at ? (
            <Button
              size="sm"
              variant="outline"
              onClick={() => setPending({ row: r, action: 'unarchive' })}
            >
              Unarchive
            </Button>
          ) : (
            <Button
              size="sm"
              variant="outline"
              onClick={() => setPending({ row: r, action: 'archive' })}
            >
              Archive
            </Button>
          )}
        </div>
      ),
      className: 'text-right',
    },
  ];

  return (
    <>
      <PageHeader
        title="MCP servers"
        description="Concrete MCP server instances.  Each one is parameterised by a catalog type and an environment tag."
        breadcrumbs={[
          { label: 'Admin', href: '/admin' },
          { label: 'MCP servers' },
        ]}
        actions={
          <Link href="/admin/mcp-servers/new">
            <Button>
              <Plus className="h-3.5 w-3.5" />
              New server
            </Button>
          </Link>
        }
      />

      <div className="mb-4 grid grid-cols-1 gap-3 md:grid-cols-4">
        <div className="space-y-1">
          <Label htmlFor="filter-type">Type</Label>
          <Select
            id="filter-type"
            value={params.type_id ?? ''}
            onChange={(e) =>
              setParams((p) => ({
                ...p,
                type_id: e.target.value || undefined,
                page: 1,
              }))
            }
          >
            <option value="">Any</option>
            {(types.data?.items ?? []).map((t) => (
              <option key={t.id} value={t.id}>
                {t.slug}
              </option>
            ))}
          </Select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="filter-env">Environment</Label>
          <Input
            id="filter-env"
            placeholder="prod / dev / …"
            value={params.env_tag ?? ''}
            onChange={(e) =>
              setParams((p) => ({
                ...p,
                env_tag: e.target.value || undefined,
                page: 1,
              }))
            }
          />
        </div>
        <div className="flex items-end gap-2 pb-1">
          <input
            id="include-archived"
            type="checkbox"
            checked={params.include_archived ?? false}
            onChange={(e) =>
              setParams((p) => ({
                ...p,
                include_archived: e.target.checked,
                page: 1,
              }))
            }
            className="h-4 w-4"
          />
          <Label htmlFor="include-archived" className="cursor-pointer">
            Include archived
          </Label>
        </div>
      </div>

      {list.error ? <ErrorBlock error={list.error} className="mb-4" /> : null}

      <DataTable
        rows={list.data?.items}
        columns={columns}
        rowKey={(r) => r.id}
        isLoading={list.isLoading}
        page={params.page ?? 1}
        pageSize={params.page_size ?? PAGE_SIZE}
        total={list.data?.total}
        onPageChange={(page) => setParams((p) => ({ ...p, page }))}
        search={params.search ?? ''}
        onSearchChange={(search) =>
          setParams((p) => ({ ...p, search: search || undefined, page: 1 }))
        }
        searchPlaceholder="Search by name…"
      />

      <ServerArchiveDialog
        pending={pending}
        onOpenChange={(o) => !o && setPending(null)}
      />
    </>
  );
}

function ServerArchiveDialog({
  pending,
  onOpenChange,
}: {
  pending: { row: MCPServerOut; action: 'archive' | 'unarchive' } | null;
  onOpenChange: (open: boolean) => void;
}) {
  const id = pending?.row.id ?? '';
  const archive = useServerArchive(id);
  const unarchive = useServerUnarchive(id);

  if (!pending) return null;

  const isArchive = pending.action === 'archive';
  return (
    <ConfirmDialog
      open
      onOpenChange={onOpenChange}
      title={isArchive ? 'Archive server?' : 'Unarchive server?'}
      description={
        isArchive ? (
          <span>
            Agents and teams that reference this server will fail at run time
            until you unarchive (or remove the reference).
          </span>
        ) : (
          <span>The server becomes selectable on agent and team edits again.</span>
        )
      }
      confirmLabel={isArchive ? 'Archive' : 'Unarchive'}
      confirmVariant={isArchive ? 'destructive' : 'default'}
      onConfirm={async () => {
        if (isArchive) await archive.mutateAsync();
        else await unarchive.mutateAsync();
      }}
    />
  );
}
