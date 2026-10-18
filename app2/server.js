// PPE Monitor - mock-up server for the pi-07 site-entry camera.
//
// What is real here:
//   * the live frame, proxied from JevCam (the Flask app on :8000 that owns the camera)
//   * JevCam's person detection boxes, when it reports any
//   * ntfy / Pushover pushes, when a topic or keys are configured in Settings
// What is simulated here:
//   * worker entries and per-item PPE detections (hard hat, vest, glasses, gloves)
//
// Only Node built-ins. Listens on process.env.PORT (3000); nginx fronts it on :80.

const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');
const os = require('os');
const crypto = require('crypto');

const PORT = Number(process.env.PORT) || 3000;
const JEVCAM = process.env.JEVCAM_URL || 'http://127.0.0.1:8000';
const ROOT = __dirname;
const DATA = path.join(ROOT, 'data');
const PHOTOS = path.join(DATA, 'photos');
fs.mkdirSync(PHOTOS, { recursive: true });

const PPE_ITEMS = ['hardHat', 'vest', 'glasses', 'gloves'];
const PPE_LABEL = { hardHat: 'Hard hat', vest: 'Safety vest', glasses: 'Safety glasses', gloves: 'Gloves' };

// ---------------------------------------------------------------------------
// Settings (persisted)
// ---------------------------------------------------------------------------
const DEFAULT_SETTINGS = {
  siteName: 'Gate A - North Entrance',
  required: { hardHat: true, vest: true, glasses: true, gloves: false },
  sensitivity: 'medium',            // low | medium | high  -> confidence threshold
  alertCooldownSec: 20,             // suppress duplicate alerts for the same worker
  shift: { dayStart: 6, nightStart: 18 },
  notifications: {
    ntfy: { enabled: false, server: 'https://ntfy.sh', topic: '' },
    pushover: { enabled: false, user: '', token: '' },
    onlyCritical: false,            // only push when hard hat or vest missing
  },
  simulation: { running: true, meanIntervalSec: 30, complianceTarget: 0.87 },
  camera: { scanIntervalSec: 2 },  // how often JevCam is polled for a frame + detections (1-60)
};
const SCAN_MIN_SEC = 1, SCAN_MAX_SEC = 60;
function clampScan(v) { const n = Math.round(Number(v)); return Number.isFinite(n) ? Math.min(SCAN_MAX_SEC, Math.max(SCAN_MIN_SEC, n)) : DEFAULT_SETTINGS.camera.scanIntervalSec; }
const SENSITIVITY_THRESHOLD = { low: 0.35, medium: 0.5, high: 0.7 };

const SETTINGS_FILE = path.join(DATA, 'settings.json');
const EVENTS_FILE = path.join(DATA, 'events.json');

function loadJson(file, fallback) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return fallback; }
}
function saveJson(file, value) {
  const tmp = file + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(value, null, 2));
  fs.renameSync(tmp, file);
}
function deepMerge(base, patch) {
  if (patch === null || typeof patch !== 'object' || Array.isArray(patch)) return patch === undefined ? base : patch;
  const out = { ...base };
  for (const [k, v] of Object.entries(patch)) {
    out[k] = (base && typeof base[k] === 'object' && !Array.isArray(base[k])) ? deepMerge(base[k], v) : v;
  }
  return out;
}

let settings = deepMerge(DEFAULT_SETTINGS, loadJson(SETTINGS_FILE, {}));
settings.camera.scanIntervalSec = clampScan(settings.camera.scanIntervalSec);

// ---------------------------------------------------------------------------
// Event store: entries (every worker seen) and alerts (non-compliant entries)
// ---------------------------------------------------------------------------
const MAX_EVENTS = 2000;
let entries = loadJson(EVENTS_FILE, null);
const photoBytes = new Map();   // id -> Buffer (recent only; disk is the long-term copy)

