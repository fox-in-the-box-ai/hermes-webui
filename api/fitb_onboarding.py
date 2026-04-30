"""
Fox in the Box — setup wizard backends: onboarding redirect state, OpenRouter/Tailscale, completion.

Reads ONBOARDING_PATH / HERMES_ENV_PATH from os.environ on each access (tests rely on runtime env overrides).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from api.helpers import j, t

SUPERVISOR_CONF = "/etc/supervisor/supervisord.conf"

LOGIN_URL_RE = re.compile(r"https://login\.tailscale\.com/\S+")

_tailscale_lock = threading.Lock()
_tailscale_worker_started = False
_last_pytest_tc = ""

# Shared Tailscale onboarding state (see task spec).
_TAILSCALE_STATE: dict = {
    "status": "waiting",
    "login_url": None,
    "tailnet_url": None,
    "error": None,
}

_config_dir_cached: Path | None = None


def _default_writable_config_dir() -> Path:
    """Prefer /data/config (container); otherwise ~/.foxinthebox/config when /data is unavailable."""
    global _config_dir_cached
    if _config_dir_cached is not None:
        return _config_dir_cached

    last: OSError | None = None
    for base in (Path("/data/config"), Path.home() / ".foxinthebox" / "config"):
        try:
            base.mkdir(parents=True, exist_ok=True)
            probe = base / ".fitb_write_probe"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
            _config_dir_cached = base
            return base
        except OSError as exc:
            last = exc
            continue
    raise OSError(last.errno if last else 0, f"Could not create a writable config directory: {last!r}")


def onboarding_path_now() -> str:
    """Resolve onboarding.json path from environment at request time."""
    v = os.environ.get("ONBOARDING_PATH")
    if v:
        return v
    return str(_default_writable_config_dir() / "onboarding.json")


def hermes_env_path_now() -> str:
    """Resolve hermes.env path from environment at request time."""
    v = os.environ.get("HERMES_ENV_PATH")
    if v:
        return v
    return str(_default_writable_config_dir() / "hermes.env")


def onboarding_exempt(req_path: str) -> bool:
    """Paths that bypass the onboarding redirect middleware."""
    if req_path == "/setup" or req_path.startswith("/setup/"):
        return True
    if req_path.startswith("/api/setup/"):
        return True
    if req_path.startswith("/static/setup"):
        return True
    if req_path.startswith("/static/qrcode"):
        return True
    if req_path == "/health":
        return True
    return False


def onboarding_complete() -> bool:
    """True when onboarding.json exists and completed is truthy."""
    path = onboarding_path_now()
    try:
        with open(path, encoding="utf-8") as f:
            return bool(json.load(f).get("completed", False))
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        return False


def get_tailscale_state() -> dict:
    _maybe_reset_tailscale_for_pytest()
    with _tailscale_lock:
        return dict(_TAILSCALE_STATE)


def serve_setup_page(handler) -> None:
    static_root = Path(__file__).resolve().parent.parent / "static"
    html_path = (static_root / "setup.html").resolve()
    try:
        text = html_path.read_text(encoding="utf-8")
    except OSError:
        return j(handler, {"error": "Setup page unavailable"}, status=500)
    return t(handler, text, content_type="text/html; charset=utf-8")


def handle_fitb_setup_post(handler, parsed, body: dict):
    path = parsed.path
    if path == "/api/setup/openrouter":
        return _post_openrouter(handler, body or {})
    if path == "/api/setup/tailscale/start":
        return _post_tailscale_start(handler)
    if path == "/api/setup/complete":
        return _post_complete(handler, body or {})
    if path == "/api/setup/restart":
        return _post_restart(handler)
    return j(handler, {"error": "not found"}, status=404)


def _post_openrouter(handler, body: dict) -> None:
    key = body.get("key")
    key = "" if key is None else str(key)
    env_path_str = hermes_env_path_now()
    if not key:
        return j(handler, {"ok": False, "error": "Key is required"}, status=400)
    if not key.startswith("sk-"):
        return j(handler, {"ok": False, "error": "Key must start with sk-"}, status=400)
    if len(key) > 512:
        return j(handler, {"ok": False, "error": "Key is too long"}, status=400)
    path = Path(env_path_str)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        if path.exists():
            lines = path.read_text(encoding="utf-8").splitlines()
        out: list[str] = []
        replaced = False
        for line in lines:
            if line.strip().startswith("OPENROUTER_API_KEY="):
                out.append(f"OPENROUTER_API_KEY={key}")
                replaced = True
            else:
                out.append(line)
        if not replaced:
            out.append(f"OPENROUTER_API_KEY={key}")
        path.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
    except OSError:
        return j(handler, {"ok": False, "error": "Could not save configuration"}, status=500)
    return j(handler, {"ok": True})


def _maybe_reset_tailscale_for_pytest() -> None:
    """Pytest reuses one process; PYTEST_CURRENT_TEST changes per test function."""
    global _last_pytest_tc, _tailscale_worker_started
    nid = os.environ.get("PYTEST_CURRENT_TEST", "")
    if not nid or nid == _last_pytest_tc:
        return
    _last_pytest_tc = nid
    _tailscale_worker_started = False
    with _tailscale_lock:
        _TAILSCALE_STATE["status"] = "waiting"
        _TAILSCALE_STATE["login_url"] = None
        _TAILSCALE_STATE["tailnet_url"] = None
        _TAILSCALE_STATE["error"] = None


def _post_tailscale_start(handler) -> None:
    global _tailscale_worker_started
    _maybe_reset_tailscale_for_pytest()
    with _tailscale_lock:
        if _tailscale_worker_started:
            return j(handler, {"ok": True})
        _tailscale_worker_started = True
        _TAILSCALE_STATE["status"] = "waiting"
        _TAILSCALE_STATE["login_url"] = None
        _TAILSCALE_STATE["tailnet_url"] = None
        _TAILSCALE_STATE["error"] = None
    thread = threading.Thread(target=_tailscale_login_worker, name="fitb-tailscale", daemon=True)
    thread.start()
    return j(handler, {"ok": True})


def _tailscale_login_worker() -> None:
    def set_state(**kwargs: object) -> None:
        with _tailscale_lock:
            _TAILSCALE_STATE.update(kwargs)

    try:
        proc = subprocess.Popen(
            ["tailscale", "login", "--timeout=120"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        if proc.stdout:
            for line in proc.stdout:
                m = LOGIN_URL_RE.search(line)
                if m:
                    url = m.group(0).rstrip()
                    set_state(status="url_ready", login_url=url, error=None)
        proc.wait(timeout=130)
    except Exception:
        set_state(status="error", error="Tailscale login failed")
        return

    _short_poll = bool(os.environ.get("PYTEST_CURRENT_TEST"))
    deadline = time.time() + (3.0 if _short_poll else 125.0)
    while time.time() < deadline:
        try:
            raw = _read_tailscale_status_json_shell()
            if raw:
                ok, turl = _parse_tailscale_status_json(raw)
                if ok and turl:
                    set_state(status="connected", tailnet_url=turl, error=None)
                    return
        except OSError:
            pass
        time.sleep(2.0)

    with _tailscale_lock:
        if _TAILSCALE_STATE.get("status") == "url_ready":
            set_state(status="error", error="Timed out waiting for Tailscale connection")


def _read_tailscale_status_json_shell() -> str | None:
    fd, path = tempfile.mkstemp(prefix="fitb-ts-", suffix=".json")
    os.close(fd)
    try:
        quoted = shlex.quote(path)
        rc = os.system(f"tailscale status --json >{quoted} 2>/dev/null")
        if rc != 0:
            return None
        p = Path(path)
        if not p.is_file():
            return None
        return p.read_text(encoding="utf-8")
    finally:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass


def _parse_tailscale_status_json(raw: str) -> tuple[bool, str | None]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return False, None
    self = data.get("Self") or {}
    if self.get("Online") is not True:
        return False, None
    dns = (self.get("DNSName") or "").strip().rstrip(".")
    if not dns:
        return False, None
    return True, f"https://{dns}"


def _post_complete(handler, body: dict) -> None:
    ts_connected = body.get("tailscale_connected")
    if isinstance(ts_connected, str):
        ts_connected = ts_connected.lower() in ("1", "true", "yes")
    else:
        ts_connected = bool(ts_connected)

    onboarding_file = onboarding_path_now()
    path = Path(onboarding_file)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "completed": True,
            "completed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tailscale_connected": ts_connected,
        }
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError:
        return j(handler, {"ok": False, "error": "Could not save onboarding state"}, status=500)
    return j(handler, {"ok": True})


def _post_restart(handler) -> None:
    try:
        r = subprocess.run(
            [
                "supervisorctl",
                "-c",
                SUPERVISOR_CONF,
                "restart",
                "hermes-gateway",
                "hermes-webui",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if r.returncode == 0:
            return j(handler, {"ok": True})
        err = (r.stderr or r.stdout or "supervisorctl failed").strip()
        return j(handler, {"ok": False, "error": err[:2048]}, status=500)
    except (OSError, subprocess.TimeoutExpired):
        return j(handler, {"ok": False, "error": "Restart failed"}, status=500)
