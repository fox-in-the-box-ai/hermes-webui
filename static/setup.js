/* Fox in the Box -- Onboarding setup wizard */

const STEPS = ['Welcome', 'API Key', 'Done'];

const state = {
  currentStep: 1,
  totalSteps: STEPS.length,
  apiKey: '',
};

// ── API helpers ──────────────────────────────────────────────────────────────

async function post(path, body) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return { status: res.status, data: await res.json() };
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

// ── Step renderers ───────────────────────────────────────────────────────────

function renderStep1() {
  return `
    <div class="step">
      <h1>Fox in the Box</h1>
      <p>Let's get you set up. This will only take a minute.</p>
      <div class="btn-actions">
        <button class="btn btn-primary" onclick="advance(2)">Next</button>
      </div>
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
    const { status, data } = await post('/api/setup/openrouter', { key });
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

document.addEventListener('DOMContentLoaded', () => {
  renderStep(1);
  updateProgress(1);
});
