'use client';

import { Plus } from 'lucide-react';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';

import { ConfirmDialog } from '@/components/admin/ConfirmDialog';
import { DataTable, type ColumnDef } from '@/components/admin/DataTable';
import { ErrorBlock } from '@/components/admin/ErrorBlock';
import { PageHeader } from '@/components/admin/PageHeader';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/Dialog';
import { FieldError } from '@/components/ui/FieldError';
import { Input } from '@/components/ui/Input';
import { Label } from '@/components/ui/Label';
import { Select } from '@/components/ui/Select';
import { Spinner } from '@/components/ui/Spinner';
import { useAuth } from '@/lib/auth/context';
import {
  useUserActivate,
  useUserCreate,
  useUserDeactivate,
  useUserRoleUpdate,
  useUsersList,
} from '@/lib/api/hooks/useUsers';
import type { UserListQuery, UserOut, UserRole } from '@/lib/api/types';
import { formatDateTime } from '@/lib/format';

const PAGE_SIZE = 25;

const userSchema = z.object({
  username: z
    .string()
    .trim()
    .min(3, 'At least 3 characters.')
    .max(64, 'At most 64 characters.')
    .regex(/^[a-zA-Z0-9._-]+$/, 'Letters, digits, dot, underscore, hyphen.'),
  password: z.string().min(8, 'At least 8 characters.'),
  role: z.enum(['admin', 'user']),
});
type UserForm = z.infer<typeof userSchema>;

export default function UsersPage() {
  const [params, setParams] = useState<UserListQuery>({
    page: 1,
    page_size: PAGE_SIZE,
    include_inactive: true,
  });
  const [createOpen, setCreateOpen] = useState(false);

  const list = useUsersList(params);

  return (
    <>
      <PageHeader
        title="Users"
        description="Account roster.  Mutations are recorded in the audit log."
        breadcrumbs={[
          { label: 'Admin', href: '/admin' },
          { label: 'Users' },
        ]}
        actions={
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="h-3.5 w-3.5" />
            New user
          </Button>
        }
      />

      <div className="mb-4 grid grid-cols-1 gap-3 md:grid-cols-3">
        <div className="space-y-1">
          <Label htmlFor="filter-role">Role</Label>
          <Select
            id="filter-role"
            value={params.role ?? ''}
            onChange={(e) =>
              setParams((p) => ({
                ...p,
                role: (e.target.value as UserRole) || undefined,
                page: 1,
              }))
            }
          >
            <option value="">Any</option>
            <option value="admin">admin</option>
            <option value="user">user</option>
          </Select>
        </div>
        <div className="flex items-end gap-2 pb-1">
          <input
            id="filter-inactive"
            type="checkbox"
            checked={params.include_inactive ?? false}
            onChange={(e) =>
              setParams((p) => ({
                ...p,
                include_inactive: e.target.checked,
                page: 1,
              }))
            }
            className="h-4 w-4"
          />
          <Label htmlFor="filter-inactive" className="cursor-pointer">
            Include inactive
          </Label>
        </div>
      </div>

      {list.error ? <ErrorBlock error={list.error} className="mb-4" /> : null}

      <UsersTable
        rows={list.data?.items}
        isLoading={list.isLoading}
        page={params.page ?? 1}
        pageSize={params.page_size ?? PAGE_SIZE}
        total={list.data?.total}
        onPageChange={(page) => setParams((p) => ({ ...p, page }))}
        search={params.search ?? ''}
        onSearchChange={(search) =>
          setParams((p) => ({ ...p, search: search || undefined, page: 1 }))
        }
      />

      <CreateUserDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
      />
    </>
  );
}

// ---------------------------------------------------------------------------
// Table with inline row actions
// ---------------------------------------------------------------------------

