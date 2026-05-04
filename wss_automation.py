#!/usr/bin/env python3
"""
USDA Web Soil Survey – Automated PDF Report Generator
======================================================
Generates 3 PDFs per property shapefile:
  {Stand}_SoilSurvey.pdf
  {Stand}_forestprod.pdf
  {Stand}_ErosionHazard_Off-Road_Off-Trail.pdf

Setup (one-time):
  pip install -r requirements.txt
  playwright install chromium

Usage:
  python wss_automation.py <input_dir> [output_dir]

  input_dir  – folder containing *_boundary.shp/shx/prj/dbf sets
  output_dir – destination for per-property sub-folders  (default: ./output)

Flags:
  --headless   Run browser invisibly (no window); omit to watch it run
  --workers N  Number of properties to process in parallel (default: 1)

Examples:
  python wss_automation.py 1063Test
  python wss_automation.py 1063Test output --headless
"""

import argparse
import asyncio
import re
import shutil
import sys
import tempfile
import traceback
import urllib.request
import zipfile
from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Page
from playwright.async_api import TimeoutError as PWTimeout
from playwright.async_api import async_playwright

# ── Tuneable timeouts (all in milliseconds) ────────────────────────────────────
NAV_TIMEOUT    = 60_000    # regular navigation / click waits
MAP_TIMEOUT    = 120_000   # map tile / report render waits
SETTLE_MS      = 1_500     # short pause after each UI action

WSS_URL = "https://websoilsurvey.nrcs.usda.gov/app/WebSoilSurvey.aspx"


# ══════════════════════════════════════════════════════════════════════════════
# Shapefile helpers
# ══════════════════════════════════════════════════════════════════════════════

def find_properties(input_dir: Path) -> dict[str, dict[str, Path]]:
    """
    Scan *input_dir* for shapefile groups named *_boundary.{ext}.
    Returns {stand_name: {ext_without_dot: Path}}.
    Only groups that have at minimum .shp + .shx + .prj are included.
    """
    props: dict[str, dict[str, Path]] = {}
    for shp in sorted(input_dir.glob("*_boundary.shp")):
        stand = shp.stem[: -len("_boundary")]   # e.g. "NKL-4"
        files: dict[str, Path] = {}
        for ext in (".shp", ".shx", ".prj", ".dbf", ".cpg"):
            p = shp.with_suffix(ext)
            if p.exists():
                files[ext.lstrip(".")] = p
        if {"shp", "shx", "prj"}.issubset(files):
            props[stand] = files
    return props


def build_zip(files: dict[str, Path], stand: str, dest: Path) -> Path:
    """
    Package all shapefile components into a flat ZIP so they can be uploaded
    via WSS's 'Create AOI from Zipped Shapefile' form.
    """
    zip_path = dest / f"{stand}_boundary.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files.values():
            zf.write(path, path.name)   # flat – no sub-directory inside zip
    return zip_path


# ══════════════════════════════════════════════════════════════════════════════
# Low-level WSS page helpers
# ══════════════════════════════════════════════════════════════════════════════

async def dismiss_warning(page: Page) -> None:
    """
    Dismiss the WSS 'Non-fatal errors' warning dialog if it appears.
    The dialog blocks all subsequent clicks; pressing Escape closes it.
    """
    try:
        backdrop = page.locator("#Warning_00000_backdrop")
        if await backdrop.is_visible(timeout=3_000):
            await page.keyboard.press("Escape")
            await backdrop.wait_for(state="hidden", timeout=10_000)
    except Exception:
        pass   # dialog may not always appear


async def wait_map_ready(page: Page) -> None:
    """Block until the 'Map Loading…' spinner disappears (or times out)."""
    try:
        await page.wait_for_selector(
            "text=Map Loading...", state="hidden", timeout=MAP_TIMEOUT
        )
    except PWTimeout:
        pass    # spinner may not always render for every load


