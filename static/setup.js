/** Fox in the Box — onboarding wizard */
const state = {
  currentStep: 1,
  totalSteps: 4,
  tailscaleConnected: false,
  tailnetUrl: null,
  pollInterval: null,
};
const NAMES = ["Welcome", "OpenRouter API Key", "Tailscale", "Done"];
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
function updateProgress(step) {
  const bar = document.getElementById("progress-bar");
  if (!bar) return;
  const dots = el("div", "progress-dots");
  for (let i = 1; i <= state.totalSteps; i++) {
    const d = el("span");
    if (i < step) d.classList.add("done");
    if (i === step) d.classList.add("active");
    dots.appendChild(d);
  }
  const lab = el("div", "progress-label", `${step} / ${state.totalSteps}  ${NAMES[step - 1]}`);
  bar.replaceChildren(dots, lab);
}
function stopPoll() {
  if (state.pollInterval) {
    clearInterval(state.pollInterval);
    state.pollInterval = null;
  }
}
async function jsonFetch(path, init) {
  const res = await fetch(path, init || {});
  let data = {};
  try {
    data = JSON.parse(await res.text() || "{}");
  } catch {}
  return { ok: res.ok, data };
}
async function postJson(path, body) {
  return jsonFetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}
async function getJson(path) {
  return jsonFetch(path);
}
function advance(n) {
  state.currentStep = n;
  renderStep(n);
  updateProgress(n);
}

function renderStep(n) {
  const c = document.getElementById("step-container");
  if (!c) return;
  c.replaceChildren();
  [render1, render2, render3, render4][n - 1](c);
}
function render1(c) {
  const k = el("div", "step-card");
  k.appendChild(el("h1", null, "\uD83E\uDD8A Fox in the Box"));
  k.appendChild(el("p", "description", "Let's get you set up."));
  k.appendChild(el("label", "field-label", "Language"));
  const s = el("select");
  s.disabled = true;
  s.title = "More languages coming soon";
  s.innerHTML = "<option value=\"en\">English</option>";
  k.appendChild(s);
  k.appendChild(el("p", "lang-note", "More languages coming soon."));
  const act = el("div", "row-actions");
  const nx = el("button", "btn btn-primary", "Next \u2192");
  nx.type = "button";
  nx.addEventListener("click", () => advance(2));
  act.appendChild(nx);
  k.appendChild(act);
  c.appendChild(k);
}

function render2(c) {
  const k = el("div", "step-card");
  k.appendChild(el("h1", null, "OpenRouter API Key"));
  k.appendChild(el("p", "description", "Fox uses OpenRouter to access AI models."));
  k.appendChild(el("label", "field-label", "API key")).htmlFor = "api-key-input";
  const inp = el("input");
  inp.type = "password";
  inp.id = "api-key-input";
  inp.autocomplete = "off";
  const err = el("div", "error-msg");
  err.setAttribute("role", "alert");
  const show = el("button", "btn btn-secondary", "Show");
  show.type = "button";
  show.addEventListener("click", () => {
    const on = inp.type === "password";
    inp.type = on ? "text" : "password";
    show.textContent = on ? "Hide" : "Show";
  });
  k.appendChild(inp);
  k.appendChild(show);
  k.appendChild(err);
  const hint = el("p", "kbd-hint");
  const a = el("a");
  a.href = "https://openrouter.ai";
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  a.textContent = "Get your free key at openrouter.ai ↗";
  hint.appendChild(a);
  k.appendChild(hint);
  const act = el("div", "row-actions row-actions--split");
  const bk = el("button", "btn btn-secondary", "\u2190 Back");
  bk.type = "button";
  bk.addEventListener("click", () => advance(1));
  const nx = el("button", "btn btn-primary", "Next \u2192");
  nx.type = "button";
  async function go() {
    err.textContent = "";
    inp.classList.remove("input-error");
    const v = inp.value.trim();
    if (!v) {
      inp.classList.add("input-error");
      err.textContent = "Enter your API key.";
      return;
    }
    if (!v.startsWith("sk-")) {
      inp.classList.add("input-error");
      err.textContent = "Key must start with sk-.";
      return;
    }
    nx.disabled = true;
    const sp = el("span", "spinner");
    nx.prepend(sp);
    const { ok, data } = await postJson("/api/setup/openrouter", { key: v });
    nx.removeChild(sp);
    nx.disabled = false;
    if (ok && data.ok) advance(3);
    else {
      inp.classList.add("input-error");
      err.textContent = typeof data.error === "string" ? data.error : "Could not save key.";
    }
  }
  nx.addEventListener("click", go);
  act.appendChild(bk);
  act.appendChild(nx);
  k.appendChild(act);
  c.appendChild(k);
}

