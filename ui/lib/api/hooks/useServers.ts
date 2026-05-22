'use client';

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryOptions,
} from '@tanstack/react-query';

import { apiFetch } from '@/lib/api/client';
import { adminPaths, withQuery } from '@/lib/api/endpoints';
import { queryKeys } from '@/lib/api/hooks/queryKeys';
import type {
  MCPServerChildResourceCreateIn,
  MCPServerChildResourceListOut,
  MCPServerChildResourceListQuery,
  MCPServerChildResourceOut,
  MCPServerChildResourceUpdateIn,
  MCPServerCreateIn,
  MCPServerListOut,
  MCPServerListQuery,
  MCPServerOut,
  MCPServerUpdateIn,
  UUID,
} from '@/lib/api/types';

// ---------------------------------------------------------------------------
// Servers
// ---------------------------------------------------------------------------

export function useServersList(
  params: MCPServerListQuery,
  options?: Partial<UseQueryOptions<MCPServerListOut>>,
) {
  return useQuery({
    queryKey: queryKeys.servers.list(params),
    queryFn: () =>
      apiFetch<MCPServerListOut>(withQuery(adminPaths.mcpServers, params)),
    placeholderData: (prev) => prev,
    ...options,
  });
}

export function useServer(id: UUID | undefined) {
  return useQuery({
    queryKey: queryKeys.servers.detail(id ?? ''),
    queryFn: () => apiFetch<MCPServerOut>(adminPaths.mcpServer(id as UUID)),
    enabled: !!id,
  });
}

export function useServerCreate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: MCPServerCreateIn) =>
      apiFetch<MCPServerOut>(adminPaths.mcpServers, { method: 'POST', body }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.servers.all });
    },
  });
}

export function useServerUpdate(id: UUID) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: MCPServerUpdateIn) =>
      apiFetch<MCPServerOut>(adminPaths.mcpServer(id), {
        method: 'PATCH',
        body,
      }),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.servers.detail(id), data);
      qc.invalidateQueries({ queryKey: queryKeys.servers.all });
      // Editing a server triggers a version bump → MCP cache will
      // detach orphan entries on the next /admin/mcp-cache read.
      qc.invalidateQueries({ queryKey: queryKeys.mcpCache.all });
    },
  });
}

export function useServerArchive(id: UUID) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiFetch(adminPaths.mcpServerArchive(id), { method: 'POST' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.servers.all });
      qc.invalidateQueries({ queryKey: queryKeys.mcpCache.all });
    },
  });
}

export function useServerUnarchive(id: UUID) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiFetch(adminPaths.mcpServerUnarchive(id), { method: 'POST' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.servers.all });
    },
  });
}

// ---------------------------------------------------------------------------
// Child resources (nested under a server)
// ---------------------------------------------------------------------------

export function useChildResourcesList(
  serverId: UUID | undefined,
  params: MCPServerChildResourceListQuery,
) {
  return useQuery({
    queryKey: queryKeys.childResources.list(serverId ?? '', params),
    queryFn: () =>
      apiFetch<MCPServerChildResourceListOut>(
        withQuery(adminPaths.childResources(serverId as UUID), params),
      ),
    enabled: !!serverId,
    placeholderData: (prev) => prev,
  });
}

export function useChildResourceCreate(serverId: UUID) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: MCPServerChildResourceCreateIn) =>
      apiFetch<MCPServerChildResourceOut>(adminPaths.childResources(serverId), {
        method: 'POST',
        body,
      }),
    onSuccess: () => {
      qc.invalidateQueries({
        queryKey: queryKeys.childResources.all(serverId),
      });
    },
  });
}

export function useChildResourceUpdate(serverId: UUID, childId: UUID) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: MCPServerChildResourceUpdateIn) =>
      apiFetch<MCPServerChildResourceOut>(
        adminPaths.childResource(serverId, childId),
        { method: 'PATCH', body },
      ),
    onSuccess: () => {
      qc.invalidateQueries({
        queryKey: queryKeys.childResources.all(serverId),
      });
      qc.invalidateQueries({ queryKey: queryKeys.mcpCache.all });
    },
  });
}

export function useChildResourceToggle(serverId: UUID, childId: UUID) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (enabled: boolean) =>
      apiFetch(
        enabled
          ? adminPaths.childResourceEnable(serverId, childId)
          : adminPaths.childResourceDisable(serverId, childId),
        { method: 'POST' },
      ),
    onSuccess: () => {
      qc.invalidateQueries({
        queryKey: queryKeys.childResources.all(serverId),
      });
      qc.invalidateQueries({ queryKey: queryKeys.mcpCache.all });
    },
  });
}
