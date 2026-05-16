"""Regression tests for PWA support (manifest + service worker).

Covers:
- manifest.json is valid JSON with required PWA fields
- sw.js has the `__CACHE_VERSION__` placeholder the server replaces at request time
- sw.js offline-fallback uses a resolved promise (not `caches.match() || fallback`
  which is broken — Promise objects are always truthy in `||` checks, so the
  fallback Response would never be used)
- /manifest.json, /manifest.webmanifest, /sw.js routes serve correct Content-Type
- index.html + ui.js reference Fox overlay assets via the relative `extensions/`
  URL prefix (post-Phase-2 of the v0.6.0 upstream-separation migration)
"""
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "static" / "manifest.json"
SW = ROOT / "static" / "sw.js"
INDEX = ROOT / "static" / "index.html"
ROUTES = ROOT / "api" / "routes.py"
UI_JS = ROOT / "static" / "ui.js"


class TestManifest:
    def test_manifest_is_valid_json(self):
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_manifest_has_required_pwa_fields(self):
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        for field in ("name", "start_url", "display", "icons"):
            assert field in data, f"manifest.json missing required field: {field}"
        assert data["display"] == "standalone", (
            "manifest.display must be 'standalone' for installable PWA"
        )
        assert isinstance(data["icons"], list) and len(data["icons"]) > 0, (
            "manifest.icons must be a non-empty list"
        )

    def test_manifest_icons_reference_existing_files(self):
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        for icon in data["icons"]:
            src = icon.get("src", "")
            if src.startswith("http"):
                continue  # external icon, skip
            # Paths are relative to the app root (where manifest is served)
            # 'static/favicon.svg' or './static/favicon.svg' both valid
            clean = src.lstrip("./")
            p = ROOT / clean
            assert p.exists(), f"manifest.json references missing icon: {src}"


class TestServiceWorker:
    def test_sw_has_cache_version_placeholder(self):
        src = SW.read_text(encoding="utf-8")
        assert "__CACHE_VERSION__" in src, (
            "sw.js must contain __CACHE_VERSION__ placeholder for the server "
            "handler at /sw.js to replace with WEBUI_VERSION at request time"
        )

    def test_sw_bypasses_api_and_stream(self):
        src = SW.read_text(encoding="utf-8")
        assert "/api/" in src, "SW must bypass /api/* (no cached auth/session responses)"
        assert "/stream" in src, "SW must bypass streaming endpoints"

    def test_sw_offline_fallback_awaits_caches_match(self):
        """caches.match() returns a Promise (always truthy in `||`), so the pattern
        `caches.match('./') || new Response(...)` is broken — the fallback Response
        is dead code and the browser falls back to its default offline page.

        The correct pattern chains the match through .then() or awaits it so the
        resolved value is what gets the `||` fallback.
        """
        src = SW.read_text(encoding="utf-8")
        # Must not use the broken shape
        broken_pattern = re.compile(
            r"caches\.match\([^)]*\)\s*\|\|\s*new\s+Response",
            re.DOTALL,
        )
        assert not broken_pattern.search(src), (
            "sw.js offline fallback uses `caches.match('./') || new Response(...)` "
            "which is dead code — caches.match() returns a Promise that's always "
            "truthy. Use `.then((cached) => cached || new Response(...))` instead."
        )
        # Positive assertion that SOME form of the working pattern is present
        has_then = ".then(" in src and "cached" in src
        has_await = "await caches.match" in src
        assert has_then or has_await, (
            "sw.js must await/then the caches.match() result before applying the fallback"
        )

    def test_sw_never_caches_api_responses(self):
        """Defensive: the SW must not cache responses from /api/* paths.
        Currently enforced by early-return before the shell-asset cache block."""
        src = SW.read_text(encoding="utf-8")
        # Look for the early-return pattern in the fetch handler
        assert "return;" in src and "/api/" in src, (
            "SW fetch handler must early-return for /api/* paths (no caching)"
        )


