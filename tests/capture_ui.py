#!/usr/bin/env python3
"""tests/capture_ui.py - Playwright capture for the React SPA.

Goal: capture whatever state the live UI at the frontend Cloud Run
service is in. The user wants to know whether the new SSE/CDC chain
is actually populating the SPA live.

The script does NOT pre-claim success. It just:

  1. Opens the frontend URL (default:
     https://frontend-472857763269.asia-southeast1.run.app/).
  2. Waits for the "Recent verdicts" panel to render.
  3. Waits up to 60s for either:
       (a) at least one <tr> row appears in the table, OR
       (b) the live dot becomes "live" (header .live-dot.live).
  4. Captures a screenshot -- either the panel screenshot (case a)
     or a full-page screenshot (case b / timeout).
  5. Saves to /tmp/label-hold-capture-<timestamp>.png.
  6. Prints to stdout: which case fired (a/b/timeout), the screenshot
     path, and a raw HTML snippet around .live-dot and the verdicts
     panel so the user can see what the SPA actually rendered.

Fallbacks:
  - If playwright is not importable: tries
    `python3 -m pip install --user playwright` then
    `python3 -m playwright install chromium` (no `--with-deps`, which
    requires sudo on this image).
  - If the install fails, exits 1 with a clear message.

Usage:
  python3 tests/capture_ui.py
  FRONTEND_URL=https://frontend-...-as.a.run.app \\
    python3 tests/capture_ui.py
"""
from __future__ import annotations

import datetime as dt
import importlib
import os
import subprocess
import sys
import textwrap
from pathlib import Path

# Hardcoded in the original brief; the task confirmed this URL.
FRONTEND_URL = os.environ.get(
    "FRONTEND_URL", "https://frontend-472857763269.asia-southeast1.run.app/"
)
WAIT_S = int(os.environ.get("CAPTURE_WAIT_S", "60"))
SCREENSHOT_DIR = os.environ.get("CAPTURE_DIR", "/tmp")


def _ensure_playwright():
    """Import playwright. If missing, install it (chromium binary
    only; no `--with-deps` because that needs sudo). Returns the
    playwright module so the caller can construct a sync browser."""
    try:
        return importlib.import_module("playwright.sync_api")
    except ModuleNotFoundError:
        pass

    print("playwright not installed; attempting to install...", file=sys.stderr)
    # Try user-site install (PEP 668 means we can't write the
    # system Python; --user is the safe default for this sandbox).
    rc = subprocess.call(
        [
            sys.executable, "-m", "pip", "install", "--user",
            "playwright", "--quiet",
        ]
    )
    if rc != 0:
        print(
            "  pip install playwright failed; cannot capture UI",
            file=sys.stderr,
        )
        sys.exit(2)

    # Now download the chromium binary. This is the slow part; the
    # binary is ~150MB so we don't stream to stdout.
    print("  downloading chromium binary (one-time, ~150MB)...", file=sys.stderr)
    rc = subprocess.call(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
    )
    if rc != 0:
        print(
            "  playwright install chromium failed; "
            "try `python3 -m playwright install chromium --with-deps` "
            "manually if you have sudo", file=sys.stderr,
        )
        sys.exit(2)

    # Re-import after install.
    return importlib.import_module("playwright.sync_api")


def _html_snippet(page, css_selector: str, around_px: int = 200) -> str:
    """Return the outerHTML of the matching element with up to
    around_px chars, trimmed. If the selector matches nothing,
    return a placeholder."""
    try:
        loc = page.locator(css_selector)
        if loc.count() == 0:
            return f"(no element matched {css_selector!r})"
        # Get the first match; trim to keep stdout readable.
        raw = loc.first.evaluate("el => el.outerHTML")
        if len(raw) > around_px * 4:
            return raw[: around_px * 4] + "...(truncated)"
        return raw
    except Exception as e:  # pragma: no cover
        return f"(error fetching {css_selector!r}: {e!r})"


