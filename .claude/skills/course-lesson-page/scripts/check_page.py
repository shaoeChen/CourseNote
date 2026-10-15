"""Check a generated lesson page: inline script syntax, external resources, screenshots.

Usage:
    python check_page.py <lesson.html> <output_dir> [--anchors s1,s5,quiz]

Writes desktop.png (1280px wide) and mobile.png (400px wide) to <output_dir>,
plus one desktop/mobile pair per anchor so long pages can be inspected section
by section. Exits non-zero when a check fails.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BROWSER_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]
BROWSER_COMMANDS = ["google-chrome", "chromium", "chromium-browser", "chrome", "msedge"]


def find_browser() -> str | None:
    for candidate in BROWSER_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    for command in BROWSER_COMMANDS:
        found = shutil.which(command)
        if found:
            return found
    return None


def check_script_syntax(html: str) -> list[str]:
    node = shutil.which("node")
    if not node:
        return ["node not found: inline script syntax was NOT checked"]
    problems: list[str] = []
    scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, flags=re.S)
    for index, source in enumerate(scripts, start=1):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
            handle.write(source)
            script_path = handle.name
        result = subprocess.run([node, "--check", script_path], capture_output=True, text=True)
        Path(script_path).unlink(missing_ok=True)
        if result.returncode != 0:
            problems.append(f"inline script #{index} has a syntax error:\n{result.stderr.strip()}")
    return problems


def check_external_resources(html: str) -> list[str]:
    """The page must work offline: no remote scripts, styles, fonts or images."""
    problems: list[str] = []
    patterns = {
        "remote <script src>": r"<script[^>]+src=[\"']https?://",
        "remote stylesheet": r"<link[^>]+href=[\"']https?://",
        "remote image": r"<img[^>]+src=[\"']https?://",
        "remote CSS url()/@import": r"(?:url\(|@import\s+)[\"']?https?://",
    }
    for label, pattern in patterns.items():
        if re.search(pattern, html, flags=re.I):
            problems.append(f"found {label}: the page would break offline")
    return problems


def screenshot(browser: str, url: str, out: Path, width: int, height: int) -> None:
    subprocess.run(
        [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            f"--window-size={width},{height}",
            f"--screenshot={out}",
            url,
        ],
        capture_output=True,
        timeout=120,
        check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--anchors", default="", help="comma-separated element ids to screenshot")
    args = parser.parse_args()

    if not args.html.exists():
        print(f"file not found: {args.html}")
        return 2
    html = args.html.read_text(encoding="utf-8")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    problems = check_script_syntax(html) + check_external_resources(html)

    browser = find_browser()
    if browser is None:
        problems.append("no Chrome/Edge found: screenshots were NOT taken")
    else:
        base_url = args.html.resolve().as_uri()
        shots = [("desktop", "", 1280, 4200), ("mobile", "", 400, 4200)]
        for anchor in filter(None, (a.strip() for a in args.anchors.split(","))):
            shots.append((f"desktop_{anchor}", f"#{anchor}", 1280, 2400))
            shots.append((f"mobile_{anchor}", f"#{anchor}", 400, 3200))
        for name, fragment, width, height in shots:
            target = args.output_dir / f"{name}.png"
            screenshot(browser, base_url + fragment, target, width, height)
            print(f"{'saved' if target.exists() else 'FAILED'}: {target}")
            if not target.exists():
                problems.append(f"screenshot failed: {name}")

    if problems:
        print("\nPROBLEMS:")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print("\nall checks passed (now look at the screenshots)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
