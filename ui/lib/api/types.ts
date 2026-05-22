/**
 * Hand-written TypeScript mirrors of the backend's
 * ``src/gargantua/api/schemas.py``.  Authoritative until we wire
 * ``openapi-typescript`` codegen (see ``pnpm codegen``); meanwhile,
 * the rule is: if a schema changes server-side, update this file too.
 *
 * Naming follows the Python: ``*Out`` for responses, ``*In`` for
 * request bodies, ``*List`` for paginated envelopes.  A ``UUID`` on
 * the wire is always a string (Python serialises it that way).
 */

// ---------------------------------------------------------------------------
// Common
// ---------------------------------------------------------------------------

export type UUID = string;

/** ISO-8601 timestamp string as produced by ``datetime.isoformat()``. */
export type ISODateTime = string;

/** Envelope fields attached to every paginated list response. */
export interface PaginationMeta {
  page: number;
  page_size: number;
  total: number;
}

/** Standard query params for paginated list endpoints. */
export interface ListQuery {
  page?: number;
  page_size?: number;
  search?: string;
}

export interface ApiErrorBody {
  detail?: string | Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

export interface LoginRequest {
  username: string;
  password: string;
}

export interface RefreshRequest {
  refresh_token: string;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: 'bearer';
  expires_in: number;
}

/** Shape of ``GET /auth/me``.  Mirrors ``gargantua.api.auth.MeResponse``. */
export interface MeResponse {
  id: UUID;
  username: string;
  role: 'admin' | 'user';
  is_active: boolean;
  scopes: string[];
}

// ---------------------------------------------------------------------------
// Users (admin)
// ---------------------------------------------------------------------------

export type UserRole = 'admin' | 'user';

export interface UserOut {
  id: UUID;
  username: string;
  role: UserRole;
  is_active: boolean;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export interface UserCreateIn {
  username: string;
  password: string;
  role: UserRole;
}

export interface UserRoleUpdateIn {
  role: UserRole;
}

export interface UserListOut extends PaginationMeta {
  items: UserOut[];
}

export interface UserListQuery extends ListQuery {
  role?: UserRole;
  include_inactive?: boolean;
}

// ---------------------------------------------------------------------------
// Audit log
// ---------------------------------------------------------------------------

export interface AuditLogOut {
  id: number;
  actor_id: UUID | null;
  action: string;
  target_type: string;
  target_id: UUID | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  created_at: ISODateTime;
}

export interface AuditLogListOut extends PaginationMeta {
  items: AuditLogOut[];
}

export interface AuditLogListQuery {
  page?: number;
  page_size?: number;
  actor_id?: UUID;
  target_type?: string;
  target_id?: UUID;
  action?: string;
}

// ---------------------------------------------------------------------------
// MCP server types (catalog)
// ---------------------------------------------------------------------------

export type MCPServerMode = 'stdio' | 'sse' | 'streamable_http';

/**
 * One entry in a type's ``config_schema``.  The admin UI renders this
 * into a form input.  ``is_secret=true`` fields land in the server's
 * encrypted ``env_vars``; non-secret fields stay plaintext.
 */
export interface ConfigSchemaField {
  name: string;
  label: string;
  /** Free-form, but the UI maps "text" / "password" / "number" / "textarea" / "select" today. */
  type: string;
  is_secret?: boolean;
  required?: boolean;
  default?: unknown;
}

export interface MCPServerTypeOut {
  id: UUID;
  slug: string;
  name: string;
  description: string | null;
  mode: MCPServerMode;
  default_command: string | null;
  default_args: unknown[];
  /** Stored as ``list[dict]`` server-side; render-time we narrow to ``ConfigSchemaField``. */
  config_schema: ConfigSchemaField[];
  default_env_vars: Record<string, unknown>;
  optional_env_vars: Record<string, unknown>;
  default_swagger_url: string | null;
  supports_swagger_child: boolean;
  version: number;
  archived_at: ISODateTime | null;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export interface MCPServerTypeCreateIn {
  slug: string;
  name: string;
  description?: string | null;
  mode: MCPServerMode;
  default_command?: string | null;
  default_args?: unknown[];
  config_schema?: ConfigSchemaField[];
  default_env_vars?: Record<string, unknown>;
  optional_env_vars?: Record<string, unknown>;
  default_swagger_url?: string | null;
  supports_swagger_child?: boolean;
}

export type MCPServerTypeUpdateIn = Partial<
  Omit<MCPServerTypeCreateIn, 'slug'>
>;

export interface MCPServerTypeListOut extends PaginationMeta {
  items: MCPServerTypeOut[];
}

export interface MCPServerTypeListQuery extends ListQuery {
  mode?: MCPServerMode;
  include_archived?: boolean;
}

// ---------------------------------------------------------------------------
// MCP servers (instances)
// ---------------------------------------------------------------------------

/** Placeholder the backend returns for masked secret fields. */
export const SECRET_PLACEHOLDER = '<redacted>';

export interface MCPServerOut {
  id: UUID;
  type_id: UUID;
  name: string;
  env_tag: string;
  command: string | null;
  args: unknown[];
  /** Plaintext non-secret values; secrets replaced by :data:`SECRET_PLACEHOLDER`. */
  env_vars: Record<string, unknown>;
  archived_at: ISODateTime | null;
  version: number;
  created_by: UUID | null;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export interface MCPServerCreateIn {
  type_id: UUID;
  name: string;
  env_tag: string;
  env_vars?: Record<string, unknown>;
  command?: string | null;
  args?: unknown[];
}

export type MCPServerUpdateIn = Partial<Omit<MCPServerCreateIn, 'type_id'>>;

export interface MCPServerListOut extends PaginationMeta {
  items: MCPServerOut[];
}

export interface MCPServerListQuery extends ListQuery {
  type_id?: UUID;
  env_tag?: string;
  include_archived?: boolean;
}

// ---------------------------------------------------------------------------
// MCP server child resources
// ---------------------------------------------------------------------------

export type ChildResourceType = 'swagger';

export interface MCPServerChildResourceOut {
  id: UUID;
  parent_mcp_server_id: UUID;
  type: ChildResourceType;
  name: string;
  url: string;
  /** Always treated as secret-bearing; values come back masked. */
  headers: Record<string, unknown>;
  enabled: boolean;
  version: number;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export interface MCPServerChildResourceCreateIn {
  type: ChildResourceType;
  name: string;
  url: string;
  headers?: Record<string, unknown>;
}

export type MCPServerChildResourceUpdateIn = Partial<
  Omit<MCPServerChildResourceCreateIn, 'type'>
>;

export interface MCPServerChildResourceListOut extends PaginationMeta {
  items: MCPServerChildResourceOut[];
}

export interface MCPServerChildResourceListQuery extends ListQuery {
  include_disabled?: boolean;
}

// ---------------------------------------------------------------------------
// Agents
// ---------------------------------------------------------------------------

export interface AgentOut {
  id: UUID;
  name: string;
  description: string | null;
  model: string;
  instructions: string;
  tools_config: Record<string, unknown>;
  mcp_server_ids: UUID[];
  child_resource_ids: UUID[];
  agent_config: Record<string, unknown>;
  archived_at: ISODateTime | null;
  version: number;
  created_by: UUID | null;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export interface AgentCreateIn {
  name: string;
  model: string;
  instructions: string;
  description?: string | null;
  tools_config?: Record<string, unknown>;
  mcp_server_ids?: UUID[];
  child_resource_ids?: UUID[];
  agent_config?: Record<string, unknown>;
}

export type AgentUpdateIn = Partial<AgentCreateIn>;

export interface AgentListOut extends PaginationMeta {
  items: AgentOut[];
}

export interface AgentListQuery extends ListQuery {
  model?: string;
  include_archived?: boolean;
}

// ---------------------------------------------------------------------------
// Teams
// ---------------------------------------------------------------------------

export type TeamMode = 'route' | 'coordinate' | 'collaborate';

export interface TeamOut {
  id: UUID;
  name: string;
  description: string | null;
  mode: TeamMode;
  member_agent_ids: UUID[];
  team_config: Record<string, unknown>;
  archived_at: ISODateTime | null;
  version: number;
  created_by: UUID | null;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export interface TeamCreateIn {
  name: string;
  mode: TeamMode;
  description?: string | null;
  member_agent_ids?: UUID[];
  team_config?: Record<string, unknown>;
}

export type TeamUpdateIn = Partial<TeamCreateIn>;

export interface TeamListOut extends PaginationMeta {
  items: TeamOut[];
}

export interface TeamListQuery extends ListQuery {
  mode?: TeamMode;
  include_archived?: boolean;
}

// ---------------------------------------------------------------------------
// MCP cache (operational introspection)
// ---------------------------------------------------------------------------

export interface MCPCacheEntryOut {
  server_id: UUID;
  child_resource_ids: UUID[];
  version: number;
  ref_count: number;
  last_used: ISODateTime;
  is_orphan: boolean;
}

export interface MCPCacheListOut {
  items: MCPCacheEntryOut[];
  total: number;
}

/** Response from ``POST /admin/mcp-cache/{server_id}/evict``. */
export interface MCPCacheEvictOut {
  evicted: boolean;
}

// ---------------------------------------------------------------------------
// Agent templates (read-only seeds)
// ---------------------------------------------------------------------------

export interface AgentTemplateOut {
  slug: string;
  name: string;
  description: string | null;
  model: string;
  suggested_mcp_server_type_slugs: string[];
  agent_config: Record<string, unknown>;
  instructions: string;
}

export interface AgentTemplateListOut {
  items: AgentTemplateOut[];
  total: number;
}

// ---------------------------------------------------------------------------
// /me (user-facing pickers — used in PR 17, declared here for completeness)
// ---------------------------------------------------------------------------

export interface MeAgentOut {
  id: UUID;
  name: string;
  description: string | null;
  model: string;
  mcp_server_ids: UUID[];
}

export interface MeAgentListOut {
  items: MeAgentOut[];
  total: number;
}

export interface MeTeamOut {
  id: UUID;
  name: string;
  description: string | null;
  mode: TeamMode;
  member_agent_ids: UUID[];
}

export interface MeTeamListOut {
  items: MeTeamOut[];
  total: number;
}
