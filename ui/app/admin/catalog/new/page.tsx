'use client';

import { useRouter } from 'next/navigation';

import { CatalogForm } from '@/components/admin/CatalogForm';
import { PageHeader } from '@/components/admin/PageHeader';
import { useCatalogCreate } from '@/lib/api/hooks/useCatalog';
import type { MCPServerTypeCreateIn } from '@/lib/api/types';

export default function CatalogNewPage() {
  const router = useRouter();
  const create = useCatalogCreate();

  return (
    <>
      <PageHeader
        title="New MCP server type"
        description="Define a new connector template.  Concrete MCP server instances will inherit its defaults and config schema."
        breadcrumbs={[
          { label: 'Admin', href: '/admin' },
          { label: 'Catalog', href: '/admin/catalog' },
          { label: 'New' },
        ]}
      />
      <CatalogForm
        mode="create"
        onCancel={() => router.push('/admin/catalog')}
        submitting={create.isPending}
        error={create.error}
        onSubmit={async (value) => {
          const out = await create.mutateAsync(value as MCPServerTypeCreateIn);
          router.push(`/admin/catalog/edit?id=${encodeURIComponent(out.id)}`);
        }}
      />
    </>
  );
}