async def save_wss_pdf(page: Page, output_path: Path) -> None:
    """
    Open the WSS 'Printable Version' options panel, click 'View', and
    download the server-generated PDF.

    WSS flow:
      1. Click 'Printable Version' unfold button → opens options panel
      2. Click 'View' → WSS sends a request to GetScript.dynamic?command=
         createprintabledocument, which returns JS containing:
             OpenExternalWindow('<pdf_url>', ...)
      3. We listen on the response event for that request, capture the body
         once (no re-fetch), parse the PDF URL with a regex, and download
         it via urllib (avoids cross-origin CORS restrictions).
      4. Dismiss the non-fatal warning dialog that WSS shows afterward.
      5. Close any PDF tab that WSS may have opened via OpenExternalWindow.
    """
    # 1. Open the Printable Version options panel
    await page.wait_for_selector(
        "#controlbarprintbibid_unfold", state="visible", timeout=NAV_TIMEOUT
    )
    await page.click("#controlbarprintbibid_unfold", timeout=NAV_TIMEOUT)
    await page.wait_for_timeout(SETTLE_MS)

    # 2. Register a one-shot response listener BEFORE clicking View so we
    #    capture the body the moment the server sends it back.
    pdf_url_future: asyncio.Future[str] = asyncio.get_event_loop().create_future()

    async def _on_response(response):
        if "createprintabledocument" not in response.url:
            return
        try:
            text = await response.text()
            m = re.search(r"OpenExternalWindow\('(https://[^']+\.pdf)'", text)
            if m and not pdf_url_future.done():
                pdf_url_future.set_result(m.group(1))
            elif not pdf_url_future.done():
                pdf_url_future.set_exception(
                    RuntimeError(
                        f"PDF URL not found in response. First 400: {text[:400]}"
                    )
                )
        except Exception as exc:
            if not pdf_url_future.done():
                pdf_url_future.set_exception(exc)

    # Debug: log every response URL for 90 s to see what fires after clicking View
    async def _debug_all(response):
        print(f"    [NET] {response.status} {response.url[:120]}")
    page.on("response", _debug_all)

    page.on("response", _on_response)

    # 3. Click View to trigger PDF generation
    await page.wait_for_selector(
        "#controlbarprintbibid_submit_button", state="visible", timeout=NAV_TIMEOUT
    )
    print("    [DBG] clicking submit button…")
    await page.click("#controlbarprintbibid_submit_button", timeout=NAV_TIMEOUT)
    print("    [DBG] submit clicked, waiting for response…")

    # 4. Wait for the response listener to resolve the PDF URL (max 90 s)
    try:
        pdf_url = await asyncio.wait_for(
            asyncio.shield(pdf_url_future), timeout=90
        )
    finally:
        page.remove_listener("response", _on_response)
        page.remove_listener("response", _debug_all)

    # 5. Download the PDF via Python urllib (bypasses CORS –
    #    PDF is served from websoilsurvey.sc.egov.usda.gov).
    with urllib.request.urlopen(pdf_url, timeout=120) as resp:
        output_path.write_bytes(resp.read())

    # 6. Dismiss the non-fatal warning WSS shows after PDF generation.
    #    Also close any PDF tab that OpenExternalWindow may have opened.
    await dismiss_warning(page)
    for extra_page in page.context.pages[1:]:   # keep only the main WSS page
        await extra_page.close()


# ══════════════════════════════════════════════════════════════════════════════
# AOI import
# ══════════════════════════════════════════════════════════════════════════════

# WSS main-tab div IDs (the tabs rendered by the outer tabbed panel)
_MAIN_TAB_IDS: dict[str, str] = {
    "Soil Map":               "Soil_Map",
    "Soil Data Explorer":     "Soil_Data_Explorer",
    "Download Soils Data":    "Download_Soils_Data",
    "Shopping Cart":          "Shopping_Cart",
}


async def click_by_text(page: Page, text: str) -> None:
    """
    Click a WSS navigation element by its visible label.

    Main tabs: resolved by known div ID (the innerText includes a hidden
    'Open Tab' sr-only span so exact text match won't work).
    Sub-tabs and buttons: found by scanning visible elements whose text
    STARTS WITH the requested label (ignoring sr-only children).
    """
    # Fast path – known main tabs with stable IDs
    if text in _MAIN_TAB_IDS:
        await page.click(f"#{_MAIN_TAB_IDS[text]}", timeout=NAV_TIMEOUT)
        return

    # General path – find a visible element whose *first text node* or
    # trimmed innerText starts with the requested text
    clicked = await page.evaluate("""(text) => {
        const lc = text.toLowerCase();
        const tags = ['a', 'span', 'div', 'button', 'input', 'td'];
        for (const tag of tags) {
            for (const el of document.querySelectorAll(tag)) {
                // Use the first text node to avoid sr-only child interference
                let label = '';
                for (const node of el.childNodes) {
                    if (node.nodeType === Node.TEXT_NODE) {
                        label = node.textContent.trim();
                        if (label) break;
                    }
                }
                if (!label) label = (el.innerText || el.textContent || '').trim();
                if (!label.toLowerCase().startsWith(lc)) continue;
                const s = window.getComputedStyle(el);
                if (s.display === 'none' || s.visibility === 'hidden') continue;
                if (!el.offsetParent) continue;
                el.click();
                return true;
            }
        }
        return false;
    }""", text)
    if not clicked:
        raise RuntimeError(f"Could not find visible element with text: '{text}'")


