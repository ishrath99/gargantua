import type { Metadata } from 'next';
import type { ReactNode } from 'react';

import { Providers } from '@/components/Providers';

import './globals.css';

export const metadata: Metadata = {
  title: 'gargantua',
  description: 'DB-first control plane for multi-agent systems and MCP servers.',
};

/**
 * Root layout for the whole UI.
 *
 * Stays a server component so Next can statically prerender the
 * HTML shell; the actual auth + query providers live one level
 * down in :component:`Providers` (client component).
 */
export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className="h-full">
      <body className="h-full">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
