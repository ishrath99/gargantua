'use client';

import { Plus } from 'lucide-react';
import { useEffect, useState } from 'react';

import { ConfirmDialog } from '@/components/admin/ConfirmDialog';
import { DataTable, type ColumnDef } from '@/components/admin/DataTable';
import { ErrorBlock } from '@/components/admin/ErrorBlock';
import { JSONField } from '@/components/admin/JSONField';
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
import { FieldError } from '@/components/ui/FieldError';
import { Input } from '@/components/ui/Input';
import { Label } from '@/components/ui/Label';
import { Spinner } from '@/components/ui/Spinner';
import {
  useChildResourceCreate,
  useChildResourceToggle,
  useChildResourceUpdate,
  useChildResourcesList,
} from '@/lib/api/hooks/useServers';
import { SECRET_PLACEHOLDER } from '@/lib/api/types';
import type {
  MCPServerChildResourceOut,
  MCPServerChildResourceUpdateIn,
  UUID,
} from '@/lib/api/types';

interface PanelProps {
  serverId: UUID;
}

/**
 * Manages the Swagger / OpenAPI child resources attached to one MCP
 * server.
 *
 * Children carry their own (encrypted) HTTP headers — those are
 * unconditionally masked on read.  The "Edit" dialog therefore uses
 * the same Replace-or-keep pattern as :class:`ServerForm` for env
 * vars: headers update is replace-all, and we don't have plaintext
 * for unchanged values.
 */
