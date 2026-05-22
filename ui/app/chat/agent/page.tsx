'use client';

import { useSearchParams } from 'next/navigation';
import { Suspense } from 'react';

import { ChatSurface } from '@/components/chat/ChatSurface';
import { ErrorBlock } from '@/components/admin/ErrorBlock';
import { LoadingBlock } from '@/components/ui/Spinner';
import { AuthGuard } from '@/components/RouteGuard';
import { useMeAgents } from '@/lib/api/hooks/usePicker';
import { runPaths } from '@/lib/api/endpoints';
import type { UUID } from '@/lib/api/types';

/**
 * Per-agent chat route at ``/chat/agent?id=<uuid>``.
 *
 * Why query-string instead of a path segment?  Next's static export
 * needs to know all dynamic routes at build time; query-strings are
 * fully client-side and don't require pre-rendering every UUID.
 */
function AgentChatContent() {
  const params = useSearchParams();
  const id = params.get('id') as UUID | null;

  const agents = useMeAgents();

  if (id === null) {
    return (
      <main className="mx-auto max-w-3xl p-8">
        <ErrorBlock
          error={new Error('Missing ``id`` query parameter — open this page from the picker.')}
        />
      </main>
    );
  }

  if (agents.isLoading) {
    return (
      <main className="mx-auto max-w-3xl p-8">
        <LoadingBlock />
      </main>
    );
  }

  if (agents.error) {
    return (
      <main className="mx-auto max-w-3xl p-8">
        <ErrorBlock error={agents.error} />
      </main>
    );
  }

  const agent = agents.data?.items.find((a) => a.id === id);
  if (agent === undefined) {
    return (
      <main className="mx-auto max-w-3xl p-8">
        <ErrorBlock
          error={new Error(`Agent ${id} isn't available to you.  Pick one from the list.`)}
        />
      </main>
    );
  }

  return (
    <ChatSurface
      runUrl={runPaths.agentRun(id)}
      title={agent.name}
      subtitle={agent.model}
    />
  );
}

export default function AgentChatPage() {
  return (
    <AuthGuard>
      <Suspense fallback={<LoadingBlock />}>
        <AgentChatContent />
      </Suspense>
    </AuthGuard>
  );
}
