"""Hermes Web UI — Tailscale connection orchestration (issue #96).

Wraps the in-container `tailscale` CLI so the desktop app can drive the
auth flow without docker-exec. Three personas are supported:

  1. Silent  — BackendState is already Running. /status returns it; no
               action needed; UI hides auth prompts.
  2. Interactive — User clicks Connect. We spawn `tailscale up` in a
               background thread, scrape the auth URL from stdout, return
               it to the client which opens it in the system browser.
               Client polls /up/poll until BackendState becomes Running.
  3. Power-user — User supplies an auth key (and/or login server,
               advertise-routes, exit-node, etc.). We pass these verbatim
               to `tailscale up` and skip the URL extraction (auth-key
               path is non-interactive).

Status state machine (kept in `_up_state`):

    idle         — nothing in flight
    starting     — subprocess spawned, no URL captured yet
    awaiting-auth — auth URL captured, waiting for user to click through
    running      — BackendState observed Running (terminal success)
    failed       — subprocess exited non-zero or polling timed out
                   (terminal; client must call /up again to retry)

The subprocess is reaped by a daemon thread. The HTTP request that
triggers /up returns immediately with the URL — never blocks on the
user's browser interaction.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)


# Auth URLs Tailscale prints. Both forms appear depending on whether the
# tailnet uses Tailscale's control plane (login.tailscale.com) or a custom
# login-server (headscale, on-prem) — power users with --login-server get
# their own host's URL. The regex matches either.
_AUTH_URL_RE = re.compile(r"https?://[^\s]*[/](?:a|register|login)/[^\s]+")
_TS_AUTH_URL_RE = re.compile(r"https?://login\.tailscale\.com[^\s]+")

# How long we'll keep `tailscale up` alive waiting for the user to click
# through. Mirrors install.sh's 600s `--timeout=` flag.
_UP_TIMEOUT_S = 600.0
# How long we'll consider a /up/poll outcome valid before the next /up
# overwrites it. Long enough that a slow user can finish auth.
_TERMINAL_GRACE_S = 900.0


# ── Subprocess wrapper ─────────────────────────────────────────────────────


def _run_tailscale(args: list[str], timeout: float = 10.0) -> tuple[int, str, str]:
    """Run a `tailscale` subcommand. Returns (returncode, stdout, stderr).
    rc=127 indicates the binary isn't on PATH (running outside the FITB
    container during dev/tests)."""
    try:
        result = subprocess.run(
            ["tailscale", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        return 127, "", "tailscale binary not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"tailscale {args[0] if args else ''} timed out"


# ── Status snapshot ────────────────────────────────────────────────────────


def get_status() -> dict[str, Any]:
    """Read `tailscale status --json` and project the bits the UI cares
    about. Returns a stable shape regardless of daemon state — the UI
    can switch on `backend_state` to decide what to render.

    BackendState values from Tailscale's controlclient package:
      NoState · NeedsLogin · NeedsMachineAuth · Stopped · Starting · Running
    """
    rc, out, err = _run_tailscale(["status", "--json"], timeout=5.0)
    if rc == 127:
        return {
            "available": False,
            "backend_state": "Unknown",
            "error": "tailscale CLI not found (running outside container?)",
        }
    if rc != 0 or not out:
        return {
            "available": True,
            "backend_state": "Unknown",
            "error": err.strip() or "tailscale status failed",
        }
    try:
        s = json.loads(out)
    except json.JSONDecodeError:
        return {
            "available": True,
            "backend_state": "Unknown",
            "error": "could not parse tailscale status JSON",
        }
    self_node = s.get("Self") or {}
    # DNSName comes back like "fox-clever.tailnet-xxxx.ts.net." — trim the
    # trailing dot for display, build an HTTPS URL the UI can show.
    dns = (self_node.get("DNSName") or "").rstrip(".")
    https_url = f"https://{dns}/" if dns else ""
    return {
        "available": True,
        "backend_state": s.get("BackendState") or "Unknown",
        "self": {
            "hostname": self_node.get("HostName") or "",
            "dns_name": dns,
            "online": bool(self_node.get("Online")),
            "tailscale_ips": self_node.get("TailscaleIPs") or [],
        },
        "magic_dns_suffix": s.get("MagicDNSSuffix") or "",
        "tailnet_url": https_url,
        "peers_count": len(s.get("Peer") or {}),
    }


# ── Up / auth flow (background subprocess + state machine) ────────────────


_up_lock = threading.Lock()
_up_state: dict[str, Any] = {
    "state": "idle",
    "auth_url": "",
    "started_at": 0.0,
    "ended_at": 0.0,
    "error": "",
}
_up_proc: subprocess.Popen | None = None
_up_log: list[str] = []


def _set_up_state(**fields) -> None:
    with _up_lock:
        _up_state.update(fields)


def _build_up_argv(opts: dict) -> list[str]:
    """Translate the request body into `tailscale up` flags. All keys are
    optional. Hostname is the only flag we always pass — the rest are
    user-supplied power-user knobs (Phase 2 will expose them in the UI).
    """
    argv = ["tailscale", "up", f"--timeout={int(_UP_TIMEOUT_S)}s"]

    hostname = (opts.get("hostname") or "").strip()
    if hostname:
        argv.append(f"--hostname={hostname}")

    login_server = (opts.get("login_server") or "").strip()
    if login_server:
        argv.append(f"--login-server={login_server}")

    advertise_routes = (opts.get("advertise_routes") or "").strip()
    if advertise_routes:
        argv.append(f"--advertise-routes={advertise_routes}")

    advertise_tags = (opts.get("advertise_tags") or "").strip()
    if advertise_tags:
        argv.append(f"--advertise-tags={advertise_tags}")

    if opts.get("accept_routes") is True:
        argv.append("--accept-routes")
    # accept-dns defaults true on Tailscale's side; only emit when explicit
    if opts.get("accept_dns") is False:
        argv.append("--accept-dns=false")

    exit_node = (opts.get("exit_node") or "").strip()
    if exit_node:
        argv.append(f"--exit-node={exit_node}")

    return argv


def _scrape_auth_url(line: str) -> str:
    """Pull an auth URL from a `tailscale up` stdout line. Tries the
    Tailscale-cloud pattern first, then the generic /a/ /register/ /login/
    fallback (covers headscale and other custom login servers)."""
    m = _TS_AUTH_URL_RE.search(line)
    if m:
        return m.group(0)
    m = _AUTH_URL_RE.search(line)
    return m.group(0) if m else ""


def _up_subprocess(argv: list[str], env: dict | None) -> None:
    """Daemon thread: spawn `tailscale up`, scrape stdout for the auth URL,
    keep the process alive until it exits (user finished auth) or until
    the timeout."""
    global _up_proc
    deadline = time.time() + _UP_TIMEOUT_S
    try:
        _up_proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
    except FileNotFoundError:
        _set_up_state(state="failed", error="tailscale CLI not found", ended_at=time.time())
        return
    except OSError as exc:
        _set_up_state(state="failed", error=f"failed to spawn tailscale up: {exc}", ended_at=time.time())
        return

    auth_url = ""
    while True:
        if time.time() > deadline:
            try:
                _up_proc.kill()
            except Exception:
                pass
            _set_up_state(state="failed", error="auth timed out before completion", ended_at=time.time())
            return
        if _up_proc.stdout is None:
            break
        line = _up_proc.stdout.readline()
        if not line:
            # EOF — process exited
            break
        line = line.rstrip()
        _up_log.append(line)
        if not auth_url:
            url = _scrape_auth_url(line)
            if url:
                auth_url = url
                _set_up_state(state="awaiting-auth", auth_url=url)

    rc = _up_proc.wait()
    _up_proc = None

    # rc=0 means tailscale up returned successfully — for interactive flows
    # this happens after the user clicks through; for auth-key flows it's
    # immediate. In both cases the daemon's BackendState should now be
    # Running, but we don't claim "running" here — the /up/poll handler
    # confirms by reading status. For auth-key paths we mark state directly.
    if rc == 0:
        st = get_status()
        if st.get("backend_state") == "Running":
            _set_up_state(state="running", ended_at=time.time())
        else:
            # Edge case: rc=0 but daemon not Running (e.g. login-only mode).
            # Treat as failed so UI re-prompts.
            _set_up_state(
                state="failed",
                error=f"tailscale up exited 0 but BackendState={st.get('backend_state')}",
                ended_at=time.time(),
            )
    else:
        tail = "\n".join(_up_log[-10:]) or "(no output)"
        _set_up_state(state="failed", error=f"tailscale up exited {rc}: {tail}", ended_at=time.time())


def start_up(opts: dict) -> dict[str, Any]:
    """POST /api/tailscale/up — spawn `tailscale up` in the background.

    If a previous attempt is still in flight (state=starting or
    awaiting-auth) AND not stale, we return the in-flight state instead
    of starting a new subprocess. This keeps Connect idempotent across
    retries / refreshes.
    """
    global _up_proc, _up_log
    with _up_lock:
        cur_state = _up_state["state"]
        if cur_state in ("starting", "awaiting-auth"):
            stale = (time.time() - _up_state["started_at"]) > _UP_TIMEOUT_S
            if not stale and _up_proc is not None and _up_proc.poll() is None:
                return {
                    "ok": True,
                    "reused": True,
                    "auth_url": _up_state["auth_url"],
                    "state": cur_state,
                }
        # Reset state for a fresh attempt
        _up_log = []
        _up_state.update({
            "state": "starting",
            "auth_url": "",
            "started_at": time.time(),
            "ended_at": 0.0,
            "error": "",
        })

    argv = _build_up_argv(opts or {})
    auth_key = (opts.get("auth_key") or "").strip()
    env = None
    if auth_key:
        import os
        env = dict(os.environ)
        env["TS_AUTHKEY"] = auth_key

    threading.Thread(target=_up_subprocess, args=(argv, env), name="tailscale-up", daemon=True).start()

    # Auth-key path is non-interactive; client doesn't need an auth_url.
    # Return immediately — the polling endpoint will tell the client when
    # BackendState is Running.
    return {
        "ok": True,
        "reused": False,
        "auth_key_used": bool(auth_key),
        "state": "starting",
    }


def get_up_progress() -> dict[str, Any]:
    """GET /api/tailscale/up/poll — current state of the in-flight (or
    most recent) up attempt. Client polls this every 2s while the modal /
    wizard step is open. Once state is `running` or `failed`, polling
    can stop.

    Bonus: when the daemon's BackendState is observed Running mid-poll,
    we promote the up-state to `running` even if the subprocess hasn't
    exited yet — this is a defense for the case where `tailscale up`
    keeps the subprocess alive past auth completion.
    """
    with _up_lock:
        snap = dict(_up_state)
    if snap["state"] in ("starting", "awaiting-auth"):
        st = get_status()
        if st.get("backend_state") == "Running":
            _set_up_state(state="running", ended_at=time.time())
            with _up_lock:
                snap = dict(_up_state)
    return {
        "state": snap["state"],
        "auth_url": snap["auth_url"],
        "error": snap["error"],
        "started_at": snap["started_at"],
        "ended_at": snap["ended_at"],
    }


def logout() -> dict[str, Any]:
    """POST /api/tailscale/logout — disconnect from the tailnet. Resets
    the up-state machine so the next Connect starts fresh."""
    rc, _out, err = _run_tailscale(["logout"], timeout=15.0)
    _set_up_state(state="idle", auth_url="", error="", started_at=0.0, ended_at=0.0)
    if rc != 0:
        return {"ok": False, "error": err.strip() or f"tailscale logout exited {rc}"}
    return {"ok": True}


# ── Tailscale Serve ────────────────────────────────────────────────────────


def configure_serve() -> dict[str, Any]:
    """POST /api/tailscale/serve — re-run `tailscale serve --bg / http://localhost:8787`.

    Idempotent. Useful when entrypoint.sh's auto-config polled too early
    and gave up, or when the user wants to re-enable Serve after a logout.
    """
    rc, _out, err = _run_tailscale(
        ["serve", "--bg", "/", "http://localhost:8787"], timeout=15.0,
    )
    if rc != 0:
        return {"ok": False, "error": err.strip() or f"tailscale serve exited {rc}"}
    return {"ok": True}


def get_serve_status() -> dict[str, Any]:
    """GET /api/tailscale/serve — current `tailscale serve status`."""
    rc, out, err = _run_tailscale(["serve", "status", "--json"], timeout=5.0)
    if rc != 0:
        return {"ok": False, "error": err.strip() or f"tailscale serve status exited {rc}"}
    try:
        return {"ok": True, "config": json.loads(out) if out else {}}
    except json.JSONDecodeError:
        return {"ok": True, "config": {}, "raw": out}


# ── Route handlers ─────────────────────────────────────────────────────────


def handle_get_status(handler) -> dict[str, Any]:
    return get_status()


def handle_post_up(handler, body: dict) -> dict[str, Any]:
    return start_up(body or {})


def handle_get_up_poll(handler) -> dict[str, Any]:
    return get_up_progress()


def handle_post_logout(handler, body: dict) -> dict[str, Any]:
    return logout()


def handle_post_serve(handler, body: dict) -> dict[str, Any]:
    return configure_serve()


def handle_get_serve(handler) -> dict[str, Any]:
    return get_serve_status()
