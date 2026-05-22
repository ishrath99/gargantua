'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense } from 'react';

import { CatalogForm } from '@/components/admin/CatalogForm';
import { ErrorBlock } from '@/components/admin/ErrorBlock';
import { PageHeader } from '@/components/admin/PageHeader';
import { LoadingBlock } from '@/components/ui/Spinner';
import { useCatalogItem, useCatalogUpdate } from '@/lib/api/hooks/useCatalog';
import type { MCPServerTypeUpdateIn, UUID } from '@/lib/api/types';

function CatalogEditInner() {
  const router = useRouter();
  const sp = useSearchParams();
  const id = (sp?.get('id') ?? '') as UUID;
  const item = useCatalogItem(id || undefined);
  const update = useCatalogUpdate(id);

  return (
    <>
      <PageHeader
        title={item.data?.name ?? 'Catalog type'}
        description={item.data?.description ?? undefined}
        breadcrumbs={[
          { label: 'Admin', href: '/admin' },
          { label: 'Catalog', href: '/admin/catalog' },
          { label: item.data?.slug ?? id },
        ]}
      />

      {!id ? (
        <ErrorBlock error="Missing ?id=… query parameter." className="mb-4" />
      ) : null}
      {item.error ? <ErrorBlock error={item.error} className="mb-4" /> : null}

      {item.isLoading ? (
        <LoadingBlock />
      ) : item.data ? (
        <CatalogForm
          mode="edit"
          initial={item.data}
          onCancel={() => router.push('/admin/catalog')}
          submitting={update.isPending}
          error={update.error}
          onSubmit={async (value) => {
            await update.mutateAsync(value as MCPServerTypeUpdateIn);
          }}
        />
      ) : null}
    </>
  );
}

export default function CatalogEditPage() {
  return (
    <Suspense fallback={<LoadingBlock />}>
      <CatalogEditInner />
    </Suspense>
  );
}
