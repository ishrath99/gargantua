'use client';

import { Plus } from 'lucide-react';
import Link from 'next/link';
import { useState } from 'react';

import { ConfirmDialog } from '@/components/admin/ConfirmDialog';
import { DataTable, type ColumnDef } from '@/components/admin/DataTable';
import { ErrorBlock } from '@/components/admin/ErrorBlock';
import { PageHeader } from '@/components/admin/PageHeader';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Label } from '@/components/ui/Label';
import { Select } from '@/components/ui/Select';
import {
  useTeamArchive,
  useTeamUnarchive,
  useTeamsList,
} from '@/lib/api/hooks/useTeams';
import type { TeamListQuery, TeamMode, TeamOut } from '@/lib/api/types';
import { formatDateTime } from '@/lib/format';

const PAGE_SIZE = 25;

export default function TeamsPage() {
  const [params, setParams] = useState<TeamListQuery>({
    page: 1,
    page_size: PAGE_SIZE,
    include_archived: false,
  });
  const [pending, setPending] = useState<{
    row: TeamOut;
    action: 'archive' | 'unarchive';
  } | null>(null);

  const list = useTeamsList(params);

  const columns: ColumnDef<TeamOut>[] = [
    {
      key: 'name',
      header: 'Name',
      cell: (r) => <span className="font-medium">{r.name}</span>,
    },
    {
      key: 'mode',
      header: 'Mode',
      cell: (r) => <Badge variant="outline">{r.mode}</Badge>,
    },
    {
      key: 'members',
      header: 'Members',
      cell: (r) => (
        <span className="tabular-nums">{r.member_agent_ids.length}</span>
      ),
      className: 'tabular-nums',
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
          <Link href={`/admin/teams/edit?id=${encodeURIComponent(r.id)}`}>
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
        title="Teams"
        description="Ordered groupings of agents — route, coordinate, or collaborate."
        breadcrumbs={[
          { label: 'Admin', href: '/admin' },
          { label: 'Teams' },
        ]}
        actions={
          <Link href="/admin/teams/new">
            <Button>
              <Plus className="h-3.5 w-3.5" />
              New team
            </Button>
          </Link>
        }
      />

      <div className="mb-4 grid grid-cols-1 gap-3 md:grid-cols-3">
        <div className="space-y-1">
          <Label htmlFor="filter-mode">Mode</Label>
          <Select
            id="filter-mode"
            value={params.mode ?? ''}
            onChange={(e) =>
              setParams((p) => ({
                ...p,
                mode: (e.target.value as TeamMode) || undefined,
                page: 1,
              }))
            }
          >
            <option value="">Any</option>
            <option value="route">route</option>
            <option value="coordinate">coordinate</option>
            <option value="collaborate">collaborate</option>
          </Select>
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

      <TeamArchiveDialog
        pending={pending}
        onOpenChange={(o) => !o && setPending(null)}
      />
    </>
  );
}

function TeamArchiveDialog({
  pending,
  onOpenChange,
}: {
  pending: { row: TeamOut; action: 'archive' | 'unarchive' } | null;
  onOpenChange: (open: boolean) => void;
}) {
  const id = pending?.row.id ?? '';
  const archive = useTeamArchive(id);
  const unarchive = useTeamUnarchive(id);
  if (!pending) return null;
  const isArchive = pending.action === 'archive';
  return (
    <ConfirmDialog
      open
      onOpenChange={onOpenChange}
      title={isArchive ? 'Archive team?' : 'Unarchive team?'}
      description={
        isArchive ? (
          <span>
            Runs against this team will fail until you unarchive.  Individual
            member agents are unaffected.
          </span>
        ) : (
          <span>The team becomes runnable again.</span>
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
