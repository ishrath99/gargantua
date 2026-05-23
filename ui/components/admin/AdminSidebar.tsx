'use client';

import {
  Box,
  ClipboardList,
  FileText,
  LayoutDashboard,
  MessageSquare,
  ServerCog,
  Sparkles,
  Users,
  Users2,
  Wrench,
} from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { type ComponentType } from 'react';

import { cn } from '@/lib/utils';

interface NavItem {
  label: string;
  href: string;
  icon: ComponentType<{ className?: string }>;
}

const NAV: NavItem[] = [
  { label: 'Chat', href: '/', icon: MessageSquare },
  { label: 'Dashboard', href: '/admin', icon: LayoutDashboard },
  { label: 'Catalog', href: '/admin/catalog', icon: Box },
  { label: 'MCP servers', href: '/admin/mcp-servers', icon: ServerCog },
  { label: 'Agents', href: '/admin/agents', icon: Sparkles },
  { label: 'Teams', href: '/admin/teams', icon: Users2 },
  { label: 'Users', href: '/admin/users', icon: Users },
  { label: 'Audit log', href: '/admin/audit', icon: ClipboardList },
  { label: 'MCP cache', href: '/admin/mcp-cache', icon: Wrench },
  { label: 'Agent templates', href: '/admin/agent-templates', icon: FileText },
];

/**
 * Sticky left rail.  On narrow viewports we'd want to collapse this
 * into a hamburger; the admin UI is a desktop-first surface so we
 * accept the simplification for now (the chat UI in PR 17 gets the
 * mobile-friendly drawer treatment).
 */
export function AdminSidebar() {
  const pathname = usePathname();
  return (
    <nav
      aria-label="Admin sections"
      className={cn(
        'sticky top-0 flex h-screen w-56 shrink-0 flex-col gap-1 overflow-y-auto border-r border-neutral-200 p-3 text-sm',
        'dark:border-neutral-800',
      )}
    >
      <div className="px-2 pb-2 pt-1 text-xs font-semibold uppercase tracking-wider text-neutral-500">
        gargantua
      </div>
      {NAV.map((item) => {
        const Icon = item.icon;
        // Active-detection is per-item-shape, not just startsWith, because
        // ``/`` (Chat) is a prefix of every URL and ``/admin`` is a prefix
        // of every nested admin URL.  Both need exact matches; only the
        // deeper /admin/<section> links should hi-light on descendants.
        const active =
          item.href === '/'
            ? pathname === '/'
            : item.href === '/admin'
              ? pathname === '/admin' || pathname === '/admin/'
              : pathname?.startsWith(item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            className={cn(
              'flex items-center gap-2 rounded-md px-2 py-1.5 transition-colors',
              active
                ? 'bg-neutral-900 text-neutral-50 dark:bg-neutral-100 dark:text-neutral-900'
                : 'text-neutral-700 hover:bg-neutral-100 dark:text-neutral-300 dark:hover:bg-neutral-900',
            )}
          >
            <Icon className="h-4 w-4 shrink-0" aria-hidden />
            <span className="truncate">{item.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
