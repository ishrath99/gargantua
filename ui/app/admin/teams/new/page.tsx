'use client';

import { useRouter } from 'next/navigation';

import { PageHeader } from '@/components/admin/PageHeader';
import { TeamForm } from '@/components/admin/TeamForm';
import { useTeamCreate } from '@/lib/api/hooks/useTeams';
import type { TeamCreateIn } from '@/lib/api/types';

export default function TeamNewPage() {
  const router = useRouter();
  const create = useTeamCreate();

  return (
    <>
      <PageHeader
        title="New team"
        description="Group existing agents into a route / coordinate / collaborate flow."
        breadcrumbs={[
          { label: 'Admin', href: '/admin' },
          { label: 'Teams', href: '/admin/teams' },
          { label: 'New' },
        ]}
      />
      <TeamForm
        mode="create"
        submitting={create.isPending}
        error={create.error}
        onCancel={() => router.push('/admin/teams')}
        onSubmit={async (value) => {
          const out = await create.mutateAsync(value as TeamCreateIn);
          router.push(`/admin/teams/edit?id=${encodeURIComponent(out.id)}`);
        }}
      />
    </>
  );
}
