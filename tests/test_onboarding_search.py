"""Tests for Task 12 — Brave Search onboarding step.

AC1  GET /api/onboarding/search returns 200, brave_configured: False when no key set
AC2  POST /api/onboarding/search with key writes to hermes.env and hermes.yaml
AC3  POST with empty key returns 200 and does NOT write to env file
AC4  GET after save returns brave_configured: True + masked key
AC5  Env path is read at request time (monkeypatched via HERMES_ENV_PATH)
AC6  'search' step exists in onboarding.js steps array (static analysis)
AC7  Skip logic: empty key POST is a no-op on the env file
AC8  entrypoint.sh contains sed block for BRAVE_API_KEY interpolation
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_server(tmp_path, monkeypatch):
    """Build a minimal fake BaseHTTPRequestHandler-style harness."""
    monkeypatch.setenv("HERMES_ENV_PATH", str(tmp_path / "hermes.env"))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(tmp_path / "hermes.yaml"))

    # Write a minimal hermes.yaml so the yaml-patch code has something to work with
    (tmp_path / "hermes.yaml").write_text(
        "mcp_servers:\n  brave:\n    env:\n      BRAVE_API_KEY: '${BRAVE_API_KEY}'\n",
        encoding="utf-8",
    )
    return tmp_path


def _call_get_search(tmp_path, monkeypatch):
    """Call the get_onboarding_search function directly."""
    from api.onboarding import get_onboarding_search
    return get_onboarding_search()


def _call_post_search(key: str, tmp_path, monkeypatch):
    """Call apply_onboarding_search directly."""
    from api.onboarding import apply_onboarding_search
    return apply_onboarding_search({"brave_api_key": key})


# ---------------------------------------------------------------------------
# AC1 — GET returns 200 payload with brave_configured: False when no key set
# ---------------------------------------------------------------------------

def test_get_search_no_key(tmp_path, monkeypatch):
    """AC1: GET returns brave_configured False when HERMES_ENV_PATH has no key."""
    _make_server(tmp_path, monkeypatch)
    result = _call_get_search(tmp_path, monkeypatch)
    assert result["brave_configured"] is False
    assert "catalog" in result
    assert "brave" in result["catalog"]


# ---------------------------------------------------------------------------
# AC2 — POST with key writes to hermes.env and patches hermes.yaml
# ---------------------------------------------------------------------------

def test_post_search_writes_env(tmp_path, monkeypatch):
    """AC2: POST with key writes BRAVE_API_KEY to hermes.env."""
    _make_server(tmp_path, monkeypatch)
    result = _call_post_search("test-key-abc123", tmp_path, monkeypatch)
    assert result.get("ok") is True

    env_file = tmp_path / "hermes.env"
    assert env_file.exists(), "hermes.env was not created"
    content = env_file.read_text(encoding="utf-8")
    assert "BRAVE_API_KEY=test-key-abc123" in content


def test_post_search_patches_yaml(tmp_path, monkeypatch):
    """AC2: POST with key patches hermes.yaml mcp_servers.brave.env.BRAVE_API_KEY."""
    _make_server(tmp_path, monkeypatch)
    _call_post_search("test-key-abc123", tmp_path, monkeypatch)

    yaml_file = tmp_path / "hermes.yaml"
    content = yaml_file.read_text(encoding="utf-8")
    assert "test-key-abc123" in content
    assert "${BRAVE_API_KEY}" not in content


# ---------------------------------------------------------------------------
# AC3 — POST with empty key is a no-op
# ---------------------------------------------------------------------------

def test_post_empty_key_noop(tmp_path, monkeypatch):
    """AC3: POST with empty key returns ok and does not write to env file."""
    _make_server(tmp_path, monkeypatch)
    env_file = tmp_path / "hermes.env"
    assert not env_file.exists(), "env file should not exist before the call"

    result = _call_post_search("", tmp_path, monkeypatch)
    assert result.get("ok") is True
    assert not env_file.exists(), "env file must NOT be created for empty key"


def test_post_whitespace_key_noop(tmp_path, monkeypatch):
    """AC3: POST with whitespace-only key is treated as empty/skip."""
    _make_server(tmp_path, monkeypatch)
    env_file = tmp_path / "hermes.env"
    result = _call_post_search("   ", tmp_path, monkeypatch)
    assert result.get("ok") is True
    assert not env_file.exists()


# ---------------------------------------------------------------------------
# AC4 — GET after save returns brave_configured True + masked key
# ---------------------------------------------------------------------------

def test_get_search_after_save_shows_masked(tmp_path, monkeypatch):
    """AC4: After saving key, GET returns brave_configured True and masked key."""
    _make_server(tmp_path, monkeypatch)
    _call_post_search("BSABRmgRWf-L1RKBM2BHGjpqgEUl_pM", tmp_path, monkeypatch)

    result = _call_get_search(tmp_path, monkeypatch)
    assert result["brave_configured"] is True
    masked = result.get("brave_key_masked", "")
    assert masked != "", "brave_key_masked should be non-empty after save"
    # Should show first 6 chars + *** — must NOT expose full key
    assert "BSABRm" in masked
    assert "pM" not in masked  # last chars should be hidden


# ---------------------------------------------------------------------------
# AC5 — Env path read at request time (not module level)
# ---------------------------------------------------------------------------

def test_env_path_read_at_request_time(tmp_path, monkeypatch):
    """AC5: Setting HERMES_ENV_PATH after import still affects the result."""
    # First call with path A
    path_a = tmp_path / "a" / "hermes.env"
    path_a.parent.mkdir()
    monkeypatch.setenv("HERMES_ENV_PATH", str(path_a))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(tmp_path / "hermes.yaml"))
    (tmp_path / "hermes.yaml").write_text(
        "mcp_servers:\n  brave:\n    env:\n      BRAVE_API_KEY: '${BRAVE_API_KEY}'\n",
        encoding="utf-8",
    )
    from api.onboarding import apply_onboarding_search
    apply_onboarding_search({"brave_api_key": "key-for-a"})
    assert path_a.exists()
    assert "key-for-a" in path_a.read_text()

    # Now switch to path B — should write there, not to path A
    path_b = tmp_path / "b" / "hermes.env"
    path_b.parent.mkdir()
    monkeypatch.setenv("HERMES_ENV_PATH", str(path_b))
    (tmp_path / "hermes.yaml").write_text(
        "mcp_servers:\n  brave:\n    env:\n      BRAVE_API_KEY: '${BRAVE_API_KEY}'\n",
        encoding="utf-8",
    )
    apply_onboarding_search({"brave_api_key": "key-for-b"})
    assert path_b.exists()
    assert "key-for-b" in path_b.read_text()
    # path_a should still have old key
    assert "key-for-b" not in path_a.read_text()


# ---------------------------------------------------------------------------
# AC6 — 'search' step in onboarding.js (static analysis)
# ---------------------------------------------------------------------------

def test_onboarding_js_has_search_step():
    """AC6: onboarding.js steps array contains 'search' between 'setup' and 'workspace'."""
    js_path = Path(__file__).parent.parent / "static" / "onboarding.js"
    assert js_path.exists(), f"onboarding.js not found at {js_path}"
    content = js_path.read_text(encoding="utf-8")
    # Check 'search' is in the steps array
    assert "'search'" in content or '"search"' in content, \
        "'search' step not found in onboarding.js"
    # Check ordering: setup before search before workspace
    setup_pos = content.find("'setup'") if "'setup'" in content else content.find('"setup"')
    search_pos = content.find("'search'") if "'search'" in content else content.find('"search"')
    workspace_pos = content.find("'workspace'") if "'workspace'" in content else content.find('"workspace"')
    assert setup_pos < search_pos < workspace_pos, \
        "Steps order must be: setup → search → workspace"


# ---------------------------------------------------------------------------
# AC7 — Skip: empty key POST does not call write (already covered by AC3)
#           Verify explicitly that existing env file is not modified
# ---------------------------------------------------------------------------

def test_post_empty_key_does_not_overwrite_existing(tmp_path, monkeypatch):
    """AC7: Empty key POST does not overwrite an existing hermes.env."""
    _make_server(tmp_path, monkeypatch)
    env_file = tmp_path / "hermes.env"
    env_file.write_text("OPENROUTER_API_KEY=existing-key\n", encoding="utf-8")

    _call_post_search("", tmp_path, monkeypatch)

    content = env_file.read_text(encoding="utf-8")
    assert "OPENROUTER_API_KEY=existing-key" in content
    assert "BRAVE_API_KEY" not in content


# ---------------------------------------------------------------------------
# AC8 — entrypoint.sh contains sed interpolation block
# ---------------------------------------------------------------------------

def test_entrypoint_has_brave_sed_patch():
    """AC8: entrypoint.sh patches BRAVE_API_KEY into hermes.yaml via sed."""
    entrypoint = Path(__file__).parents[3] / "packages" / "integration" / "entrypoint.sh"
    assert entrypoint.exists(), f"entrypoint.sh not found at {entrypoint}"
    content = entrypoint.read_text(encoding="utf-8")
    assert "BRAVE_API_KEY" in content, "BRAVE_API_KEY not referenced in entrypoint.sh"
    assert "sed" in content, "sed command not found in entrypoint.sh"
    # Should contain a sed substitution for the placeholder
    assert re.search(r"sed.*BRAVE_API_KEY", content), \
        "No sed substitution for BRAVE_API_KEY found in entrypoint.sh"
