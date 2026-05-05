"""Hermes Web UI -- Local AI fallback orchestration (issue #9).

Bridges three subsystems:

1. The download manager from #10 (`api/models_download`) — pulls the GGUF
   model file lazily into /data/models/.
2. The bundled `llama-server` binary at /app/llama-cpp/llama-server,
   supervised by supervisord with autostart=false (so it costs zero
   idle RAM until the user actually opts in).
3. The failover classifier in `api/streaming.py` — when a remote
   provider call fails with a transient error and the user has opted in,
   the agent re-targets the request at this module's local endpoint.

The "Local fallback" tile in Settings → Providers is the user-facing
entry. Toggling ON:
  a. starts (or continues) the model download via #10,
  b. once the file is on disk, asks supervisord to start llama-server,
  c. records the opt-in in settings.json:local_fallback_enabled.

Toggling OFF:
  a. stops llama-server via supervisord (frees RAM immediately),
  b. clears the opt-in. Downloaded model file is preserved on /data —
     deleting it is a separate explicit action.

Routing decisions for a chat request:
  - Returns ``http://127.0.0.1:8643/v1`` as the fallback base URL when
    `local_fallback_enabled` AND the model is downloaded AND the
    llama-server program is RUNNING in supervisord.
  - Otherwise returns None and the caller surfaces the upstream error.

Failure modes that keep the rest of Fox running:
  - Model file missing → toggle ON triggers download, llama-server stays
    stopped until file ready. UI shows "Downloading X%".
  - llama-server crash loop → supervisord's startretries=2 caps it; UI
    surfaces the supervisorctl status as "FATAL".
  - supervisorctl missing (upstream Hermes / dev environments without
    supervisord) → start/stop calls degrade to no-ops; the rest of the
    module still works for Hermes WebUI users running outside Fox.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


# ── Constants ──────────────────────────────────────────────────────────────

# Settings key — mirrors the provider-key persistence pattern: a single
# bool in settings.json. The UI's Local fallback tile binds to this.
SETTINGS_KEY = "local_fallback_enabled"

# The model registered in api/models_download.KNOWN_MODELS that the
# fallback runtime targets. Hard-coded here because llama-server's command
# line in supervisord.conf points at this exact path.
MODEL_ID = "phi4-mini"

# Bound in supervisord.conf — keep in sync if you change either.
LLAMA_SERVER_HOST = "127.0.0.1"
LLAMA_SERVER_PORT = 8643
LLAMA_SERVER_BASE_URL = f"http://{LLAMA_SERVER_HOST}:{LLAMA_SERVER_PORT}/v1"
LLAMA_SERVER_HEALTH = f"http://{LLAMA_SERVER_HOST}:{LLAMA_SERVER_PORT}/health"

# supervisord program name — must match supervisord.conf [program:...].
SUPERVISOR_PROGRAM = "llama-server"

# Failover classifier — the set of error signatures the upstream
# `streaming.py` hands to us as "this looks like a transient provider
# blip". Used in `should_failover()`.
_FAILOVER_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})
_FAILOVER_ERROR_SUBSTRINGS = (
    "rate limit",
    "rate-limit",
    "too many requests",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "internal server error",
    "connection reset",
    "connection refused",
    "connection aborted",
    "remote disconnected",
    "remote end closed",
    "timed out",
    "temporary failure in name resolution",
    "name or service not known",
    "no route to host",
)
# These DO NOT trigger failover — they're config errors, not provider
# outages. Switching to a local model would mask the real problem.
_NEVER_FAILOVER_SUBSTRINGS = (
    "invalid api key",
    "incorrect api key",
    "authentication",
    "unauthorized",
    "forbidden",
    "model not found",
    "model_not_found",
    "billing",
    "quota",
    "credits",
    "permission denied",
)


# ── Settings persistence ───────────────────────────────────────────────────


def is_enabled() -> bool:
    """Read the opt-in flag from settings.json. Defaults to False."""
    try:
        from api.config import load_settings
        return bool(load_settings().get(SETTINGS_KEY, False))
    except Exception:
        return False


def set_enabled(enabled: bool) -> bool:
    """Write the opt-in flag. Returns the new value (or False if the
    write failed — rare)."""
    try:
        from api.config import load_settings, save_settings
        s = load_settings()
        s[SETTINGS_KEY] = bool(enabled)
        save_settings(s)
        return bool(enabled)
    except Exception as exc:
        logger.exception("Failed to persist %s: %s", SETTINGS_KEY, exc)
        return False


# ── supervisord control ────────────────────────────────────────────────────


def _supervisorctl(*args: str, timeout: float = 10.0) -> tuple[int, str, str]:
    """Run supervisorctl. Returns (rc, stdout, stderr). rc=127 means
    supervisorctl isn't on PATH — callers treat that as a soft no-op
    (we're outside a Fox container)."""
    if not shutil.which("supervisorctl"):
        return 127, "", "supervisorctl not on PATH"
    try:
        result = subprocess.run(
            ["supervisorctl", "-c", "/etc/supervisor/supervisord.conf", *args],
            capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"supervisorctl {args[0] if args else ''} timed out"
    except OSError as exc:
        return 1, "", str(exc)


def supervisor_status() -> str:
    """Return one of: STOPPED | STARTING | RUNNING | BACKOFF | EXITED |
    FATAL | UNKNOWN | NO_SUPERVISOR. UNKNOWN if the program name isn't in
    supervisorctl status; NO_SUPERVISOR when supervisorctl is absent."""
    rc, out, _err = _supervisorctl("status", SUPERVISOR_PROGRAM, timeout=5.0)
    if rc == 127:
        return "NO_SUPERVISOR"
    # supervisorctl prints e.g.:
    #   llama-server      RUNNING   pid 1234, uptime 0:00:13
    #   llama-server      STOPPED   Not started
    for line in (out or "").splitlines():
        parts = line.split()
        if not parts or parts[0] != SUPERVISOR_PROGRAM:
            continue
        if len(parts) >= 2:
            return parts[1]
    return "UNKNOWN"


def start_llama_server() -> dict[str, Any]:
    """Start the llama-server program. Idempotent — already-running is
    treated as success."""
    status = supervisor_status()
    if status == "RUNNING":
        return {"ok": True, "status": status, "note": "already running"}
    if status == "NO_SUPERVISOR":
        return {"ok": False, "status": status, "error": "Supervisor not available"}
    rc, _out, err = _supervisorctl("start", SUPERVISOR_PROGRAM, timeout=15.0)
    new_status = supervisor_status()
    if rc == 0 or new_status in ("RUNNING", "STARTING"):
        return {"ok": True, "status": new_status}
    return {"ok": False, "status": new_status, "error": err.strip() or "Failed to start llama-server"}


def stop_llama_server() -> dict[str, Any]:
    """Stop the llama-server program. Idempotent — already-stopped
    returns success."""
    status = supervisor_status()
    if status in ("STOPPED", "EXITED", "NO_SUPERVISOR"):
        return {"ok": True, "status": status, "note": "already stopped"}
    rc, _out, err = _supervisorctl("stop", SUPERVISOR_PROGRAM, timeout=15.0)
    new_status = supervisor_status()
    if rc == 0 or new_status in ("STOPPED", "EXITED"):
        return {"ok": True, "status": new_status}
    return {"ok": False, "status": new_status, "error": err.strip() or "Failed to stop llama-server"}


# ── Health ─────────────────────────────────────────────────────────────────


def _server_healthy(timeout: float = 1.0) -> bool:
    """True if llama-server's /health returns 200."""
    try:
        with urllib.request.urlopen(LLAMA_SERVER_HEALTH, timeout=timeout) as resp:  # nosec B310
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


# ── Routing decisions ─────────────────────────────────────────────────────


def get_fallback_endpoint() -> dict[str, Any] | None:
    """Return the fallback endpoint config if it's both opted-in and
    actually serving, else None.

    The streaming.py exception handler calls this; if it returns None,
    the upstream provider error is surfaced to the user (no silent
    fallback to a non-functional local model)."""
    if not is_enabled():
        return None
    if supervisor_status() != "RUNNING":
        return None
    return {
        "base_url": LLAMA_SERVER_BASE_URL,
        "model": MODEL_ID,
        "api_key": "",  # llama-server doesn't require one
        "provider": "local-fallback",
    }


def should_failover(error: Exception | str | None, http_status: int | None = None) -> bool:
    """Classify whether an upstream error is failover-eligible.

    Returns True for transient provider issues (5xx, 429, connection
    errors). Returns False for config errors (auth, model-not-found,
    quota, billing) — masking those with a local model would hide the
    real problem from the user."""
    if http_status is not None and http_status in _FAILOVER_HTTP_STATUSES:
        # Even with a matching status, NEVER substring takes precedence.
        # Some providers return 401 with a body that says "rate limit"
        # — auth is auth, regardless of cosmetic phrasing.
        msg_lower = str(error or "").lower()
        if any(s in msg_lower for s in _NEVER_FAILOVER_SUBSTRINGS):
            return False
        return True
    msg = str(error or "").lower()
    if not msg:
        return False
    if any(s in msg for s in _NEVER_FAILOVER_SUBSTRINGS):
        return False
    return any(s in msg for s in _FAILOVER_ERROR_SUBSTRINGS)


# ── Status (for Settings tile + reactive modal) ────────────────────────────


def get_status() -> dict[str, Any]:
    """Snapshot of every piece of state the Settings tile + reactive
    modal need to render. One round-trip per Settings render."""
    enabled = is_enabled()

    # Model installation state from #10's manager.
    try:
        from api.models_download import list_models
        models = list_models().get("models", [])
        model = next((m for m in models if m["id"] == MODEL_ID), None)
    except Exception:
        model = None

    sup_status = supervisor_status()
    healthy = (sup_status == "RUNNING") and _server_healthy()

    # Derive a single user-facing state string. The UI dispatches on this.
    if not enabled:
        ui_state = "disabled"
    elif model is None:
        ui_state = "missing-model-registry"
    elif not model.get("installed"):
        if (model.get("state") or {}).get("status") == "running":
            ui_state = "downloading"
        else:
            ui_state = "needs-download"
    elif sup_status == "NO_SUPERVISOR":
        ui_state = "no-supervisor"
    elif sup_status not in ("RUNNING", "STARTING"):
        ui_state = "starting"  # the toggle-on path will start it momentarily
    elif sup_status == "STARTING" or not healthy:
        ui_state = "warming"
    else:
        ui_state = "ready"

    return {
        "enabled": enabled,
        "model_id": MODEL_ID,
        "model_installed": bool(model and model.get("installed")),
        "model_state": (model or {}).get("state"),
        "model_size_bytes": (model or {}).get("expected_size_bytes", 0),
        "supervisor_status": sup_status,
        "server_healthy": healthy,
        "endpoint": LLAMA_SERVER_BASE_URL if healthy else None,
        "ui_state": ui_state,
    }


# ── Toggle implementation (the Settings → Providers Local fallback tile) ──


def _start_when_ready(timeout_s: float = 600.0, poll_s: float = 1.0) -> None:
    """Background watcher: once the model file appears on disk, ask
    supervisord to start llama-server. Runs in a daemon thread so the
    HTTP request that triggered the toggle returns immediately.

    This handles the long-tail case of a 2.5 GB download — we don't want
    the toggle-on POST to block until the bytes finish, but we DO want
    llama-server to come up automatically when they do."""
    try:
        from api.models_download import KNOWN_MODELS, _is_final_present
    except Exception:
        return
    model = KNOWN_MODELS.get(MODEL_ID)
    if not model:
        return
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not is_enabled():
            return  # user toggled off mid-download
        if _is_final_present(model):
            res = start_llama_server()
            logger.info("Local fallback: model ready, started llama-server: %s", res)
            return
        time.sleep(poll_s)


def enable() -> dict[str, Any]:
    """Toggle ON. Persists the flag, kicks the download if needed, and
    schedules llama-server start when the model file lands.

    Returns the post-call status snapshot the UI uses to render the
    tile state immediately."""
    set_enabled(True)

    # Make sure the model is on disk (or downloading).
    try:
        from api.models_download import KNOWN_MODELS, _is_final_present, start_download
        model = KNOWN_MODELS.get(MODEL_ID)
        if model and not _is_final_present(model):
            start_download(MODEL_ID)
    except Exception:
        logger.exception("Local fallback: could not initiate model download")

    # If model already present, start immediately. Otherwise spawn the
    # background watcher that starts llama-server once the file appears.
    try:
        from api.models_download import KNOWN_MODELS, _is_final_present
        model = KNOWN_MODELS.get(MODEL_ID)
        if model and _is_final_present(model):
            start_llama_server()
        else:
            threading.Thread(
                target=_start_when_ready, name="local-fallback-watcher", daemon=True,
            ).start()
    except Exception:
        logger.exception("Local fallback: could not schedule llama-server start")

    return get_status()


def disable() -> dict[str, Any]:
    """Toggle OFF. Persists the flag and stops llama-server (frees RAM).
    Downloaded model file is preserved on /data — explicit delete is a
    separate action via the #10 manager."""
    set_enabled(False)
    try:
        stop_llama_server()
    except Exception:
        logger.exception("Local fallback: could not stop llama-server")
    return get_status()


# ── Route handlers ─────────────────────────────────────────────────────────


def handle_get_status(handler) -> dict[str, Any]:
    """GET /api/local-fallback/status — full snapshot."""
    return get_status()


def handle_post_enable(handler, body: dict) -> dict[str, Any]:
    """POST /api/local-fallback/enable — toggle on."""
    return enable()


def handle_post_disable(handler, body: dict) -> dict[str, Any]:
    """POST /api/local-fallback/disable — toggle off."""
    return disable()
