'use client';

/**
 * Chat picker — pick an agent or a team to talk to.
 *
 * Lists everything the caller can run (``GET /me/agents`` +
 * ``GET /me/teams``).  Cards are grouped by kind and link straight
 * to the chat surface; clicking one mints a fresh chat tab.
 */

import { Bot, Users2 } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';

import { ErrorBlock } from '@/components/admin/ErrorBlock';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/Card';
import { LoadingBlock } from '@/components/ui/Spinner';
import { AuthGuard } from '@/components/RouteGuard';
import { useMeAgents, useMeTeams } from '@/lib/api/hooks/usePicker';
import { useAuth } from '@/lib/auth/context';
import { cn } from '@/lib/utils';

function PickerContent() {
  const router = useRouter();
  const { logout, user } = useAuth();
  const agents = useMeAgents();
  const teams = useMeTeams();

  const isLoading = agents.isLoading || teams.isLoading;
  const error = agents.error ?? teams.error;
  const agentItems = agents.data?.items ?? [];
  const teamItems = teams.data?.items ?? [];

  return (
    <main className="mx-auto flex min-h-screen max-w-5xl flex-col gap-6 p-8">
      <header className="flex items-center justify-between border-b border-neutral-200 pb-4 dark:border-neutral-800">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Chat</h1>
          <p className="text-sm text-neutral-500">
            Signed in as <span className="font-mono">{user?.username ?? '…'}</span>.
            Pick an agent or team to start a conversation.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href="/"
            className="rounded border border-neutral-300 px-3 py-1.5 text-sm font-medium hover:bg-neutral-50 dark:border-neutral-700 dark:hover:bg-neutral-900"
          >
            Home
          </Link>
          <button
            type="button"
            onClick={() => {
              logout();
              router.replace('/login/');
            }}
            className="rounded border border-neutral-300 px-3 py-1.5 text-sm font-medium hover:bg-neutral-50 dark:border-neutral-700 dark:hover:bg-neutral-900"
          >
            Log out
          </button>
        </div>
      </header>

      {error ? <ErrorBlock error={error} /> : null}
      {isLoading ? <LoadingBlock /> : null}

      {!isLoading && !error ? (
        <>
          <Section
            icon={<Bot className="h-4 w-4" aria-hidden />}
            title="Agents"
            description="Single-agent definitions with their own tools and instructions."
            count={agentItems.length}
          >
            {agentItems.length === 0 ? (
              <EmptyHint text="No agents are available to you yet.  Ask an admin." />
            ) : (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {agentItems.map((a) => (
                  <PickerCard
                    key={a.id}
                    href={`/chat/agent/?id=${a.id}`}
                    name={a.name}
                    model={a.model}
                    description={a.description}
                    badge={`${a.mcp_server_ids.length} server${a.mcp_server_ids.length === 1 ? '' : 's'}`}
                  />
                ))}
              </div>
            )}
          </Section>

          <Section
            icon={<Users2 className="h-4 w-4" aria-hidden />}
            title="Teams"
            description="Ordered groups of agents that coordinate to answer a prompt."
            count={teamItems.length}
          >
            {teamItems.length === 0 ? (
              <EmptyHint text="No teams are available to you yet." />
            ) : (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {teamItems.map((t) => (
                  <PickerCard
                    key={t.id}
                    href={`/chat/team/?id=${t.id}`}
                    name={t.name}
                    model={t.mode}
                    description={t.description}
                    badge={`${t.member_agent_ids.length} member${t.member_agent_ids.length === 1 ? '' : 's'}`}
                  />
                ))}
              </div>
            )}
          </Section>
        </>
      ) : null}
    </main>
  );
}

function Section({
  icon,
  title,
  description,
  count,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  count: number;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-3">
      <div className="flex items-baseline justify-between">
        <h2 className="flex items-center gap-2 text-lg font-medium">
          {icon}
          {title}
          <span className="rounded bg-neutral-100 px-1.5 py-0.5 text-[10px] font-mono text-neutral-500 dark:bg-neutral-800 dark:text-neutral-400">
            {count}
          </span>
        </h2>
        <p className="text-xs text-neutral-500">{description}</p>
      </div>
      {children}
    </section>
  );
}

function PickerCard({
  href,
  name,
  model,
  description,
  badge,
}: {
  href: string;
  name: string;
  model: string;
  description: string | null;
  badge: string;
}) {
  return (
    <Link href={href} className="group">
      <Card
        className={cn(
          'h-full transition-colors',
          'group-hover:border-neutral-400 dark:group-hover:border-neutral-600',
        )}
      >
        <CardHeader className="space-y-1">
          <CardTitle className="flex items-center justify-between gap-2 text-sm">
            <span className="truncate">{name}</span>
            <span className="rounded bg-neutral-100 px-1.5 py-0.5 text-[10px] font-mono text-neutral-500 dark:bg-neutral-800 dark:text-neutral-400">
              {badge}
            </span>
          </CardTitle>
          <CardDescription className="font-mono text-[11px] text-neutral-500">
            {model}
          </CardDescription>
        </CardHeader>
        <CardContent className="pt-0 text-xs text-neutral-600 dark:text-neutral-300">
          <p className="line-clamp-3">
            {description ?? <span className="italic text-neutral-400">No description.</span>}
          </p>
        </CardContent>
      </Card>
    </Link>
  );
}

function EmptyHint({ text }: { text: string }) {
  return (
    <div className="rounded-md border border-dashed border-neutral-300 bg-neutral-50 p-6 text-center text-sm text-neutral-500 dark:border-neutral-700 dark:bg-neutral-900/40">
      {text}
    </div>
  );
}

export default function ChatPickerPage() {
  return (
    <AuthGuard>
      <PickerContent />
    </AuthGuard>
  );
}
