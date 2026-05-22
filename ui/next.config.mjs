/**
 * Next.js config for the gargantua UI.
 *
 * We build with ``output: 'export'`` so ``next build`` produces a
 * fully static ``out/`` directory.  FastAPI serves that directory as
 * ``StaticFiles`` under ``/`` in PR 18; in dev we run Next on :3000
 * and proxy API calls to :7777 (configured per-request, not here).
 *
 * The trade-offs of static export:
 *   * No middleware.ts (we gate routes client-side via <AuthGuard>).
 *   * No server components doing data fetching at request time —
 *     every page either is static or fetches client-side via fetch.
 *   * No image optimisation API (we use the unoptimized escape hatch).
 *
 * For a DB-first SPA-style app this is exactly the shape we want.
 */
/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'export',
  // Required when ``output: 'export'`` — Next refuses to ship the
  // image optimisation API in a static build.
  images: { unoptimized: true },
  // Trailing slashes keep the static export tree friendly to any
  // server that maps ``/foo/`` to ``foo/index.html``.
  trailingSlash: true,
  // The UI never knows its own deployment path; the wrapper FastAPI
  // app mounts ``out/`` at ``/`` so relative links are correct.
  reactStrictMode: true,
};

export default nextConfig;
