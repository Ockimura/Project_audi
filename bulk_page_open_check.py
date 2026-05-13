#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
bulk_page_open_check.py

Mass page opening/loading checker based on the navigation and DOM-stabilization
logic from the original Playwright script.

Features:
1) Reads URLs from a user-provided TXT file.
2) Opens pages in normal mode or in a relaxed mode that tries to ignore
   HTTPS/suspicious-site restrictions.
3) Can run headless, without showing pages to the user, or headful, with a
   visible browser window.
4) Writes opening/loading errors to a CSV file. If the CSV already exists,
   the program adds a new audit column. If it does not exist, the program
   creates it. The default CSV filename is a11y_page_errors.csv.

CSV format:
- The first column is url.
- Every next column is the start date/time of one mass audit run.
- A cell is empty when the page opened and stabilized successfully.
- A cell contains error details when navigation or stable DOM loading failed.

Examples:
    python bulk_page_open_check.py --urls-file urls.txt
    python bulk_page_open_check.py --urls-file urls.txt --show-browser
    python bulk_page_open_check.py --urls-file urls.txt --ignore-suspicious
    python bulk_page_open_check.py --urls-file urls.txt --csv report.csv --timeout 60000
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from playwright.async_api import (
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

DEFAULT_CSV_BASENAME = "a11y_page_errors"
DEFAULT_NAV_TIMEOUT_MS = 45_000
DEFAULT_STABLE_RETRIES = 5
DEFAULT_STABLE_NETWORK_IDLE_TIMEOUT_MS = 8_000
DEFAULT_STABLE_DELAY_SEC = 0.6
DEFAULT_MAX_RETRIES = 3


@dataclass
class CheckResult:
    url: str
    final_url: Optional[str]
    ok: bool
    error: str


def normalize_csv_path(name: str) -> Path:
    """Return a CSV path; append .csv when the user did not provide a suffix."""
    p = Path(name).expanduser()
    if p.suffix.lower() != ".csv":
        p = p.with_suffix(".csv")
    return p


def normalize_url(raw: str) -> str:
    """Add https:// when the URL has no scheme."""
    value = raw.strip()
    if not value:
        return ""
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value):
        return value
    return "https://" + value


def read_urls(path: Path) -> List[str]:
    """
    Read URLs from a TXT file.

    Empty lines and lines starting with # are ignored. Duplicate URLs are
    removed while preserving order.
    """
    if not path.exists():
        raise FileNotFoundError(f"TXT file not found: {path}")

    seen = set()
    urls: List[str] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        url = normalize_url(stripped)
        if not url:
            continue
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def compact_error(error: object) -> str:
    """Make Playwright errors shorter and safe for a single CSV cell."""
    text = str(error or "").strip()
    text = re.sub(r"\s+", " ", text)
    # Keep the useful part before a verbose call log, if Playwright adds one.
    text = text.split("=========================== logs ===========================")[0].strip()
    return text[:2000]


async def try_proceed_interstitial(page) -> bool:
    """
    Try to continue through Chromium security/interstitial pages.

    This mostly helps with certificate/privacy interstitials where Chromium
    exposes #details-button and #proceed-link. Some phishing/malware blocks do
    not legally or technically allow proceeding; those will remain failures.
    """
    script = """
    () => {
      const click = (selector) => {
        const element = document.querySelector(selector);
        if (!element) return false;
        element.click();
        return true;
      };

      const clickedDetails = click('#details-button') || click('button#details-button');
      const clickedProceed = click('#proceed-link') || click('a#proceed-link');
      return clickedDetails || clickedProceed;
    }
    """
    try:
        clicked = bool(await page.evaluate(script))
        if clicked:
            await page.wait_for_load_state("domcontentloaded", timeout=5_000)
            return True
    except Exception:
        return False
    return False


async def page_goto(
    page,
    url: str,
    *,
    retries: int,
    nav_timeout_ms: int,
    ignore_suspicious: bool,
) -> Tuple[bool, Optional[str]]:
    """Open a page with retries. Return (success, error)."""
    last_error: Optional[str] = None

    for attempt in range(1, retries + 1):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=nav_timeout_ms)

            if ignore_suspicious:
                await try_proceed_interstitial(page)

            return True, None
        except (PlaywrightTimeoutError, PlaywrightError) as exc:
            last_error = compact_error(exc)

            # A browser interstitial may still be present after a navigation error.
            if ignore_suspicious and await try_proceed_interstitial(page):
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=10_000)
                    return True, None
                except Exception as after_proceed_exc:
                    last_error = compact_error(after_proceed_exc)

            if attempt < retries:
                await asyncio.sleep(2)

    return False, last_error or "Unknown navigation error"


