/* Fox in the Box — local-fallback polish (issue #9 deferred polish).
 *
 * Two pieces of UX layered on top of v0.4.1's silent failover:
 *
 *   1. Reactive modal — when a chat stream fails on a remote provider AND
 *      the user has NOT opted into local fallback, surface a one-time
 *      modal offering to enable it. Listens to `fitb:stream-error`
 *      dispatched from messages.js (apperror SSE handler).
 *
 *   2. Recovery banner — when local fallback IS enabled, periodically
 *      probe the user's primary remote provider's reachability via the
 *      backend's lightweight remote-health endpoint. If remote is back,
 *      show a top banner offering to switch off local fallback.
 *
 * Both use sessionStorage for "don't re-fire this session" flags so the
 * UI doesn't pester the user. State resets on page reload.
 */

(function () {
  'use strict';

  const MODAL_DISMISSED = 'fitb.fallback_modal_seen';
  const BANNER_DISMISSED = 'fitb.recovery_banner_dismissed';
  const RECOVERY_POLL_MS = 90 * 1000;
  // Error types the modal reacts to. Excludes errors where local fallback
  // wouldn't help (auth/quota/model-not-found — local model can't fix
  // those).
  const ELIGIBLE_TYPES = new Set([
    'stream_interrupted', 'rate_limit', 'no_response', 'unknown',
  ]);

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  async function fetchJson(url) {
    const res = await fetch(url, { credentials: 'same-origin' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return res.json();
  }

  async function postJson(url, body) {
    const res = await fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    return { ok: res.ok, data: await res.json().catch(() => ({})) };
  }

  // ── Reactive modal (offer enable when remote breaks) ────────────────────

  function buildModal() {
    const wrap = document.createElement('div');
    wrap.className = 'fitb-fb-modal-backdrop';
    wrap.setAttribute('role', 'dialog');
    wrap.setAttribute('aria-modal', 'true');
    wrap.setAttribute('aria-labelledby', 'fitbFbModalTitle');
    wrap.innerHTML = `
      <div class="fitb-fb-modal">
        <h2 id="fitbFbModalTitle">Your provider is having trouble</h2>
        <p>Want to enable a local AI model as a fallback? It runs on your computer, so chats keep working when your provider is rate-limited, down, or offline. ~2.5 GB download the first time.</p>
        <div class="fitb-fb-modal-status" id="fitbFbModalStatus"></div>
        <div class="fitb-fb-modal-actions">
          <button type="button" class="fitb-btn fitb-btn-link" id="fitbFbModalDismiss">Not now</button>
          <button type="button" class="fitb-btn fitb-btn-primary" id="fitbFbModalEnable">Enable local fallback</button>
        </div>
      </div>
    `;
    return wrap;
  }

  function closeNode(n) { if (n && n.parentNode) n.parentNode.removeChild(n); }

  // Module-level guard against duplicate concurrent renders. sessionStorage
  // is the cross-error/cross-reload guard; this is a tighter "is one open
  // RIGHT NOW in this tab" guard so two stream-errors firing 50ms apart
  // don't stack.
  let _modalOpen = false;

  async function showReactiveModal() {
    if (sessionStorage.getItem(MODAL_DISMISSED)) return;
    if (_modalOpen) return;
    if (document.querySelector('.fitb-fb-modal-backdrop')) return;  // safety
    _modalOpen = true;

    const wrap = buildModal();
    document.body.appendChild(wrap);

    const enable = wrap.querySelector('#fitbFbModalEnable');
    const dismiss = wrap.querySelector('#fitbFbModalDismiss');
    const status = wrap.querySelector('#fitbFbModalStatus');

    const closeAndDismiss = (markSeen) => {
      _modalOpen = false;
      if (markSeen) sessionStorage.setItem(MODAL_DISMISSED, '1');
      closeNode(wrap);
    };

    dismiss.addEventListener('click', () => closeAndDismiss(true));
    enable.addEventListener('click', async () => {
      enable.disabled = true;
      dismiss.disabled = true;
      status.textContent = 'Enabling…';
      const r = await postJson('/api/local-fallback/enable', {});
      if (!r.ok || !r.data || r.data.enabled === false) {
        // QA fix: previously sessionStorage MODAL_DISMISSED was set on entry,
        // so a failed enable left the modal locked-out for the rest of the
        // session even though the user never successfully enabled. Now we
        // only mark dismissed on explicit dismiss or success path — failure
        // re-enables the buttons so the user can retry.
        status.textContent = (r.data && r.data.error) || 'Failed to enable.';
        enable.disabled = false;
        dismiss.disabled = false;
        return;
      }
      status.textContent = 'Enabled. Your next failure will silently use local.';
      // Recovery banner can now start polling — it boots its own poll loop
      // by listening for storage events would be complex, so we just nudge
      // the next page load to start it. For this session, that's fine.
      setTimeout(() => closeAndDismiss(true), 1500);
    });
    // Escape closes (treat as dismiss).
    const onKey = (e) => {
      if (e.key === 'Escape') {
        document.removeEventListener('keydown', onKey);
        closeAndDismiss(true);
      }
    };
    document.addEventListener('keydown', onKey);
  }

  async function maybeReactToError(detail) {
    if (sessionStorage.getItem(MODAL_DISMISSED)) return;
    if (!detail || !ELIGIBLE_TYPES.has(detail.type)) return;
    // Check current opt-in state. If already enabled, the modal has nothing
    // to offer — local fallback already attempted to handle this and the
    // user is seeing the error precisely because both paths failed.
    let s;
    try {
      s = await fetchJson('/api/local-fallback/status');
    } catch (e) {
      return;
    }
    if (!s) return;
    if (s.enabled) return;  // already opted in
    if (s.ui_state === 'no-supervisor') return;  // outside container, can't run local
    if (s.ui_state === 'missing-model-registry') return;  // config broken
    showReactiveModal();
  }

  // ── Recovery banner (offer switch-off when remote is back) ──────────────

  let _bannerNode = null;
  let _recoveryPollTimer = null;

  function buildBanner() {
    const wrap = document.createElement('div');
    wrap.className = 'fitb-fb-banner';
    wrap.setAttribute('role', 'status');
    wrap.innerHTML = `
      <div class="fitb-fb-banner-text">Your remote provider looks reachable again. Switch off local fallback to use it?</div>
      <div class="fitb-fb-banner-actions">
        <button type="button" class="fitb-btn fitb-btn-link" id="fitbFbBannerDismiss">Keep local</button>
        <button type="button" class="fitb-btn fitb-btn-primary" id="fitbFbBannerSwitch">Switch back</button>
      </div>
    `;
    return wrap;
  }

  async function showRecoveryBanner() {
    if (sessionStorage.getItem(BANNER_DISMISSED)) return;
    if (_bannerNode) return;
    _bannerNode = buildBanner();
    document.body.appendChild(_bannerNode);

    const dismiss = _bannerNode.querySelector('#fitbFbBannerDismiss');
    const switchBtn = _bannerNode.querySelector('#fitbFbBannerSwitch');

    dismiss.addEventListener('click', () => {
      sessionStorage.setItem(BANNER_DISMISSED, '1');
      closeNode(_bannerNode);
      _bannerNode = null;
    });
    switchBtn.addEventListener('click', async () => {
      switchBtn.disabled = true;
      dismiss.disabled = true;
      const r = await postJson('/api/local-fallback/disable', {});
      if (!r.ok || !r.data || r.data.enabled === true) {
        switchBtn.disabled = false;
        dismiss.disabled = false;
        return;
      }
      sessionStorage.setItem(BANNER_DISMISSED, '1');
      closeNode(_bannerNode);
      _bannerNode = null;
      stopRecoveryPolling();
    });
  }

  async function recoveryTick() {
    if (sessionStorage.getItem(BANNER_DISMISSED)) {
      stopRecoveryPolling();
      return;
    }
    // QA fix: previously this kept polling forever even after the banner
    // was visible — a 90s heartbeat to the probe URLs for the lifetime
    // of the open tab. Once the banner is up, polling has no purpose
    // until the user dismisses or switches; the dismiss/switch handlers
    // restart polling if appropriate.
    if (_bannerNode) {
      stopRecoveryPolling();
      return;
    }
    let s;
    try {
      s = await fetchJson('/api/local-fallback/status');
    } catch (e) {
      _recoveryPollTimer = setTimeout(recoveryTick, RECOVERY_POLL_MS);
      return;
    }
    if (!s || !s.enabled) {
      // User toggled off in Settings — no banner needed.
      stopRecoveryPolling();
      return;
    }
    let h;
    try {
      h = await fetchJson('/api/local-fallback/remote-health');
    } catch (e) {
      _recoveryPollTimer = setTimeout(recoveryTick, RECOVERY_POLL_MS);
      return;
    }
    if (h && h.remote_healthy) {
      showRecoveryBanner();
      return;  // showRecoveryBanner sets _bannerNode; next-tick guard kicks in
    }
    _recoveryPollTimer = setTimeout(recoveryTick, RECOVERY_POLL_MS);
  }

  function startRecoveryPolling() {
    if (_recoveryPollTimer) return;
    // First tick after a short delay so we don't fire during page-load
    // contention.
    _recoveryPollTimer = setTimeout(recoveryTick, 5000);
  }

  function stopRecoveryPolling() {
    if (_recoveryPollTimer) {
      clearTimeout(_recoveryPollTimer);
      _recoveryPollTimer = null;
    }
  }

  // ── Boot ────────────────────────────────────────────────────────────────

  async function boot() {
    // Wire reactive modal
    window.addEventListener('fitb:stream-error', (e) => {
      maybeReactToError(e && e.detail);
    });

    // Decide whether to start recovery polling
    let s;
    try {
      s = await fetchJson('/api/local-fallback/status');
    } catch (e) {
      return;
    }
    if (s && s.enabled && s.ui_state !== 'no-supervisor') {
      startRecoveryPolling();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
