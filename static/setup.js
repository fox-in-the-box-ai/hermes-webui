/* Fox in the Box -- Onboarding setup wizard */

const STEPS = ['Welcome', 'API Key', 'Done'];

const state = {
  currentStep: 1,
  totalSteps: STEPS.length,
  apiKey: '',
  welcomeText: null,        // populated on boot from /api/setup/welcome
  ollama: null,             // {running, host, version, models[]} populated on Welcome
};

// ── API helpers ──────────────────────────────────────────────────────────────

async function post(path, body) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  return { status: res.status, data: await res.json() };
}

async function getJson(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error('HTTP ' + res.status);
  return res.json();
}

// ── Progress bar ─────────────────────────────────────────────────────────────

function updateProgress(step) {
  const bar = document.getElementById('progress-bar');
  let html = '';
  for (let i = 1; i <= state.totalSteps; i++) {
    const cls = i < step ? 'done' : i === step ? 'active' : '';
    html += `<div class="step-dot ${cls}"></div>`;
    if (i < state.totalSteps) {
      html += `<div class="step-line ${i < step ? 'done' : ''}"></div>`;
    }
  }
  html += `<div class="progress-label">${step} / ${state.totalSteps} &mdash; ${STEPS[step - 1]}</div>`;
  bar.innerHTML = html;
}

// ── Render helpers ───────────────────────────────────────────────────────────

