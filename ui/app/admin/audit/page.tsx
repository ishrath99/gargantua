'use client';

import { useState } from 'react';

import { DataTable, type ColumnDef } from '@/components/admin/DataTable';
import { ErrorBlock } from '@/components/admin/ErrorBlock';
import { PageHeader } from '@/components/admin/PageHeader';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/Dialog';
import { Input } from '@/components/ui/Input';
import { Label } from '@/components/ui/Label';
import { Select } from '@/components/ui/Select';
import { useAuditList } from '@/lib/api/hooks/useAudit';
import type { AuditLogListQuery, AuditLogOut } from '@/lib/api/types';
import { formatDateTime, shortId } from '@/lib/format';

const ACTIONS = [
  'create',
  'update',
  'archive',
  'unarchive',
  'role_change',
  'deactivate',
  'activate',
] as const;

const PAGE_SIZE = 50;

/**
 * Audit log inspector.
 *
 * The backend's ``/admin/audit`` is page-paginated and supports
 * filtering by ``actor_id``, ``target_type``, ``target_id``, and
 * ``action``.  We let the user filter freely; entering a partial
 * UUID just produces zero results since the backend matches exactly.
 */
export default function AuditPage() {
  const [params, setParams] = useState<AuditLogListQuery>({
    page: 1,
    page_size: PAGE_SIZE,
  });
  const [active, setActive] = useState<AuditLogOut | null>(null);

  const list = useAuditList(params);

  const columns: ColumnDef<AuditLogOut>[] = [
    {
      key: 'created_at',
      header: 'Time',
      cell: (r) => (
        <span className="text-xs text-neutral-500">
          {formatDateTime(r.created_at)}
        </span>
      ),
    },
    {
      key: 'actor',
      header: 'Actor',
      cell: (r) => (
        <span className="font-mono text-xs" title={r.actor_id ?? undefined}>
          {shortId(r.actor_id)}
        </span>
      ),
    },
    {
      key: 'action',
      header: 'Action',
      cell: (r) => <Badge variant="outline">{r.action}</Badge>,
    },
    {
      key: 'target',
      header: 'Target',
      cell: (r) => (
        <span className="font-mono text-xs">
          {r.target_type}
          {r.target_id ? `/${shortId(r.target_id)}` : ''}
        </span>
      ),
    },
    {
      key: 'actions',
      header: '',
      cell: (r) => (
        <Button size="sm" variant="ghost" onClick={() => setActive(r)}>
          View
        </Button>
      ),
      className: 'text-right',
    },
  ];

  return (
    <>
      <PageHeader
        title="Audit log"
        description="Every mutating admin action, newest first.  Click an entry to inspect the before / after diff captured at write time."
        breadcrumbs={[
          { label: 'Admin', href: '/admin' },
          { label: 'Audit log' },
        ]}
      />

      <div className="mb-4 grid grid-cols-1 gap-3 md:grid-cols-4">
        <div className="space-y-1">
          <Label htmlFor="filter-target-type">Target type</Label>
          <Input
            id="filter-target-type"
            placeholder="agent / team / …"
            value={params.target_type ?? ''}
            onChange={(e) =>
              setParams((p) => ({
                ...p,
                target_type: e.target.value || undefined,
                page: 1,
              }))
            }
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="filter-action">Action</Label>
          <Select
            id="filter-action"
            value={params.action ?? ''}
            onChange={(e) =>
              setParams((p) => ({
                ...p,
                action: e.target.value || undefined,
                page: 1,
              }))
            }
          >
            <option value="">Any</option>
            {ACTIONS.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </Select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="filter-actor">Actor ID (UUID)</Label>
          <Input
            id="filter-actor"
            placeholder="exact uuid"
            value={params.actor_id ?? ''}
            onChange={(e) =>
              setParams((p) => ({
                ...p,
                actor_id: e.target.value || undefined,
                page: 1,
              }))
            }
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="filter-target-id">Target ID (UUID)</Label>
          <Input
            id="filter-target-id"
            placeholder="exact uuid"
            value={params.target_id ?? ''}
            onChange={(e) =>
              setParams((p) => ({
                ...p,
                target_id: e.target.value || undefined,
                page: 1,
              }))
            }
          />
        </div>
      </div>

      {list.error ? <ErrorBlock error={list.error} className="mb-4" /> : null}

      <DataTable
        rows={list.data?.items}
        columns={columns}
        rowKey={(r) => String(r.id)}
        isLoading={list.isLoading}
        page={params.page ?? 1}
        pageSize={params.page_size ?? PAGE_SIZE}
        total={list.data?.total}
        onPageChange={(page) => setParams((p) => ({ ...p, page }))}
      />

      <DiffDialog entry={active} onOpenChange={(o) => !o && setActive(null)} />
    </>
  );
}

function DiffDialog({
  entry,
  onOpenChange,
}: {
  entry: AuditLogOut | null;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={!!entry} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>Audit entry #{entry?.id ?? '—'}</DialogTitle>
          {entry ? (
            <DialogDescription>
              <span className="font-mono">{entry.action}</span> on{' '}
              <span className="font-mono">
                {entry.target_type}
                {entry.target_id ? `/${entry.target_id}` : ''}
              </span>{' '}
              by <span className="font-mono">{entry.actor_id ?? '—'}</span> ·{' '}
              {formatDateTime(entry.created_at)}
            </DialogDescription>
          ) : null}
        </DialogHeader>
        {entry ? (
          <div className="grid grid-cols-2 gap-3 overflow-hidden">
            <DiffPane title="Before" payload={entry.before} />
            <DiffPane title="After" payload={entry.after} />
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function DiffPane({
  title,
  payload,
}: {
  title: string;
  payload: Record<string, unknown> | null;
}) {
  return (
    <div className="flex min-w-0 flex-col">
      <div className="mb-1 text-xs font-medium uppercase tracking-wider text-neutral-500">
        {title}
      </div>
      <pre className="max-h-96 overflow-auto rounded-md bg-neutral-100 p-2 text-xs leading-snug dark:bg-neutral-900">
        {payload ? JSON.stringify(payload, null, 2) : '(empty)'}
      </pre>
    </div>
  );
}