function persistEvents() {
  if (entries.length > MAX_EVENTS) entries = entries.slice(-MAX_EVENTS);
  saveJson(EVENTS_FILE, entries);
}

function rnd(a, b) { return a + Math.random() * (b - a); }
function pick(arr) { return arr[Math.floor(Math.random() * arr.length)]; }
function round2(x) { return Math.round(x * 100) / 100; }

function shiftFor(date) {
  const h = date.getHours();
  const { dayStart, nightStart } = settings.shift;
  return h >= dayStart && h < nightStart ? 'day' : 'night';
}

// Simulate what a PPE model would report for one worker walking through the gate.
function simulateDetections(forceCompliant) {
  const det = {};
  const target = settings.simulation.complianceTarget;
  const compliantIntent = forceCompliant === undefined ? Math.random() < target : forceCompliant;
  for (const item of PPE_ITEMS) {
    const required = settings.required[item];
    let present;
    if (compliantIntent) present = required ? true : Math.random() < 0.6;
    else present = Math.random() < (item === 'hardHat' ? 0.55 : item === 'vest' ? 0.6 : 0.5);
    if (!compliantIntent && PPE_ITEMS.every((i) => det[i] ? det[i].present : true) && item === 'gloves' && present) {
      present = false; // make sure a non-compliant intent misses at least one item
    }
    let confidence = present ? rnd(0.72, 0.99) : rnd(0.02, 0.3);
    if (Math.random() < 0.05) confidence = rnd(0.42, 0.68); // borderline call: sensitivity decides
    det[item] = { present, confidence: round2(confidence) };
  }
  return det;
}

function evaluate(det) {
  const threshold = SENSITIVITY_THRESHOLD[settings.sensitivity] || 0.5;
  const missing = [];
  const uncertain = [];
  for (const item of PPE_ITEMS) {
    if (!settings.required[item]) continue;
    const d = det[item];
    if (d.confidence < threshold) {
      missing.push(item);
      if (d.confidence >= 0.3) uncertain.push(item);
    }
  }
  return { compliant: missing.length === 0, missing, uncertain, threshold };
}

let lastAlertByWorker = new Map();
const workerPool = Array.from({ length: 48 }, (_, i) => `W-${1000 + Math.floor(rnd(0, 900)) + i}`);

function makeEntry(when, opts = {}) {
  const det = simulateDetections(opts.forceCompliant);
  const verdict = evaluate(det);
  const id = crypto.randomBytes(6).toString('hex');
  const box = opts.box || {
    left: round2(rnd(0.28, 0.5)), top: round2(rnd(0.12, 0.25)),
    right: 0, bottom: 0,
  };
  if (!box.right) { box.right = round2(box.left + rnd(0.14, 0.22)); box.bottom = round2(box.top + rnd(0.55, 0.72)); }
  const entry = {
    id,
    ts: when.toISOString(),
    worker: opts.worker || pick(workerPool),
    shift: shiftFor(when),
    detections: det,
    compliant: verdict.compliant,
    missing: verdict.missing,
    uncertain: verdict.uncertain,
    box,
    source: opts.source || 'simulated',
    photo: null,
    alert: null,
  };
  if (!verdict.compliant) {
    const last = lastAlertByWorker.get(entry.worker) || 0;
    const suppressed = when.getTime() - last < settings.alertCooldownSec * 1000;
    entry.alert = {
      severity: verdict.missing.includes('hardHat') || verdict.missing.includes('vest') ? 'critical' : 'warning',
      suppressed,
      acknowledged: false,
      notified: {},
    };
    if (!suppressed) lastAlertByWorker.set(entry.worker, when.getTime());
  }
  return entry;
}

