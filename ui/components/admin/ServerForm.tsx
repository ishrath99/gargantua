'use client';

import { AlertCircle } from 'lucide-react';
import { useMemo, useState } from 'react';

import { ErrorBlock } from '@/components/admin/ErrorBlock';
import { JSONField } from '@/components/admin/JSONField';
import { SecretField } from '@/components/admin/SecretField';
import { Button } from '@/components/ui/Button';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { Label } from '@/components/ui/Label';
import { Select } from '@/components/ui/Select';
import { Spinner } from '@/components/ui/Spinner';
import { Textarea } from '@/components/ui/Textarea';
import {
  SECRET_PLACEHOLDER,
  type ConfigSchemaField,
  type MCPServerCreateIn,
  type MCPServerOut,
  type MCPServerTypeOut,
  type MCPServerUpdateIn,
} from '@/lib/api/types';

export interface ServerFormProps {
  mode: 'create' | 'edit';
  type: MCPServerTypeOut;
  initial?: MCPServerOut;
  onCancel: () => void;
  onSubmit: (
    value: MCPServerCreateIn | MCPServerUpdateIn,
  ) => Promise<void>;
  submitting?: boolean;
  error?: unknown;
}

interface FormState {
  name: string;
  env_tag: string;
  command: string;
  args: unknown[];
  /** Plain map keyed by ConfigSchemaField.name. */
  envValues: Record<string, string>;
  /** Names of secret fields the user has clicked "Replace" on. */
  replacingSecrets: Set<string>;
  /** Whether the env_vars block has been touched at all (dirty bit). */
  envVarsDirty: boolean;
}

function defaultsForCreate(t: MCPServerTypeOut): Record<string, string> {
  const out: Record<string, string> = {};
  // Pre-fill non-secret fields with their schema-declared defaults.
  for (const f of t.config_schema ?? []) {
    if (f.is_secret) continue;
    const d = f.default;
    if (d === undefined || d === null) continue;
    out[f.name] = String(d);
  }
  return out;
}

function initialState(
  type: MCPServerTypeOut,
  initial?: MCPServerOut,
): FormState {
  return {
    name: initial?.name ?? '',
    env_tag: initial?.env_tag ?? '',
    command: initial?.command ?? type.default_command ?? '',
    args: initial?.args ?? type.default_args ?? [],
    envValues: initial
      ? Object.fromEntries(
          Object.entries(initial.env_vars).map(([k, v]) => [
            k,
            v === null || v === undefined ? '' : String(v),
          ]),
        )
      : defaultsForCreate(type),
    replacingSecrets: new Set(),
    envVarsDirty: false,
  };
}

/**
 * Schema-driven MCP server form.  Renders one input per
 * ``config_schema`` field defined on the parent type, plus the
 * universal fields (``name``, ``env_tag``, ``command``, ``args``).
 *
 * Secret handling
 *   The backend masks secret env vars on read with the literal
 *   :data:`SECRET_PLACEHOLDER`.  Updates are **replace-all** — sending
 *   ``env_vars`` in the PATCH overwrites the entire stored dict.
 *
 *   So when editing, we:
 *     1. Render each secret field as a masked :class:`SecretField`
 *        until the user clicks "Replace".  Once replaced, it switches
 *        to a real password input.
 *     2. Track a single ``envVarsDirty`` bit; if the user never touches
 *        an env var, we omit ``env_vars`` from the PATCH entirely
 *        (so unmasked secrets stay safe on the server).
 *     3. If ``envVarsDirty`` is true and there are still masked
 *        secrets, surface a banner explaining that submitting will
 *        clear them.  The user can either Replace them or cancel.
 */
