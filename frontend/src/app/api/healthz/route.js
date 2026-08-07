/**
 * Liveness of the frontend container only: it does not test the backend,
 * otherwise an LLM outage would restart the UI in a loop for no reason.
 */

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET() {
  return Response.json({ status: 'ok', service: 'medibot-frontend' });
}
