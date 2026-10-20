// SPDX-License-Identifier: AGPL-3.0-only
// LedgerStitch — mock-setup.js : in-browser mock of first-run setup (/api/setup/*), for ?mock=1.
// Nothing is downloaded or installed: install/pull jobs are simulated with timers.
//
// URL switches:  &onboarding=1            first launch (no workspaces yet) → the setup wizard
//                &ollama=none|stopped|running|ready   local AI state (default none)
//                &localai=running|error    a background "Set up local AI" job already in flight / stopped
//                &os=mac|windows|linux     what the system check reports (default mac)
//                &server=1                 server mode (Docker: the compose "ollama" service; default running)
//                &lowmem=1                 too little RAM / disk: the consent dialog warns, needs "Download anyway"

const qs = new URLSearchParams(location.search);
const OS = { mac: ['macOS', '15.1', 'arm64', 'Darwin'], windows: ['Windows', '11 (10.0.26100)', 'AMD64', 'Windows'], linux: ['Ubuntu 24.04 LTS', '', 'x86_64', 'Linux'] }[qs.get('os')] || ['macOS', '15.1', 'arm64', 'Darwin'];
const SERVER = qs.get('server') === '1';
const LOWMEM = qs.get('lowmem') === '1';
const RECOMMENDED = 'glm-4.7-flash:latest';       // mirrors ai.local_ollama.RECOMMENDED_MODEL (the one local model)
const MODEL = { model: RECOMMENDED, label: 'GLM-4.7-Flash', size: '19 GB', ram: '24 GB' };
const OPERATOR_URL = 'http://ollama:11434';
let consent = null;                                // {at, model, endpoint, download_anyway}
const LOCALAI = qs.get('localai') || '';
let setupJob = null;                               // the one-click background job (ollama.setup)
let localAiError = LOCALAI === 'error' ? 'The download stopped: the connection to Ollama was lost.' : null;
let localAiRequested = LOCALAI === 'error';
const start = qs.get('ollama') || (SERVER ? 'running' : 'none');
const ollama = {
  installed: start !== 'none',
  running: start === 'running' || start === 'ready',
  models: start === 'ready' ? [RECOMMENDED] : [],
};
const jobs = new Map();
let jobSeq = 0;
if (LOCALAI === 'running') setTimeout(() => { consent = { at: new Date().toISOString(), model: RECOMMENDED, endpoint: 'http://127.0.0.1:11434', download_anyway: false }; startSetupJob({ setLocalModel: () => {} }); localAiRequested = true; }, 0);
if (LOCALAI === 'error') consent = { at: new Date().toISOString(), model: RECOMMENDED, endpoint: 'http://127.0.0.1:11434', download_anyway: false };

function plan() {
  if (ollama.installed && !ollama.running) return { method: 'start', auto: true, path: '/Applications/Ollama.app' };
  if (OS[3] === 'Darwin') return { method: 'zip', auto: true, url: 'https://ollama.com/download/Ollama-darwin.zip' };
  if (OS[3] === 'Windows') return { method: 'exe', auto: true, url: 'https://ollama.com/download/OllamaSetup.exe' };
  return { method: 'script', auto: false, command: 'curl -fsSL https://ollama.com/install.sh | sh' };
}

