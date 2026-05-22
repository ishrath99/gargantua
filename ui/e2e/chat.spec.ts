/**
 * Chat surface end-to-end smokes.
 *
 * Stubs ``/me/agents`` so the picker has one card, then stubs the
 * SSE run endpoint to emit a tiny scripted event sequence.  We don't
 * touch the backend.
 *
 * Why a streaming stub via ``page.route`` works
 *   Playwright's ``route.fulfill`` takes a ``body`` string and a
 *   ``headers`` object — the browser treats that as the entire
 *   response.  For an SSE-style read we hand it the concatenated
 *   ``data: <json>\\n\\n`` framing including the ``[DONE]``
 *   sentinel; the chat surface's reader drains it the same way
 *   it would drain a real stream.  This isn't true incremental
 *   streaming (the browser sees one big body), but it covers the
 *   parser + reducer wiring, which is what an e2e is for.
 */

import { expect, test, type Page } from '@playwright/test';

const API = 'http://stub.test';

const TOKEN_PAIR = {
  access_token: 'acc.test.token',
  refresh_token: 'ref.test.token',
  token_type: 'bearer',
  expires_in: 900,
};

const USER_ME = {
  id: '22222222-2222-2222-2222-222222222222',
  username: 'alice',
  role: 'user' as const,
  is_active: true,
  scopes: ['gargantua:user'],
};

const AGENT = {
  id: '33333333-3333-3333-3333-333333333333',
  name: 'SRE Helper',
  description: 'On-call assistant for gargantua.',
  model: 'openai:gpt-4o-mini',
  mcp_server_ids: [],
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

function sseBody(events: Array<Record<string, unknown>>): string {
  // Mirror the backend's framing exactly: each event is a single
  // ``data: <json>\n\n`` frame, terminated by ``data: [DONE]\n\n``.
  return (
    events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('') +
    'data: [DONE]\n\n'
  );
}

test.describe('chat picker + agent surface', () => {
  test.beforeEach(async ({ page }) => {
    await page.route(`${API}/auth/me`, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(USER_ME),
      }),
    );
    await page.route(`${API}/me/agents`, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: [AGENT], total: 1 }),
      }),
    );
    await page.route(`${API}/me/teams`, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: [], total: 0 }),
      }),
    );
    await seedSession(page);
  });

  test('picker lists agents and links to the chat surface', async ({ page }) => {
    await page.goto('/chat/');
    await expect(page.getByText('SRE Helper').first()).toBeVisible();
    await expect(page.getByText('openai:gpt-4o-mini').first()).toBeVisible();
  });

  test('streamed run renders deltas + tool call inline', async ({ page }) => {
    let runPayload: Record<string, unknown> | null = null;

    await page.route(
      new RegExp(`${API}/v1/agents/${AGENT.id}/runs/?`),
      async (route, request) => {
        runPayload = request.postDataJSON() as Record<string, unknown>;
        await route.fulfill({
          status: 200,
          headers: { 'content-type': 'text/event-stream' },
          body: sseBody([
            { event: 'RunStarted', run_id: 'r-1', session_id: 's-1' },
            { event: 'RunContent', content: 'Looking ' },
            { event: 'RunContent', content: 'up issues…' },
            {
              event: 'ToolCallStarted',
              tool: { tool_call_id: 'tc-1', tool_name: 'github_search' },
            },
            {
              event: 'ToolCallCompleted',
              tool: {
                tool_call_id: 'tc-1',
                tool_name: 'github_search',
                tool_args: { q: 'is:open' },
                result: '[1, 2]',
              },
            },
            { event: 'RunContent', content: ' Found 2.' },
            { event: 'RunCompleted', content: 'Looking up issues… Found 2.' },
          ]),
        });
      },
    );

    await page.goto(`/chat/agent/?id=${AGENT.id}`);

    // Surface mounted with the right header.
    await expect(page.getByRole('heading', { name: 'SRE Helper' })).toBeVisible();

    // Send a prompt.  Use ``getByRole('textbox')`` to disambiguate
    // from the "Send message" button which also has "Message" in its
    // accessible name.
    await page.getByRole('textbox', { name: 'Message' }).fill('list open issues');
    await page.getByRole('button', { name: /send message/i }).click();

    // The user bubble shows the prompt.
    await expect(page.getByText('list open issues')).toBeVisible();

    // The assistant accumulates the deltas into one rendered string.
    await expect(page.getByText('Looking up issues… Found 2.')).toBeVisible();

    // The tool-call card is threaded in.
    await expect(page.getByText('github_search')).toBeVisible();

    // The send button reappears once streaming ends.
    await expect(page.getByRole('button', { name: /send message/i })).toBeVisible();

    // And the request included our chosen ``session_id`` + ``stream=true``.
    // ``runPayload`` is typed via the outer closure; ``expect`` doesn't
    // narrow it for TS, so we re-bind to a local cast for the asserts.
    expect(runPayload).not.toBeNull();
    const captured = runPayload as unknown as Record<string, unknown>;
    expect(captured.stream).toBe(true);
    expect(typeof captured.session_id).toBe('string');
    expect(captured.input).toBe('list open issues');
  });
});
