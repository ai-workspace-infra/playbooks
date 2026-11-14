#!/usr/bin/env node
// qmd-spoke-sync — one command, two-way asynchronous Spoke <-> Hub sync over HTTPS.
// Managed by Ansible role qmd_standalone.
//   push: Outbox = local manifest diff vs last pushed manifest -> /plan -> /put
//         (only missing/changed files) -> /commit (Hub assigns fences in PG).
//   pull: /changes?since_fence (other origins) -> /file per latest key -> sha256
//         verify -> atomic write into read-only mirror -> advance since_fence.
// Idempotent and resumable: state advances only after verified steps.
// Transport: QMD_HUB_URL (https, Bearer token from 0600 file). Optional fallback
// QMD_HUB_TUNNEL_SSH opens a restricted SSH port-forward to the same API.
// Usage: qmd-spoke-sync [--push-only|--pull-only] [--force-push] [--watch] [--transport=https|tunnel|auto]
import { createHash } from "node:crypto";
import { spawn, spawnSync } from "node:child_process";
import { readFileSync, writeFileSync, existsSync, mkdirSync, renameSync, readdirSync, unlinkSync, appendFileSync, openSync, closeSync, statSync } from "node:fs";
import { join, dirname } from "node:path";
import { homedir } from "node:os";

const envFile = process.env.QMD_SPOKE_ENV || join(homedir(), "qmd-spoke", "sync.env");
const env = {};
for (const l of readFileSync(envFile, "utf8").split("\n")) { const m = l.match(/^([A-Z0-9_]+)=(.*)$/); if (m) env[m[1]] = m[2].replace(/^"|"$/g, ""); }
const args = new Set(process.argv.slice(2));
const transportArg = [...args].find((a) => a.startsWith("--transport="))?.split("=")[1] || env.QMD_HUB_TRANSPORT || "auto";
const ROOT = env.QMD_SPOKE_ROOT, ORIGIN = env.QMD_SPOKE_ORIGIN, STATE = join(ROOT, "state"), MIRROR = join(ROOT, "mirror"), QMD = join(ROOT, "bin", "qmd");
// Bearer credential: QMD_HUB_TOKEN_FILE (machine sync token, 0600) when present, else the
// `qmd login` profile for this Hub (<qmd config>/credentials.json, keyed by Hub origin).
function loadToken() {
  if (env.QMD_HUB_TOKEN_FILE && existsSync(env.QMD_HUB_TOKEN_FILE)) return readFileSync(env.QMD_HUB_TOKEN_FILE, "utf8").trim();
  const cfgDir = process.env.QMD_CONFIG_DIR || join(process.env.XDG_CONFIG_HOME || join(homedir(), ".config"), "qmd");
  const credFile = env.QMD_CREDENTIALS_FILE || process.env.QMD_CREDENTIALS_FILE || join(cfgDir, "credentials.json");
  try {
    const p = JSON.parse(readFileSync(credFile, "utf8")).profiles?.[new URL(env.QMD_HUB_URL).origin.toLowerCase()];
    if (p?.token) return String(p.token).trim();
  } catch { /* fall through */ }
  throw new Error(`no Hub credential: set QMD_HUB_TOKEN_FILE or run \`qmd login --hub ${env.QMD_HUB_URL}\``);
}
const TOKEN = loadToken();
mkdirSync(STATE, { recursive: true, mode: 0o700 }); mkdirSync(MIRROR, { recursive: true });
const log = (m) => { const l = `${new Date().toISOString()} ${m}`; console.error(l); appendFileSync(join(STATE, "sync.log"), l + "\n"); };
const sha = (b) => createHash("sha256").update(b).digest("hex");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// single-run lock
const lockPath = join(STATE, "sync.lock");
try { const fd = openSync(lockPath, "wx"); closeSync(fd); } catch {
  const age = Date.now() - statSync(lockPath).mtimeMs; if (age < 30 * 60 * 1000) { console.log("sync already running"); process.exit(0); } }
writeFileSync(lockPath, String(process.pid));
const unlock = () => { try { unlinkSync(lockPath); } catch {} };
process.on("exit", unlock); process.on("SIGINT", () => process.exit(130)); process.on("SIGTERM", () => process.exit(143));

