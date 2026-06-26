"""Maison Zina calendar sync orchestrator.

Run order:
  1. Load topology (config.yaml) and feed URLs (FEEDS_JSON env or feeds.local.json).
  2. Fetch every source feed -> parse real bookings.
       * On fetch failure, fall back to the last-known-good cached state.
       * If a feed has neither a working fetch nor a cache, ABORT without
         writing any output (fail-safe: never unblock dates on a transient
         error, which could invite a double booking).
  3. Apply the room <-> full-house hierarchy (logic.compute_blocks).
  4. Write one .ics per listing into docs/ and refresh docs/index.html.

Outputs contain busy dates only (no guest data) and are safe to publish.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

import yaml

from ical_utils import DateRange, build_ics, fetch_feed, parse_busy_ranges
from logic import compute_blocks

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
DOCS_DIR = ROOT / "public"
STATE_DIR = ROOT / "state"


def log(message: str) -> None:
    print(f"[mz-sync] {message}", flush=True)


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_feed_urls() -> dict[str, str]:
    raw = os.environ.get("FEEDS_JSON")
    if raw:
        return json.loads(raw)
    local = ROOT / "feeds.local.json"
    if local.exists():
        return json.loads(local.read_text(encoding="utf-8"))
    return {}


def _serialize(ranges: list[DateRange]) -> list[list[str]]:
    return [[s.isoformat(), e.isoformat()] for s, e in ranges]


def _deserialize(data: list[list[str]]) -> list[DateRange]:
    return [(dt.date.fromisoformat(s), dt.date.fromisoformat(e)) for s, e in data]


def gather_busy(config: dict, feed_urls: dict[str, str]) -> tuple[dict[str, list[DateRange]], list[str]]:
    """Fetch all source feeds with fail-safe caching.

    Returns (busy_by_key, unrecoverable_keys).
    """
    busy: dict[str, list[DateRange]] = {}
    unrecoverable: list[str] = []

    for listing in config["listings"]:
        for platform in config["platforms"]:
            key = f"{listing['id']}.{platform}"
            cache_path = STATE_DIR / f"{key}.json"
            url = feed_urls.get(key)

            def use_cache(reason: str) -> None:
                if cache_path.exists():
                    busy[key] = _deserialize(json.loads(cache_path.read_text(encoding="utf-8")))
                    log(f"{key}: {reason}; using cached state ({len(busy[key])} range(s))")
                else:
                    unrecoverable.append(key)
                    log(f"{key}: {reason}; NO cache available")

            if not url or "REPLACE" in url:
                use_cache("no URL configured")
                continue

            try:
                ranges = parse_busy_ranges(fetch_feed(url))
                busy[key] = ranges
                cache_path.write_text(json.dumps(_serialize(ranges)), encoding="utf-8")
                log(f"{key}: fetched {len(ranges)} busy range(s)")
            except Exception as exc:  # noqa: BLE001 - any failure -> fail-safe
                use_cache(f"fetch failed ({exc})")

    return busy, unrecoverable


def write_outputs(config: dict, blocks: dict[str, list[DateRange]]) -> list[tuple[str, str, str, int]]:
    rows: list[tuple[str, str, str, int]] = []
    by_id = {l["id"]: l for l in config["listings"]}
    for listing in config["listings"]:
        for platform in config["platforms"]:
            key = f"{listing['id']}.{platform}"
            ranges = blocks.get(key, [])
            ics = build_ics(key, f"{by_id[listing['id']]['name']} \u2013 {platform}", ranges)
            (DOCS_DIR / f"{key}.ics").write_bytes(ics)
            rows.append((key, listing["name"], platform, len(ranges)))
            log(f"wrote {key}.ics: {len(ranges)} block(s)")
    return rows


def write_index(config: dict, rows: list[tuple[str, str, str, int]]) -> None:
    generated = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    items = "\n".join(
        f'      <tr><td>{name}</td><td>{platform}</td>'
        f'<td><a href="{key}.ics">{key}.ics</a></td><td>{count}</td></tr>'
        for key, name, platform, count in rows
    )
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{config['site_name']} \u2014 synced calendars</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #222; }}
    table {{ border-collapse: collapse; margin-top: 1rem; }}
    th, td {{ border: 1px solid #ccc; padding: .4rem .8rem; text-align: left; }}
    th {{ background: #f5f5f5; }}
    code {{ background: #f0f0f0; padding: .1rem .3rem; border-radius: 3px; }}
  </style>
</head>
<body>
  <h1>{config['site_name']} \u2014 synced calendars</h1>
  <p>Generated: {generated}</p>
  <p>Import each feed below into the matching listing's calendar import slot.
     Each feed contains derived blocks only (busy dates, no guest data).</p>
  <table>
    <thead><tr><th>Listing</th><th>Platform</th><th>Feed</th><th>Blocks</th></tr></thead>
    <tbody>
{items}
    </tbody>
  </table>
</body>
</html>
"""
    (DOCS_DIR / "index.html").write_text(html, encoding="utf-8")


def main() -> int:
    config = load_config()
    feed_urls = load_feed_urls()
    STATE_DIR.mkdir(exist_ok=True)
    DOCS_DIR.mkdir(exist_ok=True)

    busy, unrecoverable = gather_busy(config, feed_urls)
    if unrecoverable:
        log(
            "ABORTING without writing outputs (fail-safe). "
            f"Unrecoverable feeds: {unrecoverable}. "
            "Existing published feeds are left untouched."
        )
        return 1

    blocks = compute_blocks(config["listings"], config["platforms"], busy)
    rows = write_outputs(config, blocks)
    write_index(config, rows)
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
