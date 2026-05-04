"""Hermes Web UI -- Onboarding wizard backend.

Provides the /setup page and /api/setup/* endpoints for first-run configuration.
The redirect middleware sends users to /setup until onboarding is complete.

Part of Fox in the Box (issue #28).
"""

import json
import logging
import os
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Onboarding state file ────────────────────────────────────────────────────

ONBOARDING_PATH = Path(os.environ.get("ONBOARDING_PATH", "/data/config/onboarding.json"))

# ── Paths exempt from redirect ───────────────────────────────────────────────

_SETUP_PREFIXES = ("/setup", "/api/setup/", "/static/setup.", "/health", "/static/favicon")


def onboarding_complete() -> bool:
    """Check whether onboarding has been completed."""
    try:
        with open(ONBOARDING_PATH) as f:
            return json.load(f).get("completed", False) is True
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False


def is_setup_path(path: str) -> bool:
    """Return True if the request path is exempt from the onboarding redirect."""
    return any(path.startswith(prefix) for prefix in _SETUP_PREFIXES)


def should_redirect_to_setup(path: str) -> bool:
    """Return True if the request should be redirected to /setup."""
    if onboarding_complete():
        return False
    if is_setup_path(path):
        return False
    return True


def redirect_to_setup(handler) -> None:
    """Send a 302 redirect to /setup."""
    handler.send_response(302)
    handler.send_header("Location", "/setup")
    handler.send_header("Content-Length", "0")
    handler.end_headers()


# ── ENV file helpers ─────────────────────────────────────────────────────────

_ENV_PATH = Path(os.environ.get("HERMES_ENV_PATH", "/data/config/hermes.env"))


def _write_env_key(key: str, value: str) -> None:
    """Write or update a key=value pair in the env file.

    Creates the file and parent directory if they do not exist.
    Preserves existing lines. Updates in-place if key already present.
    """
    _ENV_PATH.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    if _ENV_PATH.exists():
        lines = _ENV_PATH.read_text(encoding="utf-8").splitlines()

    found = False
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            existing_key = stripped.split("=", 1)[0].strip()
            if existing_key == key:
                lines[i] = f"{key}={value}"
                found = True
                break

    if not found:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"{key}={value}")

    _ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── Setup route handlers ─────────────────────────────────────────────────────


def handle_setup_page(handler) -> None:
    """Serve the setup.html page."""
    from api.config import REPO_ROOT
    setup_path = REPO_ROOT / "static" / "setup.html"
    if not setup_path.exists():
        handler.send_response(500)
        handler.send_header("Content-Type", "text/plain")
        body = b"Setup page not found"
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
        return
    html = setup_path.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(html)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(html)


def handle_setup_openrouter(handler, body: dict) -> dict:
    """Validate and persist an OpenRouter API key.

    Returns a result dict. Never logs the key value.
    """
    key = body.get("key", "")
    if not isinstance(key, str):
        return {"ok": False, "error": "Key must be a string."}
    key = key.strip()
    if not key:
        return {"ok": False, "error": "API key is required."}
    if not key.startswith("sk-"):
        return {"ok": False, "error": "Key must start with sk-."}
    if len(key) > 512:
        return {"ok": False, "error": "Key is too long."}

    try:
        _write_env_key("OPENROUTER_API_KEY", key)
    except OSError as exc:
        logger.error("Failed to write env file: %s", exc)
        return {"ok": False, "error": "Failed to save key."}

    return {"ok": True}


def handle_setup_complete(handler, body: dict) -> dict:
    """Mark onboarding as complete and write the state file."""
    tailscale_connected = bool(body.get("tailscale_connected", False))

    ONBOARDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "completed": True,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "tailscale_connected": tailscale_connected,
    }
    ONBOARDING_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return {"ok": True}


def handle_setup_restart(handler) -> dict:
    """Restart hermes-gateway and hermes-webui via supervisorctl."""
    try:
        result = subprocess.run(
            [
                "supervisorctl",
                "-c", "/etc/supervisor/supervisord.conf",
                "restart", "hermes-gateway", "hermes-webui",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return {"ok": True}
        return {"ok": False, "error": result.stderr.strip() or "Restart failed."}
    except FileNotFoundError:
        return {"ok": False, "error": "supervisorctl not found."}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Restart timed out."}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