// Seed two days of plausible history on first run so the dashboard is not empty.
function seedHistory() {
  const out = [];
  const now = new Date();
  for (let dayOffset = 6; dayOffset >= 0; dayOffset--) {
    const day = new Date(now); day.setDate(now.getDate() - dayOffset); day.setHours(0, 0, 0, 0);
    const count = dayOffset === 0 ? 0 : 55 + Math.floor(rnd(0, 40));
    for (let i = 0; i < count; i++) {
      // bias toward shift changes: 06:00-08:00 and 18:00-20:00, plus a mid-day trickle
      const r = Math.random();
      let hour = r < 0.4 ? rnd(6, 8.5) : r < 0.7 ? rnd(18, 20.5) : rnd(0, 24);
      const t = new Date(day); t.setHours(Math.floor(hour), Math.floor((hour % 1) * 60), Math.floor(rnd(0, 60)), 0);
      out.push(makeEntry(t));
    }
  }
  // today: from midnight up to now, same shape
  const sinceMidnight = (now - new Date(now).setHours(0, 0, 0, 0)) / 3600000;
  const todayCount = Math.round(sinceMidnight * 4.5);
  for (let i = 0; i < todayCount; i++) {
    const r = Math.random();
    let hour = r < 0.45 && sinceMidnight > 6 ? rnd(6, Math.min(8.5, sinceMidnight)) : rnd(0, sinceMidnight);
    const t = new Date(now); t.setHours(Math.floor(hour), Math.floor((hour % 1) * 60), Math.floor(rnd(0, 60)), 0);
    if (t < now) out.push(makeEntry(t));
  }
  out.sort((a, b) => a.ts.localeCompare(b.ts));
  // older alerts were handled by the previous shift; the last couple of hours are still open
  for (const e of out) if (e.alert) e.alert.acknowledged = now - new Date(e.ts) > 2 * 36e5 || Math.random() < 0.4;
  return out;
}

if (!Array.isArray(entries) || entries.length === 0) {
  entries = seedHistory();
  persistEvents();
}

// ---------------------------------------------------------------------------
// Camera: JevCam proxy
// ---------------------------------------------------------------------------
const camera = { available: false, lastFrameAt: null, lastState: null, error: null, persons: [] };

function fetchBuffer(url, timeoutMs = 4000) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks) }));
    });
    req.on('error', reject);
    req.setTimeout(timeoutMs, () => req.destroy(new Error('timeout')));
  });
}

async function pollCamera() {
  try {
    const r = await fetchBuffer(`${JEVCAM}/api/state`);
    if (r.status !== 200) throw new Error(`jevcam state ${r.status}`);
    const st = JSON.parse(r.body.toString('utf8'));
    camera.lastState = { ts: st.ts, latency_ms: st.latency_ms, error: st.error, frames: st.frames, targets: st.config && st.config.targets };
    camera.persons = (st.detections || [])
      .filter((d) => d.drawn && /person|worker|people/i.test(d.label))
      .map((d) => ({ label: d.label, confidence: d.box_conf, box: d.box }));
    const f = await fetchBuffer(`${JEVCAM}/latest.jpg`);
    camera.available = f.status === 200 && f.body.length > 1000;
    if (camera.available) { camera.lastFrame = f.body; camera.lastFrameAt = new Date().toISOString(); }
    camera.error = camera.available ? (st.error || null) : 'JevCam has no frame yet';
  } catch (e) {
    camera.available = false;
    camera.error = `JevCam unreachable (${e.message})`;
  }
}
let cameraTimer = null;
function scheduleCamera() { clearInterval(cameraTimer); cameraTimer = setInterval(pollCamera, settings.camera.scanIntervalSec * 1000); }
scheduleCamera();
pollCamera();

// ---------------------------------------------------------------------------
// Notifications (ntfy / Pushover) - real pushes, only when configured
// ---------------------------------------------------------------------------
function postRequest(url, body, headers) {
  return new Promise((resolve) => {
    const u = new URL(url);
    const mod = u.protocol === 'https:' ? https : http;
    const req = mod.request(u, { method: 'POST', headers }, (res) => {
      let data = '';
      res.on('data', (c) => (data += c));
      res.on('end', () => resolve({ ok: res.statusCode >= 200 && res.statusCode < 300, status: res.statusCode, body: data.slice(0, 200) }));
    });
    req.on('error', (e) => resolve({ ok: false, error: e.message }));
    req.setTimeout(8000, () => req.destroy(new Error('timeout')));
    req.end(body);
  });
}