class TestPWARoutes:
    def test_manifest_route_serves_correct_content_type(self):
        src = ROUTES.read_text(encoding="utf-8")
        # The handler block for /manifest.json
        idx = src.find('"/manifest.json"')
        assert idx != -1, "routes.py must handle /manifest.json"
        block = src[idx:idx + 800]
        assert "application/manifest+json" in block, (
            "manifest.json route must serve Content-Type: application/manifest+json"
        )
        assert "no-store" in block or "Cache-Control" in block, (
            "manifest.json should have Cache-Control: no-store so updates are picked up"
        )

    def test_sw_route_injects_cache_version(self):
        src = ROUTES.read_text(encoding="utf-8")
        idx = src.find('"/sw.js"')
        assert idx != -1, "routes.py must handle /sw.js"
        block = src[idx:idx + 1000]
        assert "__CACHE_VERSION__" in block, (
            "sw.js route must replace __CACHE_VERSION__ with the current WEBUI_VERSION"
        )
        assert "WEBUI_VERSION" in block, (
            "sw.js route must import and use WEBUI_VERSION for cache busting"
        )

    def test_sw_route_url_encodes_cache_version(self):
        src = ROUTES.read_text(encoding="utf-8")
        idx = src.find('"/sw.js"')
        assert idx != -1, "routes.py must handle /sw.js"
        block = src[idx:idx + 1200]
        assert "quote(WEBUI_VERSION, safe=\"\")" in block, (
            "sw.js route must URL-encode the injected cache version so unusual git tags "
            "cannot break the JavaScript string literal"
        )

    def test_sw_route_sets_service_worker_allowed(self):
        src = ROUTES.read_text(encoding="utf-8")
        idx = src.find('"/sw.js"')
        block = src[idx:idx + 1000]
        assert "Service-Worker-Allowed" in block, (
            "sw.js route must set Service-Worker-Allowed header so the SW can control "
            "the expected scope"
        )


class TestIndexHtmlIntegration:
    def test_index_links_manifest(self):
        src = INDEX.read_text(encoding="utf-8")
        assert 'rel="manifest"' in src, "index.html must link to manifest.json"

    def test_index_registers_service_worker(self):
        src = INDEX.read_text(encoding="utf-8")
        assert "serviceWorker" in src and "register" in src, (
            "index.html must register the service worker"
        )

    def test_index_uses_version_placeholders_for_static_assets(self):
        src = INDEX.read_text(encoding="utf-8")
        assert "sw.js?v=__WEBUI_VERSION__" in src
        assert "static/ui.js?v=__WEBUI_VERSION__" in src

    def test_index_versions_stylesheet(self):
        """Regression for #1507: the `<link rel=stylesheet>` for style.css MUST
        carry the same `?v=__WEBUI_VERSION__` cache-bust query as the JS files.

        Without the version, a stale service worker controlling a tab across a
        version upgrade would intercept `static/style.css`, find an exact URL
        match in its old shell cache, and return OLD CSS — while the new JS
        URLs (which DO carry `?v=`) miss the cache and load fresh. The mismatch
        breaks the layout until a force-refresh bypasses the SW.
        """
        src = INDEX.read_text(encoding="utf-8")
        assert "static/style.css?v=__WEBUI_VERSION__" in src, (
            "static/style.css must carry ?v=__WEBUI_VERSION__ so an old service "
            "worker controlling the tab across a version upgrade does not return "
            "stale CSS — see #1507"
        )
        # And the unversioned form must NOT appear (defensive — catches accidental
        # reverts that leave both lines).
        assert 'href="static/style.css"' not in src, (
            "unversioned static/style.css link found — must include "
            "?v=__WEBUI_VERSION__ for cache busting"
        )

    def test_sw_shell_assets_match_versioned_asset_urls(self):
        """The service worker's SHELL_ASSETS pre-cache list must use the same
        `?v=__CACHE_VERSION__` suffix on JS+CSS that index.html sends, so that
        the pre-cached entries actually serve when the page requests them.

        Without this, every `cache.match()` for a versioned asset URL (e.g.
        `static/style.css?v=vN`) would miss against the unversioned pre-cached
        entry (`static/style.css`), defeating the pre-cache.
        """
        src = SW.read_text(encoding="utf-8")
        # Versioned shell assets must include the cache version query.
        for asset in (
            "style.css",
            "boot.js",
            "ui.js",
            "messages.js",
            "sessions.js",
            "panels.js",
            "commands.js",
            "icons.js",
            "i18n.js",
            "workspace.js",
            "terminal.js",
        ):
            # Either inline `?v=__CACHE_VERSION__` or via the VQ constant
            # produces a URL string the cache lookup can match.
            has_inline = f"{asset}?v=__CACHE_VERSION__" in src
            has_concat = f"{asset}' + VQ" in src or f"{asset}\" + VQ" in src
            assert has_inline or has_concat, (
                f"sw.js SHELL_ASSETS entry for {asset} must carry "
                "?v=__CACHE_VERSION__ to match the URL the page requests"
            )

    def test_index_route_url_encodes_asset_version(self):
        src = ROUTES.read_text(encoding="utf-8")
        idx = src.find('parsed.path in ("/", "/index.html")')
        if idx == -1:
            idx = src.find('parsed.path.startswith("/session/")')
        assert idx != -1, "routes.py must handle /, /index.html, and /session/<id>"
        block = src[idx:idx + 800]
        assert "quote(WEBUI_VERSION, safe=\"\")" in block, (
            "index route must URL-encode the cache-busting version token before "
            "injecting it into script src attributes and service worker registration"
        )

    def test_index_sw_registration_uses_relative_path(self):
        """Regression: service worker registration MUST stay relative (no leading slash).

        index.html sets a dynamic <base href> via script at the top of <head>.
        All static asset paths must be relative so that installs behind a reverse
        proxy at a subpath (e.g. /hermes/) resolve correctly.

        An absolute '/sw.js' breaks subpath mounts because the browser requests
        <origin>/sw.js — outside the proxy mount root.  A relative 'sw.js'
        resolves to <origin><base>/sw.js, which is correct for both root and
        subpath installs.  See issue #1481 review feedback.
        """
        src = INDEX.read_text(encoding="utf-8")
        # Must contain the relative form
        assert "'sw.js?v=" in src, (
            "serviceWorker.register() must use relative 'sw.js' path, "
            "not absolute '/sw.js' — subpath mounts depend on <base href> resolution"
        )
        # Must NOT contain the absolute form
        assert "'/sw.js?v=" not in src, (
            "serviceWorker.register() must NOT use absolute '/sw.js' path — "
            "this breaks installs behind a reverse proxy at a subpath"
        )

    def test_index_has_ios_pwa_meta_tags(self):
        src = INDEX.read_text(encoding="utf-8")
        assert "apple-mobile-web-app-capable" in src, (
            "index.html should include Apple PWA meta tags for iOS home-screen support"
        )


