'use client';

import { useQueries } from '@tanstack/react-query';
import { useMemo, useState } from 'react';

import { ErrorBlock } from '@/components/admin/ErrorBlock';
import { JSONField } from '@/components/admin/JSONField';
import { MultiSelect, type MultiSelectOption } from '@/components/admin/MultiSelect';
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
import { Spinner } from '@/components/ui/Spinner';
import { Textarea } from '@/components/ui/Textarea';
import { apiFetch } from '@/lib/api/client';
import { adminPaths, withQuery } from '@/lib/api/endpoints';
import { useServersList } from '@/lib/api/hooks/useServers';
import { queryKeys } from '@/lib/api/hooks/queryKeys';
import type {
  AgentCreateIn,
  AgentOut,
  AgentUpdateIn,
  MCPServerChildResourceListOut,
  UUID,
} from '@/lib/api/types';

export interface AgentFormProps {
  mode: 'create' | 'edit';
  initial?: AgentOut;
  /** Optional pre-fill from an agent template (Markdown + suggested servers). */
  preset?: {
    name?: string;
    description?: string | null;
    model?: string;
    instructions?: string;
    agent_config?: Record<string, unknown>;
  };
  onCancel: () => void;
  onSubmit: (value: AgentCreateIn | AgentUpdateIn) => Promise<void>;
  submitting?: boolean;
  error?: unknown;
}

interface FormState {
  name: string;
  description: string;
  model: string;
  instructions: string;
  tools_config: Record<string, unknown>;
  agent_config: Record<string, unknown>;
  mcp_server_ids: UUID[];
  child_resource_ids: UUID[];
}

function initialState(
  initial?: AgentOut,
  preset?: AgentFormProps['preset'],
): FormState {
  return {
    name: initial?.name ?? preset?.name ?? '',
    description: initial?.description ?? preset?.description ?? '',
    model: initial?.model ?? preset?.model ?? '',
    instructions: initial?.instructions ?? preset?.instructions ?? '',
    tools_config: initial?.tools_config ?? {},
    agent_config: initial?.agent_config ?? preset?.agent_config ?? {},
    mcp_server_ids: initial?.mcp_server_ids ?? [],
    child_resource_ids: initial?.child_resource_ids ?? [],
  };
}

/**
 * Shared agent edit form.
 *
 * The "interesting" UX bit is the two linked multi-selects: pick MCP
 * servers, then pick which of *their* child resources (Swagger sub-
 * resources) to inject.  We fetch children lazily, one
 * ``GET /admin/mcp-servers/{id}/child-resources`` per selected server,
 * via :func:`useQueries`.
 *
 * Cross-server consistency: when the user removes a server, we also
 * prune any child IDs that belonged to it.  This mirrors the
 * backend's invariant ("child_resource_ids must belong to selected
 * servers") so we don't surface a 400 after the user clicks Save.
 */
