'use client';

import { useMemo, useState } from 'react';

import { ErrorBlock } from '@/components/admin/ErrorBlock';
import { JSONField } from '@/components/admin/JSONField';
import {
  OrderedMultiSelect,
} from '@/components/admin/OrderedMultiSelect';
import type { MultiSelectOption } from '@/components/admin/MultiSelect';
import { Button } from '@/components/ui/Button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { Label } from '@/components/ui/Label';
import { Select } from '@/components/ui/Select';
import { Spinner } from '@/components/ui/Spinner';
import { Textarea } from '@/components/ui/Textarea';
import { useAgentsList } from '@/lib/api/hooks/useAgents';
import type {
  TeamCreateIn,
  TeamMode,
  TeamOut,
  TeamUpdateIn,
  UUID,
} from '@/lib/api/types';

export interface TeamFormProps {
  mode: 'create' | 'edit';
  initial?: TeamOut;
  onCancel: () => void;
  onSubmit: (value: TeamCreateIn | TeamUpdateIn) => Promise<void>;
  submitting?: boolean;
  error?: unknown;
}

interface FormState {
  name: string;
  description: string;
  modeField: TeamMode;
  member_agent_ids: UUID[];
  team_config: Record<string, unknown>;
}

function initialState(initial?: TeamOut): FormState {
  return {
    name: initial?.name ?? '',
    description: initial?.description ?? '',
    modeField: initial?.mode ?? 'route',
    member_agent_ids: initial?.member_agent_ids ?? [],
    team_config: initial?.team_config ?? {},
  };
}

/**
 * Team edit form.
 *
 * Member order matters — in ``route`` and ``coordinate`` modes the
 * first member is treated as the team lead by the backend's Agno
 * adapter.  We surface that via :class:`OrderedMultiSelect` which
 * exposes explicit up/down/remove controls instead of a chip list.
 */
export function TeamForm({
  mode,
  initial,
  onCancel,
  onSubmit,
  submitting,
  error,
}: TeamFormProps) {
  const [s, setS] = useState<FormState>(() => initialState(initial));
  const [configValidity, setConfigValidity] = useState<string | undefined>();
  const [topError, setTopError] = useState<string | null>(null);

  // Agents pool.  Include archived so the form doesn't silently drop
  // attachments while we're editing (we still warn on submit if any
  // selected agent is archived).
  const agents = useAgentsList({
    page: 1,
    page_size: 200,
    include_archived: true,
  });

  const agentOptions: MultiSelectOption[] = useMemo(
    () =>
      (agents.data?.items ?? []).map((a) => ({
        value: a.id,
        label: a.name,
        description: a.archived_at
          ? `archived · ${a.model}`
          : a.model,
      })),
    [agents.data],
  );

  function setField<K extends keyof FormState>(key: K, value: FormState[K]) {
    setS((p) => ({ ...p, [key]: value }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setTopError(null);

    if (!s.name.trim()) {
      setTopError('Name is required.');
      return;
    }
    if (s.member_agent_ids.length === 0) {
      setTopError('A team needs at least one member.');
      return;
    }
    if (configValidity) {
      setTopError(`Invalid JSON in team_config: ${configValidity}`);
      return;
    }

    const payload: TeamCreateIn = {
      name: s.name.trim(),
      description: s.description.trim() || null,
      mode: s.modeField,
      member_agent_ids: s.member_agent_ids,
      team_config: s.team_config ?? {},
    };
    if (mode === 'create') {
      await onSubmit(payload);
    } else {
      await onSubmit(payload as TeamUpdateIn);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="grid grid-cols-1 gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium text-neutral-500">
            Identity
          </CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor="name">Name</Label>
            <Input
              id="name"
              value={s.name}
              onChange={(e) => setField('name', e.target.value)}
              placeholder="Customer onboarding"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="mode">Mode</Label>
            <Select
              id="mode"
              value={s.modeField}
              onChange={(e) => setField('modeField', e.target.value as TeamMode)}
            >
              <option value="route">route</option>
              <option value="coordinate">coordinate</option>
              <option value="collaborate">collaborate</option>
            </Select>
            <p className="text-xs text-neutral-500">
              <strong>route</strong>: lead picks one member to handle the
              query. <strong>coordinate</strong>: lead delegates and merges.{' '}
              <strong>collaborate</strong>: every member sees every turn.
            </p>
          </div>
          <div className="space-y-1 md:col-span-2">
            <Label htmlFor="description">Description</Label>
            <Textarea
              id="description"
              value={s.description}
              onChange={(e) => setField('description', e.target.value)}
              rows={2}
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium text-neutral-500">
            Members
          </CardTitle>
          <CardDescription>
            Order matters — the first member is treated as the lead in{' '}
            <code>route</code> / <code>coordinate</code> modes.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <OrderedMultiSelect
            options={agentOptions}
            value={s.member_agent_ids}
            onChange={(v) => setField('member_agent_ids', v)}
            addPlaceholder="Add an agent…"
            ariaLabel="Members"
          />
          {agents.error ? <ErrorBlock error={agents.error} className="mt-2" /> : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium text-neutral-500">
            Team config
          </CardTitle>
          <CardDescription>
            Free-form JSON passed through to the Agno team adapter.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <JSONField
            id="team_config"
            ariaLabel="Team config"
            value={s.team_config}
            onChange={(v) =>
              setField('team_config', (v as Record<string, unknown>) ?? {})
            }
            onValidityChange={setConfigValidity}
            rows={4}
            placeholder='{ "max_turns": 5 }'
          />
        </CardContent>
      </Card>

      {topError ? <ErrorBlock error={topError} /> : null}
      {error ? <ErrorBlock error={error} /> : null}

      <div className="flex items-center justify-end gap-2">
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={submitting}>
          {submitting ? <Spinner className="h-3.5 w-3.5 text-white" /> : null}
          {mode === 'create' ? 'Create team' : 'Save changes'}
        </Button>
      </div>
    </form>
  );
}
