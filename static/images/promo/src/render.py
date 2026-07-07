#!/usr/bin/env python3
"""Render promo HTML files to PNG via preinstalled Chromium (Playwright).

Usage: python render.py <html> <out.png> [width] [height]
Renders at deviceScaleFactor=2 -> output is 2x the CSS size.
"""
import sys, pathlib
from playwright.sync_api import sync_playwright

CHROME = "/opt/pw-browsers/chromium"

def main():
    html = pathlib.Path(sys.argv[1]).resolve()
    out = pathlib.Path(sys.argv[2]).resolve()
    w = int(sys.argv[3]) if len(sys.argv) > 3 else 1200
    h = int(sys.argv[4]) if len(sys.argv) > 4 else 1500
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME,
                              args=["--ignore-certificate-errors", "--force-color-profile=srgb"])
        page = b.new_page(viewport={"width": w, "height": h}, device_scale_factor=2)
        page.goto(html.as_uri())
        page.wait_for_timeout(400)
        try:
            page.evaluate("document.fonts.ready")
            page.wait_for_function("document.fonts.status === 'loaded'", timeout=8000)
        except Exception as e:
            print("font wait:", e)
        page.wait_for_timeout(300)
        page.screenshot(path=str(out), clip={"x": 0, "y": 0, "width": w, "height": h})
        b.close()
    print("wrote", out)

if __name__ == "__main__":
    main()
