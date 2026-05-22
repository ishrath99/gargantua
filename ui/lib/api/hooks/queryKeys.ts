/**
 * Central registry of TanStack Query keys.
 *
 * Conventions:
 *   * Top-level namespace mirrors the URL path's first segment
 *     (``adminMcpServerTypes`` ≈ ``/admin/mcp-server-types``).
 *   * ``all`` is the root tuple — invalidating it nukes every cached
 *     read for the entity, which is what mutations want.
 *   * ``list(params)`` and ``detail(id)`` produce narrower tuples for
 *     more surgical invalidation when we care to be specific.
 *
 * Why centralise: typos in query keys silently break cache invalidation
 * (the mutation works, but the list doesn't refresh).  Forcing every
 * hook through these helpers makes that class of bug a compile error.
 */

import type {
  AgentListQuery,
  AuditLogListQuery,
  MCPServerChildResourceListQuery,
  MCPServerListQuery,
  MCPServerTypeListQuery,
  TeamListQuery,
  UUID,
  UserListQuery,
} from '@/lib/api/types';

export const queryKeys = {
  // Auth
  me: () => ['auth', 'me'] as const,

  // Users
  users: {
    all: ['admin', 'users'] as const,
    list: (params: UserListQuery) =>
      ['admin', 'users', 'list', params] as const,
    detail: (id: UUID) => ['admin', 'users', 'detail', id] as const,
  },

  // Audit
  audit: {
    all: ['admin', 'audit'] as const,
    list: (params: AuditLogListQuery) =>
      ['admin', 'audit', 'list', params] as const,
  },

  // MCP server types (catalog)
  catalog: {
    all: ['admin', 'catalog'] as const,
    list: (params: MCPServerTypeListQuery) =>
      ['admin', 'catalog', 'list', params] as const,
    detail: (id: UUID) => ['admin', 'catalog', 'detail', id] as const,
  },

  // MCP servers
  servers: {
    all: ['admin', 'mcp-servers'] as const,
    list: (params: MCPServerListQuery) =>
      ['admin', 'mcp-servers', 'list', params] as const,
    detail: (id: UUID) => ['admin', 'mcp-servers', 'detail', id] as const,
  },

  // Child resources (scoped under a server)
  childResources: {
    all: (serverId: UUID) =>
      ['admin', 'mcp-servers', serverId, 'children'] as const,
    list: (serverId: UUID, params: MCPServerChildResourceListQuery) =>
      [
        'admin',
        'mcp-servers',
        serverId,
        'children',
        'list',
        params,
      ] as const,
    detail: (serverId: UUID, childId: UUID) =>
      ['admin', 'mcp-servers', serverId, 'children', 'detail', childId] as const,
  },

  // Agents
  agents: {
    all: ['admin', 'agents'] as const,
    list: (params: AgentListQuery) =>
      ['admin', 'agents', 'list', params] as const,
    detail: (id: UUID) => ['admin', 'agents', 'detail', id] as const,
    templates: ['admin', 'agent-templates'] as const,
    template: (slug: string) =>
      ['admin', 'agent-templates', slug] as const,
  },

  // Teams
  teams: {
    all: ['admin', 'teams'] as const,
    list: (params: TeamListQuery) => ['admin', 'teams', 'list', params] as const,
    detail: (id: UUID) => ['admin', 'teams', 'detail', id] as const,
  },

  // MCP cache (runtime)
  mcpCache: {
    all: ['admin', 'mcp-cache'] as const,
  },

  // /me/* — runnable surface for the chat picker (PR 17).
  // ``me`` at the top is the auth-me ping; ``picker`` is the
  // user-visible catalog of agents + teams a caller can run.
  picker: {
    agents: ['me', 'agents'] as const,
    teams: ['me', 'teams'] as const,
  },
};
