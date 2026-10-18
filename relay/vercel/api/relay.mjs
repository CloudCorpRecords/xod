// Vercel runs this for every path (vercel.json sends them all here, with the original path in ?p=).
import { handle, storeFromEnv } from "../../relay.mjs";

async function run(request) {
  const store = storeFromEnv(process.env);
  if (!store) {
    return new Response(JSON.stringify({ detail: "This relay has no database yet: connect Redis (Upstash) to the project in Vercel → Storage." }),
      { status: 503, headers: { "content-type": "application/json" } });
  }
  const url = new URL(request.url);
  const path = url.searchParams.get("p") || url.pathname;
  const ip = (request.headers.get("x-forwarded-for") || "?").split(",")[0].trim();
  return handle(request, store, process.env.XOD_RELAY_DOMAIN || request.headers.get("x-forwarded-host") || url.host, path, ip);
}

export const GET = run;
export const POST = run;
export const DELETE = run;