export function AgentForm({
  mode,
  initial,
  preset,
  onCancel,
  onSubmit,
  submitting,
  error,
}: AgentFormProps) {
  const [s, setS] = useState<FormState>(() => initialState(initial, preset));
  const [toolsValidity, setToolsValidity] = useState<string | undefined>();
  const [agentValidity, setAgentValidity] = useState<string | undefined>();
  const [topError, setTopError] = useState<string | null>(null);

  // Server options.  Limit to active servers + the ones currently
  // referenced (so an archived attachment still shows up while we're
  // editing — otherwise it would silently disappear from the list).
  const servers = useServersList({
    page: 1,
    page_size: 200,
    include_archived: true,
  });

  const serverOptions: MultiSelectOption[] = useMemo(
    () =>
      (servers.data?.items ?? []).map((srv) => ({
        value: srv.id,
        label: `${srv.name} · ${srv.env_tag}`,
        description: srv.archived_at ? 'archived' : undefined,
      })),
    [servers.data],
  );

  // Children for each selected server.  Using useQueries lets us fan
  // out without conditionally calling hooks in a loop.
  const childResults = useQueries({
    queries: s.mcp_server_ids.map((serverId) => ({
      queryKey: queryKeys.childResources.list(serverId, {
        page: 1,
        page_size: 200,
        include_disabled: true,
      }),
      queryFn: () =>
        apiFetch<MCPServerChildResourceListOut>(
          withQuery(adminPaths.childResources(serverId), {
            page: 1,
            page_size: 200,
            include_disabled: true,
          }),
        ),
    })),
  });

  // Build (id → label) and surface only available child IDs.
  const childOptions: MultiSelectOption[] = useMemo(() => {
    const byServerName = new Map(
      (servers.data?.items ?? []).map((s) => [s.id, s.name] as const),
    );
    const out: MultiSelectOption[] = [];
    childResults.forEach((res, idx) => {
      const serverId = s.mcp_server_ids[idx];
      const serverName = byServerName.get(serverId) ?? serverId.slice(0, 8);
      for (const c of res.data?.items ?? []) {
        out.push({
          value: c.id,
          label: `${serverName} › ${c.name}`,
          description: c.enabled ? c.url : 'disabled',
        });
      }
    });
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [childResults, servers.data, s.mcp_server_ids]);

  function setField<K extends keyof FormState>(key: K, value: FormState[K]) {
    setS((p) => ({ ...p, [key]: value }));
  }

  function setServerIds(next: UUID[]) {
    // Drop child IDs whose owning server was just removed.  We don't
    // know the parent until childOptions catches up, so we conservatively
    // keep any child ID we *do* still see in childOptions (which only
    // contains kids of currently-selected servers).
    const allowed = new Set(childOptions.map((o) => o.value));
    const filteredChildren = s.child_resource_ids.filter((id) =>
      allowed.has(id),
    );
    setS((p) => ({
      ...p,
      mcp_server_ids: next,
      child_resource_ids: filteredChildren,
    }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setTopError(null);

    if (!s.name.trim()) {
      setTopError('Name is required.');
      return;
    }
    if (!s.model.trim()) {
      setTopError('Model is required.');
      return;
    }
    if (!s.instructions.trim()) {
      setTopError('Instructions are required.');
      return;
    }
    if (toolsValidity) {
      setTopError(`Invalid JSON in tools_config: ${toolsValidity}`);
      return;
    }
    if (agentValidity) {
      setTopError(`Invalid JSON in agent_config: ${agentValidity}`);
      return;
    }

    const payload: AgentCreateIn = {
      name: s.name.trim(),
      description: s.description.trim() || null,
      model: s.model.trim(),
      instructions: s.instructions,
      tools_config: s.tools_config ?? {},
      agent_config: s.agent_config ?? {},
      mcp_server_ids: s.mcp_server_ids,
      child_resource_ids: s.child_resource_ids,
    };
    if (mode === 'create') {
      await onSubmit(payload);
    } else {
      await onSubmit(payload as AgentUpdateIn);
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
              placeholder="Triage agent"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="model">Model</Label>
            <Input
              id="model"
              value={s.model}
              onChange={(e) => setField('model', e.target.value)}
              placeholder="openai:gpt-4o-mini"
            />
            <p className="text-xs text-neutral-500">
              Any model string the backend&apos;s model registry resolves.
            </p>
          </div>
          <div className="space-y-1 md:col-span-2">
            <Label htmlFor="description">Description</Label>
            <Textarea
              id="description"
              value={s.description}
              onChange={(e) => setField('description', e.target.value)}
              rows={2}
              placeholder="What does this agent do?"
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium text-neutral-500">
            Instructions
          </CardTitle>
          <CardDescription>
            Markdown is fine — runtime passes the string as-is to the model.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Textarea
            id="instructions"
            value={s.instructions}
            onChange={(e) => setField('instructions', e.target.value)}
            rows={10}
            placeholder="You are a helpful assistant…"
            className="font-mono text-xs"
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium text-neutral-500">
            Tools
          </CardTitle>
          <CardDescription>
            MCP servers, child resources, and bespoke tool config.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-3">
          <div className="space-y-1">
            <Label>MCP servers</Label>
            <MultiSelect
              options={serverOptions}
              value={s.mcp_server_ids}
              onChange={setServerIds}
              placeholder="Pick servers…"
              ariaLabel="MCP servers"
            />
            {servers.error ? <ErrorBlock error={servers.error} /> : null}
          </div>
          <div className="space-y-1">
            <Label>Child resources</Label>
            <MultiSelect
              options={childOptions}
              value={s.child_resource_ids}
              onChange={(v) => setField('child_resource_ids', v)}
              placeholder={
                s.mcp_server_ids.length === 0
                  ? 'Pick at least one server first.'
                  : 'Pick child resources…'
              }
              disabled={s.mcp_server_ids.length === 0}
              ariaLabel="Child resources"
            />
          </div>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor="tools_config">Tools config (JSON object)</Label>
              <JSONField
                id="tools_config"
                ariaLabel="Tools config"
                value={s.tools_config}
                onChange={(v) =>
                  setField('tools_config', (v as Record<string, unknown>) ?? {})
                }
                onValidityChange={setToolsValidity}
                rows={4}
                placeholder='{ "allow_repls": true }'
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="agent_config">Agent config (JSON object)</Label>
              <JSONField
                id="agent_config"
                ariaLabel="Agent config"
                value={s.agent_config}
                onChange={(v) =>
                  setField('agent_config', (v as Record<string, unknown>) ?? {})
                }
                onValidityChange={setAgentValidity}
                rows={4}
                placeholder='{ "temperature": 0.2 }'
              />
            </div>
          </div>
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
          {mode === 'create' ? 'Create agent' : 'Save changes'}
        </Button>
      </div>
    </form>
  );
}