async function notify(entry, { test = false } = {}) {
  const n = settings.notifications;
  const results = {};
  const missing = entry.missing.map((m) => PPE_LABEL[m]).join(', ');
  const title = test ? 'PPE Monitor test alert' : `PPE alert - ${entry.worker}`;
  const message = test
    ? `Test from ${settings.siteName} on ${os.hostname()}. Alerts are reaching this device.`
    : `${settings.siteName}: worker ${entry.worker} entered without ${missing}. ${new Date(entry.ts).toLocaleTimeString()} (${entry.shift} shift).`;
  if (n.ntfy.enabled && n.ntfy.topic) {
    const r = await postRequest(`${n.ntfy.server.replace(/\/$/, '')}/${encodeURIComponent(n.ntfy.topic)}`, message, {
      'Content-Type': 'text/plain',
      Title: title,
      Priority: entry.alert && entry.alert.severity === 'critical' ? '4' : '3',
      Tags: 'warning,construction_worker',
    });
    results.ntfy = r.ok ? 'sent' : `failed (${r.status || r.error})`;
  }
  if (n.pushover.enabled && n.pushover.user && n.pushover.token) {
    const form = new URLSearchParams({ token: n.pushover.token, user: n.pushover.user, title, message, priority: '0' }).toString();
    const r = await postRequest('https://api.pushover.net/1/messages.json', form, { 'Content-Type': 'application/x-www-form-urlencoded' });
    results.pushover = r.ok ? 'sent' : `failed (${r.status || r.error})`;
  }
  return results;
}

// ---------------------------------------------------------------------------
// Live simulation loop
// ---------------------------------------------------------------------------
const sseClients = new Set();
function broadcast(type, payload) {
  const line = `event: ${type}\ndata: ${JSON.stringify(payload)}\n\n`;
  for (const res of sseClients) res.write(line);
}

async function recordEntry(opts = {}) {
  const now = new Date();
  const entry = makeEntry(now, opts);
  if (camera.available && camera.lastFrame) {
    entry.photo = `/photos/${entry.id}.jpg`;
    photoBytes.set(entry.id, camera.lastFrame);
    if (photoBytes.size > 200) photoBytes.delete(photoBytes.keys().next().value);
    fs.writeFile(path.join(PHOTOS, `${entry.id}.jpg`), camera.lastFrame, () => {});
    if (camera.persons[0]) { entry.box = camera.persons[0].box; entry.source = 'camera'; }
  }
  entries.push(entry);
  persistEvents();
  broadcast('entry', entry);
  if (entry.alert && !entry.alert.suppressed) {
    const critical = entry.alert.severity === 'critical';
    if (!settings.notifications.onlyCritical || critical) {
      entry.alert.notified = await notify(entry);
      persistEvents();
    }
    broadcast('alert', entry);
  }
  broadcast('stats', stats());
  return entry;
}

let simTimer = null;
function scheduleNext() {
  clearTimeout(simTimer);
  if (!settings.simulation.running) return;
  const mean = Math.max(3, settings.simulation.meanIntervalSec);
  const hour = new Date().getHours();
  const busy = (hour >= 6 && hour < 9) || (hour >= 18 && hour < 21) ? 0.5 : 1;
  const wait = -Math.log(1 - Math.random()) * mean * busy * 1000; // exponential inter-arrival
  simTimer = setTimeout(async () => { await recordEntry(); scheduleNext(); }, Math.min(wait, mean * 4000));
}
scheduleNext();

