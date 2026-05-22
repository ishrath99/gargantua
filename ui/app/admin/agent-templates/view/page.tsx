'use client';

import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { Suspense } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { ErrorBlock } from '@/components/admin/ErrorBlock';
import { PageHeader } from '@/components/admin/PageHeader';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/Card';
import { LoadingBlock } from '@/components/ui/Spinner';
import { useAgentTemplate } from '@/lib/api/hooks/useAgents';

/**
 * Read-only template detail view.
 *
 * Why a query-string parameter instead of a route segment: this app
 * is shipped as a static export (``next.config.mjs`` has
 * ``output: 'export'``), and dynamic route segments would force us
 * to enumerate every slug at build time via ``generateStaticParams``.
 * Since templates are loaded from the backend at runtime, we'd have
 * no list to enumerate — a query-string-keyed single page sidesteps
 * the constraint entirely while still giving us a shareable URL.
 */
function TemplateDetail() {
  const sp = useSearchParams();
  const slug = sp?.get('slug') ?? '';
  const item = useAgentTemplate(slug || undefined);

  return (
    <>
      <PageHeader
        title={item.data?.name ?? slug ?? 'Template'}
        description={item.data?.description ?? undefined}
        breadcrumbs={[
          { label: 'Admin', href: '/admin' },
          { label: 'Agent templates', href: '/admin/agent-templates' },
          { label: slug || '—' },
        ]}
        actions={
          slug ? (
            <Link href={`/admin/agents/new?template=${encodeURIComponent(slug)}`}>
              <Button>New agent from template</Button>
            </Link>
          ) : null
        }
      />

      {!slug ? (
        <ErrorBlock
          error="Missing ?slug=… query parameter."
          className="mb-4"
        />
      ) : null}
      {item.error ? <ErrorBlock error={item.error} className="mb-4" /> : null}

      {item.isLoading ? (
        <LoadingBlock />
      ) : item.data ? (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <Card className="lg:col-span-1">
            <CardHeader>
              <CardTitle className="text-sm font-medium text-neutral-500">
                Metadata
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <Row
                label="Slug"
                value={<code className="font-mono text-xs">{item.data.slug}</code>}
              />
              <Row
                label="Model"
                value={<Badge variant="outline">{item.data.model}</Badge>}
              />
              <Row
                label="Suggested servers"
                value={
                  item.data.suggested_mcp_server_type_slugs.length === 0 ? (
                    <span className="text-neutral-400">—</span>
                  ) : (
                    <div className="flex flex-wrap gap-1">
                      {item.data.suggested_mcp_server_type_slugs.map((s) => (
                        <Badge key={s} variant="secondary">
                          {s}
                        </Badge>
                      ))}
                    </div>
                  )
                }
              />
              <Row
                label="Agent config"
                value={
                  Object.keys(item.data.agent_config).length === 0 ? (
                    <span className="text-neutral-400">empty</span>
                  ) : (
                    <pre className="overflow-x-auto rounded bg-neutral-100 p-2 text-xs dark:bg-neutral-900">
                      {JSON.stringify(item.data.agent_config, null, 2)}
                    </pre>
                  )
                }
              />
            </CardContent>
          </Card>

          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle className="text-sm font-medium text-neutral-500">
                Instructions
              </CardTitle>
            </CardHeader>
            <CardContent>
              <article className="prose prose-sm max-w-none dark:prose-invert prose-pre:bg-neutral-100 dark:prose-pre:bg-neutral-900">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {item.data.instructions}
                </ReactMarkdown>
              </article>
            </CardContent>
          </Card>
        </div>
      ) : null}
    </>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="grid grid-cols-1 gap-1">
      <dt className="text-xs uppercase tracking-wider text-neutral-500">
        {label}
      </dt>
      <dd>{value}</dd>
    </div>
  );
}

export default function AgentTemplateViewPage() {
  return (
    <Suspense fallback={<LoadingBlock />}>
      <TemplateDetail />
    </Suspense>
  );
}
