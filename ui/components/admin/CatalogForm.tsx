'use client';

import { useState } from 'react';

import { ErrorBlock } from '@/components/admin/ErrorBlock';
import { JSONField } from '@/components/admin/JSONField';
import { Button } from '@/components/ui/Button';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/Card';
import { FieldError } from '@/components/ui/FieldError';
import { Input } from '@/components/ui/Input';
import { Label } from '@/components/ui/Label';
import { Select } from '@/components/ui/Select';
import { Spinner } from '@/components/ui/Spinner';
import { Textarea } from '@/components/ui/Textarea';
import type {
  ConfigSchemaField,
  MCPServerMode,
  MCPServerTypeCreateIn,
  MCPServerTypeOut,
  MCPServerTypeUpdateIn,
} from '@/lib/api/types';

const SLUG_RE = /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/;

export interface CatalogFormProps {
  mode: 'create' | 'edit';
  initial?: MCPServerTypeOut;
  onSubmit: (
    value: MCPServerTypeCreateIn | MCPServerTypeUpdateIn,
  ) => Promise<void>;
  onCancel: () => void;
  submitting?: boolean;
  error?: unknown;
}

interface FormState {
  slug: string;
  name: string;
  description: string;
  modeField: MCPServerMode;
  default_command: string;
  default_args: unknown[];
  config_schema: ConfigSchemaField[];
  default_env_vars: Record<string, unknown>;
  optional_env_vars: Record<string, unknown>;
  default_swagger_url: string;
  supports_swagger_child: boolean;
}

function toFormState(t?: MCPServerTypeOut): FormState {
  return {
    slug: t?.slug ?? '',
    name: t?.name ?? '',
    description: t?.description ?? '',
    modeField: t?.mode ?? 'stdio',
    default_command: t?.default_command ?? '',
    default_args: t?.default_args ?? [],
    config_schema: t?.config_schema ?? [],
    default_env_vars: t?.default_env_vars ?? {},
    optional_env_vars: t?.optional_env_vars ?? {},
    default_swagger_url: t?.default_swagger_url ?? '',
    supports_swagger_child: t?.supports_swagger_child ?? false,
  };
}

/**
 * Shared "edit a catalog entry" form used by both ``/admin/catalog/new``
 * and ``/admin/catalog/edit``.
 *
 * Why JSON editors for ``config_schema`` / ``default_args`` /
 * ``default_env_vars``: those are technical fields with no obvious
 * fixed form shape (the schema is dynamic, the args are arbitrary
 * argv).  A typed JSON textarea gives us 95% of the value without
 * inventing a mini-form builder.  We can iterate on a richer
 * config-schema editor later if/when it earns its keep.
 */
