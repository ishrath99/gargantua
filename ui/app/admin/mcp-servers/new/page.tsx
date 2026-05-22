'use client';

import { useRouter } from 'next/navigation';
import { useState } from 'react';

import { ErrorBlock } from '@/components/admin/ErrorBlock';
import { PageHeader } from '@/components/admin/PageHeader';
import { ServerForm } from '@/components/admin/ServerForm';
import { Button } from '@/components/ui/Button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/Card';
import { Label } from '@/components/ui/Label';
import { Select } from '@/components/ui/Select';
import { LoadingBlock } from '@/components/ui/Spinner';
import { useCatalogItem, useCatalogList } from '@/lib/api/hooks/useCatalog';
import { useServerCreate } from '@/lib/api/hooks/useServers';
import type { MCPServerCreateIn, UUID } from '@/lib/api/types';

export default function McpServerNewPage() {
  const router = useRouter();
  const [typeId, setTypeId] = useState<UUID | ''>('');
  const types = useCatalogList({
    page: 1,
    page_size: 100,
    include_archived: false,
  });
  const type = useCatalogItem(typeId || undefined);
  const create = useServerCreate();

  return (
    <>
      <PageHeader
        title="New MCP server"
        description="Pick a catalog type, then fill in the per-instance config."
        breadcrumbs={[
          { label: 'Admin', href: '/admin' },
          { label: 'MCP servers', href: '/admin/mcp-servers' },
          { label: 'New' },
        ]}
      />

      <div className="mb-4">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium text-neutral-500">
              Pick a type
            </CardTitle>
            <CardDescription>
              The type&apos;s <code>config_schema</code> drives the rest of the form.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-1">
              <Label htmlFor="type-id">Type</Label>
              <Select
                id="type-id"
                value={typeId}
                onChange={(e) => setTypeId(e.target.value as UUID | '')}
              >
                <option value="">Select a type…</option>
                {(types.data?.items ?? []).map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.slug} — {t.name}
                  </option>
                ))}
              </Select>
            </div>
            {types.error ? <ErrorBlock error={types.error} /> : null}
          </CardContent>
        </Card>
      </div>

      {typeId ? (
        type.isLoading ? (
          <LoadingBlock />
        ) : type.error ? (
          <ErrorBlock error={type.error} />
        ) : type.data ? (
          <ServerForm
            mode="create"
            type={type.data}
            submitting={create.isPending}
            error={create.error}
            onCancel={() => router.push('/admin/mcp-servers')}
            onSubmit={async (value) => {
              const out = await create.mutateAsync(value as MCPServerCreateIn);
              router.push(
                `/admin/mcp-servers/edit?id=${encodeURIComponent(out.id)}`,
              );
            }}
          />
        ) : null
      ) : (
        <div className="flex justify-end">
          <Button variant="ghost" onClick={() => router.push('/admin/mcp-servers')}>
            Cancel
          </Button>
        </div>
      )}
    </>
  );
}
