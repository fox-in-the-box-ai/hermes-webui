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

  // Ollama branch — issue #11 Option B. If a host-side Ollama daemon is
  // detected with at least one model installed, surface a fast-path that
  // skips the API key step entirely and drops the user into chat with a
  // local model. Falls back gracefully when the probe fails.
  let ollamaBlock = '';
  if (state.ollama && state.ollama.running && Array.isArray(state.ollama.models) && state.ollama.models.length > 0) {
    const first = state.ollama.models[0];
    const modelName = escapeHtml(first.name || '');
    ollamaBlock = `
      <div class="ollama-detected">
        <div class="ollama-detected-title">Local model detected</div>
        <p>You have <code>${modelName}</code> running on your computer via Ollama. Skip the API key step and chat with it locally — your data never leaves your machine.</p>
        <button class="btn btn-secondary" onclick="useLocalOllama('${escapeHtml(first.name || '')}')">Use ${modelName}</button>
      </div>
    `;
  }

  return `
    <div class="step">
      <h1>Fox in the Box</h1>
      ${body}
      ${ollamaBlock}
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
  // Welcome content (externalized) and Ollama detection run in parallel —
  // both are best-effort; the wizard renders with sensible fallbacks if
  // either probe fails.
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
