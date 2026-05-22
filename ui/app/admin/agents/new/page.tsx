'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense } from 'react';

import { AgentForm } from '@/components/admin/AgentForm';
import { ErrorBlock } from '@/components/admin/ErrorBlock';
import { PageHeader } from '@/components/admin/PageHeader';
import { LoadingBlock } from '@/components/ui/Spinner';
import { useAgentTemplate } from '@/lib/api/hooks/useAgents';
import { useAgentCreate } from '@/lib/api/hooks/useAgents';
import type { AgentCreateIn } from '@/lib/api/types';

function AgentNewInner() {
  const router = useRouter();
  const sp = useSearchParams();
  const templateSlug = sp?.get('template') ?? '';
  const create = useAgentCreate();
  const template = useAgentTemplate(templateSlug || undefined);

  // Wait for template hydration before mounting the form so the
  // initial state captures the preset values.  This avoids the
  // "form mounts with empty state, then re-renders" flicker.
  if (templateSlug && template.isLoading) {
    return (
      <>
        <PageHeader
          title="New agent"
          description="Loading template…"
          breadcrumbs={[
            { label: 'Admin', href: '/admin' },
            { label: 'Agents', href: '/admin/agents' },
            { label: 'New' },
          ]}
        />
        <LoadingBlock />
      </>
    );
  }

  const preset = template.data
    ? {
        name: template.data.name,
        description: template.data.description,
        model: template.data.model,
        instructions: template.data.instructions,
        agent_config: template.data.agent_config,
      }
    : undefined;

  return (
    <>
      <PageHeader
        title="New agent"
        description={
          preset
            ? `Pre-filled from template "${template.data?.slug}".`
            : undefined
        }
        breadcrumbs={[
          { label: 'Admin', href: '/admin' },
          { label: 'Agents', href: '/admin/agents' },
          { label: preset ? `New from ${template.data?.slug}` : 'New' },
        ]}
      />
      {template.error ? <ErrorBlock error={template.error} className="mb-4" /> : null}
      <AgentForm
        mode="create"
        preset={preset}
        submitting={create.isPending}
        error={create.error}
        onCancel={() => router.push('/admin/agents')}
        onSubmit={async (value) => {
          const out = await create.mutateAsync(value as AgentCreateIn);
          router.push(`/admin/agents/edit?id=${encodeURIComponent(out.id)}`);
        }}
      />
    </>
  );
}

export default function AgentNewPage() {
  return (
    <Suspense fallback={<LoadingBlock />}>
      <AgentNewInner />
    </Suspense>
  );
}