async def click_accordion(page: Page, label: str) -> None:
    """
    Click a WSS left-panel accordion header by visible label text.
    WSS wraps each header in a <div class="header [closed]"> that contains
    a hidden <span class="sr-only"> — we must target the div, not the span,
    otherwise Playwright resolves to the invisible element and times out.
    """
    hdr = page.locator("div.header", has_text=label).first
    # Only click if the section is currently closed
    cls = await hdr.get_attribute("class") or ""
    if "closed" in cls:
        await hdr.click(timeout=NAV_TIMEOUT)
        await page.wait_for_timeout(SETTLE_MS)


async def import_aoi(page: Page, zip_path: Path) -> None:
    """
    Use 'Create AOI from Zipped Shapefile' to load the property boundary.

    WSS left-panel flow:
      [Import AOI] accordion  →  [Create AOI from Zipped Shapefile] accordion
      → single <input type="file"> for the .zip  →  [Set AOI] button
      → success confirmed by 'AOI Properties' panel appearing.
    """
    # 1. Expand parent 'Import AOI' accordion if collapsed
    await click_accordion(page, "Import AOI")

    # 2. Expand the 'Create AOI from Zipped Shapefile' sub-accordion
    await click_accordion(page, "Create AOI from Zipped Shapefile")

    # 3. Attach the zip to the now-visible file input.
    #    WSS renders a single <input type="file"> inside this sub-panel.
    file_input = page.locator("input[type='file']").last
    await file_input.set_input_files(str(zip_path))
    await page.wait_for_timeout(SETTLE_MS)

    # 4. Click the *visible* Set AOI button.
    #    Each AOI import section has its own Set AOI button; only the one inside
    #    the currently-expanded section is visible.  We use JS to find and click
    #    the first visible candidate so we don't accidentally hit a hidden one.
    clicked = await page.evaluate("""() => {
        const candidates = Array.from(
            document.querySelectorAll('input, button, a')
        );
        const btn = candidates.find(el => {
            const text = (el.value || el.textContent || '').trim().toLowerCase();
            if (text !== 'set aoi') return false;
            const style = window.getComputedStyle(el);
            return style.display !== 'none'
                && style.visibility !== 'hidden'
                && el.offsetParent !== null;
        });
        if (btn) { btn.click(); return true; }
        return false;
    }""")
    if not clicked:
        raise RuntimeError("Could not find a visible 'Set AOI' button on the page")

    await page.wait_for_selector("text=AOI Properties", timeout=MAP_TIMEOUT)
    await page.wait_for_timeout(2_000)   # allow map tiles to settle


# ══════════════════════════════════════════════════════════════════════════════
# Three PDF generators
# ══════════════════════════════════════════════════════════════════════════════

async def generate_soil_survey(page: Page, stand: str, out_dir: Path) -> None:
    """
    Soil Map tab  →  wait for map  →  Printable Version  →  PDF.
    """
    await click_by_text(page, "Soil Map")
    await wait_map_ready(page)
    await page.wait_for_timeout(3_000)   # extra settle for control bar to render

    dest = out_dir / f"{stand}_SoilSurvey.pdf"
    await save_wss_pdf(page, dest)
    print(f"    ✓  {dest.name}")


async def generate_forestprod(page: Page, stand: str, out_dir: Path) -> None:
    """
    Soil Data Explorer  →  Soil Reports tab  →  Vegetative Productivity accordion
    →  Forestland Productivity  →  View Soil Report  →  Printable Version  →  PDF.
    """
    await click_by_text(page, "Soil Data Explorer")
    await page.wait_for_timeout(SETTLE_MS)

    await click_by_text(page, "Soil Reports")
    await page.wait_for_timeout(SETTLE_MS)

    await click_accordion(page, "Vegetative Productivity")

    # Radio/link for the specific report
    await page.click("text=Forestland Productivity", timeout=NAV_TIMEOUT)
    await page.wait_for_timeout(SETTLE_MS)

    await page.click("text=View Soil Report", timeout=NAV_TIMEOUT)
    await page.wait_for_load_state("networkidle", timeout=MAP_TIMEOUT)
    await page.wait_for_timeout(SETTLE_MS)

    dest = out_dir / f"{stand}_forestprod.pdf"
    await save_wss_pdf(page, dest)
    print(f"    ✓  {dest.name}")


