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
  TeamCreateIn,
  TeamListOut,
  TeamListQuery,
  TeamOut,
  TeamUpdateIn,
  UUID,
} from '@/lib/api/types';

export function useTeamsList(
  params: TeamListQuery,
  options?: Partial<UseQueryOptions<TeamListOut>>,
) {
  return useQuery({
    queryKey: queryKeys.teams.list(params),
    queryFn: () => apiFetch<TeamListOut>(withQuery(adminPaths.teams, params)),
    placeholderData: (prev) => prev,
    ...options,
  });
}

export function useTeam(id: UUID | undefined) {
  return useQuery({
    queryKey: queryKeys.teams.detail(id ?? ''),
    queryFn: () => apiFetch<TeamOut>(adminPaths.team(id as UUID)),
    enabled: !!id,
  });
}

export function useTeamCreate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: TeamCreateIn) =>
      apiFetch<TeamOut>(adminPaths.teams, { method: 'POST', body }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.teams.all });
    },
  });
}

export function useTeamUpdate(id: UUID) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: TeamUpdateIn) =>
      apiFetch<TeamOut>(adminPaths.team(id), { method: 'PATCH', body }),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.teams.detail(id), data);
      qc.invalidateQueries({ queryKey: queryKeys.teams.all });
    },
  });
}

export function useTeamArchive(id: UUID) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch(adminPaths.teamArchive(id), { method: 'POST' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.teams.all });
    },
  });
}

export function useTeamUnarchive(id: UUID) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch(adminPaths.teamUnarchive(id), { method: 'POST' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.teams.all });
    },
  });
}