function resources() {
  const disk = LOWMEM ? 18.4 : 214.6, mem = LOWMEM ? 16 : 36;
  return { disk_free_bytes: disk * 1024 ** 3, disk_free_gb: disk, disk_approx: SERVER, need_disk_gb: 25,
    mem_total_bytes: mem * 1024 ** 3, mem_total_gb: mem, need_mem_gb: 24, disk_ok: disk >= 25, mem_ok: mem >= 24, ok: disk >= 25 && mem >= 24 };
}
function ollamaBlock() {
  return { running: ollama.running, version: ollama.running ? '0.40.6' : null, models: [...ollama.models], url: SERVER ? OPERATOR_URL : 'http://127.0.0.1:11434',
    installed: ollama.installed, path: !SERVER && ollama.installed ? '/Applications/Ollama.app' : null, plan: plan(), install_allowed: !SERVER,
    show: true, operator_url: SERVER ? OPERATOR_URL : null, fix: SERVER ? 'docker compose up -d ollama' : null,
    recommended_model: RECOMMENDED, recommended_size: MODEL.size, model_label: MODEL.label, model_ram: MODEL.ram,
    has_recommended: ollama.models.includes(RECOMMENDED), resources: resources(), local_ai: localAiOut() };
}
/** GET /api/setup/local-ai: the background job's state (off | queued | running | ready | error). */
function localAiOut() {
  const job = setupJob ? jobOut(setupJob) : null;
  const base = { model: RECOMMENDED, label: MODEL.label, size: MODEL.size, ram: MODEL.ram, job, error: null, progress: null, message: '',
    consent, endpoint: SERVER ? OPERATOR_URL : 'http://127.0.0.1:11434', server_mode: SERVER };
  if (job && !job.finished) return { ...base, state: 'running', progress: job.progress, message: job.message, completed: job.completed, total: job.total };
  if (localAiRequested && localAiError) return { ...base, state: 'error', error: localAiError, message: localAiError };
  if (ollama.models.includes(RECOMMENDED)) return { ...base, state: 'ready', message: 'Ready' };
  return { ...base, state: 'off' };
}
function startSetupJob(ctx) {
  const needInstall = !ollama.running;
  const steps = [
    ...(needInstall ? (ollama.installed
      ? [{ step: 'launch', message: 'Starting Ollama', ms: 1500 }, { step: 'wait', message: 'Waiting for Ollama', ms: 1200 }]
      : [{ step: 'download', message: 'Downloading Ollama', ms: 4200, bar: true }, { step: 'verify', message: 'Checking signature', ms: 900 },
        { step: 'launch', message: 'Starting Ollama', ms: 900 }, { step: 'wait', message: 'Waiting for Ollama', ms: 900 }]) : []),
    { step: 'pull', message: 'Preparing', ms: 600 }, { step: 'pull', message: `Downloading ${MODEL.label}`, ms: 20000, bar: true }, { step: 'pull', message: 'Verifying', ms: 700 }];
  setupJob = newJob('ollama.setup', steps, () => {
    ollama.installed = true; ollama.running = true; localAiRequested = false; localAiError = null;
    if (!ollama.models.includes(RECOMMENDED)) ollama.models.push(RECOMMENDED);
    ctx.setLocalModel(RECOMMENDED);
  }, 19_015_612_340);
  return setupJob;
}

/** A simulated job: progress is a function of elapsed time; `finish` runs once at the end. */
function newJob(kind, steps, finish, total = 0) {
  const id = `job${++jobSeq}`;
  const job = { id, kind, t0: Date.now(), steps, finish, total, done: false };
  jobs.set(id, job);
  return job;
}
function jobOut(job) {
  const dur = job.steps.reduce((a, s) => a + s.ms, 0);
  const el = Date.now() - job.t0;
  if (el >= dur && !job.done) { job.done = true; job.finish && job.finish(); }
  if (job.done) return { id: job.id, kind: job.kind, state: 'done', step: 'done', message: job.kind === 'ollama.install' ? 'Ollama is ready' : 'Model ready',
    progress: 1, completed: job.total, total: job.total, error: null, result: job.kind === 'ollama.install' ? { signer: 'Developer ID Application: Ollama Inc.' } : { model: RECOMMENDED }, finished: true };
  let acc = 0, cur = job.steps[0], within = 0;
  for (const s of job.steps) { if (el < acc + s.ms) { cur = s; within = (el - acc) / s.ms; break; } acc += s.ms; }
  const progress = cur.bar ? Math.min(0.99, within) : null;
  return { id: job.id, kind: job.kind, state: cur.state || 'running', step: cur.step, message: cur.message,
    progress, completed: progress != null && cur.step === 'pull' ? Math.round(job.total * progress) : 0, total: cur.step === 'pull' ? job.total : 0, error: null, result: {}, finished: false };
}

