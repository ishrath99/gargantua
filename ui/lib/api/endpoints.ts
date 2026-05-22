/**
 * Single source of truth for backend URL paths.
 *
 * Why this exists: by centralising every URL string here, the
 * codebase audit for "does the UI still match the backend's HTTP
 * surface?" reduces to reading one file.  The query-page hooks in
 * ``lib/api/hooks/*`` call into these helpers; you should never see
 * a bare ``/admin/...`` string anywhere else in ``ui/``.
 *
 * No fetch logic lives here — that's ``lib/api/client.ts``'s job.
 */

import type { UUID } from '@/lib/api/types';

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

export const authPaths = {
  login: '/auth/login',
  refresh: '/auth/refresh',
  me: '/auth/me',
} as const;

// ---------------------------------------------------------------------------
// Admin
// ---------------------------------------------------------------------------

export const adminPaths = {
  // Users
  users: '/admin/users',
  user: (id: UUID) => `/admin/users/${id}`,
  userRole: (id: UUID) => `/admin/users/${id}/role`,
  userDeactivate: (id: UUID) => `/admin/users/${id}/deactivate`,
  userActivate: (id: UUID) => `/admin/users/${id}/activate`,

  // Audit
  audit: '/admin/audit',
  auditEntry: (id: number) => `/admin/audit/${id}`,

  // MCP server types (catalog)
  mcpServerTypes: '/admin/mcp-server-types',
  mcpServerType: (id: UUID) => `/admin/mcp-server-types/${id}`,
  mcpServerTypeArchive: (id: UUID) => `/admin/mcp-server-types/${id}/archive`,
  mcpServerTypeUnarchive: (id: UUID) =>
    `/admin/mcp-server-types/${id}/unarchive`,

  // MCP servers
  mcpServers: '/admin/mcp-servers',
  mcpServer: (id: UUID) => `/admin/mcp-servers/${id}`,
  mcpServerArchive: (id: UUID) => `/admin/mcp-servers/${id}/archive`,
  mcpServerUnarchive: (id: UUID) => `/admin/mcp-servers/${id}/unarchive`,

  // Child resources (nested under a server)
  childResources: (serverId: UUID) =>
    `/admin/mcp-servers/${serverId}/child-resources`,
  childResource: (serverId: UUID, childId: UUID) =>
    `/admin/mcp-servers/${serverId}/child-resources/${childId}`,
  childResourceEnable: (serverId: UUID, childId: UUID) =>
    `/admin/mcp-servers/${serverId}/child-resources/${childId}/enable`,
  childResourceDisable: (serverId: UUID, childId: UUID) =>
    `/admin/mcp-servers/${serverId}/child-resources/${childId}/disable`,

  // Agents
  agents: '/admin/agents',
  agent: (id: UUID) => `/admin/agents/${id}`,
  agentArchive: (id: UUID) => `/admin/agents/${id}/archive`,
  agentUnarchive: (id: UUID) => `/admin/agents/${id}/unarchive`,
  agentTemplates: '/admin/agent-templates',
  agentTemplate: (slug: string) =>
    `/admin/agent-templates/${encodeURIComponent(slug)}`,

  // Teams
  teams: '/admin/teams',
  team: (id: UUID) => `/admin/teams/${id}`,
  teamArchive: (id: UUID) => `/admin/teams/${id}/archive`,
  teamUnarchive: (id: UUID) => `/admin/teams/${id}/unarchive`,

  // MCP cache (runtime introspection)
  mcpCache: '/admin/mcp-cache',
  mcpCacheEvict: (serverId: UUID) => `/admin/mcp-cache/${serverId}/evict`,
} as const;

// ---------------------------------------------------------------------------
// /me (user-facing pickers; used in PR 17)
// ---------------------------------------------------------------------------

export const mePaths = {
  agents: '/me/agents',
  teams: '/me/teams',
} as const;

// ---------------------------------------------------------------------------
// Runtime (used in PR 17)
// ---------------------------------------------------------------------------

export const runPaths = {
  agentRun: (id: UUID) => `/v1/agents/${id}/runs`,
  teamRun: (id: UUID) => `/v1/teams/${id}/runs`,
} as const;

// ---------------------------------------------------------------------------
// Query-string serialiser
// ---------------------------------------------------------------------------

/**
 * Build a query string for the typed list-query objects in ``types.ts``.
 *
 * Rules:
 *   * Skip ``undefined`` and ``null``.
 *   * Booleans serialise as ``"true"`` / ``"false"`` (FastAPI's
 *     default).
 *   * Arrays are emitted as repeated keys, e.g. ``?ids=a&ids=b``.
 *   * Numbers are coerced via ``String``.
 *
 * Returns a string starting with ``"?"`` when there's at least one
 * param, or ``""`` when the input has nothing useful to send.
 *
 * The ``object`` (vs. ``Record<string, unknown>``) parameter type is
 * deliberate: it accepts the typed ``*ListQuery`` interfaces in
 * ``types.ts`` without requiring every one of them to declare an
 * index signature.  ``Object.entries`` does the right thing on
 * arbitrary objects.
 */
export function buildQueryString(params: object | undefined): string {
  if (!params) return '';
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null) continue;
    if (Array.isArray(value)) {
      for (const v of value) {
        if (v === undefined || v === null) continue;
        search.append(key, String(v));
      }
    } else {
      search.append(key, String(value));
    }
  }
  const s = search.toString();
  return s ? `?${s}` : '';
}

/** Convenience: append the query-string form of ``params`` to ``path``. */
export function withQuery(path: string, params: object | undefined): string {
  return `${path}${buildQueryString(params)}`;
}