function UsersTable(props: {
  rows: UserOut[] | undefined;
  isLoading: boolean;
  page: number;
  pageSize: number;
  total: number | undefined;
  onPageChange: (p: number) => void;
  search: string;
  onSearchChange: (s: string) => void;
}) {
  const { user: me } = useAuth();
  const [pending, setPending] = useState<{
    user: UserOut;
    kind: 'role' | 'toggle';
  } | null>(null);

  const columns: ColumnDef<UserOut>[] = [
    {
      key: 'username',
      header: 'Username',
      cell: (r) => <span className="font-mono">{r.username}</span>,
    },
    {
      key: 'role',
      header: 'Role',
      cell: (r) => (
        <Badge variant={r.role === 'admin' ? 'default' : 'secondary'}>
          {r.role}
        </Badge>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      cell: (r) =>
        r.is_active ? (
          <Badge variant="success">active</Badge>
        ) : (
          <Badge variant="warning">inactive</Badge>
        ),
    },
    {
      key: 'created_at',
      header: 'Created',
      cell: (r) => (
        <span className="text-xs text-neutral-500">
          {formatDateTime(r.created_at)}
        </span>
      ),
    },
    {
      key: 'actions',
      header: '',
      cell: (r) => {
        const isSelf = me?.id === r.id;
        return (
          <div className="flex items-center justify-end gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={isSelf}
              title={isSelf ? "You can't change your own role." : undefined}
              onClick={() => setPending({ user: r, kind: 'role' })}
            >
              {r.role === 'admin' ? 'Demote' : 'Promote'}
            </Button>
            <Button
              size="sm"
              variant={r.is_active ? 'outline' : 'default'}
              disabled={isSelf}
              title={isSelf ? "You can't deactivate yourself." : undefined}
              onClick={() => setPending({ user: r, kind: 'toggle' })}
            >
              {r.is_active ? 'Deactivate' : 'Activate'}
            </Button>
          </div>
        );
      },
      className: 'text-right',
    },
  ];

  return (
    <>
      <DataTable
        rows={props.rows}
        columns={columns}
        rowKey={(r) => r.id}
        isLoading={props.isLoading}
        page={props.page}
        pageSize={props.pageSize}
        total={props.total}
        onPageChange={props.onPageChange}
        search={props.search}
        onSearchChange={props.onSearchChange}
        searchPlaceholder="Filter by username…"
      />
      <UserRowActionDialog
        pending={pending}
        onOpenChange={(o) => !o && setPending(null)}
      />
    </>
  );
}

function UserRowActionDialog({
  pending,
  onOpenChange,
}: {
  pending: { user: UserOut; kind: 'role' | 'toggle' } | null;
  onOpenChange: (open: boolean) => void;
}) {
  const userId = pending?.user.id ?? '';
  const roleMut = useUserRoleUpdate(userId);
  const deactivateMut = useUserDeactivate(userId);
  const activateMut = useUserActivate(userId);

  if (!pending) {
    // Render nothing when there's no pending action; the parent owns the
    // open/close state, so we don't need an inert dialog instance here.
    return null;
  }

  const u = pending.user;
  if (pending.kind === 'role') {
    const nextRole: UserRole = u.role === 'admin' ? 'user' : 'admin';
    return (
      <ConfirmDialog
        open
        onOpenChange={onOpenChange}
        title={u.role === 'admin' ? 'Demote to user?' : 'Promote to admin?'}
        description={
          <span>
            <span className="font-mono">{u.username}</span> will become{' '}
            <span className="font-mono">{nextRole}</span>.  Effective on
            their next request.
          </span>
        }
        confirmLabel={nextRole === 'admin' ? 'Promote' : 'Demote'}
        confirmVariant={nextRole === 'user' ? 'destructive' : 'default'}
        onConfirm={async () => {
          await roleMut.mutateAsync({ role: nextRole });
        }}
      />
    );
  }

  return (
    <ConfirmDialog
      open
      onOpenChange={onOpenChange}
      title={u.is_active ? 'Deactivate account?' : 'Activate account?'}
      description={
        u.is_active ? (
          <span>
            <span className="font-mono">{u.username}</span> won&apos;t be able to
            log in, and their existing tokens will fail on the next refresh.
          </span>
        ) : (
          <span>
            Re-enables <span className="font-mono">{u.username}</span>.  Their
            password is unchanged.
          </span>
        )
      }
      confirmLabel={u.is_active ? 'Deactivate' : 'Activate'}
      confirmVariant={u.is_active ? 'destructive' : 'default'}
      onConfirm={async () => {
        if (u.is_active) {
          await deactivateMut.mutateAsync();
        } else {
          await activateMut.mutateAsync();
        }
      }}
    />
  );
}

// ---------------------------------------------------------------------------
// Create dialog
// ---------------------------------------------------------------------------

function CreateUserDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const create = useUserCreate();
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<UserForm>({
    resolver: zodResolver(userSchema),
    defaultValues: { username: '', password: '', role: 'user' },
  });

  async function onSubmit(values: UserForm) {
    await create.mutateAsync(values);
    reset();
    onOpenChange(false);
  }

  function handleOpenChange(o: boolean) {
    if (!o) {
      reset();
      create.reset();
    }
    onOpenChange(o);
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create user</DialogTitle>
          <DialogDescription>
            Passwords are hashed at rest with bcrypt.  The new user can sign
            in immediately.
          </DialogDescription>
        </DialogHeader>

        <form
          onSubmit={handleSubmit(onSubmit)}
          className="grid grid-cols-1 gap-3"
          noValidate
        >
          <div className="space-y-1">
            <Label htmlFor="new-username">Username</Label>
            <Input
              id="new-username"
              autoComplete="off"
              autoCapitalize="none"
              spellCheck={false}
              aria-invalid={!!errors.username}
              {...register('username')}
            />
            <FieldError message={errors.username?.message} />
          </div>

          <div className="space-y-1">
            <Label htmlFor="new-password">Password</Label>
            <Input
              id="new-password"
              type="password"
              autoComplete="new-password"
              aria-invalid={!!errors.password}
              {...register('password')}
            />
            <FieldError message={errors.password?.message} />
          </div>

          <div className="space-y-1">
            <Label htmlFor="new-role">Role</Label>
            <Select id="new-role" {...register('role')}>
              <option value="user">user</option>
              <option value="admin">admin</option>
            </Select>
          </div>

          {create.error ? <ErrorBlock error={create.error} /> : null}

          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => handleOpenChange(false)}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? <Spinner className="h-3.5 w-3.5" /> : null}
              Create
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
