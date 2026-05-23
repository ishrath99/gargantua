/**
 * End-to-end login smoke test.
 *
 * Every API call is stubbed via ``page.route``; no real backend is
 * required.  This catches the wiring between the form, the auth
 * context, the API client, and the post-login redirect — i.e. the
 * exact set of integration bugs that unit tests can't see.
 */

import { expect, test } from '@playwright/test';

const API = 'http://stub.test';

const TOKEN_PAIR = {
  access_token: 'acc.test.token',
  refresh_token: 'ref.test.token',
  token_type: 'bearer',
  expires_in: 900,
};

const ME = {
  id: '11111111-1111-1111-1111-111111111111',
  username: 'admin',
  role: 'admin' as const,
  is_active: true,
  scopes: ['gargantua:admin', 'gargantua:user'],
};

test.describe('login flow', () => {
  test('unauthenticated visit to / redirects to /login', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveURL(/\/login\/?$/);
    await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  });

  test('valid credentials -> tokens stored, redirected home', async ({ page }) => {
    let loginPayload: { username: string; password: string } | null = null;

    await page.route(`${API}/api/auth/login`, async (route, request) => {
      loginPayload = request.postDataJSON() as typeof loginPayload;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(TOKEN_PAIR),
      });
    });

    await page.route(`${API}/api/auth/me`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(ME),
      });
    });

    await page.goto('/login');
    await page.getByLabel('Username').fill('admin');
    await page.getByLabel('Password').fill('hunter2');
    await page.getByRole('button', { name: 'Sign in' }).click();

    // Lands on the home page once auth state propagates.
    await expect(page).toHaveURL(/\/$/);
    // Welcome banner renders ``username`` inside a <span class="font-mono">
    // — scope to that to avoid matching the word "admin" in body copy.
    await expect(page.locator('header span.font-mono').first()).toHaveText('admin');

    // Verified the backend received the credentials we typed.
    expect(loginPayload).toEqual({ username: 'admin', password: 'hunter2' });

    // Tokens persisted to localStorage as expected.
    const access = await page.evaluate(() =>
      window.localStorage.getItem('gargantua.access_token'),
    );
    expect(access).toBe(TOKEN_PAIR.access_token);
  });

  test('wrong password -> inline error, stays on /login', async ({ page }) => {
    await page.route(`${API}/api/auth/login`, async (route) => {
      await route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Invalid credentials' }),
      });
    });

    await page.goto('/login');
    await page.getByLabel('Username').fill('admin');
    await page.getByLabel('Password').fill('wrong');
    await page.getByRole('button', { name: 'Sign in' }).click();

    // The page has multiple ``role=alert`` nodes (Next.js injects a
    // route announcer); scope by text to assert on ours specifically.
    await expect(page.getByText('Invalid username or password.')).toBeVisible();
    await expect(page).toHaveURL(/\/login\/?$/);
  });

  test('logout clears tokens and bounces to /login', async ({ page }) => {
    // Seed a logged-in session by stashing tokens before navigating.
    await page.route(`${API}/api/auth/me`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(ME),
      });
    });

    await page.addInitScript((pair) => {
      window.localStorage.setItem('gargantua.access_token', pair.access_token);
      window.localStorage.setItem('gargantua.refresh_token', pair.refresh_token);
      window.localStorage.setItem(
        'gargantua.access_expires_at_ms',
        String(Date.now() + pair.expires_in * 1000),
      );
    }, TOKEN_PAIR);

    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'gargantua' })).toBeVisible();

    await page.getByRole('button', { name: 'Log out' }).click();
    await expect(page).toHaveURL(/\/login\/?$/);

    const access = await page.evaluate(() =>
      window.localStorage.getItem('gargantua.access_token'),
    );
    expect(access).toBeNull();
  });
});
