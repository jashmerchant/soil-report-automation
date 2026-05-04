"""Temporary debug script - dumps visible nav elements after AOI import."""
import asyncio, shutil, tempfile
from pathlib import Path
from playwright.async_api import async_playwright
from wss_automation import import_aoi, build_zip, find_properties

WSS_URL = "https://websoilsurvey.nrcs.usda.gov/app/WebSoilSurvey.aspx"

DUMP_JS = """() => {
    const out = [];
    for (const tag of ['li', 'a', 'span', 'div']) {
        for (const el of document.querySelectorAll(tag)) {
            const raw = el.innerText || el.textContent || '';
            const t = raw.trim().replace(/\\s+/g, ' ').substring(0, 80);
            const s = window.getComputedStyle(el);
            const vis = s.display !== 'none'
                     && s.visibility !== 'hidden'
                     && el.offsetParent !== null;
            if (vis && t.length > 2 && t.length < 60) {
                out.push(tag + '|' + (el.id||'') + '|' + el.className.substring(0,50) + '|' + t);
            }
        }
    }
    return out.slice(0, 80);
}"""

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()

        print("Loading WSS…")
        await page.goto(WSS_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_load_state("networkidle", timeout=120000)

        files = find_properties(Path("RRG-2"))["RRG-2"]
        tmp = Path(tempfile.mkdtemp())
        zip_path = build_zip(files, "RRG-2", tmp)

        print("Importing AOI…")
        await import_aoi(page, zip_path)
        print("AOI done — dumping visible elements:\n")

        rows = await page.evaluate(DUMP_JS)
        for r in rows:
            print(" ", r)

        shutil.rmtree(tmp, ignore_errors=True)
        await browser.close()

asyncio.run(main())
