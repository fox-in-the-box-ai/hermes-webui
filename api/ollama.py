"""Hermes Web UI -- Local Ollama integration.

Auto-detects a host-side Ollama daemon (issue #66) and exposes it as a
first-class provider tile. Routes user-selected models through the
existing `custom` OpenAI-compat path by writing model.{provider,base_url,name}
into config.yaml and triggering a gateway hot-reload — no hermes-agent
patches required.

The container can reach a host-side Ollama at:
- `http://host.docker.internal:11434` on Docker Desktop (macOS/Windows)
- `http://host.docker.internal:11434` on Linux IF the container was
  started with `--add-host=host.docker.internal:host-gateway` (Docker
  Engine 20.10+; Fox in the Box's Electron + install.sh do this in v0.3.0+)
- `http://localhost:11434` on native installs (rare; Fox normally runs
  inside Docker)

Older containers that predate the v0.3.0 host-gateway addition will see
"not detected" on Linux until they're re-created.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


# Ordered probe candidates. host.docker.internal first because that's the
# canonical Docker → host route; localhost as a fallback for rare native
# installs. Both share the default Ollama port.
_PROBE_HOSTS = (
    "http://host.docker.internal:11434",
    "http://localhost:11434",
)
_PROBE_TIMEOUT_SEC = 1.0
_CACHE_TTL_SEC = 10.0  # keep Settings-page loads snappy without hiding state changes too long

# Module-level cache for the detection probe. We expect a few calls per
# Settings render; caching avoids waiting on two TCP connect timeouts each
# time. Cleared via `clear_cache()` for tests / explicit refresh.
_cache_lock = threading.Lock()
_cache: dict[str, Any] | None = None
_cache_at: float = 0.0


def clear_cache() -> None:
    """Drop the detection cache (used by the Settings refresh button)."""
    global _cache, _cache_at
    with _cache_lock:
        _cache = None
        _cache_at = 0.0


def _http_json(url: str, method: str = "GET", body: dict | None = None,
               timeout: float = _PROBE_TIMEOUT_SEC) -> dict | None:
    """Minimal urllib JSON client. Returns parsed JSON or None on any
    failure. Stays inside stdlib — Hermes WebUI is intentionally
    framework-free."""
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 — internal HTTP only
            raw = resp.read()
        if not raw:
            return {}
        return json.loads(raw)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError):
        return None


def _probe() -> dict[str, Any]:
    """Return {up, host, version} for the first reachable Ollama daemon, or
    {up: False} if none. `host` is the base URL (no `/api` suffix)."""
    for base in _PROBE_HOSTS:
        info = _http_json(f"{base}/api/version", timeout=_PROBE_TIMEOUT_SEC)
        if info and isinstance(info, dict) and "version" in info:
            return {"up": True, "host": base, "version": info.get("version", "")}
    return {"up": False, "host": "", "version": ""}


def _cached_probe(force: bool = False) -> dict[str, Any]:
    """Return a cached probe result, refreshing if the entry is older than
    `_CACHE_TTL_SEC` or `force=True`."""
    global _cache, _cache_at
    now = time.time()
    with _cache_lock:
        if not force and _cache is not None and (now - _cache_at) < _CACHE_TTL_SEC:
            return dict(_cache)
        result = _probe()
        _cache = result
        _cache_at = now
        return dict(result)


# ── Status + model-list endpoints ───────────────────────────────────────────


def get_status(force_refresh: bool = False) -> dict[str, Any]:
    """Return the current Ollama detection state for Settings UI."""
    p = _cached_probe(force=force_refresh)
    return {
        "running": bool(p.get("up")),
        "host": p.get("host", ""),
        "version": p.get("version", ""),
    }


def get_models() -> dict[str, Any]:
    """Return installed models from the detected Ollama daemon. Returns
    `{"running": False, "models": []}` if no daemon was found."""
    p = _cached_probe()
    if not p.get("up"):
        return {"running": False, "host": "", "models": []}
    base = p["host"]
    raw = _http_json(f"{base}/api/tags", timeout=3.0)
    if raw is None or not isinstance(raw, dict):
        return {"running": False, "host": base, "models": [], "error": "Ollama responded but /api/tags failed"}
    models = []
    for entry in raw.get("models", []) or []:
        if not isinstance(entry, dict):
            continue
        details = entry.get("details") or {}
        models.append({
            "name": entry.get("name") or entry.get("model") or "",
            "size_bytes": entry.get("size") or 0,
            "parameter_size": details.get("parameter_size") or "",
            "quantization": details.get("quantization_level") or "",
            "family": details.get("family") or "",
            "modified_at": entry.get("modified_at") or "",
        })
    return {"running": True, "host": base, "models": models}


# ── Model selection (writes config.yaml, hot-reloads gateway) ──────────────


def use_model(model_name: str) -> dict[str, Any]:
    """Activate a local Ollama model for chat by writing
    model.{provider,base_url,name} into config.yaml and reloading the
    runtime. Routes through the existing `custom` OpenAI-compat path —
    no hermes-agent change needed."""
    if not isinstance(model_name, str):
        return {"ok": False, "error": "model name must be a string"}
    name = model_name.strip()
    if not name:
        return {"ok": False, "error": "model name is required"}

    p = _cached_probe()
    if not p.get("up"):
        return {"ok": False, "error": "Local Ollama daemon not detected. Is it running?"}

    base_url = f"{p['host']}/v1"

    # Lazy import to keep this module import-cheap at WebUI startup.
    from api.config import (
        _get_config_path,
        _save_yaml_config_file,
        get_config,
        reload_config,
    )

    cfg = get_config()
    if not isinstance(cfg, dict):
        cfg = {}
    model_cfg = cfg.get("model")
    if not isinstance(model_cfg, dict):
        model_cfg = {}
    model_cfg["provider"] = "custom"
    model_cfg["base_url"] = base_url
    model_cfg["name"] = name
    # Local Ollama is keyless; clear any stale top-level api_key so a prior
    # provider's key isn't accidentally sent on requests.
    model_cfg.pop("api_key", None)
    cfg["model"] = model_cfg

    try:
        _save_yaml_config_file(_get_config_path(), cfg)
        reload_config()
    except Exception as exc:
        logger.exception("Failed to switch active model to local Ollama: %s", exc)
        return {"ok": False, "error": f"Failed to update config: {exc}"}

    # Best-effort gateway hot-reload (mirrors providers.py:_reload_provider_runtime
    # added in v0.2.0 PR #61). Safe no-op outside FITB.
    try:
        from api.providers import _reload_provider_runtime
        _reload_provider_runtime()
    except Exception:
        pass

    return {
        "ok": True,
        "active_model": name,
        "base_url": base_url,
        "provider": "custom",
    }


# ── Route handlers ─────────────────────────────────────────────────────────


def handle_get_status(handler) -> dict[str, Any]:
    """GET /api/ollama/status — fast detection probe."""
    return get_status()


def handle_get_models(handler) -> dict[str, Any]:
    """GET /api/ollama/models — installed models on the detected daemon."""
    return get_models()


def handle_post_use_model(handler, body: dict) -> dict[str, Any]:
    """POST /api/ollama/use-model {"model": "<name>"} — activate a model."""
    return use_model(body.get("model", ""))


def handle_post_refresh(handler) -> dict[str, Any]:
    """POST /api/ollama/refresh — drop the detection cache and re-probe."""
    clear_cache()
    return get_status(force_refresh=True)