export function ServerForm({
  mode,
  type,
  initial,
  onCancel,
  onSubmit,
  submitting,
  error,
}: ServerFormProps) {
  const [s, setS] = useState<FormState>(() => initialState(type, initial));
  const [argsValidity, setArgsValidity] = useState<string | undefined>();
  const [topError, setTopError] = useState<string | null>(null);

  function setField<K extends keyof FormState>(key: K, value: FormState[K]) {
    setS((p) => ({ ...p, [key]: value }));
  }

  function setEnvValue(name: string, value: string) {
    setS((p) => ({
      ...p,
      envValues: { ...p.envValues, [name]: value },
      envVarsDirty: true,
    }));
  }

  function replaceSecret(name: string) {
    setS((p) => {
      const next = new Set(p.replacingSecrets);
      next.add(name);
      return {
        ...p,
        replacingSecrets: next,
        envValues: { ...p.envValues, [name]: '' },
        envVarsDirty: true,
      };
    });
  }

  const schema: ConfigSchemaField[] = useMemo(
    () => type.config_schema ?? [],
    [type.config_schema],
  );
  const secretFields = useMemo(
    () => schema.filter((f) => f.is_secret),
    [schema],
  );
  const stillMaskedSecrets = useMemo(
    () =>
      mode === 'edit' && s.envVarsDirty
        ? secretFields.filter(
            (f) =>
              !s.replacingSecrets.has(f.name) &&
              s.envValues[f.name] === SECRET_PLACEHOLDER,
          )
        : [],
    [mode, s.envVarsDirty, secretFields, s.envValues, s.replacingSecrets],
  );

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setTopError(null);

    if (!s.name.trim()) {
      setTopError('Name is required.');
      return;
    }
    if (!s.env_tag.trim()) {
      setTopError('Environment tag is required.');
      return;
    }
    if (argsValidity) {
      setTopError(`Invalid JSON in args: ${argsValidity}`);
      return;
    }
    if (stillMaskedSecrets.length > 0) {
      setTopError(
        `Submitting would clear masked secrets (${stillMaskedSecrets
          .map((f) => f.name)
          .join(', ')}).  Click "Replace" and re-enter values, or cancel.`,
      );
      return;
    }
    // Required-field check on create only.  On edit, missing fields are
    // assumed unchanged via the dirty-bit logic below.
    if (mode === 'create') {
      const missing = schema.filter(
        (f) => f.required && !s.envValues[f.name]?.trim(),
      );
      if (missing.length > 0) {
        setTopError(
          `Missing required fields: ${missing.map((f) => f.label).join(', ')}.`,
        );
        return;
      }
    }

    // Build the env_vars payload.  Drop empty strings so we don't write
    // explicit empty values for fields the user left blank.
    const env_vars: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(s.envValues)) {
      if (v === '' || v === undefined || v === null) continue;
      // Never write the placeholder string back to the server.
      if (v === SECRET_PLACEHOLDER) continue;
      env_vars[k] = v;
    }

    if (mode === 'create') {
      const payload: MCPServerCreateIn = {
        type_id: type.id,
        name: s.name.trim(),
        env_tag: s.env_tag.trim(),
        command: s.command.trim() || null,
        args: s.args ?? [],
        env_vars,
      };
      await onSubmit(payload);
      return;
    }

    const patch: MCPServerUpdateIn = {
      name: s.name.trim(),
      env_tag: s.env_tag.trim(),
      command: s.command.trim() || null,
      args: s.args ?? [],
    };
    if (s.envVarsDirty) {
      patch.env_vars = env_vars;
    }
    await onSubmit(patch);
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
              placeholder="GitHub prod"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="env_tag">Environment tag</Label>
            <Input
              id="env_tag"
              value={s.env_tag}
              onChange={(e) => setField('env_tag', e.target.value)}
              placeholder="prod / dev / …"
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium text-neutral-500">
            Transport overrides
          </CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-3">
          <div className="space-y-1">
            <Label htmlFor="command">
              {type.mode === 'stdio' ? 'Command' : 'URL'}
            </Label>
            <Input
              id="command"
              value={s.command}
              onChange={(e) => setField('command', e.target.value)}
              placeholder={
                type.mode === 'stdio'
                  ? type.default_command ?? 'npx -y …'
                  : type.default_command ?? 'https://…'
              }
            />
            <p className="text-xs text-neutral-500">
              Falls back to the type&apos;s default ({type.default_command ?? '—'})
              when blank.
            </p>
          </div>
          <div className="space-y-1">
            <Label htmlFor="args">Args (JSON array)</Label>
            <JSONField
              id="args"
              ariaLabel="Args"
              value={s.args}
              onChange={(v) => setField('args', (v as unknown[]) ?? [])}
              onValidityChange={setArgsValidity}
              rows={3}
              placeholder='["--port", "9000"]'
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium text-neutral-500">
            Environment variables
          </CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-3">
          {schema.length === 0 ? (
            <p className="text-sm text-neutral-500">
              The type doesn&apos;t declare any config_schema entries.  You can
              still set free-form env vars via the JSON editor below.
            </p>
          ) : (
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              {schema.map((f) => (
                <SchemaField
                  key={f.name}
                  field={f}
                  value={s.envValues[f.name] ?? ''}
                  replacing={s.replacingSecrets.has(f.name)}
                  onReplace={() => replaceSecret(f.name)}
                  onChange={(v) => setEnvValue(f.name, v)}
                />
              ))}
            </div>
          )}

          {mode === 'edit' && s.envVarsDirty ? (
            <div
              role="status"
              className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-200"
            >
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <div>
                Saving will replace the server&apos;s full env_vars dict.  Any secret
                you didn&apos;t <strong>Replace</strong> would be cleared on save —
                click Replace and re-enter values to keep them.
              </div>
            </div>
          ) : null}
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
          {mode === 'create' ? 'Create server' : 'Save changes'}
        </Button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// One input per ConfigSchemaField
// ---------------------------------------------------------------------------

function SchemaField({
  field,
  value,
  replacing,
  onReplace,
  onChange,
}: {
  field: ConfigSchemaField;
  value: string;
  replacing: boolean;
  onReplace: () => void;
  onChange: (v: string) => void;
}) {
  const id = `env-${field.name}`;
  const isSecret = !!field.is_secret;
  const isMasked = isSecret && !replacing && value === SECRET_PLACEHOLDER;

  return (
    <div className="space-y-1">
      <Label htmlFor={id}>
        {field.label}
        {field.required ? <span className="text-red-500"> *</span> : null}
        <span className="ml-2 font-mono text-xs text-neutral-400">
          {field.name}
        </span>
      </Label>

      {isSecret ? (
        <SecretField
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          masked={isMasked}
          onClear={onReplace}
          placeholder={field.required ? 'required' : undefined}
        />
      ) : field.type === 'textarea' ? (
        <Textarea
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          rows={3}
        />
      ) : field.type === 'number' ? (
        <Input
          id={id}
          type="number"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : field.type === 'select' ? (
        <Select
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="">—</option>
        </Select>
      ) : (
        <Input
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </div>
  );
}
