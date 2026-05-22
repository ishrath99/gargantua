'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense } from 'react';

import { ChildResourcePanel } from '@/components/admin/ChildResourcePanel';
import { ErrorBlock } from '@/components/admin/ErrorBlock';
import { PageHeader } from '@/components/admin/PageHeader';
import { ServerForm } from '@/components/admin/ServerForm';
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '@/components/ui/Tabs';
import { LoadingBlock } from '@/components/ui/Spinner';
import { useCatalogItem } from '@/lib/api/hooks/useCatalog';
import { useServer, useServerUpdate } from '@/lib/api/hooks/useServers';
import type { MCPServerUpdateIn, UUID } from '@/lib/api/types';

function McpServerEditInner() {
  const router = useRouter();
  const sp = useSearchParams();
  const id = (sp?.get('id') ?? '') as UUID;
  const initialTab = sp?.get('tab') === 'children' ? 'children' : 'config';

  const server = useServer(id || undefined);
  const type = useCatalogItem(server.data?.type_id);
  const update = useServerUpdate(id);

  return (
    <>
      <PageHeader
        title={server.data?.name ?? 'MCP server'}
        description={
          server.data
            ? `${server.data.env_tag} · ${type.data?.slug ?? '—'}`
            : undefined
        }
        breadcrumbs={[
          { label: 'Admin', href: '/admin' },
          { label: 'MCP servers', href: '/admin/mcp-servers' },
          { label: server.data?.name ?? id },
        ]}
      />

      {!id ? (
        <ErrorBlock error="Missing ?id=… query parameter." className="mb-4" />
      ) : null}
      {server.error ? <ErrorBlock error={server.error} className="mb-4" /> : null}
      {type.error ? <ErrorBlock error={type.error} className="mb-4" /> : null}

      {server.isLoading || type.isLoading ? <LoadingBlock /> : null}

      {server.data && type.data ? (
        <Tabs defaultValue={initialTab}>
          <TabsList>
            <TabsTrigger value="config">Config</TabsTrigger>
            <TabsTrigger
              value="children"
              disabled={!type.data.supports_swagger_child}
              title={
                type.data.supports_swagger_child
                  ? undefined
                  : 'This type does not support Swagger child resources.'
              }
            >
              Child resources
            </TabsTrigger>
          </TabsList>
          <TabsContent value="config" className="pt-4">
            <ServerForm
              mode="edit"
              type={type.data}
              initial={server.data}
              submitting={update.isPending}
              error={update.error}
              onCancel={() => router.push('/admin/mcp-servers')}
              onSubmit={async (value) => {
                await update.mutateAsync(value as MCPServerUpdateIn);
              }}
            />
          </TabsContent>
          <TabsContent value="children" className="pt-4">
            <ChildResourcePanel serverId={server.data.id} />
          </TabsContent>
        </Tabs>
      ) : null}
    </>
  );
}

export default function McpServerEditPage() {
  return (
    <Suspense fallback={<LoadingBlock />}>
      <McpServerEditInner />
    </Suspense>
  );
}