def _verdicts_html_snippet(page) -> str:
    """Return a small HTML chunk around the #lots-table (Recent verdicts
    panel). Robust: works whether the table is empty (the empty row
    <td colspan=6>Loading…</td>) or populated."""
    try:
        loc = page.locator("#lots-table")
        if loc.count() == 0:
            return "(#lots-table not in DOM yet)"
        return loc.first.evaluate("el => el.outerHTML")[:3000]
    except Exception as e:  # pragma: no cover
        return f"(error: {e!r})"


def main() -> int:
    sync_api = _ensure_playwright()
    from playwright.sync_api import TimeoutError as PWTimeout  # type: ignore

    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = Path(SCREENSHOT_DIR) / f"label-hold-capture-{ts}.png"

    print(f"capture_ui: opening {FRONTEND_URL}")
    print(f"  wait budget: {WAIT_S}s, screenshot: {out_path}")

    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 900},
            ignore_https_errors=True,
        )
        page = ctx.new_page()

        fired_case = "timeout"  # default unless something else matches
        matched_write_id = None

        try:
            page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=30_000)
        except PWTimeout:
            print(f"  goto timed out within 30s; continuing with what's loaded")

        # 2. Wait for "Recent verdicts" panel.
        try:
            page.get_by_text("Recent verdicts", exact=False).first.wait_for(
                state="visible", timeout=15_000
            )
            print("  Recent verdicts panel: visible")
        except PWTimeout:
            print("  Recent verdicts panel: NOT visible (continuing)")

        # 3. Poll the two conditions until one fires or the budget runs out.
        deadline = dt.datetime.now() + dt.timedelta(seconds=WAIT_S)
        while dt.datetime.now() < deadline:
            # (a) any data row in the table body.
            try:
                # The SPA renders rows inside #lots-body tbody. Skip
                # the initial placeholder row (its only cell has the
                # `empty` class).
                rows = page.locator("#lots-body tr").all()
                for r in rows:
                    cls = (r.get_attribute("class") or "") + " " + (
                        (r.locator("td").first.get_attribute("class") or "")
                        if r.locator("td").count() else ""
                    )
                    if "empty" in cls.lower():
                        continue
                    # any non-empty row counts
                    fired_case = "a"
                    matched_write_id = True
                    break
            except Exception:
                pass

            if fired_case == "a":
                break

            # (b) header live dot became "live".
            try:
                dot = page.locator("header .live-dot")
                if dot.count() and "live" in (dot.first.get_attribute("class") or ""):
                    fired_case = "b"
                    break
            except Exception:
                pass

            page.wait_for_timeout(500)

        # 4+5. Capture screenshot. If case (a), scope to the panel;
        # otherwise full page.
        if fired_case == "a":
            target = page.locator("#lots-table")
            if target.count():
                target.first.screenshot(path=str(out_path))
            else:
                page.screenshot(path=str(out_path), full_page=True)
        else:
            page.screenshot(path=str(out_path), full_page=True)

        # 7. Print summary + raw HTML around .live-dot and the verdicts panel.
        live_dot_html = _html_snippet(page, "header .live-dot")
        live_status_html = _html_snippet(page, "#live-status")
        verdicts_html = _verdicts_html_snippet(page)

        print("\n=== RESULT ===")
        print(f"  fired_case : {fired_case}")
        print(f"  screenshot : {out_path}")
        print(f"  size_bytes : {out_path.stat().st_size if out_path.exists() else 0}")
        print(f"  url        : {page.url}")
        print("\n--- header .live-dot ---")
        print(textwrap.indent(live_dot_html, "  "))
        print("\n--- #live-status ---")
        print(textwrap.indent(live_status_html, "  "))
        print("\n--- #lots-table (Recent verdicts panel) ---")
        print(textwrap.indent(verdicts_html, "  "))

        browser.close()

    if fired_case == "timeout":
        print(
            "\nNote: case=timeout. This means the SPA rendered but the\n"
            "      SSE stream did not deliver a lot row AND the live\n"
            "      dot did not flip to 'live'. Check:\n"
            "        - dashboard BFF /api/stream reachable (curl -i .../api/stream)\n"
            "        - adk-runtime revision includes /__eventarc/publish\n"
            "        - Eventarc IAM propagation (90s post-deploy wait)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
