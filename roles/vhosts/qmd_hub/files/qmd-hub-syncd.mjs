#!/usr/bin/env node
// qmd-hub-syncd — Hub-Spoke Delta Sync API (zero dependencies).
// Managed by Ansible role qmd_hub. Served behind Traefik TLS (https://<hub>/api/v1/sync/*).
//   GET  /api/v1/sync/health                         (no auth)
//   POST /api/v1/sync/plan    {origin, manifest}     -> {need:[[collection,path]], remove:n}
//   POST /api/v1/sync/put     {origin, files:[{collection,path,sha256,content_b64}]}
//   POST /api/v1/sync/commit  {origin, manifest}     -> {fence, new}
//   GET  /api/v1/sync/changes?since_fence=N&exclude_origin=o -> {changes, max_fence}
//   GET  /api/v1/sync/file?origin=&collection=&path= -> raw markdown (X-Content-Sha256)
//   GET  /api/v1/sync/events                         -> SSE "fence" events
// manifest = {<collection>: {<path>: <sha256>}}. Auth: Authorization: Bearer <token file>.
import { createServer } from "node:http";
import { createHash, timingSafeEqual } from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { readFileSync, existsSync, mkdirSync, writeFileSync, renameSync, readdirSync, statSync, unlinkSync } from "node:fs";
import { join, dirname } from "node:path";

const run = promisify(execFile);
const ROOT = process.env.QMD_HUB_ROOT || "/opt/qmd-hub";
const DATA = join(ROOT, "data", "origins");
const HOST = process.env.QMD_HUB_SYNC_HOST || "127.0.0.1";
const PORT = Number(process.env.QMD_HUB_SYNC_PORT || 8182);
const TOKEN = readFileSync(process.env.QMD_HUB_SYNC_TOKEN_FILE || "/etc/qmd-hub/sync-token", "utf8").trim();
const MAX_BODY = 32 * 1024 * 1024;
if (TOKEN.length < 32) throw new Error("sync token too short");

const SLUG = /^[a-z0-9][a-z0-9._-]{0,62}$/;
const okPath = (p) => typeof p === "string" && p.length <= 1024 && p.endsWith(".md") && !p.startsWith("/")
  && !p.split("/").some((s) => s === ".." || s === "." || s === "" || s.startsWith(".qmd-")) && !/[\t\n\r\0\\]/.test(p);
const sha = (b) => createHash("sha256").update(b).digest("hex");
const log = (...a) => console.log(new Date().toISOString(), ...a);

function authorized(req) {
  const h = req.headers.authorization || "";
  if (!h.startsWith("Bearer ")) return false;
  const a = Buffer.from(h.slice(7).trim()); const b = Buffer.from(TOKEN);
  return a.length === b.length && timingSafeEqual(a, b);
}
function send(res, code, obj, headers = {}) {
  const body = typeof obj === "string" ? obj : JSON.stringify(obj);
  res.writeHead(code, { "content-type": typeof obj === "string" ? "text/markdown; charset=utf-8" : "application/json", "cache-control": "no-store", ...headers });
  res.end(body);
}
async function body(req) {
  const chunks = []; let n = 0;
  for await (const c of req) { n += c.length; if (n > MAX_BODY) throw Object.assign(new Error("body too large"), { code: 413 }); chunks.push(c); }
  return JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
}
function listFiles(dir, base = "") {
  const out = [];
  if (!existsSync(dir)) return out;
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    if (e.name.startsWith(".qmd-")) continue;
    const rel = base ? `${base}/${e.name}` : e.name;
    if (e.isDirectory()) out.push(...listFiles(join(dir, e.name), rel));
    else if (e.isFile() && e.name.endsWith(".md")) out.push(rel);
  }
  return out;
}
function checkManifest(origin, manifest) {
  if (!SLUG.test(origin || "") || origin === "hub") throw Object.assign(new Error("invalid origin"), { code: 400 });
  if (!manifest || typeof manifest !== "object") throw Object.assign(new Error("manifest required"), { code: 400 });
  for (const [c, files] of Object.entries(manifest)) {
    if (!SLUG.test(c)) throw Object.assign(new Error(`invalid collection ${c}`), { code: 400 });
    for (const [p, h] of Object.entries(files)) if (!okPath(p) || !/^[0-9a-f]{64}$/.test(h)) throw Object.assign(new Error(`invalid entry ${c}/${p}`), { code: 400 });
  }
}
const fileSha = (f) => (existsSync(f) ? sha(readFileSync(f)) : null);

let busy = Promise.resolve();
const serial = (fn) => (busy = busy.then(fn, fn));

