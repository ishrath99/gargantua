'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense } from 'react';

import { ErrorBlock } from '@/components/admin/ErrorBlock';
import { PageHeader } from '@/components/admin/PageHeader';
import { TeamForm } from '@/components/admin/TeamForm';
import { LoadingBlock } from '@/components/ui/Spinner';
import { useTeam, useTeamUpdate } from '@/lib/api/hooks/useTeams';
import type { TeamUpdateIn, UUID } from '@/lib/api/types';

function TeamEditInner() {
  const router = useRouter();
  const sp = useSearchParams();
  const id = (sp?.get('id') ?? '') as UUID;

  const team = useTeam(id || undefined);
  const update = useTeamUpdate(id);

  return (
    <>
      <PageHeader
        title={team.data?.name ?? 'Team'}
        description={team.data?.description ?? undefined}
        breadcrumbs={[
          { label: 'Admin', href: '/admin' },
          { label: 'Teams', href: '/admin/teams' },
          { label: team.data?.name ?? id },
        ]}
      />

      {!id ? (
        <ErrorBlock error="Missing ?id=… query parameter." className="mb-4" />
      ) : null}
      {team.error ? <ErrorBlock error={team.error} className="mb-4" /> : null}

      {team.isLoading ? <LoadingBlock /> : null}

      {team.data ? (
        <TeamForm
          mode="edit"
          initial={team.data}
          submitting={update.isPending}
          error={update.error}
          onCancel={() => router.push('/admin/teams')}
          onSubmit={async (value) => {
            await update.mutateAsync(value as TeamUpdateIn);
          }}
        />
      ) : null}
    </>
  );
}

export default function TeamEditPage() {
  return (
    <Suspense fallback={<LoadingBlock />}>
      <TeamEditInner />
    </Suspense>
  );
}
