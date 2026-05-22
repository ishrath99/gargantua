'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense } from 'react';

import { AgentForm } from '@/components/admin/AgentForm';
import { ErrorBlock } from '@/components/admin/ErrorBlock';
import { PageHeader } from '@/components/admin/PageHeader';
import { LoadingBlock } from '@/components/ui/Spinner';
import { useAgent, useAgentUpdate } from '@/lib/api/hooks/useAgents';
import type { AgentUpdateIn, UUID } from '@/lib/api/types';

function AgentEditInner() {
  const router = useRouter();
  const sp = useSearchParams();
  const id = (sp?.get('id') ?? '') as UUID;

  const agent = useAgent(id || undefined);
  const update = useAgentUpdate(id);

  return (
    <>
      <PageHeader
        title={agent.data?.name ?? 'Agent'}
        description={agent.data?.description ?? undefined}
        breadcrumbs={[
          { label: 'Admin', href: '/admin' },
          { label: 'Agents', href: '/admin/agents' },
          { label: agent.data?.name ?? id },
        ]}
      />

      {!id ? (
        <ErrorBlock error="Missing ?id=… query parameter." className="mb-4" />
      ) : null}
      {agent.error ? <ErrorBlock error={agent.error} className="mb-4" /> : null}

      {agent.isLoading ? <LoadingBlock /> : null}

      {agent.data ? (
        <AgentForm
          mode="edit"
          initial={agent.data}
          submitting={update.isPending}
          error={update.error}
          onCancel={() => router.push('/admin/agents')}
          onSubmit={async (value) => {
            await update.mutateAsync(value as AgentUpdateIn);
          }}
        />
      ) : null}
    </>
  );
}

export default function AgentEditPage() {
  return (
    <Suspense fallback={<LoadingBlock />}>
      <AgentEditInner />
    </Suspense>
  );
}
