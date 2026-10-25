#!/usr/bin/env python3
"""Download every chart from the MDMC API into a local folder."""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ============================
# CONFIGURATION
# ============================
API_URL = "https://api.mdmc.moe/v3/charts"
DEFAULT_OUTPUT_DIR = "Custom_Albums"  # <--- CHANGE THIS (or pass -o on the CLI)
DEFAULT_WORKERS = 4
TIMEOUT = (10, 60)  # (connect, read) seconds
CHUNK_SIZE = 64 * 1024
MAX_NAME_LEN = 150

log = logging.getLogger("mdmc")

# ============================
# Filename sanitizing
# ============================
_INVALID_CHARS = re.compile(r'[\\/*?:"<>|\x00-\x1f]')
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(name: str) -> str:
    """Make a string safe to use as a filename on Windows, macOS and Linux."""
    name = _INVALID_CHARS.sub("", name).strip()
    name = name[:MAX_NAME_LEN].rstrip(". ")  # Windows dislikes trailing dots/spaces
    if not name:
        name = "Untitled"
    if name.split(".")[0].upper() in _RESERVED_NAMES:
        name = f"_{name}"
    return name


def build_filenames(charts: dict[str, str]) -> dict[str, str]:
    """Map chart_id -> filename. Duplicate titles get the chart id appended,
    so two different charts never overwrite (or get skipped as) each other."""
    safe = {cid: sanitize_filename(title) for cid, title in charts.items()}
    counts = Counter(safe.values())
    return {
        cid: f"{name} [{cid}].mdm" if counts[name] > 1 else f"{name}.mdm"
        for cid, name in safe.items()
    }


# ============================
# HTTP session with retries
# ============================
def make_session(workers: int) -> requests.Session:
    retry = Retry(
        total=5,
        backoff_factor=1,  # 1s, 2s, 4s, ...
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_maxsize=max(workers, 4))
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers["User-Agent"] = "mdmc-chart-downloader/2.0"
    return session


# ============================
# Fetch all chart metadata
# ============================
def fetch_all_charts(session: requests.Session) -> dict[str, str]:
    """Return {chart_id: title} for every chart, de-duplicated by id."""
    charts: dict[str, str] = {}
    page = 1

    while True:
        log.info("Fetching page %d...", page)
        resp = session.get(API_URL, params={"page": page}, timeout=TIMEOUT)
        resp.raise_for_status()
        page_charts = resp.json().get("charts", [])
        if not page_charts:
            break

        added = 0
        for chart in page_charts:
            chart_id = chart.get("id") or chart.get("_id")
            if not chart_id or chart_id in charts:
                continue
            charts[chart_id] = chart.get("title") or "Unknown Title"
            added += 1

        if added == 0:  # API is repeating pages -> avoid an infinite loop
            log.warning("Page %d contained no new charts; stopping.", page)
            break
        page += 1

    return charts


# ============================
# Download a single chart file
# ============================
def download_chart(
    session: requests.Session, chart_id: str, filename: str, output_dir: Path
) -> str:
    """Download one chart. Returns 'downloaded' or 'skipped'."""
    dest = output_dir / filename
    if dest.exists():
        log.info("Skipping (already downloaded): %s", filename)
        return "skipped"

    # Write to a temp file first so an interrupted download never leaves a
    # truncated .mdm that would be skipped as "already downloaded" next run.
    tmp = dest.with_name(dest.name + ".part")
    url = f"{API_URL}/{chart_id}/download"
    log.info("Downloading: %s", filename)

    try:
        with session.get(url, stream=True, timeout=TIMEOUT) as resp:
            resp.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(CHUNK_SIZE):
                    f.write(chunk)
        tmp.replace(dest)  # atomic rename
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    log.info("Saved: %s", filename)
    return "downloaded"


# ============================
# MAIN
# ============================
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Download all charts from MDMC.")
    p.add_argument("-o", "--output", default=DEFAULT_OUTPUT_DIR,
                   help=f"output folder (default: {DEFAULT_OUTPUT_DIR})")
    p.add_argument("-w", "--workers", type=int, default=DEFAULT_WORKERS,
                   help=f"parallel downloads (default: {DEFAULT_WORKERS})")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    workers = max(1, args.workers)

    session = make_session(workers)
    charts = fetch_all_charts(session)
    log.info("Found %d charts", len(charts))

    filenames = build_filenames(charts)
    stats = Counter()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(download_chart, session, cid, filenames[cid], output_dir): cid
            for cid in charts
        }
        try:
            for fut in as_completed(futures):
                cid = futures[fut]
                try:
                    stats[fut.result()] += 1
                except Exception as e:
                    stats["failed"] += 1
                    log.error("Failed to download %s (%s): %s", cid, charts[cid], e)
        except KeyboardInterrupt:
            log.warning("Interrupted; cancelling remaining downloads...")
            pool.shutdown(wait=False, cancel_futures=True)
            return 130

    log.info(
        "Done. %d downloaded, %d skipped, %d failed.",
        stats["downloaded"], stats["skipped"], stats["failed"],
    )
    return 1 if stats["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())