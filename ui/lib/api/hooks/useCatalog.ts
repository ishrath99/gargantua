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
  MCPServerTypeCreateIn,
  MCPServerTypeListOut,
  MCPServerTypeListQuery,
  MCPServerTypeOut,
  MCPServerTypeUpdateIn,
  UUID,
} from '@/lib/api/types';

export function useCatalogList(
  params: MCPServerTypeListQuery,
  options?: Partial<UseQueryOptions<MCPServerTypeListOut>>,
) {
  return useQuery({
    queryKey: queryKeys.catalog.list(params),
    queryFn: () =>
      apiFetch<MCPServerTypeListOut>(withQuery(adminPaths.mcpServerTypes, params)),
    placeholderData: (prev) => prev,
    ...options,
  });
}

export function useCatalogItem(id: UUID | undefined) {
  return useQuery({
    queryKey: queryKeys.catalog.detail(id ?? ''),
    queryFn: () =>
      apiFetch<MCPServerTypeOut>(adminPaths.mcpServerType(id as UUID)),
    enabled: !!id,
  });
}

export function useCatalogCreate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: MCPServerTypeCreateIn) =>
      apiFetch<MCPServerTypeOut>(adminPaths.mcpServerTypes, {
        method: 'POST',
        body,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.catalog.all });
    },
  });
}

export function useCatalogUpdate(id: UUID) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: MCPServerTypeUpdateIn) =>
      apiFetch<MCPServerTypeOut>(adminPaths.mcpServerType(id), {
        method: 'PATCH',
        body,
      }),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.catalog.detail(id), data);
      qc.invalidateQueries({ queryKey: queryKeys.catalog.all });
    },
  });
}

export function useCatalogArchive(id: UUID) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiFetch(adminPaths.mcpServerTypeArchive(id), { method: 'POST' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.catalog.all });
    },
  });
}

export function useCatalogUnarchive(id: UUID) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiFetch(adminPaths.mcpServerTypeUnarchive(id), { method: 'POST' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.catalog.all });
    },
  });
}
