/**
 * BFF: server-side relay to the FastAPI backend.
 *
 * The browser only ever talks to its own origin. The API key stays on the
 * server (`BACKEND_API_KEY`, never `NEXT_PUBLIC_*`, which would be inlined into
 * the JavaScript bundle and therefore readable by any user), and the backend
 * URL is resolved at runtime — a `NEXT_PUBLIC_*` variable would be frozen at
 * `next build` time.
 */

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
// A CPU-bound LLM generation can exceed a minute.
export const maxDuration = 300;

const BACKEND_URL = (process.env.BACKEND_URL || 'http://localhost:8000').replace(/\/+$/, '');
const BACKEND_API_KEY = process.env.BACKEND_API_KEY || '';

// Strict allowlist: the relay must expose only the routes the UI needs, and
// above all must not become an open proxy into the application network.
const ALLOWED_ROUTES = [
  { method: 'POST', pattern: /^query$/ },
  { method: 'POST', pattern: /^query\/stream$/ },
  { method: 'POST', pattern: /^upload$/ },
  { method: 'GET', pattern: /^documents$/ },
  { method: 'DELETE', pattern: /^documents\/[^/]+$/ },
  { method: 'GET', pattern: /^ready$/ },
];

const FORWARDED_REQUEST_HEADERS = ['content-type', 'accept', 'x-request-id'];
const FORWARDED_RESPONSE_HEADERS = ['content-type', 'x-request-id', 'retry-after'];

function isAllowed(method, path) {
  return ALLOWED_ROUTES.some((route) => route.method === method && route.pattern.test(path));
}

function jsonError(status, detail) {
  return Response.json({ detail }, { status });
}

async function proxy(request, context) {
  const { path = [] } = await context.params;
  const segments = Array.isArray(path) ? path : [path];

  // Defence in depth: `..` cannot appear in segments already split by Next,
  // but reject any attempt explicitly.
  if (segments.some((segment) => !segment || segment === '.' || segment === '..')) {
    return jsonError(400, 'Invalid path.');
  }

  const targetPath = segments.join('/');
  if (!isAllowed(request.method, targetPath)) {
    return jsonError(404, 'Unknown route.');
  }

  const url = new URL(`${BACKEND_URL}/${segments.map(encodeURIComponent).join('/')}`);
  url.search = new URL(request.url).search;

  const headers = new Headers();
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  if (BACKEND_API_KEY) headers.set('X-API-Key', BACKEND_API_KEY);

  const hasBody = request.method !== 'GET' && request.method !== 'DELETE';

  let upstream;
  try {
    upstream = await fetch(url, {
      method: request.method,
      headers,
      // `duplex: 'half'` is required to relay a streamed body (PDF upload)
      // without buffering it entirely in Next's memory.
      ...(hasBody ? { body: request.body, duplex: 'half' } : {}),
      cache: 'no-store',
    });
  } catch (error) {
    console.error(`[bff] backend unreachable on ${targetPath}:`, error.message);
    return jsonError(502, 'The analysis service is unreachable.');
  }

  const responseHeaders = new Headers();
  for (const name of FORWARDED_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }
  responseHeaders.set('Cache-Control', 'no-store');
  // Prevents any intermediate buffering of the SSE stream.
  responseHeaders.set('X-Accel-Buffering', 'no');

  return new Response(upstream.body, {
    status: upstream.status,
    headers: responseHeaders,
  });
}

export const GET = proxy;
export const POST = proxy;
export const DELETE = proxy;
