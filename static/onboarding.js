/* Fox in the Box — Onboarding Wizard
 * 3-step flow: Provider → Password → Done
 * Default model: moonshotai/kimi-k2.5 (set silently, no workspace/model step)
 */

(function () {
  'use strict';

  // ── Constants ────────────────────────────────────────────────────────────
  const DEFAULT_MODEL = 'moonshotai/kimi-k2.5';

  // Per-provider API key guidance shown inline
  const PROVIDER_GUIDANCE = {
    openrouter: {
      label: 'OpenRouter',
      steps: [
        'Go to <a href="https://openrouter.ai/keys" target="_blank" rel="noopener">openrouter.ai/keys</a>',
        'Sign in (free account) → click <strong>Create key</strong>',
        'Copy the key and paste it below',
      ],
      keyless: false,
    },
    anthropic: {
      label: 'Anthropic',
      steps: [
        'Go to <a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noopener">console.anthropic.com → API Keys</a>',
        'Click <strong>Create Key</strong> and give it a name',
        'Copy the key and paste it below',
      ],
      keyless: false,
    },
    openai: {
      label: 'OpenAI',
      steps: [
        'Go to <a href="https://platform.openai.com/api-keys" target="_blank" rel="noopener">platform.openai.com → API keys</a>',
        'Click <strong>Create new secret key</strong>',
        'Copy the key and paste it below — it won\'t be shown again',
      ],
      keyless: false,
    },
    google: {
      label: 'Google (Gemini)',
      steps: [
        'Go to <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener">aistudio.google.com → Get API key</a>',
        'Click <strong>Create API key</strong> and select a project',
        'Copy the key and paste it below',
      ],
      keyless: false,
    },
    deepseek: {
      label: 'DeepSeek',
      steps: [
        'Go to <a href="https://platform.deepseek.com/api_keys" target="_blank" rel="noopener">platform.deepseek.com → API Keys</a>',
        'Click <strong>Create API Key</strong>',
        'Copy the key and paste it below',
      ],
      keyless: false,
    },
    mistral: {
      label: 'Mistral',
      steps: [
        'Go to <a href="https://console.mistral.ai/api-keys/" target="_blank" rel="noopener">console.mistral.ai → API Keys</a>',
        'Click <strong>Create new key</strong>',
        'Copy the key and paste it below',
      ],
      keyless: false,
    },
    'x.ai': {
      label: 'xAI (Grok)',
      steps: [
        'Go to <a href="https://console.x.ai/" target="_blank" rel="noopener">console.x.ai → API Keys</a>',
        'Click <strong>Create API Key</strong>',
        'Copy the key and paste it below',
      ],
      keyless: false,
    },
    ollama: {
      label: 'Ollama',
      steps: [
        'Make sure Ollama is running locally (<code>ollama serve</code>)',
        'No API key needed — leave the field below blank',
        'Adjust the base URL if you\'re not on the default port',
      ],
      keyless: true,
    },
    lmstudio: {
      label: 'LM Studio',
      steps: [
        'Open LM Studio → <strong>Local Server</strong> tab → click <strong>Start Server</strong>',
        'No API key needed — leave the field below blank',
        'The default base URL is <code>http://localhost:1234/v1</code>',
      ],
      keyless: true,
    },
    custom: {
      label: 'Custom endpoint',
      steps: [
        'Enter the base URL of your OpenAI-compatible server below',
        'Add an API key only if your server requires authentication',
        'Use the <strong>Test connection</strong> button to verify',
      ],
      keyless: null, // may or may not need key
    },
  };

  // ── State ─────────────────────────────────────────────────────────────────
  let _step = 0;          // 0 = Provider, 1 = Password, 2 = Done
  let _status = null;     // raw /api/onboarding/status response
  let _catalog = [];      // provider catalog from status
  let _selectedProvider = null;
  let _apiKey = '';
  let _baseUrl = '';
  let _password = '';
  let _probeState = 'idle'; // idle | probing | ok | error

  const STEPS = [
    { key: 'provider', titleKey: 'onboarding_step_provider_title', descKey: 'onboarding_step_provider_desc' },
    { key: 'password', titleKey: 'onboarding_step_password_title', descKey: 'onboarding_step_password_desc' },
    { key: 'done',     titleKey: 'onboarding_step_done_title',     descKey: 'onboarding_step_done_desc' },
  ];

  // ── i18n helper ───────────────────────────────────────────────────────────
  function t(key, vars) {
    if (window.i18n && window.i18n[key]) {
      let s = window.i18n[key];
      if (vars) Object.entries(vars).forEach(([k, v]) => { s = s.replace('{' + k + '}', v); });
      return s;
    }
    return key;
  }

  // ── Bootstrap ─────────────────────────────────────────────────────────────
  async function maybeShowOnboarding() {
    try {
      const r = await fetch('/api/onboarding/status');
      if (!r.ok) return;
      _status = await r.json();
      if (_status.onboarding_completed) return;
      _catalog = _status.provider_catalog || [];
      showOnboarding();
    } catch (_) {
      /* gateway not ready yet — skip silently */
    }
  }

  function showOnboarding() {
    const overlay = document.getElementById('onboardingOverlay');
    if (!overlay) return;
    _step = 0;
    renderSidebar();
    renderStep();
    overlay.style.display = 'flex';
  }

  // ── Sidebar ────────────────────────────────────────────────────────────────
  function renderSidebar() {
    const container = document.getElementById('onboardingSteps');
    if (!container) return;
    container.innerHTML = STEPS.map((s, i) => {
      const active = i === _step;
      const done = i < _step;
      const cls = ['onboarding-step', active ? 'active' : '', done ? 'done' : ''].filter(Boolean).join(' ');
      const indexContent = done
        ? '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>'
        : (i + 1);
      return `<div class="${cls}">
        <div class="onboarding-step-index">${indexContent}</div>
        <div>
          <div class="onboarding-step-title">${t(s.titleKey)}</div>
          <div class="onboarding-step-desc">${t(s.descKey)}</div>
        </div>
      </div>`;
    }).join('');
  }

  // ── Step renderer ─────────────────────────────────────────────────────────
  function renderStep() {
    renderSidebar();
    const body = document.getElementById('onboardingBody');
    const notice = document.getElementById('onboardingNotice');
    const backBtn = document.getElementById('onboardingBackBtn');
    const skipBtn = document.getElementById('onboardingSkipBtn');
    const nextBtn = document.getElementById('onboardingNextBtn');
    if (!body || !notice) return;

    notice.style.display = 'none';
    notice.className = 'onboarding-status';

    backBtn.style.display = _step > 0 ? '' : 'none';
    skipBtn.style.display = _step < 2 ? '' : 'none';
    nextBtn.textContent = _step === 1 ? t('onboarding_continue') : t('onboarding_continue');
    nextBtn.style.display = '';

    if (_step === 0) renderProviderStep(body, notice);
    else if (_step === 1) renderPasswordStep(body, notice);
    else renderDoneStep(body, notice);
  }

  // ── Step 0: Provider ──────────────────────────────────────────────────────
  function renderProviderStep(body, notice) {
    // Show a notice if provider is already configured
    if (_status && _status.chat_ready) {
      showNotice(notice, t('onboarding_notice_setup_already_ready'), 'success');
    } else {
      showNotice(notice, t('onboarding_notice_setup_required'), 'info');
    }

    // Build provider options — group into easy_start and self_hosted/specialized
    const easyProviders = _catalog.filter(p => p.category === 'easy_start');
    const otherProviders = _catalog.filter(p => p.category !== 'easy_start');

    // Auto-select first easy provider if nothing chosen yet
    if (!_selectedProvider && easyProviders.length > 0) {
      _selectedProvider = easyProviders[0].id;
    }

    let html = `
      <div class="onboarding-field">
        <span>${t('onboarding_provider_label')}</span>
        <div class="onboarding-provider-grid">
          ${easyProviders.map(p => providerChip(p)).join('')}
        </div>`;

    if (otherProviders.length > 0) {
      html += `<details class="onboarding-more-providers">
        <summary>${t('onboarding_more_providers')}</summary>
        <div class="onboarding-provider-grid onboarding-provider-grid--other">
          ${otherProviders.map(p => providerChip(p)).join('')}
        </div>
      </details>`;
    }
    html += '</div>';

    // Selected provider detail
    const sel = _catalog.find(p => p.id === _selectedProvider);
    if (sel) {
      html += renderProviderDetail(sel);
    }

    body.innerHTML = html;

    // Wire up chip clicks
    body.querySelectorAll('.onboarding-provider-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        _selectedProvider = chip.dataset.provider;
        _apiKey = '';
        _baseUrl = '';
        _probeState = 'idle';
        renderStep();
      });
    });

    // Wire up input fields
    const keyInput = body.querySelector('#ob-api-key');
    if (keyInput) keyInput.addEventListener('input', e => { _apiKey = e.target.value; });

    const urlInput = body.querySelector('#ob-base-url');
    if (urlInput) urlInput.addEventListener('input', e => { _baseUrl = e.target.value; });

    const probeBtn = body.querySelector('#ob-probe-btn');
    if (probeBtn) probeBtn.addEventListener('click', runProbe);
  }

  function providerChip(p) {
    const active = p.id === _selectedProvider;
    return `<button class="onboarding-provider-chip${active ? ' active' : ''}" data-provider="${p.id}" type="button">
      ${p.name}
    </button>`;
  }

  function renderProviderDetail(provider) {
    const guidance = PROVIDER_GUIDANCE[provider.id] || null;
    const isKeyless = guidance && guidance.keyless === true;
    const needsBaseUrl = provider.needs_base_url || provider.id === 'custom' || provider.id === 'ollama' || provider.id === 'lmstudio';

    let html = '<div class="onboarding-provider-detail">';

    // Step-by-step guidance
    if (guidance && guidance.steps.length > 0) {
      html += `<div class="onboarding-key-guide">
        <div class="onboarding-key-guide-label">${isKeyless ? t('onboarding_guide_setup') : t('onboarding_guide_get_key')}</div>
        <ol class="onboarding-key-guide-steps">
          ${guidance.steps.map(s => `<li>${s}</li>`).join('')}
        </ol>
      </div>`;
    }

    // Base URL field (for self-hosted / custom)
    if (needsBaseUrl) {
      html += `<div class="onboarding-field">
        <span>${t('onboarding_base_url_label')}</span>
        <input id="ob-base-url" type="url" placeholder="${t('onboarding_base_url_placeholder')}" value="${_baseUrl}" autocomplete="off" spellcheck="false">
        <div class="onboarding-api-key-help">${t('onboarding_base_url_help')}</div>
      </div>`;
    }

    // API key field
    if (!isKeyless) {
      html += `<div class="onboarding-field">
        <span>${isKeyless ? t('onboarding_api_key_label_optional') : t('onboarding_api_key_label')}</span>
        <input id="ob-api-key" type="password" placeholder="${t('onboarding_api_key_placeholder')}" value="${_apiKey}" autocomplete="off" spellcheck="false">
      </div>`;
    } else {
      html += `<div class="onboarding-field">
        <span>${t('onboarding_api_key_label_optional')}</span>
        <input id="ob-api-key" type="password" placeholder="${t('onboarding_api_key_placeholder_optional')}" value="${_apiKey}" autocomplete="off" spellcheck="false">
        <div class="onboarding-api-key-help">${t('onboarding_api_key_help_keyless')}</div>
      </div>`;
    }

    // Test connection button + probe result
    html += `<div class="onboarding-probe-row">
      <button class="onboarding-probe-btn" id="ob-probe-btn" type="button"${_probeState === 'probing' ? ' disabled' : ''}>
        ${_probeState === 'probing' ? t('onboarding_probe_probing') : t('onboarding_probe_test_button')}
      </button>
    </div>`;

    if (_probeState !== 'idle') {
      const cls = _probeState === 'ok' ? 'onboarding-probe-ok'
        : _probeState === 'probing' ? 'onboarding-probe-probing'
        : 'onboarding-probe-error';
      html += `<div class="onboarding-probe-banner ${cls}" id="ob-probe-banner">${_probeResult || ''}</div>`;
    }

    html += '</div>';
    return html;
  }

  // ── Probe ─────────────────────────────────────────────────────────────────
  let _probeResult = '';

  async function runProbe() {
    _probeState = 'probing';
    _probeResult = t('onboarding_probe_probing');
    renderStep();

    const body = {
      provider: _selectedProvider,
      api_key: _apiKey,
      base_url: _baseUrl,
    };

    try {
      const r = await fetch('/api/onboarding/probe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await r.json();
      if (data.ok) {
        _probeState = 'ok';
        _probeResult = t('onboarding_probe_ok', { n: data.model_count || '?' });
      } else {
        _probeState = 'error';
        _probeResult = probeErrorMsg(data.error_code || 'generic');
      }
    } catch (_) {
      _probeState = 'error';
      _probeResult = t('onboarding_probe_error_generic');
    }
    renderStep();
  }

  function probeErrorMsg(code) {
    const map = {
      invalid_url: 'onboarding_probe_error_invalid_url',
      dns: 'onboarding_probe_error_dns',
      connect_refused: 'onboarding_probe_error_connect_refused',
      timeout: 'onboarding_probe_error_timeout',
      http_4xx: 'onboarding_probe_error_http_4xx',
      http_5xx: 'onboarding_probe_error_http_5xx',
      parse: 'onboarding_probe_error_parse',
    };
    return t(map[code] || 'onboarding_probe_error_generic');
  }

  // ── Step 1: Password ──────────────────────────────────────────────────────
  function renderPasswordStep(body, notice) {
    const alreadySet = _status && _status.password_enabled;
    showNotice(notice,
      alreadySet ? t('onboarding_notice_password_enabled') : t('onboarding_notice_password_recommended'),
      alreadySet ? 'success' : 'info'
    );

    body.innerHTML = `
      <div class="onboarding-field">
        <span>${t('onboarding_password_label')}</span>
        <input id="ob-password" type="password" placeholder="${t('onboarding_password_placeholder')}" value="${_password}" autocomplete="new-password">
        <div class="onboarding-api-key-help">${t('onboarding_password_help')}</div>
      </div>`;

    const pwInput = body.querySelector('#ob-password');
    if (pwInput) pwInput.addEventListener('input', e => { _password = e.target.value; });
  }

  // ── Step 2: Done ──────────────────────────────────────────────────────────
  function renderDoneStep(body, notice) {
    const nextBtn = document.getElementById('onboardingNextBtn');
    if (nextBtn) {
      nextBtn.textContent = t('onboarding_open');
      nextBtn.style.fontWeight = '700';
    }
    const skipBtn = document.getElementById('onboardingSkipBtn');
    if (skipBtn) skipBtn.style.display = 'none';

    body.innerHTML = `
      <div class="onboarding-done-wrap">
        <div class="onboarding-done-avatar">
          <img src="static/fox_avatar_cropped.jpg" alt="Fox" width="72" height="72">
        </div>
        <h3 class="onboarding-done-headline">${t('onboarding_done_headline')}</h3>
        <p class="onboarding-done-body">${t('onboarding_done_body')}</p>
      </div>`;
  }

  // ── Navigation ────────────────────────────────────────────────────────────
  async function nextOnboardingStep() {
    if (_step === 0) {
      const err = await saveProviderStep();
      if (err) { showStepError(err); return; }
      _step = 1;
    } else if (_step === 1) {
      await savePasswordStep();
      _step = 2;
    } else if (_step === 2) {
      await completeOnboarding();
      return;
    }
    renderStep();
  }

  function prevOnboardingStep() {
    if (_step > 0) { _step--; renderStep(); }
  }

  async function skipOnboarding() {
    try {
      await fetch('/api/onboarding/complete', { method: 'POST' });
    } catch (_) {}
    hideOnboarding();
  }

  // ── Save helpers ──────────────────────────────────────────────────────────
  async function saveProviderStep() {
    if (!_selectedProvider) return t('onboarding_error_provider_required');
    const sel = _catalog.find(p => p.id === _selectedProvider);
    const isKeyless = sel && (PROVIDER_GUIDANCE[sel.id] || {}).keyless === true;
    const needsBaseUrl = sel && (sel.needs_base_url || sel.id === 'custom');

    if (needsBaseUrl && !_baseUrl) return t('onboarding_error_base_url_required');
    if (!isKeyless && !_apiKey && !(_status && _status.chat_ready)) {
      // Allow continuing without a key only if already configured
    }

    try {
      const r = await fetch('/api/onboarding/apply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider: _selectedProvider,
          api_key: _apiKey,
          base_url: _baseUrl,
          model: DEFAULT_MODEL,
        }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        return d.error || t('onboarding_error_provider_required');
      }
    } catch (_) {
      return t('onboarding_error_provider_required');
    }
    return null;
  }

  async function savePasswordStep() {
    if (!_password) return;
    try {
      await fetch('/api/setup/password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: _password }),
      });
    } catch (_) {}
  }

  async function completeOnboarding() {
    try {
      await fetch('/api/onboarding/complete', { method: 'POST' });
    } catch (_) {}
    hideOnboarding();
  }

  function hideOnboarding() {
    const overlay = document.getElementById('onboardingOverlay');
    if (overlay) overlay.style.display = 'none';
  }

  // ── UI helpers ────────────────────────────────────────────────────────────
  function showNotice(el, msg, type) {
    el.innerHTML = msg;
    el.className = 'onboarding-status ' + (type || 'info');
    el.style.display = '';
  }

  function showStepError(msg) {
    const notice = document.getElementById('onboardingNotice');
    if (notice) showNotice(notice, msg, 'warn');
  }

  // ── Public API (called from index.html onclick handlers) ──────────────────
  window.nextOnboardingStep = nextOnboardingStep;
  window.prevOnboardingStep = prevOnboardingStep;
  window.skipOnboarding = skipOnboarding;

  // ── Init ──────────────────────────────────────────────────────────────────
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', maybeShowOnboarding);
  } else {
    maybeShowOnboarding();
  }
})();