export function CatalogForm({
  mode,
  initial,
  onSubmit,
  onCancel,
  submitting,
  error,
}: CatalogFormProps) {
  const [s, setS] = useState<FormState>(() => toFormState(initial));
  const [validity, setValidity] = useState<Record<string, string | undefined>>({});
  const [topError, setTopError] = useState<string | null>(null);

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setS((p) => ({ ...p, [key]: value }));
  }

  function handleJsonValidity(key: string) {
    return (err: string | undefined) => {
      setValidity((v) => ({ ...v, [key]: err }));
    };
  }

  const slugError =
    mode === 'create' && s.slug && !SLUG_RE.test(s.slug)
      ? 'Lowercase letters, digits, and hyphens only (must start + end alphanumeric).'
      : undefined;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setTopError(null);

    if (!s.name.trim()) {
      setTopError('Name is required.');
      return;
    }
    if (mode === 'create') {
      if (!s.slug.trim()) {
        setTopError('Slug is required.');
        return;
      }
      if (slugError) {
        setTopError(slugError);
        return;
      }
    }
    if (s.modeField === 'stdio' && !s.default_command.trim()) {
      setTopError('Default command is required for stdio servers.');
      return;
    }
    const stillInvalid = Object.entries(validity).find(([, v]) => !!v);
    if (stillInvalid) {
      setTopError(`Invalid JSON in "${stillInvalid[0]}": ${stillInvalid[1]}`);
      return;
    }

    const payload: MCPServerTypeCreateIn = {
      slug: s.slug.trim(),
      name: s.name.trim(),
      description: s.description.trim() || null,
      mode: s.modeField,
      default_command: s.default_command.trim() || null,
      default_args: s.default_args ?? [],
      config_schema: s.config_schema ?? [],
      default_env_vars: s.default_env_vars ?? {},
      optional_env_vars: s.optional_env_vars ?? {},
      default_swagger_url: s.default_swagger_url.trim() || null,
      supports_swagger_child: s.supports_swagger_child,
    };

    if (mode === 'edit') {
      const { slug: _omit, ...rest } = payload;
      await onSubmit(rest as MCPServerTypeUpdateIn);
    } else {
      await onSubmit(payload);
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
            <Label htmlFor="slug">Slug</Label>
            <Input
              id="slug"
              value={s.slug}
              onChange={(e) => set('slug', e.target.value)}
              disabled={mode === 'edit'}
              aria-invalid={!!slugError}
              placeholder="github / linear / …"
            />
            <FieldError message={slugError} />
            {mode === 'edit' ? (
              <p className="text-xs text-neutral-500">
                Slugs are immutable — they&apos;re used as the stable lookup key
                from agent code.
              </p>
            ) : null}
          </div>
          <div className="space-y-1">
            <Label htmlFor="name">Name</Label>
            <Input
              id="name"
              value={s.name}
              onChange={(e) => set('name', e.target.value)}
              placeholder="GitHub"
            />
          </div>
          <div className="space-y-1 md:col-span-2">
            <Label htmlFor="description">Description</Label>
            <Textarea
              id="description"
              value={s.description}
              onChange={(e) => set('description', e.target.value)}
              placeholder="What does this MCP server connect to?"
              rows={2}
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium text-neutral-500">
            Transport
          </CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor="mode">Mode</Label>
            <Select
              id="mode"
              value={s.modeField}
              onChange={(e) => set('modeField', e.target.value as MCPServerMode)}
            >
              <option value="stdio">stdio</option>
              <option value="sse">sse</option>
              <option value="streamable_http">streamable_http</option>
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="default_command">
              {s.modeField === 'stdio' ? 'Default command' : 'Default URL'}
            </Label>
            <Input
              id="default_command"
              value={s.default_command}
              onChange={(e) => set('default_command', e.target.value)}
              placeholder={
                s.modeField === 'stdio'
                  ? 'npx -y @modelcontextprotocol/server-github'
                  : 'https://mcp.example.com/sse'
              }
            />
            <p className="text-xs text-neutral-500">
              {s.modeField === 'stdio'
                ? 'Executable launched by the runtime.  Concrete servers can override.'
                : 'HTTP endpoint of the MCP transport.  Concrete servers can override.'}
            </p>
          </div>
          <div className="space-y-1 md:col-span-2">
            <Label htmlFor="default_args">Default args (JSON array)</Label>
            <JSONField
              id="default_args"
              ariaLabel="Default args"
              value={s.default_args}
              onChange={(v) => set('default_args', (v as unknown[]) ?? [])}
              onValidityChange={handleJsonValidity('default_args')}
              placeholder='["--flag", "value"]'
              rows={3}
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium text-neutral-500">
            Config schema &amp; defaults
          </CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-3">
          <div className="space-y-1">
            <Label htmlFor="config_schema">
              Config schema (list of fields)
            </Label>
            <JSONField
              id="config_schema"
              ariaLabel="Config schema"
              value={s.config_schema}
              onChange={(v) =>
                set('config_schema', (v as ConfigSchemaField[]) ?? [])
              }
              onValidityChange={handleJsonValidity('config_schema')}
              rows={6}
              placeholder={`[
  { "name": "GITHUB_TOKEN", "label": "GitHub PAT", "type": "password", "is_secret": true, "required": true }
]`}
            />
            <p className="text-xs text-neutral-500">
              The admin form on each concrete server is generated from this
              schema.  ``is_secret=true`` fields land in encrypted env vars.
            </p>
          </div>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor="default_env_vars">
                Default env vars (JSON object)
              </Label>
              <JSONField
                id="default_env_vars"
                ariaLabel="Default env vars"
                value={s.default_env_vars}
                onChange={(v) =>
                  set(
                    'default_env_vars',
                    (v as Record<string, unknown>) ?? {},
                  )
                }
                onValidityChange={handleJsonValidity('default_env_vars')}
                rows={4}
                placeholder='{ "LOG_LEVEL": "info" }'
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="optional_env_vars">
                Optional env vars (JSON object)
              </Label>
              <JSONField
                id="optional_env_vars"
                ariaLabel="Optional env vars"
                value={s.optional_env_vars}
                onChange={(v) =>
                  set(
                    'optional_env_vars',
                    (v as Record<string, unknown>) ?? {},
                  )
                }
                onValidityChange={handleJsonValidity('optional_env_vars')}
                rows={4}
                placeholder='{ "MCP_TIMEOUT": "30" }'
              />
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium text-neutral-500">
            Swagger child resources
          </CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <div className="flex items-end gap-2 pb-1">
            <input
              id="supports_swagger_child"
              type="checkbox"
              checked={s.supports_swagger_child}
              onChange={(e) =>
                set('supports_swagger_child', e.target.checked)
              }
              className="h-4 w-4"
            />
            <Label htmlFor="supports_swagger_child" className="cursor-pointer">
              Supports Swagger / OpenAPI children
            </Label>
          </div>
          <div className="space-y-1">
            <Label htmlFor="default_swagger_url">Default Swagger URL</Label>
            <Input
              id="default_swagger_url"
              value={s.default_swagger_url}
              onChange={(e) => set('default_swagger_url', e.target.value)}
              disabled={!s.supports_swagger_child}
              placeholder="https://api.example.com/openapi.json"
            />
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
          {mode === 'create' ? 'Create' : 'Save changes'}
        </Button>
      </div>
    </form>
  );
}
