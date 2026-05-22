'use client';

import { useSearchParams } from 'next/navigation';
import { Suspense } from 'react';

import { ChatSurface } from '@/components/chat/ChatSurface';
import { ErrorBlock } from '@/components/admin/ErrorBlock';
import { LoadingBlock } from '@/components/ui/Spinner';
import { AuthGuard } from '@/components/RouteGuard';
import { useMeTeams } from '@/lib/api/hooks/usePicker';
import { runPaths } from '@/lib/api/endpoints';
import type { UUID } from '@/lib/api/types';

/** ``/chat/team?id=<uuid>`` — team-run surface. */
function TeamChatContent() {
  const params = useSearchParams();
  const id = params.get('id') as UUID | null;

  const teams = useMeTeams();

  if (id === null) {
    return (
      <main className="mx-auto max-w-3xl p-8">
        <ErrorBlock
          error={new Error('Missing ``id`` query parameter — open this page from the picker.')}
        />
      </main>
    );
  }

  if (teams.isLoading) {
    return (
      <main className="mx-auto max-w-3xl p-8">
        <LoadingBlock />
      </main>
    );
  }

  if (teams.error) {
    return (
      <main className="mx-auto max-w-3xl p-8">
        <ErrorBlock error={teams.error} />
      </main>
    );
  }

  const team = teams.data?.items.find((t) => t.id === id);
  if (team === undefined) {
    return (
      <main className="mx-auto max-w-3xl p-8">
        <ErrorBlock
          error={new Error(`Team ${id} isn't available to you.  Pick one from the list.`)}
        />
      </main>
    );
  }

  return (
    <ChatSurface
      runUrl={runPaths.teamRun(id)}
      title={team.name}
      subtitle={`${team.mode} • ${team.member_agent_ids.length} member${team.member_agent_ids.length === 1 ? '' : 's'}`}
    />
  );
}

export default function TeamChatPage() {
  return (
    <AuthGuard>
      <Suspense fallback={<LoadingBlock />}>
        <TeamChatContent />
      </Suspense>
    </AuthGuard>
  );
}
