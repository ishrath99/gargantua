/**
 * TanStack Query hooks for the chat picker.
 *
 * The ``/me/agents`` and ``/me/teams`` endpoints return the trimmed
 * projection of runnable agents/teams the caller is allowed to use.
 * Unlike the admin lists, they are *not* paginated — every authed
 * user gets the full set in one shot.
 *
 * We keep these queries on a moderate ``staleTime`` because the
 * picker rarely needs to be perfectly fresh (operators rarely add
 * new agents during a chat session).  Mutations on the admin side
 * still invalidate ``picker.agents`` / ``picker.teams`` for the rare
 * tab-still-open case.
 */

import { useQuery } from '@tanstack/react-query';

import { apiFetch } from '@/lib/api/client';
import { mePaths } from '@/lib/api/endpoints';
import { queryKeys } from '@/lib/api/hooks/queryKeys';
import type { MeAgentListOut, MeTeamListOut } from '@/lib/api/types';

const STALE_MS = 30_000;

export function useMeAgents() {
  return useQuery({
    queryKey: queryKeys.picker.agents,
    queryFn: () => apiFetch<MeAgentListOut>(mePaths.agents),
    staleTime: STALE_MS,
  });
}

export function useMeTeams() {
  return useQuery({
    queryKey: queryKeys.picker.teams,
    queryFn: () => apiFetch<MeTeamListOut>(mePaths.teams),
    staleTime: STALE_MS,
  });
}
