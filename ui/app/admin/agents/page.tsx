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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/Dialog';
import { Input } from '@/components/ui/Input';
import { Label } from '@/components/ui/Label';
import {
  useAgentArchive,
  useAgentUnarchive,
  useAgentTemplatesList,
  useAgentsList,
} from '@/lib/api/hooks/useAgents';
import type { AgentListQuery, AgentOut } from '@/lib/api/types';
import { formatDateTime } from '@/lib/format';
import { useRouter } from 'next/navigation';

const PAGE_SIZE = 25;

export default function AgentsPage() {
  const router = useRouter();
  const [params, setParams] = useState<AgentListQuery>({
    page: 1,
    page_size: PAGE_SIZE,
    include_archived: false,
  });
  const [pending, setPending] = useState<{
    row: AgentOut;
    action: 'archive' | 'unarchive';
  } | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);

  const list = useAgentsList(params);

  const columns: ColumnDef<AgentOut>[] = [
    {
      key: 'name',
      header: 'Name',
      cell: (r) => <span className="font-medium">{r.name}</span>,
    },
    {
      key: 'model',
      header: 'Model',
      cell: (r) => (
        <Badge variant="outline" className="font-mono">
          {r.model}
        </Badge>
      ),
    },
    {
      key: 'servers',
      header: 'Servers',
      cell: (r) => (
        <span className="tabular-nums">{r.mcp_server_ids.length}</span>
      ),
      className: 'tabular-nums',
    },
    {
      key: 'children',
      header: 'Children',
      cell: (r) => (
        <span className="tabular-nums">{r.child_resource_ids.length}</span>
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
          <Link href={`/admin/agents/edit?id=${encodeURIComponent(r.id)}`}>
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
        title="Agents"
        description="Single-agent definitions: model, instructions, attached MCP servers."
        breadcrumbs={[
          { label: 'Admin', href: '/admin' },
          { label: 'Agents' },
        ]}
        actions={
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={() => setPickerOpen(true)}>
              From template…
            </Button>
            <Link href="/admin/agents/new">
              <Button>
                <Plus className="h-3.5 w-3.5" />
                New agent
              </Button>
            </Link>
          </div>
        }
      />

      <div className="mb-4 grid grid-cols-1 gap-3 md:grid-cols-3">
        <div className="space-y-1">
          <Label htmlFor="filter-model">Model</Label>
          <Input
            id="filter-model"
            placeholder="openai:gpt-…"
            value={params.model ?? ''}
            onChange={(e) =>
              setParams((p) => ({
                ...p,
                model: e.target.value || undefined,
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

      <AgentArchiveDialog
        pending={pending}
        onOpenChange={(o) => !o && setPending(null)}
      />

      <TemplatePickerDialog
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        onPick={(slug) => {
          setPickerOpen(false);
          router.push(`/admin/agents/new?template=${encodeURIComponent(slug)}`);
        }}
      />
    </>
  );
}

function AgentArchiveDialog({
  pending,
  onOpenChange,
}: {
  pending: { row: AgentOut; action: 'archive' | 'unarchive' } | null;
  onOpenChange: (open: boolean) => void;
}) {
  const id = pending?.row.id ?? '';
  const archive = useAgentArchive(id);
  const unarchive = useAgentUnarchive(id);
  if (!pending) return null;
  const isArchive = pending.action === 'archive';
  return (
    <ConfirmDialog
      open
      onOpenChange={onOpenChange}
      title={isArchive ? 'Archive agent?' : 'Unarchive agent?'}
      description={
        isArchive ? (
          <span>
            Teams referencing this agent will fail at run time until you
            unarchive or remove the reference.
          </span>
        ) : (
          <span>The agent becomes selectable from team edit again.</span>
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

function TemplatePickerDialog({
  open,
  onOpenChange,
  onPick,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onPick: (slug: string) => void;
}) {
  const templates = useAgentTemplatesList();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Start from a template</DialogTitle>
          <DialogDescription>
            Picks a starter instructions blob you can edit freely.  The
            template&apos;s suggested MCP server types appear on the New Agent
            page for reference.
          </DialogDescription>
        </DialogHeader>
        {templates.error ? <ErrorBlock error={templates.error} /> : null}
        <ul className="max-h-80 divide-y divide-neutral-200 overflow-y-auto rounded-md border border-neutral-200 dark:divide-neutral-800 dark:border-neutral-800">
          {(templates.data?.items ?? []).map((t) => (
            <li key={t.slug}>
              <button
                type="button"
                onClick={() => onPick(t.slug)}
                className="block w-full px-3 py-2 text-left text-sm hover:bg-neutral-50 dark:hover:bg-neutral-900"
              >
                <div className="font-medium">{t.name}</div>
                <div className="font-mono text-xs text-neutral-500">
                  {t.slug} · {t.model}
                </div>
                {t.description ? (
                  <div className="mt-1 text-xs text-neutral-500">
                    {t.description}
                  </div>
                ) : null}
              </button>
            </li>
          ))}
          {templates.data && templates.data.items.length === 0 ? (
            <li className="px-3 py-2 text-xs text-neutral-500">
              No templates seeded.
            </li>
          ) : null}
        </ul>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