// ---------------------------------------------------------------------------
// Stats
// ---------------------------------------------------------------------------
function startOfDay(d) { const x = new Date(d); x.setHours(0, 0, 0, 0); return x; }

function stats() {
  const now = new Date();
  const today0 = startOfDay(now);
  const todays = entries.filter((e) => new Date(e.ts) >= today0);
  const compliant = todays.filter((e) => e.compliant).length;
  const byShift = {};
  for (const s of ['day', 'night']) {
    const list = todays.filter((e) => e.shift === s);
    const ok = list.filter((e) => e.compliant).length;
    byShift[s] = { entered: list.length, compliant: ok, rate: list.length ? ok / list.length : null,
      alerts: list.filter((e) => e.alert && !e.alert.suppressed).length };
  }
  const missingCounts = Object.fromEntries(PPE_ITEMS.map((i) => [i, 0]));
  for (const e of todays) for (const m of e.missing) missingCounts[m]++;
  const hourly = Array.from({ length: 24 }, (_, h) => ({ hour: h, compliant: 0, nonCompliant: 0 }));
  for (const e of todays) { const h = new Date(e.ts).getHours(); hourly[h][e.compliant ? 'compliant' : 'nonCompliant']++; }
  const week = [];
  for (let i = 6; i >= 0; i--) {
    const d0 = startOfDay(new Date(now.getFullYear(), now.getMonth(), now.getDate() - i));
    const d1 = new Date(d0); d1.setDate(d0.getDate() + 1);
    const list = entries.filter((e) => { const t = new Date(e.ts); return t >= d0 && t < d1; });
    const ok = list.filter((e) => e.compliant).length;
    week.push({ date: d0.toISOString().slice(0, 10), entered: list.length, rate: list.length ? ok / list.length : null });
  }
  const openAlerts = entries.filter((e) => e.alert && !e.alert.suppressed && !e.alert.acknowledged).length;
  return {
    now: now.toISOString(),
    currentShift: shiftFor(now),
    today: { entered: todays.length, compliant, rate: todays.length ? compliant / todays.length : null,
      alerts: todays.filter((e) => e.alert && !e.alert.suppressed).length, openAlerts },
    byShift, missingCounts, hourly, week,
  };
}

// ---------------------------------------------------------------------------
// HTTP
// ---------------------------------------------------------------------------
const page = () => fs.readFileSync(path.join(ROOT, 'index.html'));