function tsPoll(ui) {
  stopPoll();
  state.pollInterval = setInterval(async () => {
    const { ok, data } = await getJson("/api/setup/tailscale/status");
    if (!ok) return;
    ui.status.replaceChildren();
    ui.link.replaceChildren();
    ui.qr.replaceChildren();
    ui.retry.hidden = true;
    ui.cont.hidden = true;
    const st = data.status;
    if (st === "waiting") ui.status.textContent = "Waiting for Tailscale…";
    else if (st === "url_ready" && data.login_url) {
      ui.status.textContent = "Open this link to sign in:";
      const lk = el("a", "tailscale-link", data.login_url);
      lk.href = data.login_url;
      lk.target = "_blank";
      lk.rel = "noopener noreferrer";
      ui.link.appendChild(lk);
      const cv = document.createElement("canvas");
      cv.setAttribute("role", "img");
      cv.setAttribute("aria-label", "Tailscale login QR");
      ui.qr.appendChild(cv);
      if (window.QRCode && QRCode.toCanvas) QRCode.toCanvas(cv, data.login_url, { width: 200 });
    } else if (st === "connected") {
      stopPoll();
      state.tailscaleConnected = true;
      state.tailnetUrl = data.tailnet_url || null;
      const okp = el("p", "success", "Connected to Tailscale.");
      ui.status.appendChild(okp);
      if (data.tailnet_url) {
        const lk = el("a", "tailscale-link", data.tailnet_url);
        lk.href = data.tailnet_url;
        lk.target = "_blank";
        lk.rel = "noopener noreferrer";
        ui.link.appendChild(lk);
      }
      ui.cont.hidden = false;
    } else if (st === "error") {
      stopPoll();
      ui.status.textContent = data.error || "Tailscale error.";
      ui.retry.hidden = false;
    }
  }, 2000);
}

function render3(c) {
  stopPoll();
  state.tailscaleConnected = false;
  state.tailnetUrl = null;
  const k = el("div", "step-card");
  k.appendChild(el("h1", null, "Secure Remote Access (optional)"));
  k.appendChild(el("p", "description", "Tailscale gives you HTTPS access from anywhere — required for mobile / PWA use."));
  const status = el("div");
  status.setAttribute("aria-live", "polite");
  const link = el("div");
  const qr = el("div");
  qr.id = "qr-canvas-wrap";
  const retry = el("button", "btn btn-secondary", "Try again");
  retry.type = "button";
  retry.hidden = true;
  const cont = el("button", "btn btn-primary", "Continue \u2192");
  cont.type = "button";
  cont.hidden = true;
  cont.addEventListener("click", () => advance(4));
  const ui = { status, link, qr, retry, cont };
  retry.addEventListener("click", () => {
    stopPoll();
    status.replaceChildren();
    link.replaceChildren();
    qr.replaceChildren();
    retry.hidden = true;
    startTs();
  });
  const act = el("div", "row-actions row-actions--split");
  const bk = el("button", "btn btn-secondary", "\u2190 Back");
  bk.type = "button";
  bk.addEventListener("click", () => {
    stopPoll();
    advance(2);
  });
  const go = el("button", "btn btn-primary", "Set up Tailscale");
  go.type = "button";
  async function startTs() {
    go.disabled = true;
    const r = await postJson("/api/setup/tailscale/start", {});
    go.disabled = false;
    if (r.ok && r.data.ok) tsPoll(ui);
  }
  go.addEventListener("click", startTs);
  act.appendChild(bk);
  act.appendChild(go);
  const skip = el("button", "link-skip", "Skip for now \u2192");
  skip.type = "button";
  skip.addEventListener("click", () => {
    stopPoll();
    state.tailscaleConnected = false;
    state.tailnetUrl = null;
    advance(4);
  });
  k.appendChild(status);
  k.appendChild(link);
  k.appendChild(qr);
  k.appendChild(retry);
  k.appendChild(cont);
  k.appendChild(act);
  k.appendChild(skip);
  c.appendChild(k);
}

function render4(c) {
  const k = el("div", "step-card");
  k.appendChild(el("h1", null, "\uD83E\uDD8A Fox is ready!"));
  k.appendChild(el("p", "description", "Access Fox at:"));
  const ul = el("ul", "access-list");
  const o = window.location.origin || `http://${window.location.hostname}:8787`;
  ul.appendChild(el("li", null, o));
  if (state.tailscaleConnected && state.tailnetUrl) {
    const li = el("li");
    const a = el("a", null, state.tailnetUrl);
    a.href = state.tailnetUrl;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    li.appendChild(a);
    ul.appendChild(li);
  }
  k.appendChild(ul);
  const msg = el("p", "description", "Restarting…");
  msg.hidden = true;
  msg.setAttribute("aria-live", "polite");
  const act = el("div", "row-actions");
  const btn = el("button", "btn btn-primary", "Open Fox");
  btn.type = "button";
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    const d = await postJson("/api/setup/complete", { tailscale_connected: state.tailscaleConnected });
    if (d.ok && d.data.ok) await postJson("/api/setup/restart", {});
    msg.hidden = false;
    setTimeout(() => {
      window.location.href = "/";
    }, 3000);
  });
  act.appendChild(btn);
  k.appendChild(msg);
  k.appendChild(act);
  c.appendChild(k);
}

document.addEventListener("DOMContentLoaded", () => {
  renderStep(1);
  updateProgress(1);
});
