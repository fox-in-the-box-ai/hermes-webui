"""Hermes Web UI -- Tailscale hostname management.

Persists ``FOX_HOSTNAME`` to the FITB env file and applies it live to the
running tailscaled via ``tailscale set --hostname``. Re-reads
``tailscale status --json`` after applying so collision suffixes
(`-1`, `-2`, …) are surfaced to the caller.

Issue #44 — Electron desktop-app first-run parity with the install.sh
hostname work from #3.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import subprocess
from pathlib import Path
from typing import Any

from api.onboarding import _write_env_key

logger = logging.getLogger(__name__)


# Mirrors the curated list in packages/scripts/install.sh:_default_hostname().
# Kept in sync intentionally — both places generate names from the same pool so
# host-script users and Electron users produce indistinguishable defaults.
_ADJECTIVES = (
    "quick", "clever", "bright", "swift", "keen",
    "amber", "nimble", "fierce", "bold", "sly",
    "golden", "autumn",
)

# Tailscale's effective hostname rule, derived from
# util/dnsname/dnsname.go:SanitizeLabel — RFC 1035 DNS label form.
_HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_MAX_LEN = 63

_ENV_PATH = Path(os.environ.get("HERMES_ENV_PATH", "/data/config/hermes.env"))
_FOX_HOSTNAME_KEY = "FOX_HOSTNAME"


def default_hostname() -> str:
    """Generate a fox-<adjective> default. Random pick keeps tailnets
    collision-free best-effort; Tailscale auto-suffixes on real collision."""
    return f"fox-{random.choice(_ADJECTIVES)}"


def sanitize_hostname(raw: str) -> str:
    """Lowercase, replace runs of non-[a-z0-9-] with a single -, strip
    leading/trailing -, truncate to 63 chars. Mirrors install.sh's
    _sanitize_hostname() byte-for-byte."""
    if not raw:
        return ""
    s = raw.strip().lower()
    s = re.sub(r"[^a-z0-9-]+", "-", s)
    s = s.strip("-")
    return s[:_MAX_LEN]


def validate_hostname(s: str) -> str | None:
    """Return ``None`` if valid, else a human-readable error message.
    Stricter than sanitize_hostname — caller should sanitize first if it
    wants automatic correction; this rejects anything not already valid."""
    if not s:
        return "Hostname is required."
    if len(s) > _MAX_LEN:
        return f"Hostname must be {_MAX_LEN} characters or fewer."
    if not _HOSTNAME_RE.match(s):
        return (
            "Hostname must contain only lowercase letters, digits, and hyphens, "
            "and must start and end with a letter or digit."
        )
    return None


def _read_configured_hostname() -> str:
    """Read FOX_HOSTNAME from hermes.env, or empty string if unset."""
    try:
        if not _ENV_PATH.exists():
            return ""
        for raw in _ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == _FOX_HOSTNAME_KEY:
                return value.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _run_tailscale(args: list[str], timeout: float = 10.0) -> tuple[int, str, str]:
    """Run a `tailscale` subcommand. Returns (returncode, stdout, stderr).
    Returncode 127 indicates the binary isn't on PATH (e.g. running outside
    the FITB container during dev/tests)."""
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


def _read_effective_hostname() -> str | None:
    """Read the running daemon's current effective hostname from
    ``tailscale status --json``. Returns ``None`` if tailscaled isn't
    reachable. The control plane appends `-1`, `-2`, … on hostname
    collision; ``Self.HostName`` reflects the suffixed form."""
    rc, out, _err = _run_tailscale(["status", "--json"], timeout=5.0)
    if rc != 0 or not out:
        return None
    try:
        status = json.loads(out)
    except json.JSONDecodeError:
        return None
    self_node = status.get("Self") or {}
    return self_node.get("HostName") or None


def get_hostname_state() -> dict[str, Any]:
    """Read everything the Settings UI needs to render the hostname field."""
    configured = _read_configured_hostname()
    effective = _read_effective_hostname()
    return {
        "configured": configured,
        "effective": effective or "",
        "default_suggestion": default_hostname() if not configured else "",
        "tailscale_running": effective is not None,
    }


def apply_hostname(hostname: str) -> dict[str, Any]:
    """Persist FOX_HOSTNAME and apply it live to tailscaled.

    Sanitizes and validates, then:
      1. Writes ``FOX_HOSTNAME=<name>`` to /data/config/hermes.env so the
         next container start (or supervisord restart) picks it up.
      2. Calls ``tailscale set --hostname=<name>`` against the running
         daemon. Surgical mutation — only the hostname pref is changed
         (cf. ``tailscale up``, which would re-apply a full preferences
         set and risk resetting unrelated flags).
      3. Re-reads ``tailscale status --json`` so the caller sees the
         effective name (control plane may have appended a collision
         suffix).
    """
    sanitized = sanitize_hostname(hostname)
    err = validate_hostname(sanitized)
    if err:
        return {"ok": False, "error": err}

    try:
        _write_env_key(_FOX_HOSTNAME_KEY, sanitized)
    except OSError as exc:
        logger.error("Failed to write FOX_HOSTNAME to hermes.env: %s", exc)
        return {"ok": False, "error": "Failed to persist hostname."}

    rc, _out, err_text = _run_tailscale(["set", f"--hostname={sanitized}"], timeout=10.0)

    # The persist already succeeded — `FOX_HOSTNAME` is in hermes.env. The
    # live `tailscale set` is a best-effort hot-apply: if it fails (daemon
    # not authed yet, not running, binary missing, etc.) the value still
    # takes effect on the next container/daemon start. Surface the reason
    # in `note` rather than failing the whole call, so first-run users who
    # haven't yet authed Tailscale don't see a scary error for what is
    # actually a successful save.
    if rc != 0:
        if rc == 127:
            note = ("Saved. Tailscale binary not on PATH from this process; "
                    "the new name will apply on the next container start.")
        else:
            note = ("Saved. Live apply skipped — Tailscale daemon may not be "
                    "running or authenticated yet. The new name will apply "
                    "on the next start.")
        if err_text.strip():
            logger.info("tailscale set --hostname rc=%d stderr=%s", rc, err_text.strip())
        return {
            "ok": True,
            "requested_hostname": sanitized,
            "effective_hostname": "",
            "applied_live": False,
            "note": note,
        }

    effective = _read_effective_hostname() or sanitized
    return {
        "ok": True,
        "requested_hostname": sanitized,
        "effective_hostname": effective,
        "applied_live": True,
        "collision_suffixed": effective != sanitized,
    }


# ── Route handlers ──────────────────────────────────────────────────────────


def handle_get_hostname(handler) -> dict[str, Any]:
    """GET /api/settings/hostname — returns current state for the UI."""
    return get_hostname_state()


def handle_set_hostname(handler, body: dict) -> dict[str, Any]:
    """POST /api/settings/hostname — body {"hostname": "<name>"}."""
    raw = body.get("hostname", "")
    if not isinstance(raw, str):
        return {"ok": False, "error": "hostname must be a string"}
    return apply_hostname(raw)