function json(res, status, body) {
  res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
  res.end(JSON.stringify(body));
}
function readBody(req) {
  return new Promise((resolve) => {
    let data = '';
    req.on('data', (c) => { data += c; if (data.length > 1e6) req.destroy(); });
    req.on('end', () => { try { resolve(JSON.parse(data || '{}')); } catch { resolve({}); } });
  });
}
function publicSettings() {
  const s = JSON.parse(JSON.stringify(settings));
  if (s.notifications.pushover.token) s.notifications.pushover.token = '••••' + s.notifications.pushover.token.slice(-4);
  return s;
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://x');
  const p = url.pathname;

  if (p === '/' || p === '/index.html') {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
    return res.end(page());
  }

  if (p === '/api/state') {
    const limit = Math.min(500, Number(url.searchParams.get('limit')) || 200);
    return json(res, 200, {
      hostname: os.hostname(), settings: publicSettings(), stats: stats(),
      camera: { available: camera.available, lastFrameAt: camera.lastFrameAt, error: camera.error, persons: camera.persons, jevcam: camera.lastState },
      entries: entries.slice(-limit),
      ppeItems: PPE_ITEMS.map((k) => ({ key: k, label: PPE_LABEL[k] })),
    });
  }

  if (p === '/api/stream') {
    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store', Connection: 'keep-alive' });
    res.write(`event: hello\ndata: ${JSON.stringify({ now: new Date().toISOString() })}\n\n`);
    sseClients.add(res);
    const ping = setInterval(() => res.write(': ping\n\n'), 15000);
    const cam = setInterval(() => res.write(`event: camera\ndata: ${JSON.stringify({ available: camera.available, lastFrameAt: camera.lastFrameAt, error: camera.error, persons: camera.persons })}\n\n`), 2000);
    req.on('close', () => { clearInterval(ping); clearInterval(cam); sseClients.delete(res); });
    return;
  }

  if (p === '/api/frame.jpg') {
    if (!camera.available || !camera.lastFrame) { res.writeHead(503, { 'Cache-Control': 'no-store' }); return res.end(); }
    res.writeHead(200, { 'Content-Type': 'image/jpeg', 'Cache-Control': 'no-store' });
    return res.end(camera.lastFrame);
  }

  if (p.startsWith('/photos/')) {
    const id = p.slice(8).replace(/[^a-f0-9]/g, '').slice(0, 12);
    const mem = photoBytes.get(id);
    if (mem) { res.writeHead(200, { 'Content-Type': 'image/jpeg', 'Cache-Control': 'max-age=86400' }); return res.end(mem); }
    return fs.readFile(path.join(PHOTOS, `${id}.jpg`), (err, buf) => {
      if (err) { res.writeHead(404); return res.end(); }
      res.writeHead(200, { 'Content-Type': 'image/jpeg', 'Cache-Control': 'max-age=86400' });
      res.end(buf);
    });
  }

  if (p === '/api/settings' && req.method === 'POST') {
    const patch = await readBody(req);
    if (patch.notifications && patch.notifications.pushover && /^••••/.test(patch.notifications.pushover.token || '')) {
      delete patch.notifications.pushover.token; // masked value echoed back: keep the stored one
    }
    settings = deepMerge(settings, patch);
    settings.sensitivity = SENSITIVITY_THRESHOLD[settings.sensitivity] ? settings.sensitivity : 'medium';
    settings.camera.scanIntervalSec = clampScan(settings.camera.scanIntervalSec);
    saveJson(SETTINGS_FILE, settings);
    scheduleNext();
    scheduleCamera();
    broadcast('settings', publicSettings());
    broadcast('stats', stats());
    return json(res, 200, publicSettings());
  }

  if (p === '/api/alerts/ack' && req.method === 'POST') {
    const { id, all } = await readBody(req);
    let n = 0;
    for (const e of entries) {
      if (e.alert && !e.alert.acknowledged && (all || e.id === id)) { e.alert.acknowledged = true; e.alert.ackedAt = new Date().toISOString(); n++; }
    }
    persistEvents();
    broadcast('stats', stats());
    return json(res, 200, { acknowledged: n });
  }

  if (p === '/api/notify/test' && req.method === 'POST') {
    const fake = { worker: 'TEST', missing: ['hardHat'], ts: new Date().toISOString(), shift: shiftFor(new Date()), alert: { severity: 'warning' } };
    const results = await notify(fake, { test: true });
    return json(res, 200, { results, configured: Object.keys(results).length > 0 });
  }

  if (p === '/api/demo/entry' && req.method === 'POST') {
    const { compliant } = await readBody(req);
    const entry = await recordEntry({ forceCompliant: typeof compliant === 'boolean' ? compliant : undefined });
    return json(res, 200, entry);
  }

  if (p === '/api/demo/reset' && req.method === 'POST') {
    entries = seedHistory();
    lastAlertByWorker = new Map();
    persistEvents();
    broadcast('reset', {});
    broadcast('stats', stats());
    return json(res, 200, { entries: entries.length });
  }

  if (p === '/api/health') return json(res, 200, { ok: true, uptime: process.uptime(), camera: camera.available, entries: entries.length });

  res.writeHead(404, { 'Content-Type': 'text/plain' });
  res.end('Not found\n');
});

server.listen(PORT, () => {
  console.log(`PPE Monitor listening on port ${PORT} (pid ${process.pid}); camera source ${JEVCAM}`);
});