export function ChildResourcePanel({ serverId }: PanelProps) {
  const [includeDisabled, setIncludeDisabled] = useState(true);
  const list = useChildResourcesList(serverId, {
    page: 1,
    page_size: 100,
    include_disabled: includeDisabled,
  });

  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<MCPServerChildResourceOut | null>(null);
  const [toggling, setToggling] = useState<MCPServerChildResourceOut | null>(
    null,
  );

  const columns: ColumnDef<MCPServerChildResourceOut>[] = [
    {
      key: 'name',
      header: 'Name',
      cell: (r) => <span className="font-medium">{r.name}</span>,
    },
    {
      key: 'type',
      header: 'Type',
      cell: (r) => <Badge variant="outline">{r.type}</Badge>,
    },
    {
      key: 'url',
      header: 'URL',
      cell: (r) => (
        <span className="break-all font-mono text-xs">{r.url}</span>
      ),
    },
    {
      key: 'enabled',
      header: 'Enabled',
      cell: (r) =>
        r.enabled ? (
          <Badge variant="success">on</Badge>
        ) : (
          <Badge variant="warning">off</Badge>
        ),
    },
    {
      key: 'actions',
      header: '',
      cell: (r) => (
        <div className="flex items-center justify-end gap-2">
          <Button size="sm" variant="outline" onClick={() => setEditing(r)}>
            Edit
          </Button>
          <Button size="sm" variant="outline" onClick={() => setToggling(r)}>
            {r.enabled ? 'Disable' : 'Enable'}
          </Button>
        </div>
      ),
      className: 'text-right',
    },
  ];

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <input
            id="include-disabled"
            type="checkbox"
            checked={includeDisabled}
            onChange={(e) => setIncludeDisabled(e.target.checked)}
            className="h-4 w-4"
          />
          <Label htmlFor="include-disabled" className="cursor-pointer text-xs">
            Include disabled
          </Label>
        </div>
        <Button size="sm" onClick={() => setCreating(true)}>
          <Plus className="h-3.5 w-3.5" />
          Add resource
        </Button>
      </div>

      {list.error ? <ErrorBlock error={list.error} /> : null}

      <DataTable
        rows={list.data?.items}
        columns={columns}
        rowKey={(r) => r.id}
        isLoading={list.isLoading}
        page={1}
        pageSize={list.data?.items.length ?? 0}
        total={list.data?.total}
        onPageChange={() => undefined}
        emptyMessage="No child resources yet.  Add one to attach a Swagger / OpenAPI spec to this server."
      />

      <CreateChildDialog
        open={creating}
        onOpenChange={setCreating}
        serverId={serverId}
      />
      <EditChildDialog
        item={editing}
        onOpenChange={(o) => !o && setEditing(null)}
        serverId={serverId}
      />
      <ToggleChildDialog
        item={toggling}
        onOpenChange={(o) => !o && setToggling(null)}
        serverId={serverId}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Create dialog
// ---------------------------------------------------------------------------

function CreateChildDialog({
  open,
  onOpenChange,
  serverId,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  serverId: UUID;
}) {
  const create = useChildResourceCreate(serverId);
  const [name, setName] = useState('');
  const [url, setUrl] = useState('');
  const [headers, setHeaders] = useState<Record<string, unknown>>({});
  const [headersValidity, setHeadersValidity] = useState<string | undefined>();
  const [topError, setTopError] = useState<string | null>(null);

  function reset() {
    setName('');
    setUrl('');
    setHeaders({});
    setHeadersValidity(undefined);
    setTopError(null);
    create.reset();
  }
  function handleOpenChange(o: boolean) {
    if (!o) reset();
    onOpenChange(o);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setTopError(null);
    if (!name.trim()) {
      setTopError('Name is required.');
      return;
    }
    if (!url.trim()) {
      setTopError('URL is required.');
      return;
    }
    if (headersValidity) {
      setTopError(`Invalid JSON in headers: ${headersValidity}`);
      return;
    }
    await create.mutateAsync({
      type: 'swagger',
      name: name.trim(),
      url: url.trim(),
      headers,
    });
    handleOpenChange(false);
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Add child resource</DialogTitle>
          <DialogDescription>
            Attach a Swagger / OpenAPI spec.  Headers are encrypted at rest
            with the active KEK and masked on read.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="grid grid-cols-1 gap-3">
          <div className="space-y-1">
            <Label htmlFor="child-name">Name</Label>
            <Input
              id="child-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="GitHub REST"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="child-url">URL</Label>
            <Input
              id="child-url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://api.example.com/openapi.json"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="child-headers">Headers (JSON object)</Label>
            <JSONField
              id="child-headers"
              ariaLabel="Headers"
              value={headers}
              onChange={(v) => setHeaders((v as Record<string, unknown>) ?? {})}
              onValidityChange={setHeadersValidity}
              rows={4}
              placeholder='{ "Authorization": "Bearer …" }'
            />
            <FieldError message={headersValidity} />
          </div>
          {topError ? <ErrorBlock error={topError} /> : null}
          {create.error ? <ErrorBlock error={create.error} /> : null}
          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => handleOpenChange(false)}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? (
                <Spinner className="h-3.5 w-3.5 text-white" />
              ) : null}
              Create
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Edit dialog
// ---------------------------------------------------------------------------

function EditChildDialog({
  item,
  onOpenChange,
  serverId,
}: {
  item: MCPServerChildResourceOut | null;
  onOpenChange: (o: boolean) => void;
  serverId: UUID;
}) {
  // Hook needs a stable childId — fall back to an empty string when no
  // dialog is active.  The hook only runs on mutate() so this is safe.
  const update = useChildResourceUpdate(serverId, item?.id ?? '');
  const [name, setName] = useState(item?.name ?? '');
  const [url, setUrl] = useState(item?.url ?? '');
  const [headers, setHeaders] = useState<Record<string, unknown>>(
    item?.headers ?? {},
  );
  const [headersDirty, setHeadersDirty] = useState(false);
  const [headersValidity, setHeadersValidity] = useState<string | undefined>();
  const [topError, setTopError] = useState<string | null>(null);

  // Re-sync local state when the active item changes.
  useResetEffect(item, () => {
    setName(item?.name ?? '');
    setUrl(item?.url ?? '');
    setHeaders(item?.headers ?? {});
    setHeadersDirty(false);
    setHeadersValidity(undefined);
    setTopError(null);
    update.reset();
  });

  const hasMaskedHeaders = useHasMaskedValues(headers);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setTopError(null);
    if (!item) return;
    if (!name.trim()) {
      setTopError('Name is required.');
      return;
    }
    if (!url.trim()) {
      setTopError('URL is required.');
      return;
    }
    if (headersDirty && headersValidity) {
      setTopError(`Invalid JSON in headers: ${headersValidity}`);
      return;
    }
    if (headersDirty && hasMaskedHeaders) {
      setTopError(
        'Submitting would clear masked headers.  Replace the placeholder values, or revert the JSON to leave headers untouched.',
      );
      return;
    }

    const payload: MCPServerChildResourceUpdateIn = {
      name: name.trim(),
      url: url.trim(),
    };
    if (headersDirty) {
      payload.headers = headers;
    }
    await update.mutateAsync(payload);
    onOpenChange(false);
  }

  return (
    <Dialog open={!!item} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Edit child resource</DialogTitle>
          <DialogDescription>
            Headers update is replace-all.  Edit the JSON to change any header
            and replace the masked placeholders for any you want to keep.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="grid grid-cols-1 gap-3">
          <div className="space-y-1">
            <Label htmlFor="edit-child-name">Name</Label>
            <Input
              id="edit-child-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="edit-child-url">URL</Label>
            <Input
              id="edit-child-url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="edit-child-headers">Headers (JSON object)</Label>
            <JSONField
              id="edit-child-headers"
              ariaLabel="Headers"
              value={headers}
              onChange={(v) => {
                setHeaders((v as Record<string, unknown>) ?? {});
                setHeadersDirty(true);
              }}
              onValidityChange={setHeadersValidity}
              rows={5}
            />
            <FieldError message={headersValidity} />
          </div>
          {topError ? <ErrorBlock error={topError} /> : null}
          {update.error ? <ErrorBlock error={update.error} /> : null}
          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={update.isPending}>
              {update.isPending ? (
                <Spinner className="h-3.5 w-3.5 text-white" />
              ) : null}
              Save
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Toggle dialog
// ---------------------------------------------------------------------------

function ToggleChildDialog({
  item,
  onOpenChange,
  serverId,
}: {
  item: MCPServerChildResourceOut | null;
  onOpenChange: (o: boolean) => void;
  serverId: UUID;
}) {
  const toggle = useChildResourceToggle(serverId, item?.id ?? '');
  if (!item) return null;
  const willEnable = !item.enabled;
  return (
    <ConfirmDialog
      open
      onOpenChange={onOpenChange}
      title={willEnable ? 'Enable resource?' : 'Disable resource?'}
      description={
        willEnable ? (
          <span>
            The resource will be included in agent runs that request this
            server&apos;s child resources.
          </span>
        ) : (
          <span>
            The resource won&apos;t be injected into new runs.  In-flight runs
            already holding warm tools are unaffected until they finish.
          </span>
        )
      }
      confirmLabel={willEnable ? 'Enable' : 'Disable'}
      confirmVariant={willEnable ? 'default' : 'destructive'}
      onConfirm={async () => {
        await toggle.mutateAsync(willEnable);
      }}
    />
  );
}

// ---------------------------------------------------------------------------
// Tiny helpers
// ---------------------------------------------------------------------------

function useResetEffect<T>(dep: T, fn: () => void) {
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(fn, [dep]);
}

function useHasMaskedValues(record: Record<string, unknown>): boolean {
  return Object.values(record).some((v) => v === SECRET_PLACEHOLDER);
}
