'use client';

import { useQuery, type UseQueryOptions } from '@tanstack/react-query';

import { apiFetch } from '@/lib/api/client';
import { adminPaths, withQuery } from '@/lib/api/endpoints';
import { queryKeys } from '@/lib/api/hooks/queryKeys';
import type { AuditLogListOut, AuditLogListQuery } from '@/lib/api/types';

export function useAuditList(
  params: AuditLogListQuery,
  options?: Partial<UseQueryOptions<AuditLogListOut>>,
) {
  return useQuery({
    queryKey: queryKeys.audit.list(params),
    queryFn: () =>
      apiFetch<AuditLogListOut>(withQuery(adminPaths.audit, params)),
    placeholderData: (prev) => prev,
    ...options,
  });
}
