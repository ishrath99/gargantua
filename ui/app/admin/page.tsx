'use client';

import Link from 'next/link';

import { PageHeader } from '@/components/admin/PageHeader';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/Card';

const SECTIONS = [
  {
    title: 'Catalog',
    href: '/admin/catalog',
    description:
      'Define MCP server *types* — the connector templates that MCP server instances are built from.',
  },
  {
    title: 'MCP servers',
    href: '/admin/mcp-servers',
    description:
      'Concrete MCP server instances (one per environment).  Each carries its own env vars and optional child resources.',
  },
  {
    title: 'Agents',
    href: '/admin/agents',
    description:
      'Single-agent definitions: model, instructions, attached MCP servers + child resources.',
  },
  {
    title: 'Teams',
    href: '/admin/teams',
    description:
      'Ordered groupings of agents in a route / coordinate / collaborate mode.',
  },
  {
    title: 'Users',
    href: '/admin/users',
    description: 'Account roster.  Promote, deactivate, reset roles.',
  },
  {
    title: 'Audit log',
    href: '/admin/audit',
    description:
      'Every mutating admin action, with before/after diff.  Read-only.',
  },
  {
    title: 'MCP cache',
    href: '/admin/mcp-cache',
    description:
      'Live snapshot of warm MCP tool handles in this process.  Evict stuck entries from here.',
  },
  {
    title: 'Agent templates',
    href: '/admin/agent-templates',
    description:
      'Read-only starter instructions shipped with the platform.  Used by "New from template" on the Agents page.',
  },
] as const;

export default function AdminDashboard() {
  return (
    <>
      <PageHeader
        title="Admin"
        description="Configure the catalog, agents, teams, and operators.  Every mutation is recorded in the audit log."
      />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {SECTIONS.map((s) => (
          <Link key={s.href} href={s.href} className="group">
            <Card className="h-full transition-colors group-hover:border-neutral-400 dark:group-hover:border-neutral-600">
              <CardHeader>
                <CardTitle className="text-base">{s.title}</CardTitle>
                <CardDescription className="text-xs">
                  {s.description}
                </CardDescription>
              </CardHeader>
              <CardContent className="pt-0 text-xs text-neutral-400">
                Open →
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>
    </>
  );
}
