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
  AgentCreateIn,
  AgentListOut,
  AgentListQuery,
  AgentOut,
  AgentTemplateListOut,
  AgentTemplateOut,
  AgentUpdateIn,
  UUID,
} from '@/lib/api/types';

export function useAgentsList(
  params: AgentListQuery,
  options?: Partial<UseQueryOptions<AgentListOut>>,
) {
  return useQuery({
    queryKey: queryKeys.agents.list(params),
    queryFn: () => apiFetch<AgentListOut>(withQuery(adminPaths.agents, params)),
    placeholderData: (prev) => prev,
    ...options,
  });
}

export function useAgent(id: UUID | undefined) {
  return useQuery({
    queryKey: queryKeys.agents.detail(id ?? ''),
    queryFn: () => apiFetch<AgentOut>(adminPaths.agent(id as UUID)),
    enabled: !!id,
  });
}

export function useAgentCreate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AgentCreateIn) =>
      apiFetch<AgentOut>(adminPaths.agents, { method: 'POST', body }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.agents.all });
    },
  });
}

export function useAgentUpdate(id: UUID) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AgentUpdateIn) =>
      apiFetch<AgentOut>(adminPaths.agent(id), { method: 'PATCH', body }),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.agents.detail(id), data);
      qc.invalidateQueries({ queryKey: queryKeys.agents.all });
    },
  });
}

export function useAgentArchive(id: UUID) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiFetch(adminPaths.agentArchive(id), { method: 'POST' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.agents.all });
    },
  });
}

export function useAgentUnarchive(id: UUID) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiFetch(adminPaths.agentUnarchive(id), { method: 'POST' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.agents.all });
    },
  });
}

// ---------------------------------------------------------------------------
// Templates (read-only)
// ---------------------------------------------------------------------------

export function useAgentTemplatesList() {
  return useQuery({
    queryKey: queryKeys.agents.templates,
    queryFn: () => apiFetch<AgentTemplateListOut>(adminPaths.agentTemplates),
  });
}

export function useAgentTemplate(slug: string | undefined) {
  return useQuery({
    queryKey: queryKeys.agents.template(slug ?? ''),
    queryFn: () =>
      apiFetch<AgentTemplateOut>(adminPaths.agentTemplate(slug as string)),
    enabled: !!slug,
  });
}