async def get_stable_page_content(
    page,
    *,
    retries: int,
    network_idle_timeout_ms: int,
    delay_sec: float,
) -> Tuple[bool, Optional[str]]:
    """
    Wait until the DOM is available and the network becomes idle, then read HTML.

    This follows the stabilization idea from the original script: wait for
    domcontentloaded, then networkidle, wait a little, and read page.content().
    """
    last_error: Optional[str] = None

    for _ in range(retries):
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=network_idle_timeout_ms)
            await page.wait_for_load_state("networkidle", timeout=network_idle_timeout_ms)
            await asyncio.sleep(delay_sec)
            _ = await page.content()
            return True, None
        except PlaywrightError as exc:
            last_error = compact_error(exc)
            await asyncio.sleep(delay_sec)

    return False, f"stable_dom_error: {last_error or 'stable DOM was not reached'}"


async def check_one_url(
    page,
    url: str,
    *,
    max_retries: int,
    nav_timeout_ms: int,
    stable_retries: int,
    stable_network_idle_timeout_ms: int,
    stable_delay_sec: float,
    ignore_suspicious: bool,
) -> CheckResult:
    """Run navigation and stable-content checks for one URL."""
    navigation_ok, navigation_error = await page_goto(
        page,
        url,
        retries=max_retries,
        nav_timeout_ms=nav_timeout_ms,
        ignore_suspicious=ignore_suspicious,
    )

    if not navigation_ok:
        return CheckResult(
            url=url,
            final_url=None,
            ok=False,
            error=f"navigation_error: {navigation_error}",
        )

    stable_ok, stable_error = await get_stable_page_content(
        page,
        retries=stable_retries,
        network_idle_timeout_ms=stable_network_idle_timeout_ms,
        delay_sec=stable_delay_sec,
    )

    if not stable_ok:
        return CheckResult(
            url=url,
            final_url=page.url,
            ok=False,
            error=stable_error or "stable_dom_error: unknown error",
        )

    return CheckResult(url=url, final_url=page.url, ok=True, error="")


def make_unique_column(existing_columns: Sequence[str], wanted: str) -> str:
    """Avoid duplicate audit column names when two runs start in the same second."""
    existing = set(existing_columns)
    if wanted not in existing:
        return wanted
    idx = 2
    while f"{wanted} #{idx}" in existing:
        idx += 1
    return f"{wanted} #{idx}"


def read_existing_csv(csv_path: Path) -> Tuple[List[str], Dict[str, Dict[str, str]]]:
    """
    Read the existing CSV into (columns, rows_by_url).

    If the file does not exist, return a new table with only the url column.
    """
    if not csv_path.exists():
        return ["url"], {}

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return ["url"], {}

        columns = list(reader.fieldnames)
        if "url" not in columns:
            raise ValueError(f"CSV file exists but has no required 'url' column: {csv_path}")

        rows_by_url: Dict[str, Dict[str, str]] = {}
        for row in reader:
            url = (row.get("url") or "").strip()
            if not url:
                continue
            rows_by_url[url] = {col: row.get(col, "") or "" for col in columns}

    return columns, rows_by_url