async function maxFence() {
  const { stdout } = await run(join(ROOT, "bin", "qmd-hub-changes"), ["999999999999"], { maxBuffer: 1 << 20 });
  return Number((stdout.match(/#max_fence (\d+)/) || [])[1] || 0);
}

const sseClients = new Set(); let lastFence = -1;
setInterval(async () => {
  if (!sseClients.size) return;
  try {
    const f = await maxFence();
    for (const r of sseClients) r.write(f !== lastFence ? `event: fence\ndata: {"max_fence":${f}}\n\n` : `: keepalive\n\n`);
    lastFence = f;
  } catch (e) { log("sse poll", e.message); }
}, 5000).unref();

const server = createServer(async (req, res) => {
  const url = new URL(req.url, "http://hub");
  const p = url.pathname.replace(/^\/api\/v1\/sync/, "");
  const t0 = Date.now();
  try {
    if (!url.pathname.startsWith("/api/v1/sync/")) return send(res, 404, { error: "not found" });
    if (p === "/health" && req.method === "GET") return send(res, 200, { ok: true, service: "qmd-hub-syncd" });
    if (!authorized(req)) return send(res, 401, { error: "unauthorized" }, { "www-authenticate": "Bearer" });

    if (p === "/plan" && req.method === "POST") {
      const { origin, manifest } = await body(req); checkManifest(origin, manifest);
      const need = []; let remove = 0;
      for (const [c, files] of Object.entries(manifest)) {
        const dir = join(DATA, origin, c);
        for (const [rel, h] of Object.entries(files)) if (fileSha(join(dir, rel)) !== h) need.push([c, rel]);
        remove += listFiles(dir).filter((rel) => !(rel in files)).length;
      }
      return send(res, 200, { need, remove });
    }
    if (p === "/put" && req.method === "POST") {
      const { origin, files } = await body(req);
      if (!SLUG.test(origin || "") || origin === "hub" || !Array.isArray(files)) return send(res, 400, { error: "bad request" });
      let written = 0;
      for (const f of files) {
        if (!SLUG.test(f.collection || "") || !okPath(f.path) || !/^[0-9a-f]{64}$/.test(f.sha256 || "")) return send(res, 400, { error: `invalid file ${f.path}` });
        const buf = Buffer.from(f.content_b64 || "", "base64");
        if (sha(buf) !== f.sha256) return send(res, 422, { error: `sha mismatch ${f.collection}/${f.path}` });
        const dest = join(DATA, origin, f.collection, f.path);
        mkdirSync(dirname(dest), { recursive: true });
        writeFileSync(`${dest}.qmd-tmp`, buf); renameSync(`${dest}.qmd-tmp`, dest); written++;
      }
      return send(res, 200, { written });
    }
    if (p === "/commit" && req.method === "POST") {
      const { origin, manifest } = await body(req); checkManifest(origin, manifest);
      const out = await serial(async () => {
        const missing = [];
        for (const [c, files] of Object.entries(manifest)) {
          const dir = join(DATA, origin, c);
          for (const [rel, h] of Object.entries(files)) if (fileSha(join(dir, rel)) !== h) missing.push(`${c}/${rel}`);
        }
        if (missing.length) return { code: 409, body: { error: "incomplete upload", missing: missing.slice(0, 50), count: missing.length } };
        let removed = 0;
        for (const [c, files] of Object.entries(manifest)) {
          const dir = join(DATA, origin, c);
          for (const rel of listFiles(dir)) if (!(rel in files)) { unlinkSync(join(dir, rel)); removed++; }
        }
        const { stdout } = await run(join(ROOT, "bin", "qmd-hub-ingest"), [origin], { maxBuffer: 4 << 20, timeout: 600000 });
        const m = stdout.match(/fence=(\d+) new=(\d+)/) || [];
        return { code: 200, body: { fence: Number(m[1] || 0), new: Number(m[2] || 0), removed } };
      });
      return send(res, out.code, out.body);
    }
    if (p === "/changes" && req.method === "GET") {
      const since = url.searchParams.get("since_fence") || "0"; const excl = url.searchParams.get("exclude_origin") || "";
      if (!/^\d+$/.test(since) || (excl && !SLUG.test(excl))) return send(res, 400, { error: "bad query" });
      const { stdout } = await run(join(ROOT, "bin", "qmd-hub-changes"), excl ? [since, excl] : [since], { maxBuffer: 64 << 20 });
      const changes = []; let max = 0;
      for (const line of stdout.split("\n")) {
        if (!line) continue;
        const mm = line.match(/^#max_fence (\d+)$/); if (mm) { max = Number(mm[1]); continue; }
        const [fence, origin, collection, path, sha256, op] = line.split("\t");
        changes.push({ fence: Number(fence), origin, collection, path, sha256, op });
      }
      return send(res, 200, { changes, max_fence: max });
    }
    if (p === "/file" && req.method === "GET") {
      const o = url.searchParams.get("origin") || "", c = url.searchParams.get("collection") || "", rel = url.searchParams.get("path") || "";
      if (!SLUG.test(o) || !SLUG.test(c) || !okPath(rel)) return send(res, 400, { error: "bad query" });
      const f = join(DATA, o, c, rel);
      if (!existsSync(f) || !statSync(f).isFile()) return send(res, 404, { error: "not found" });
      const buf = readFileSync(f);
      res.writeHead(200, { "content-type": "text/markdown; charset=utf-8", "x-content-sha256": sha(buf), "cache-control": "no-store" });
      return res.end(buf);
    }
    if (p === "/events" && req.method === "GET") {
      res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-store", connection: "keep-alive", "x-accel-buffering": "no" });
      res.write(`event: fence\ndata: {"max_fence":${await maxFence()}}\n\n`);
      sseClients.add(res); req.on("close", () => sseClients.delete(res));
      return;
    }
    return send(res, 404, { error: "not found" });
  } catch (e) {
    log("error", req.method, p, e.message);
    if (!res.headersSent) send(res, e.code === 413 || e.code === 400 ? e.code : 500, { error: e.code ? e.message : "internal error" });
  } finally {
    if (p !== "/events") log(req.method, url.pathname, res.statusCode, `${Date.now() - t0}ms`);
  }
});
server.requestTimeout = 300000;
server.listen(PORT, HOST, () => log(`qmd-hub-syncd listening on http://${HOST}:${PORT}/api/v1/sync/`));