let BASE = env.QMD_HUB_URL.replace(/\/+$/, ""); let tunnel = null; let transport = "https";
async function api(method, path, body, { raw = false, retries = 3 } = {}) {
  for (let i = 0; ; i++) {
    try {
      const r = await fetch(`${BASE}/api/v1/sync${path}`, { method, headers: { authorization: `Bearer ${TOKEN}`, ...(body ? { "content-type": "application/json" } : {}) }, body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(300000) });
      if (raw) { if (!r.ok) throw new Error(`${path} HTTP ${r.status}`); return { buf: Buffer.from(await r.arrayBuffer()), sha: r.headers.get("x-content-sha256") }; }
      const j = await r.json().catch(() => ({}));
      if (r.status >= 500 || r.status === 429) throw new Error(`${path} HTTP ${r.status} ${j.error || ""}`);
      if (!r.ok) return Object.assign(j, { _status: r.status });
      return j;
    } catch (e) { if (i + 1 >= retries) throw e; log(`retry ${i + 1} ${method} ${path}: ${e.message}`); await sleep(2000 * (i + 1)); }
  }
}
// Authenticated probe: the public ingress (forwardAuth) answers 401 on every path without the token.
async function healthy() { try { const r = await fetch(`${BASE}/api/v1/sync/health`, { headers: { authorization: `Bearer ${TOKEN}` }, signal: AbortSignal.timeout(10000) }); return r.ok; } catch { return false; } }
async function openTunnel() {
  const port = 18182 + Math.floor(Math.random() * 500);
  tunnel = spawn("ssh", ["-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-o", "ConnectTimeout=15", "-o", "IdentitiesOnly=yes", "-i", env.QMD_HUB_SSH_KEY, "-L", `127.0.0.1:${port}:${env.QMD_HUB_TUNNEL_TARGET}`, env.QMD_HUB_TUNNEL_SSH], { stdio: "ignore" });
  process.on("exit", () => tunnel?.kill());
  BASE = `http://127.0.0.1:${port}`;
  for (let i = 0; i < 20; i++) { await sleep(500); if (await healthy()) return; }
  throw new Error("ssh tunnel to hub sync API failed");
}
async function connect() {
  if (transportArg !== "tunnel" && (await healthy())) { transport = "https"; return; }
  if (transportArg === "https" || !env.QMD_HUB_TUNNEL_SSH) throw new Error(`hub ${BASE} unreachable over https`);
  log(`https ${BASE} unreachable; falling back to SSH tunnel ${env.QMD_HUB_TUNNEL_SSH}`);
  await openTunnel(); transport = "ssh-tunnel";
}
function walk(dir, base = "") {
  const out = []; if (!existsSync(dir)) return out;
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    if (e.name === ".git" || e.name.startsWith(".qmd-")) continue;
    const rel = base ? `${base}/${e.name}` : e.name;
    if (e.isDirectory()) out.push(...walk(join(dir, e.name), rel));
    else if (e.isFile() && e.name.endsWith(".md")) out.push(rel);
  }
  return out.sort();
}
const collections = (env.QMD_PUSH_COLLECTIONS || "").trim().split(/\s+/).filter(Boolean).map((s) => { const i = s.indexOf("="); return [s.slice(0, i), s.slice(i + 1)]; });
const readJson = (f, d) => { try { return JSON.parse(readFileSync(f, "utf8")); } catch { return d; } };
const writeJson = (f, v) => { writeFileSync(f + ".tmp", JSON.stringify(v)); renameSync(f + ".tmp", f); };
const qmdUpdate = () => { const r = spawnSync(QMD, ["update"], { stdio: "ignore" }); if (r.status !== 0) log("local qmd update failed"); };

async function push() {
  const manifest = {}; const paths = {};
  for (const [name, dir] of collections) { manifest[name] = {}; for (const rel of walk(dir)) { manifest[name][rel] = sha(readFileSync(join(dir, rel))); } paths[name] = dir; }
  const prev = readJson(join(STATE, "pushed.manifest.json"), null);
  let outbox = 0;
  for (const [c, files] of Object.entries(manifest)) { const p = prev?.[c] || {}; for (const [k, h] of Object.entries(files)) if (p[k] !== h) outbox++; for (const k of Object.keys(p)) if (!(k in files)) outbox++; }
  for (const c of Object.keys(prev || {})) if (!(c in manifest)) outbox++;
  if (prev && outbox === 0 && !args.has("--force-push")) { log("push skipped: outbox empty"); return { fence: null, outbox: 0, uploaded: 0, newFences: 0 }; }
  const plan = await api("POST", "/plan", { origin: ORIGIN, manifest });
  if (plan._status) throw new Error(`plan rejected: ${plan.error}`);
  let batch = [], size = 0, uploaded = 0;
  const flush = async () => { if (!batch.length) return; const r = await api("POST", "/put", { origin: ORIGIN, files: batch }); if (r._status) throw new Error(`put rejected: ${r.error}`); uploaded += r.written; batch = []; size = 0; };
  for (const [c, rel] of plan.need) {
    const buf = readFileSync(join(paths[c], rel));
    batch.push({ collection: c, path: rel, sha256: manifest[c][rel], content_b64: buf.toString("base64") }); size += buf.length;
    if (size > 4 * 1024 * 1024 || batch.length >= 200) await flush();
  }
  await flush();
  const commit = await api("POST", "/commit", { origin: ORIGIN, manifest });
  if (commit._status) throw new Error(`commit rejected (${commit._status}): ${commit.error} ${commit.count ?? ""}`);
  writeJson(join(STATE, "pushed.manifest.json"), manifest); writeFileSync(join(STATE, "last_push_fence"), String(commit.fence));
  log(`push ok: outbox=${outbox} uploaded=${uploaded} removed=${commit.removed} new_fences=${commit.new} hub_fence=${commit.fence}`);
  return { fence: commit.fence, outbox, uploaded, newFences: commit.new };
}