class TestFoxOverlayExtensionRefs:
    """Phase 2 of v0.6.0 migration moved Fox-only assets out of static/ to be
    served via upstream's HERMES_WEBUI_EXTENSION_DIR mechanism at /extensions/.

    These tests catch accidental reverts that would re-introduce `static/...`
    references to assets that no longer live in this fork's static/ tree.

    URL form MUST stay relative (`extensions/...`, no leading slash) per the
    line-16 comment in static/index.html about subpath-mount support.
    """

    _MOVED_ASSETS = (
        "static/fox-in-the-box.css",
        "static/fox-in-the-box.js",
        "static/fox_avatar_cropped.jpg",
        "static/onboarding-preview.js",
        "static/fallback-polish.js",
        "static/hostname-prompt.js",
        "static/fonts/Manrope[wght].woff2",
        "static/fonts/Sora[wght].woff2",
    )

    def test_index_avatar_uses_extensions_url(self):
        src = INDEX.read_text(encoding="utf-8")
        assert "extensions/images/fox_avatar_cropped.jpg" in src, (
            "index.html must reference fox_avatar via the relative "
            "`extensions/images/fox_avatar_cropped.jpg` URL — overlay-served"
        )

    def test_ui_js_assistant_avatar_uses_extensions_url(self):
        src = UI_JS.read_text(encoding="utf-8")
        assert "extensions/images/fox_avatar_cropped.jpg" in src, (
            "ui.js _assistantRoleHtml must reference the avatar via "
            "`extensions/images/fox_avatar_cropped.jpg` (relative path)"
        )

    def test_no_leftover_static_references_in_index(self):
        src = INDEX.read_text(encoding="utf-8")
        for path in self._MOVED_ASSETS:
            assert path not in src, (
                f"index.html must not reference moved asset {path!r} — "
                "use the overlay-served `extensions/...` URL instead"
            )

    def test_no_leftover_static_references_in_ui_js(self):
        src = UI_JS.read_text(encoding="utf-8")
        for path in self._MOVED_ASSETS:
            assert path not in src, (
                f"ui.js must not reference moved asset {path!r}"
            )

    def test_extension_paths_are_relative(self):
        """Subpath-mount safety: `extensions/...` references must not start
        with a leading slash. See static/index.html line 16."""
        for source_file in (INDEX, UI_JS):
            src = source_file.read_text(encoding="utf-8")
            # Look for absolute `/extensions/` in src/href attributes — the
            # extensions.py-injected tags in the Dockerfile use absolute URLs
            # (different code path), but in-HTML img/link refs must be relative.
            for absolute in ('src="/extensions/', "src='/extensions/", 'href="/extensions/', "href='/extensions/"):
                assert absolute not in src, (
                    f"{source_file.name} contains absolute `{absolute}...` ref — "
                    "Fox overlay refs must stay relative for subpath-mount support"
                )
