/**
 * Admin-shell + CRUD smokes.
 *
 * The backend is stubbed via ``page.route`` so these tests are
 * hermetic.  They cover:
 *   * Scope gate — a ``role: 'user'`` visitor lands on ``/`` when
 *     they try ``/admin``, never reaches the chrome.
 *   * Shell — the sidebar links render for an admin.
 *   * CRUD smoke — list + edit on the Catalog (mcp-server-types)
 *     surface, since it's the simplest entity (no schema-driven
 *     env vars, no nested children).
 */

import { expect, test, type Page } from '@playwright/test';

const API = 'http://stub.test';

const TOKEN_PAIR = {
  access_token: 'acc.test.token',
  refresh_token: 'ref.test.token',
  token_type: 'bearer',
  expires_in: 900,
};

const ADMIN_ME = {
  id: '11111111-1111-1111-1111-111111111111',
  username: 'admin',
  role: 'admin' as const,
  is_active: true,
  scopes: ['gargantua:admin', 'gargantua:user'],
};

const USER_ME = {
  id: '22222222-2222-2222-2222-222222222222',
  username: 'alice',
  role: 'user' as const,
  is_active: true,
  scopes: ['gargantua:user'],
};

const TYPE = {
  id: '33333333-3333-3333-3333-333333333333',
  slug: 'github',
  name: 'GitHub',
  description: null,
  mode: 'stdio' as const,
  default_command: 'npx -y server-github',
  default_args: [],
  config_schema: [],
  default_env_vars: {},
  optional_env_vars: {},
  default_swagger_url: null,
  supports_swagger_child: false,
  version: 1,
  archived_at: null,
  created_at: '2024-01-15T12:00:00Z',
  updated_at: '2024-01-15T12:00:00Z',
};

async function seedSession(page: Page) {
  await page.addInitScript((pair) => {
    window.localStorage.setItem('gargantua.access_token', pair.access_token);
    window.localStorage.setItem('gargantua.refresh_token', pair.refresh_token);
    window.localStorage.setItem(
      'gargantua.access_expires_at_ms',
      String(Date.now() + pair.expires_in * 1000),
    );
  }, TOKEN_PAIR);
}

test.describe('admin scope gate', () => {
  test('non-admin role does not see the admin chrome', async ({ page }) => {
    await page.route(`${API}/auth/me`, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(USER_ME),
      }),
    );
    await seedSession(page);

    await page.goto('/admin/');
    // AuthGuard.replace('/') bounces the visitor to the home page.
    await expect(page).toHaveURL(/\/$/);
    // And the admin chrome is never mounted on the way out.
    await expect(
      page.getByRole('navigation', { name: 'Admin sections' }),
    ).toHaveCount(0);
  });
});

test.describe('admin shell', () => {
  test.beforeEach(async ({ page }) => {
    await page.route(`${API}/auth/me`, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(ADMIN_ME),
      }),
    );
    // Stub the dashboard data fetches we don't care about here.
    await page.route(new RegExp(`${API}/admin/.*`), (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 25 }),
      }),
    );
    await seedSession(page);
  });

  test('renders the sidebar nav', async ({ page }) => {
    await page.goto('/admin/');
    await expect(
      page.getByRole('navigation', { name: 'Admin sections' }),
    ).toBeVisible();
    // Sidebar text labels (not link roles — the Catalog title also
    // appears as an <h3> in the dashboard cards, which would make
    // a getByRole('link', { name: 'Catalog' }) ambiguous).
    await expect(
      page.locator('nav[aria-label="Admin sections"]').getByText('Catalog'),
    ).toBeVisible();
    await expect(
      page.locator('nav[aria-label="Admin sections"]').getByText('MCP servers'),
    ).toBeVisible();
  });
});

test.describe('catalog CRUD smoke', () => {
  test('list renders one type and edit page hydrates from /admin/mcp-server-types/{id}', async ({
    page,
  }) => {
    await page.route(`${API}/auth/me`, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(ADMIN_ME),
      }),
    );
    await seedSession(page);

    let patchPayload: Record<string, unknown> | null = null;

    // List → one item.
    await page.route(
      new RegExp(`${API}/admin/mcp-server-types(\\?.*)?$`),
      (route) =>
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            items: [TYPE],
            total: 1,
            page: 1,
            page_size: 25,
          }),
        }),
    );

    // Detail.
    await page.route(`${API}/admin/mcp-server-types/${TYPE.id}`, async (route, request) => {
      if (request.method() === 'PATCH') {
        patchPayload = request.postDataJSON() as Record<string, unknown>;
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            ...TYPE,
            name: (patchPayload.name as string) ?? TYPE.name,
          }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(TYPE),
      });
    });

    // List page (trailing slash to match next.config trailingSlash).
    await page.goto('/admin/catalog/');
    await expect(page.getByText('GitHub').first()).toBeVisible();
    await expect(page.getByText('github').first()).toBeVisible();

    // Navigate to edit.
    await page.getByRole('link', { name: 'Edit' }).first().click();
    await expect(page).toHaveURL(
      new RegExp(`/admin/catalog/edit/?\\?id=${TYPE.id}`),
    );

    // Form hydrates with the current name; slug is disabled in edit.
    const name = page.getByLabel('Name');
    await expect(name).toHaveValue('GitHub');
    await expect(page.getByLabel('Slug')).toBeDisabled();

    await name.fill('GitHub Cloud');
    await page.getByRole('button', { name: /save changes/i }).click();

    await expect.poll(() => patchPayload).not.toBeNull();
    // ``patchPayload`` is typed via the closure above; the narrowing
    // doesn't carry through ``expect.poll`` so we re-assert non-null
    // and index back into the captured payload directly.
    const captured = patchPayload as Record<string, unknown> | null;
    expect(captured?.name).toBe('GitHub Cloud');
  });
});