async def generate_erosion_hazard(page: Page, stand: str, out_dir: Path) -> None:
    """
    Soil Data Explorer  →  Suitabilities and Limitations for Use tab
    →  Land Management accordion  →  Erosion Hazard (Off-Road, Off-Trail)
    →  View Rating  →  Printable Version  →  PDF.
    """
    await click_by_text(page, "Soil Data Explorer")
    await page.wait_for_timeout(SETTLE_MS)

    await click_by_text(page, "Suitabilities and Limitations for Use")
    await page.wait_for_timeout(SETTLE_MS)

    await click_accordion(page, "Land Management")

    await page.click("text=Erosion Hazard (Off-Road, Off-Trail)", timeout=NAV_TIMEOUT)
    await page.wait_for_timeout(SETTLE_MS)

    await page.click("text=View Rating", timeout=NAV_TIMEOUT)
    await wait_map_ready(page)
    await page.wait_for_timeout(2_000)

    dest = out_dir / f"{stand}_ErosionHazard_Off-Road_Off-Trail.pdf"
    await save_wss_pdf(page, dest)
    print(f"    ✓  {dest.name}")


# ══════════════════════════════════════════════════════════════════════════════
# Per-property orchestrator
# ══════════════════════════════════════════════════════════════════════════════

async def process_property(
    browser: Browser,
    stand: str,
    files: dict[str, Path],
    output_root: Path,
    semaphore: asyncio.Semaphore,
) -> None:
    """Open a fresh browser context, generate all 3 PDFs, then clean up."""
    async with semaphore:
        out_dir = output_root / stand
        out_dir.mkdir(parents=True, exist_ok=True)

        tmp = Path(tempfile.mkdtemp())
        context: BrowserContext | None = None
        try:
            zip_path = build_zip(files, stand, tmp)

            context = await browser.new_context(
                viewport={"width": 1440, "height": 900},
                accept_downloads=True,
            )
            page = await context.new_page()

            print(f"\n[{stand}]  Loading WSS …")
            await page.goto(WSS_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            await page.wait_for_load_state("networkidle", timeout=MAP_TIMEOUT)

            print(f"[{stand}]  Importing AOI …")
            await import_aoi(page, zip_path)

            print(f"[{stand}]  Generating PDFs …")
            await generate_soil_survey(page, stand, out_dir)
            await generate_forestprod(page, stand, out_dir)
            await generate_erosion_hazard(page, stand, out_dir)

            print(f"[{stand}]  ✓  All 3 PDFs saved → {out_dir}")

        except Exception as exc:
            print(f"[{stand}]  ✗  FAILED: {exc}")
            traceback.print_exc()
        finally:
            if context:
                await context.close()
            shutil.rmtree(tmp, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

async def main(input_dir: Path, output_root: Path, headless: bool, workers: int) -> None:
    properties = find_properties(input_dir)
    if not properties:
        print(f"ERROR: No *_boundary.shp files found in {input_dir}")
        sys.exit(1)

    print(f"Found {len(properties)} properties: {', '.join(properties)}")
    print(f"Output root : {output_root}")
    print(f"Headless    : {headless}   Workers: {workers}\n")
    output_root.mkdir(parents=True, exist_ok=True)

    semaphore = asyncio.Semaphore(workers)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        tasks = [
            process_property(browser, stand, files, output_root, semaphore)
            for stand, files in properties.items()
        ]
        await asyncio.gather(*tasks)
        await browser.close()

    print("\nDone – all properties processed.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Automate USDA Web Soil Survey PDF report generation."
    )
    parser.add_argument(
        "input_dir",
        nargs="?",
        default="1063Test",
        help="Folder containing *_boundary shapefile sets (default: 1063Test)",
    )
    parser.add_argument(
        "output_dir",
        nargs="?",
        default="output",
        help="Destination folder for per-property PDF sub-folders (default: output)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser without a visible window",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        metavar="N",
        help="Parallel browser contexts (default: 1; increase cautiously)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(
        main(
            input_dir=Path(args.input_dir),
            output_root=Path(args.output_dir),
            headless=args.headless,
            workers=args.workers,
        )
    )