async function pull() {
  const since = Number(readFileSync(join(STATE, "since_fence"), "utf8").trim() || 0) || 0;
  const d = await api("GET", `/changes?since_fence=${since}&exclude_origin=${encodeURIComponent(ORIGIN)}`);
  if (d._status) throw new Error(`changes rejected: ${d.error}`);
  // latest change per key wins (highest fence); content hash decides whether a fetch is needed
  const latest = new Map();
  for (const c of d.changes) latest.set(`${c.origin}\t${c.collection}\t${c.path}`, c);
  let fetched = 0, deleted = 0, failed = 0;
  for (const c of latest.values()) {
    const t = join(MIRROR, c.origin, c.collection, c.path);
    if (!t.startsWith(MIRROR + "/") || c.path.includes("..")) { failed++; continue; }
    if (c.op === "delete") { if (existsSync(t)) { unlinkSync(t); deleted++; } continue; }
    if (existsSync(t) && sha(readFileSync(t)) === c.sha256) continue;
    try {
      const r = await api("GET", `/file?origin=${encodeURIComponent(c.origin)}&collection=${encodeURIComponent(c.collection)}&path=${encodeURIComponent(c.path)}`, null, { raw: true });
      if (sha(r.buf) !== c.sha256) { failed++; continue; } // superseded on hub; next run catches the newer fence
      mkdirSync(dirname(t), { recursive: true }); writeFileSync(t + ".qmd-tmp", r.buf); renameSync(t + ".qmd-tmp", t); fetched++;
    } catch (e) { failed++; log(`pull ${c.origin}/${c.collection}/${c.path}: ${e.message}`); }
  }
  const next = failed ? since : d.max_fence;
  writeFileSync(join(STATE, "since_fence.tmp"), String(next)); renameSync(join(STATE, "since_fence.tmp"), join(STATE, "since_fence"));
  log(`pull ${failed ? "partial" : "ok"}: changes=${d.changes.length} fetched=${fetched} deleted=${deleted} failed=${failed} since_fence ${since} -> ${next}`);
  return { since, next, changes: d.changes.length, fetched, failed };
}

async function once() {
  for (const g of (env.QMD_GIT_SOURCES || "").split(/\s+/).filter(Boolean)) {
    const r = spawnSync("git", ["-C", g, "pull", "-q", "--ff-only"], { stdio: "ignore", timeout: 120000 }); if (r.status !== 0) log(`git refresh skipped: ${g}`);
  }
  qmdUpdate();
  const p = args.has("--pull-only") ? null : await push();
  const q = args.has("--push-only") ? null : await pull();
  if (q && (q.fetched || q.changes)) qmdUpdate();
  console.log(JSON.stringify({ origin: ORIGIN, transport, hub: env.QMD_HUB_URL, push: p, pull: q }));
  return q && q.failed ? 1 : 0;
}

if (!existsSync(join(STATE, "since_fence"))) writeFileSync(join(STATE, "since_fence"), "0");
try {
  await connect();
  if (!args.has("--watch")) process.exit(await once());
  // --watch: subscribe to SSE fence events and sync on change (Ctrl-C to stop)
  await once();
  for (;;) {
    try {
      const r = await fetch(`${BASE}/api/v1/sync/events`, { headers: { authorization: `Bearer ${TOKEN}` } });
      let buf = ""; let last = null;
      for await (const chunk of r.body) {
        buf += Buffer.from(chunk).toString("utf8");
        let i; while ((i = buf.indexOf("\n\n")) >= 0) { const ev = buf.slice(0, i); buf = buf.slice(i + 2);
          const m = ev.match(/data: \{"max_fence":(\d+)\}/); if (m && m[1] !== last) { if (last !== null) await once(); last = m[1]; } }
      }
    } catch (e) { log(`watch: ${e.message}`); }
    await sleep(5000);
  }
} catch (e) { log(`sync failed: ${e.message}`); process.exit(1); }
