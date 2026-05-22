'use client';

import Link from 'next/link';

import { ErrorBlock } from '@/components/admin/ErrorBlock';
import { PageHeader } from '@/components/admin/PageHeader';
import { Badge } from '@/components/ui/Badge';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/Card';
import { LoadingBlock } from '@/components/ui/Spinner';
import { useAgentTemplatesList } from '@/lib/api/hooks/useAgents';

/**
 * Read-only catalog of agent templates.  Each is a markdown
 * instructions blob seeded by the platform — admins use them to
 * pre-fill the "New from template" flow on the Agents page.
 */
export default function AgentTemplatesPage() {
  const list = useAgentTemplatesList();

  return (
    <>
      <PageHeader
        title="Agent templates"
        description="Starter instructions shipped with gargantua.  Each template is a markdown blob you can stamp into a new agent and edit freely from there."
        breadcrumbs={[
          { label: 'Admin', href: '/admin' },
          { label: 'Agent templates' },
        ]}
      />

      {list.error ? <ErrorBlock error={list.error} className="mb-4" /> : null}

      {list.isLoading ? (
        <LoadingBlock />
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {(list.data?.items ?? []).map((t) => (
            <Link
              key={t.slug}
              href={`/admin/agent-templates/view?slug=${encodeURIComponent(t.slug)}`}
              className="group"
            >
              <Card className="h-full transition-colors group-hover:border-neutral-400 dark:group-hover:border-neutral-600">
                <CardHeader>
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <CardTitle className="text-base">{t.name}</CardTitle>
                      <CardDescription className="font-mono text-xs">
                        {t.slug}
                      </CardDescription>
                    </div>
                    <Badge variant="outline" className="font-mono text-xs">
                      {t.model}
                    </Badge>
                  </div>
                </CardHeader>
                <CardContent className="space-y-2 text-sm">
                  {t.description ? (
                    <p className="text-neutral-600 dark:text-neutral-300">
                      {t.description}
                    </p>
                  ) : null}
                  {t.suggested_mcp_server_type_slugs.length > 0 ? (
                    <div className="flex flex-wrap gap-1 pt-1">
                      {t.suggested_mcp_server_type_slugs.map((slug) => (
                        <Badge key={slug} variant="secondary" className="text-xs">
                          {slug}
                        </Badge>
                      ))}
                    </div>
                  ) : null}
                </CardContent>
              </Card>
            </Link>
          ))}
          {list.data && list.data.items.length === 0 ? (
            <p className="text-sm text-neutral-500">No templates seeded.</p>
          ) : null}
        </div>
      )}
    </>
  );
}