export function setupRoutes(path, method, init, ctx) {
  if (!path.startsWith('/api/setup/')) return null;
  const { json, err, bodyOf } = ctx;
  if (path === '/api/setup/local-ai/model' && method === 'DELETE') {
    if (!ollama.running) return err(409, 'Ollama isn’t reachable, so the model can’t be removed right now.');
    const had = ollama.models.includes(RECOMMENDED);
    ollama.models = ollama.models.filter(m => m !== RECOMMENDED);
    return json({ ...localAiOut(), removed: had });
  }
  if (path === '/api/setup/local-ai') {
    if (method === 'GET') return json(localAiOut());
    if (method === 'DELETE') { localAiRequested = false; localAiError = null; setupJob = null; consent = null; return json(localAiOut()); }
    const b = bodyOf(init);
    if (!b.consent) return err(400, 'Setting up local AI needs your OK first.', { consent_required: true });
    if ((b.model || RECOMMENDED) !== RECOMMENDED) return err(422, 'Local AI uses one model only.');
    if (SERVER && !ollama.running) return err(409, 'The Ollama service isn’t reachable.', { fix: 'docker compose up -d ollama', reachable: false });
    const p = plan();
    if (!ollama.running && !p.auto) return json({ manual: true, plan: p, command: p.command, job: null, local_ai: localAiOut() });
    const res = resources();
    if (!res.ok && !b.download_anyway) return err(409, 'This machine may be too small for the model.', { resources: res, needs_override: true });
    consent = { at: new Date().toISOString().replace(/\.\d+Z$/, 'Z'), model: RECOMMENDED, endpoint: SERVER ? OPERATOR_URL : 'http://127.0.0.1:11434', download_anyway: !res.ok };
    localAiRequested = true; localAiError = null;
    ctx.setLocalRequested && ctx.setLocalRequested(RECOMMENDED);
    const job = setupJob && !jobOut(setupJob).finished ? setupJob : startSetupJob(ctx);
    return json({ job: jobOut(job), local_ai: localAiOut() }, 202);
  }
  if (path === '/api/setup/check') {
    return json({
      os: { name: OS[0], version: OS[1], arch: OS[2], ok: true },
      disk: { free_bytes: 214.6 * 1024 ** 3, free_gb: 214.6, ok: true, model_ok: true },
      port: { port: 8765, host: SERVER ? '0.0.0.0' : '127.0.0.1', ok: true },
      addons: [],
      ollama: ollamaBlock(), server_mode: SERVER, onboarding: ctx.onboarding(),
    });
  }
  if (path === '/api/setup/ollama/install' && method === 'POST') {
    const b = bodyOf(init);
    if (SERVER) return err(403, 'Installing software is turned off in server mode.');
    if (!b.consent) return err(400, 'Installing Ollama needs your OK first.', { consent_required: true });
    if (ollama.running) return json({ already: true, job: null, ollama: ollamaBlock() });
    const p = plan();
    if (!p.auto) return json({ manual: true, plan: p, command: p.command, job: null });
    const steps = p.method === 'start'
      ? [{ step: 'launch', message: 'Starting Ollama', ms: 1500 }, { step: 'wait', message: 'Waiting for Ollama', ms: 1200 }]
      : [{ step: 'download', message: 'Downloading Ollama', ms: 4200, bar: true }, { step: 'verify', message: 'Checking signature', ms: 900 },
        { step: 'launch', message: 'Starting Ollama', ms: 900 }, { step: 'wait', message: 'Waiting for Ollama', ms: 900 }];
    const job = newJob('ollama.install', steps, () => { ollama.installed = true; ollama.running = true; }, p.method === 'start' ? 0 : 187_400_000);
    return json({ job: jobOut(job), plan: p }, 202);
  }
  if (path === '/api/setup/ollama/pull' && method === 'POST') {
    const b = bodyOf(init);
    if ((b.model || RECOMMENDED) !== RECOMMENDED) return err(422, 'Local AI uses one model only.');
    if (!b.consent || !consent) return err(403, 'Downloading the model needs your OK in the consent dialog first.', { consent_required: true });
    if (!ollama.running) return err(409, 'Ollama isn’t running. Install or open it first.');
    const job = newJob('ollama.pull', [{ step: 'pull', message: 'Preparing', ms: 600 },
      { step: 'pull', message: `Downloading ${MODEL.label}`, ms: 6500, bar: true }, { step: 'pull', message: 'Verifying', ms: 700 }],
    () => { if (!ollama.models.includes(RECOMMENDED)) ollama.models.push(RECOMMENDED); ctx.setLocalModel(RECOMMENDED); }, 19_015_612_340);
    return json({ job: jobOut(job) }, 202);
  }
  let m;
  if ((m = path.match(/^\/api\/setup\/jobs\/([\w-]+)$/))) {
    const job = jobs.get(m[1]);
    return job ? json(jobOut(job)) : err(404, 'No such setup job.');
  }
  if (path === '/api/setup/complete' && method === 'POST') { ctx.setOnboarding(true); return json({ onboarding: ctx.onboarding() }); }
  if (path === '/api/setup/reset' && method === 'POST') { ctx.setOnboarding(false); return json({ onboarding: { ...ctx.onboarding(), needed: true } }); }
  if (path === '/api/setup/demo' && method === 'POST') {
    const b = bodyOf(init);
    if (!b.passphrase || b.passphrase.length < 8) return err(422, 'Passphrase must be at least 8 characters.');
    return json({ ...ctx.loadDemo(), demo: { files: 31, failed: 0, transactions: 642 } }, 201);
  }
  return err(404, `Mock has no handler for ${method} ${path}`);
}
