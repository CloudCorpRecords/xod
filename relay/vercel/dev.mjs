// The Vercel relay on this computer, with its data in memory (lost when it stops): for trying it out and for the tests.
//   node relay/vercel/dev.mjs [port] [domain]      e.g. node relay/vercel/dev.mjs 8793 localhost:8793
import http from "node:http";
import { handle, memory } from "../relay.mjs";

const port = Number(process.argv[2] || 8793);
const domain = process.argv[3] || `localhost:${port}`;
const store = memory();

http.createServer(async (req, res) => {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const body = chunks.length ? Buffer.concat(chunks) : undefined;
  const request = new Request(`http://${req.headers.host}${req.url}`, { method: req.method, headers: req.headers,
    body: ["GET", "HEAD"].includes(req.method) ? undefined : body });
  const path = new URL(request.url).pathname;
  const out = await handle(request, store, domain, path, req.socket.remoteAddress);
  res.writeHead(out.status, Object.fromEntries(out.headers));
  res.end(Buffer.from(await out.arrayBuffer()));
}).listen(port, "127.0.0.1", () => console.log(`Xod Post Office (Vercel version, in memory) on http://127.0.0.1:${port}  domain ${domain}`));
