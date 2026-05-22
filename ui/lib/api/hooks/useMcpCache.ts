'use client';

import {
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';

import { apiFetch } from '@/lib/api/client';
import { adminPaths } from '@/lib/api/endpoints';
import { queryKeys } from '@/lib/api/hooks/queryKeys';
import type {
  MCPCacheEvictOut,
  MCPCacheListOut,
  UUID,
} from '@/lib/api/types';

export function useMcpCacheList() {
  return useQuery({
    queryKey: queryKeys.mcpCache.all,
    queryFn: () => apiFetch<MCPCacheListOut>(adminPaths.mcpCache),
    // Tight cadence — the cache is volatile.  Operators are usually
    // staring at this page; a 5s background refetch is fine.
    refetchInterval: 5_000,
  });
}

export function useMcpCacheEvict() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (serverId: UUID) =>
      apiFetch<MCPCacheEvictOut>(adminPaths.mcpCacheEvict(serverId), {
        method: 'POST',
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.mcpCache.all });
    },
  });
}
