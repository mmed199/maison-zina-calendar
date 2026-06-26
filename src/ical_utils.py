"""iCalendar helpers for the Maison Zina calendar sync.

Date model
----------
Every booking is represented as a half-open date range ``[start, end)`` where
``end`` is the *checkout* day and is therefore **exclusive**. This matches how
Airbnb / Booking emit all-day VEVENTs (``DTEND`` = checkout date) and means
back-to-back stays (one guest checks out the same day another checks in) do
**not** falsely collide.

Loop guard
----------
Generated blocks are tagged with ``SYNC_UID_PREFIX`` / ``SYNC_MARKER``. When we
read source feeds we skip any event carrying that tag, so even if a platform
re-exports a block we previously gave it, it can never be re-ingested as a
"booking" and cascade. This makes the system safe regardless of each
platform's re-export behaviour.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import requests
from icalendar import Calendar, Event

SYNC_UID_PREFIX = "mz-sync-"
SYNC_MARKER = "Maison Zina sync"

DateRange = tuple[dt.date, dt.date]


def fetch_feed(url: str, timeout: int = 30) -> str:
    """Return the raw iCalendar text for ``url``.

    Supports ``file://`` URLs and local paths (used by tests / local runs) in
    addition to http(s). Raises on HTTP errors or non-iCalendar responses.
    """
    if url.startswith("file://"):
        text = Path(url[len("file://"):]).read_text(encoding="utf-8")
    elif os.path.exists(url):
        text = Path(url).read_text(encoding="utf-8")
    else:
        resp = requests.get(
            url, timeout=timeout, headers={"User-Agent": "MaisonZinaSync/1.0"}
        )
        resp.raise_for_status()
        text = resp.text
    if "BEGIN:VCALENDAR" not in text:
        raise ValueError("Response does not look like an iCalendar feed")
    return text


def _to_date(value: object) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    raise TypeError(f"Unsupported date value: {value!r}")


def parse_busy_ranges(ics_text: str) -> list[DateRange]:
    """Parse busy ``[start, end)`` ranges from an iCalendar string.

    Skips our own generated blocks (loop guard). Returns merged ranges.
    """
    cal = Calendar.from_ical(ics_text)
    ranges: list[DateRange] = []
    for comp in cal.walk("VEVENT"):
        uid = str(comp.get("UID", ""))
        summary = str(comp.get("SUMMARY", ""))
        if uid.startswith(SYNC_UID_PREFIX) or SYNC_MARKER in summary:
            continue  # never re-ingest our own derived blocks

        dtstart_field = comp.get("DTSTART")
        if dtstart_field is None:
            continue
        start = _to_date(dtstart_field.dt)

        dtend_field = comp.get("DTEND")
        end = _to_date(dtend_field.dt) if dtend_field is not None else start + dt.timedelta(days=1)
        if end <= start:
            end = start + dt.timedelta(days=1)

        ranges.append((start, end))

    return merge_ranges(ranges)


def merge_ranges(ranges: list[DateRange]) -> list[DateRange]:
    """Merge overlapping or adjacent ``[start, end)`` ranges."""
    if not ranges:
        return []
    ordered = sorted(ranges)
    merged: list[list[dt.date]] = [list(ordered[0])]
    for start, end in ordered[1:]:
        if start <= merged[-1][1]:  # overlap or touch (exclusive end)
            if end > merged[-1][1]:
                merged[-1][1] = end
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def union_ranges(*range_lists: list[DateRange]) -> list[DateRange]:
    """Union of several range lists, returned merged."""
    combined: list[DateRange] = []
    for rl in range_lists:
        combined.extend(rl)
    return merge_ranges(combined)


def build_ics(
    listing_key: str,
    listing_name: str,
    ranges: list[DateRange],
    now: dt.datetime | None = None,
) -> bytes:
    """Build an iCalendar feed of derived blocks for one listing."""
    now = now or dt.datetime.now(dt.timezone.utc)
    cal = Calendar()
    cal.add("prodid", "-//Maison Zina//Calendar Sync//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", f"{listing_name} (blocks)")

    for start, end in ranges:
        event = Event()
        event.add(
            "uid",
            f"{SYNC_UID_PREFIX}{listing_key}-{start.isoformat()}-{end.isoformat()}@maisonzina",
        )
        event.add("dtstamp", now)
        event.add("dtstart", start)  # date object -> VALUE=DATE (all-day)
        event.add("dtend", end)      # exclusive checkout day preserved
        event.add("summary", f"Blocked \u2013 {SYNC_MARKER}")
        event.add("transp", "OPAQUE")
        cal.add_component(event)

    return cal.to_ical()