def write_results_to_csv(csv_path: Path, audit_column: str, results: Iterable[CheckResult]) -> None:
    """Append one audit run as a new column in the CSV."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    columns, rows_by_url = read_existing_csv(csv_path)
    audit_column = make_unique_column(columns, audit_column)
    columns.append(audit_column)

    for result in results:
        if result.url not in rows_by_url:
            rows_by_url[result.url] = {col: "" for col in columns}
            rows_by_url[result.url]["url"] = result.url
        else:
            for col in columns:
                rows_by_url[result.url].setdefault(col, "")

        rows_by_url[result.url][audit_column] = result.error if not result.ok else ""

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for url in sorted(rows_by_url.keys()):
            writer.writerow({col: rows_by_url[url].get(col, "") for col in columns})


def browser_args(ignore_suspicious: bool) -> List[str]:
    """Chromium args for the selected mode."""
    args = [
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
    ]

    if ignore_suspicious:
        args.extend(
            [
                "--ignore-certificate-errors",
                "--allow-running-insecure-content",
                "--disable-web-security",
                "--safebrowsing-disable-download-protection",
                "--disable-features=SafeBrowsingEnhancedProtection,SafeBrowsingProtection",
            ]
        )

    return args


async def run(args: argparse.Namespace) -> int:
    urls_file = Path(args.urls_file).expanduser()
    csv_path = normalize_csv_path(args.csv)
    urls = read_urls(urls_file)

    if not urls:
        print(f"No URLs found in {urls_file}", file=sys.stderr)
        return 2

    audit_started_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    results: List[CheckResult] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=not args.show_browser,
            args=browser_args(args.ignore_suspicious),
        )
        context = await browser.new_context(ignore_https_errors=args.ignore_suspicious)

        try:
            page = await context.new_page()

            for index, url in enumerate(urls, 1):
                print(f"[{index}/{len(urls)}] {url}")
                result = await check_one_url(
                    page,
                    url,
                    max_retries=args.retries,
                    nav_timeout_ms=args.timeout,
                    stable_retries=args.stable_retries,
                    stable_network_idle_timeout_ms=args.stable_timeout,
                    stable_delay_sec=args.stable_delay,
                    ignore_suspicious=args.ignore_suspicious,
                )
                results.append(result)

                if result.ok:
                    print(f"    OK -> {result.final_url}")
                else:
                    print(f"    ERROR: {result.error}")

                if args.new_page_per_url:
                    await page.close()
                    page = await context.new_page()

        finally:
            await context.close()
            await browser.close()

    write_results_to_csv(csv_path, audit_started_at, results)

    failed = sum(1 for r in results if not r.ok)
    ok = len(results) - failed
    print("\nDone")
    print(f"CSV: {csv_path}")
    print(f"Audit column: {audit_started_at}")
    print(f"OK: {ok}")
    print(f"Errors: {failed}")
    return 1 if failed and args.fail_on_error else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Mass-check page opening/loading errors and append them to a CSV audit table."
    )
    parser.add_argument(
        "--urls-file",
        required=True,
        help="Path to TXT file with one URL per line. Empty lines and # comments are ignored.",
    )
    parser.add_argument(
        "--csv",
        default=DEFAULT_CSV_BASENAME,
        help="CSV output filename. Default: a11y_page_errors.csv",
    )
    parser.add_argument(
        "--show-browser",
        action="store_true",
        help="Show Chromium window to the user. By default the browser runs headless.",
    )
    parser.add_argument(
        "--ignore-suspicious",
        action="store_true",
        help=(
            "Relax HTTPS/security restrictions: ignore certificate errors, add Chromium flags, "
            "and try to proceed through browser interstitial pages when possible."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_NAV_TIMEOUT_MS,
        help=f"Navigation timeout in ms. Default: {DEFAULT_NAV_TIMEOUT_MS}",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help=f"Navigation retry count. Default: {DEFAULT_MAX_RETRIES}",
    )
    parser.add_argument(
        "--stable-retries",
        type=int,
        default=DEFAULT_STABLE_RETRIES,
        help=f"Stable DOM retry count. Default: {DEFAULT_STABLE_RETRIES}",
    )
    parser.add_argument(
        "--stable-timeout",
        type=int,
        default=DEFAULT_STABLE_NETWORK_IDLE_TIMEOUT_MS,
        help=f"Timeout in ms for domcontentloaded/networkidle during stabilization. Default: {DEFAULT_STABLE_NETWORK_IDLE_TIMEOUT_MS}",
    )
    parser.add_argument(
        "--stable-delay",
        type=float,
        default=DEFAULT_STABLE_DELAY_SEC,
        help=f"Delay in seconds before reading page.content(). Default: {DEFAULT_STABLE_DELAY_SEC}",
    )
    parser.add_argument(
        "--new-page-per-url",
        action="store_true",
        help="Open each URL in a fresh tab instead of reusing one page.",
    )
    parser.add_argument(
        "--fail-on-error",
        action="store_true",
        help="Return exit code 1 when at least one URL fails.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Interrupted by user", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Fatal error: {compact_error(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
