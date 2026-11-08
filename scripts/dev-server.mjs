// Local dev server: serves the static site and runs api/concierge.js like Vercel does.
// Usage: npm start  ->  http://localhost:8000  (kept in scripts/ so Vercel does not treat it as the app entrypoint)   (put GEMINI_API_KEY in .env, which is gitignored)
import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");   // repo root
const PORT = Number(process.env.PORT || 8000);

// minimal .env loader (KEY=value lines)
const envFile = path.join(ROOT, ".env");
if (existsSync(envFile)) {
  for (const line of readFileSync(envFile, "utf8").split("\n")) {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/);
    if (m && !process.env[m[1]]) process.env[m[1]] = m[2].replace(/^["']|["']$/g, "");
  }
}
const apis = {
  "/api/concierge": await import(path.join(ROOT, "api", "concierge.js")),
  "/api/config": await import(path.join(ROOT, "api", "config.js")),
};

const TYPES = { ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
  ".json": "application/json", ".geojson": "application/geo+json", ".png": "image/png", ".jpg": "image/jpeg",
  ".svg": "image/svg+xml", ".ico": "image/x-icon" };
const BLOCKED = [/^\/\.env/, /^\/\.git/, /^\/\.venv/, /^\/node_modules/];

createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  try {
    if (apis[url.pathname]) {
      const api = apis[url.pathname];
      const chunks = [];
      for await (const c of req) chunks.push(c);
      const request = new Request(url, { method: req.method, headers: req.headers, body: req.method === "POST" ? Buffer.concat(chunks) : undefined });
      const handler = api[req.method];
      const response = handler ? await handler(request) : new Response("Method not allowed", { status: 405 });
      res.writeHead(response.status, Object.fromEntries(response.headers));
      res.end(Buffer.from(await response.arrayBuffer()));
      return;
    }
    if (BLOCKED.some((r) => r.test(url.pathname))) { res.writeHead(404).end(); return; }
    let file = path.join(ROOT, decodeURIComponent(url.pathname));
    if (!file.startsWith(ROOT)) { res.writeHead(403).end(); return; }
    if ((await stat(file).catch(() => null))?.isDirectory()) file = path.join(file, "index.html");
    const data = await readFile(file);
    res.writeHead(200, { "content-type": TYPES[path.extname(file)] || "application/octet-stream" });
    res.end(data);
  } catch {
    res.writeHead(404, { "content-type": "text/plain" }).end("Not found");
  }
}).listen(PORT, () => console.log(`ParkShark on http://localhost:${PORT}  (concierge ${process.env.GEMINI_API_KEY ? "configured" : "NOT configured: add GEMINI_API_KEY to .env"})`));
