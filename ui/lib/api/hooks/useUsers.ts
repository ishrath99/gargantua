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
  UserCreateIn,
  UserListOut,
  UserListQuery,
  UserOut,
  UserRoleUpdateIn,
  UUID,
} from '@/lib/api/types';

export function useUsersList(
  params: UserListQuery,
  options?: Partial<UseQueryOptions<UserListOut>>,
) {
  return useQuery({
    queryKey: queryKeys.users.list(params),
    queryFn: () => apiFetch<UserListOut>(withQuery(adminPaths.users, params)),
    placeholderData: (prev) => prev,
    ...options,
  });
}

export function useUserCreate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: UserCreateIn) =>
      apiFetch<UserOut>(adminPaths.users, { method: 'POST', body }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.users.all });
    },
  });
}

export function useUserRoleUpdate(id: UUID) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: UserRoleUpdateIn) =>
      apiFetch<UserOut>(adminPaths.userRole(id), { method: 'PATCH', body }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.users.all });
    },
  });
}

export function useUserDeactivate(id: UUID) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiFetch(adminPaths.userDeactivate(id), { method: 'POST' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.users.all });
    },
  });
}

export function useUserActivate(id: UUID) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiFetch(adminPaths.userActivate(id), { method: 'POST' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.users.all });
    },
  });
}