function escapeHtml(s) {
  return String(s || '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// Render plain-text welcome content (paragraph-split). Keeps the wizard
// dependency-free — no markdown library — while still letting users edit
// onboarding.md to customize the message. Issue #11.
function renderWelcomeBody(text) {
  if (!text) return '';
  const paragraphs = String(text).trim().split(/\n\s*\n/);
  return paragraphs.map(p => `<p>${escapeHtml(p).replace(/\n/g, '<br>')}</p>`).join('');
}

// Skip CTA inserted on every step. Bypasses API-key collection and marks
// onboarding complete; user can configure providers later via Settings.
function skipFooter() {
  return `
    <div class="skip-row">
      <button class="btn-link" type="button" onclick="skipOnboarding()">Skip for now — I'll configure later</button>
    </div>
  `;
}

// ── Step renderers ───────────────────────────────────────────────────────────

function renderStep1() {
  const body = renderWelcomeBody(state.welcomeText)
    || '<p>Let&apos;s get you set up. This will only take a minute.</p>';

  // Local-model branch priority (issue #69):
  //   1. Ollama detected with a model installed → use it (fastest, user already
  //      configured something locally)
  //   2. Bundled llama.cpp model already on disk → use it (instant, no download)
  //   3. Bundled llama.cpp installable (in-container, supervisord present) →
  //      offer download CTA (~2.5 GB)
  //   4. None → only the OpenRouter path (Next button) and skip footer.
  let localBlock = '';
  if (state.ollama && state.ollama.running && Array.isArray(state.ollama.models) && state.ollama.models.length > 0) {
    const first = state.ollama.models[0];
    const modelName = escapeHtml(first.name || '');
    localBlock = `
      <div class="ollama-detected">
        <div class="ollama-detected-title">Local model detected</div>
        <p>You have <code>${modelName}</code> running on your computer via Ollama. Skip the API key step and chat with it locally — your data never leaves your machine.</p>
        <button class="btn btn-secondary" onclick="useLocalOllama('${escapeHtml(first.name || '')}')">Use ${modelName}</button>
      </div>
    `;
  } else if (state.localFallback
             && state.localFallback.ui_state !== 'no-supervisor'
             && state.localFallback.ui_state !== 'missing-model-registry') {
    if (state.localFallback.model_installed) {
      localBlock = `
        <div class="ollama-detected">
          <div class="ollama-detected-title">Bundled local model ready</div>
          <p>Phi-4-mini is already on disk. Skip the API key step and chat with it locally — your data never leaves your machine.</p>
          <button class="btn btn-secondary" onclick="useLlamaCppFallback()">Use bundled local model</button>
        </div>
      `;
    } else {
      const bytes = state.localFallback.model_size_bytes || 0;
      const sizeText = bytes > 0 ? ` (~${(bytes / 1073741824).toFixed(1)} GB)` : '';
      localBlock = `
        <div class="ollama-detected">
          <div class="ollama-detected-title">Run a local model — no API key needed</div>
          <p>Download Phi-4-mini${sizeText} and chat without an internet connection. Your data never leaves your machine.</p>
          <button class="btn btn-secondary" onclick="useLlamaCppFallback()">Download &amp; use local model</button>
        </div>
      `;
    }
  }

  return `
    <div class="step">
      <h1>Fox in the Box</h1>
      ${body}
      ${localBlock}
      <div class="btn-actions">
        <button class="btn btn-primary" onclick="advance(2)">Next</button>
      </div>
      ${skipFooter()}
    </div>
  `;
}

function renderStep2() {
  return `
    <div class="step">
      <h1>OpenRouter API Key</h1>
      <p>Fox uses OpenRouter to access AI models. You'll need an API key to continue.</p>
      <label for="api-key">API Key</label>
      <div class="input-wrapper">
        <input id="api-key" type="password" placeholder="sk-or-..." autocomplete="off" spellcheck="false">
        <button class="toggle-vis" type="button" onclick="toggleKeyVisibility()" aria-label="Toggle key visibility">show</button>
      </div>
      <div class="hint">
        Get your free key at <a href="https://openrouter.ai/keys" target="_blank" rel="noopener">openrouter.ai</a>
      </div>
      <div id="key-error" class="error-msg"></div>
      <div class="btn-actions">
        <button id="submit-key" class="btn btn-primary" onclick="submitApiKey()">Next</button>
      </div>
      ${skipFooter()}
    </div>
  `;
}

function renderStep3() {
  return `
    <div class="step">
      <h1>Fox is ready!</h1>
      <p>Your assistant is configured and ready to go.</p>
      <ul class="url-list">
        <li>Local: <code>http://localhost:8787</code></li>
      </ul>
      <div class="btn-actions">
        <button id="open-fox" class="btn btn-primary" onclick="completSetup()">Open Fox</button>
      </div>
    </div>
  `;
}

// ── Navigation ───────────────────────────────────────────────────────────────

function advance(n) {
  state.currentStep = n;
  renderStep(n);
  updateProgress(n);
}

function renderStep(n) {
  const container = document.getElementById('step-container');
  switch (n) {
    case 1: container.innerHTML = renderStep1(); break;
    case 2: container.innerHTML = renderStep2(); break;
    case 3: container.innerHTML = renderStep3(); break;
  }
}

// ── Key input helpers ────────────────────────────────────────────────────────

function toggleKeyVisibility() {
  const input = document.getElementById('api-key');
  const btn = input.parentElement.querySelector('.toggle-vis');
  if (input.type === 'password') {
    input.type = 'text';
    btn.textContent = 'hide';
  } else {
    input.type = 'password';
    btn.textContent = 'show';
  }
}

function setKeyError(msg) {
  const el = document.getElementById('key-error');
  if (el) el.textContent = msg;
  const input = document.getElementById('api-key');
  if (input) {
    if (msg) input.classList.add('has-error');
    else input.classList.remove('has-error');
  }
}

function setSubmitting(busy) {
  const btn = document.getElementById('submit-key');
  if (!btn) return;
  btn.disabled = busy;
  btn.innerHTML = busy ? '<span class="spinner"></span> Saving...' : 'Next';
}

// ── Submit API key ───────────────────────────────────────────────────────────

async function submitApiKey() {
  const input = document.getElementById('api-key');
  const key = (input ? input.value : '').trim();

  setKeyError('');

  if (!key) {
    setKeyError('API key is required.');
    return;
  }
  if (!key.startsWith('sk-')) {
    setKeyError('Key must start with sk-.');
    return;
  }

  setSubmitting(true);
  try {
    const { data } = await post('/api/setup/openrouter', { key });
    if (data.ok) {
      state.apiKey = key;
      advance(3);
    } else {
      setKeyError(data.error || 'Failed to save key.');
    }
  } catch (e) {
    setKeyError('Network error. Please try again.');
  } finally {
    setSubmitting(false);
  }
}

// ── Skip and local-Ollama paths (issue #11) ─────────────────────────────────

async function skipOnboarding() {
  if (!confirm("Skip the wizard? You can configure providers any time from Settings.")) return;
  try {
    await post('/api/setup/skip', {});
  } catch (e) {
    // Non-fatal — backend may have already marked complete.
  }
  // No supervisord restart needed — no env was written. Redirect immediately.
  window.location.href = '/';
}

async function useLocalOllama(modelName) {
  if (!modelName) return;
  try {
    const r = await post('/api/ollama/use-model', { model: modelName });
    if (!r.data.ok) {
      alert('Could not switch to local model: ' + (r.data.error || 'unknown error'));
      return;
    }
  } catch (e) {
    alert('Network error while switching to local model.');
    return;
  }
  // Mark onboarding complete and proceed. The gateway hot-reload was
  // triggered server-side by use_model() — webui keeps running.
  try { await post('/api/setup/complete', { tailscale_connected: false }); } catch (e) {}
  setTimeout(() => { window.location.href = '/'; }, 800);
}

// Issue #69: bundled llama.cpp fast-path. Toggles on local fallback (kicks
// download via #10's manager if needed), polls status, shows progress in
// place of the welcome step, and finishes onboarding when llama-server is
// ready.
function _renderLocalFallbackProgress(snapshot) {
  const container = document.getElementById('step-container');
  if (!container) return;

  const ui = (snapshot && snapshot.ui_state) || 'starting';
  const ms = snapshot && snapshot.model_state;
  const downloaded = ms && typeof ms.bytes_downloaded === 'number' ? ms.bytes_downloaded : 0;
  const total = ms && typeof ms.bytes_total === 'number' && ms.bytes_total > 0
    ? ms.bytes_total
    : (snapshot && snapshot.model_size_bytes) || 0;
  const pct = total > 0 ? Math.min(100, Math.floor((downloaded / total) * 100)) : 0;
  const mb = (n) => (n / 1048576).toFixed(0);

  let title, detail, showBar;
  switch (ui) {
    case 'needs-download':
      title = 'Preparing download…';
      detail = 'Queueing the model download.';
      showBar = false;
      break;
    case 'downloading':
      title = 'Downloading local model';
      detail = total > 0
        ? `${mb(downloaded)} / ${mb(total)} MB (${pct}%)`
        : `${mb(downloaded)} MB downloaded`;
      showBar = true;
      break;
    case 'starting':
      title = 'Starting local model server…';
      detail = 'Almost there.';
      showBar = false;
      break;
    case 'warming':
      title = 'Warming up the model…';
      detail = 'Loading weights into memory.';
      showBar = false;
      break;
    case 'ready':
      title = 'Local model is ready';
      detail = 'Opening Fox…';
      showBar = false;
      break;
    default:
      title = 'Setting up local model…';
      detail = '';
      showBar = false;
  }

  const bar = showBar
    ? `<div class="dl-bar"><div class="dl-bar-fill" style="width:${pct}%"></div></div>`
    : '';

  container.innerHTML = `
    <div class="step">
      <h1>${escapeHtml(title)}</h1>
      <p>${escapeHtml(detail)}</p>
      ${bar}
      <div class="hint">You can close this window — the download keeps going in the background.</div>
    </div>
  `;
}

async function useLlamaCppFallback() {
  // Kick the toggle. enable() returns immediately with the current snapshot;
  // download (if needed) and llama-server start happen in background threads.
  let snapshot;
  try {
    const r = await post('/api/local-fallback/enable', {});
    if (!r.data || r.data.enabled === false) {
      alert('Could not enable local fallback: ' + ((r.data && r.data.error) || 'unknown error'));
      return;
    }
    snapshot = r.data;
  } catch (e) {
    alert('Network error while enabling local fallback.');
    return;
  }

  _renderLocalFallbackProgress(snapshot);

  // Poll until ui_state === 'ready'. Phi-4-mini is ~2.5 GB; on a typical
  // residential connection this can take several minutes, so we poll
  // patiently rather than time out.
  const startedAt = Date.now();
  const maxMs = 30 * 60 * 1000;  // 30 minutes — generous for slow links
  const tick = async () => {
    if (Date.now() - startedAt > maxMs) {
      alert('Local model setup is taking longer than expected. Check Settings → Providers later.');
      return;
    }
    let s;
    try {
      s = await getJson('/api/local-fallback/status');
    } catch (e) {
      setTimeout(tick, 3000);
      return;
    }
    _renderLocalFallbackProgress(s);
    if (s.ui_state === 'ready') {
      try { await post('/api/setup/complete', { tailscale_connected: false }); } catch (e) {}
      setTimeout(() => { window.location.href = '/'; }, 800);
      return;
    }
    setTimeout(tick, 2000);
  };
  setTimeout(tick, 1500);
}

// ── Complete setup ───────────────────────────────────────────────────────────

async function completSetup() {
  const btn = document.getElementById('open-fox');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Starting...';
  }

  try {
    await post('/api/setup/complete', { tailscale_connected: false });
  } catch (e) {
    // Non-fatal -- config is written, proceed anyway
  }

  try {
    await post('/api/setup/restart', {});
  } catch (e) {
    // Non-fatal -- redirect anyway
  }

  // Wait for services to restart, then redirect
  setTimeout(() => { window.location.href = '/'; }, 3000);
}

// ── Boot ─────────────────────────────────────────────────────────────────────

async function loadOnboardingState() {
  // Welcome content (externalized), Ollama detection, and bundled-llama.cpp
  // status all run in parallel — the wizard renders with sensible fallbacks
  // if any probe fails. (#69 added local-fallback to the probe set.)
  const tasks = [
    getJson('/api/setup/welcome').then(d => { state.welcomeText = d.text || null; }).catch(() => {}),
    getJson('/api/ollama/status').then(async (s) => {
      if (s && s.running) {
        try {
          const m = await getJson('/api/ollama/models');
          state.ollama = Object.assign({}, s, { models: (m && m.models) || [] });
        } catch (e) {
          state.ollama = Object.assign({}, s, { models: [] });
        }
      } else {
        state.ollama = { running: false, models: [] };
      }
    }).catch(() => { state.ollama = { running: false, models: [] }; }),
    getJson('/api/local-fallback/status').then(s => {
      // s.ui_state ∈ {disabled, needs-download, downloading, starting,
      //   warming, ready, no-supervisor, missing-model-registry}.
      // We care about: "ready" (instant fast-path) and supervisor-available
      // states (offer the download CTA). "no-supervisor" hides the CTA.
      state.localFallback = s || null;
    }).catch(() => { state.localFallback = null; }),
  ];
  await Promise.all(tasks);
}

document.addEventListener('DOMContentLoaded', async () => {
  // Show the welcome step immediately with hardcoded fallback content,
  // then re-render once the probes return so the user never sees a
  // blank screen if the API is slow.
  renderStep(1);
  updateProgress(1);
  await loadOnboardingState();
  if (state.currentStep === 1) renderStep(1);
});
