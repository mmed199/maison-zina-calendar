"""Tests for the Maison Zina sync logic.

Run with:  python -m pytest -q      (from the project root)
        or:  python tests/test_logic.py
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ical_utils import (  # noqa: E402
    SYNC_MARKER,
    build_ics,
    merge_ranges,
    parse_busy_ranges,
    union_ranges,
)
from logic import compute_blocks  # noqa: E402

LISTINGS = [
    {"id": "zina", "name": "Zina Room", "type": "room"},
    {"id": "room2", "name": "Room 2", "type": "room"},
    {"id": "room3", "name": "Room 3", "type": "room"},
    {"id": "room4", "name": "Room 4", "type": "room"},
    {"id": "fh4", "name": "Full House (4)", "type": "full_house"},
    {"id": "fh5", "name": "Full House (5)", "type": "full_house"},
]
PLATFORMS = ["airbnb", "booking"]


def d(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def rng(a: str, b: str):
    return (d(a), d(b))


# --------------------------------------------------------------------------- #
# The core regression: a single room booking must NOT block other rooms.
# --------------------------------------------------------------------------- #
def test_room_booking_does_not_loop_to_other_rooms():
    busy = {"zina.airbnb": [rng("2026-07-10", "2026-07-12")]}
    blocks = compute_blocks(LISTINGS, PLATFORMS, busy)

    # Both full houses blocked on every platform.
    for key in ("fh4.airbnb", "fh4.booking", "fh5.airbnb", "fh5.booking"):
        assert blocks[key] == [rng("2026-07-10", "2026-07-12")], key

    # Same room blocked on the other platform (cross-platform sync).
    assert blocks["zina.booking"] == [rng("2026-07-10", "2026-07-12")]

    # The OTHER rooms must stay completely free -> this is the loop being gone.
    for key in ("room2.airbnb", "room2.booking", "room3.airbnb", "room4.booking"):
        assert blocks[key] == [], key


def test_full_house_booking_blocks_all_rooms_and_other_full_house():
    busy = {"fh5.booking": [rng("2026-08-01", "2026-08-05")]}
    blocks = compute_blocks(LISTINGS, PLATFORMS, busy)

    for room in ("zina", "room2", "room3", "room4"):
        for platform in PLATFORMS:
            assert blocks[f"{room}.{platform}"] == [rng("2026-08-01", "2026-08-05")]
    # The other full house is blocked too.
    assert blocks["fh4.airbnb"] == [rng("2026-08-01", "2026-08-05")]
    assert blocks["fh5.airbnb"] == [rng("2026-08-01", "2026-08-05")]


def test_two_different_rooms_independent_but_both_block_full_house():
    busy = {
        "zina.airbnb": [rng("2026-07-10", "2026-07-12")],
        "room2.booking": [rng("2026-07-20", "2026-07-22")],
    }
    blocks = compute_blocks(LISTINGS, PLATFORMS, busy)

    # zina only sees its own dates; room2 only sees its own dates.
    assert blocks["zina.booking"] == [rng("2026-07-10", "2026-07-12")]
    assert blocks["room2.airbnb"] == [rng("2026-07-20", "2026-07-22")]
    # room3 untouched.
    assert blocks["room3.airbnb"] == []
    # Full houses see the union of both room bookings.
    assert blocks["fh4.airbnb"] == [
        rng("2026-07-10", "2026-07-12"),
        rng("2026-07-20", "2026-07-22"),
    ]


# --------------------------------------------------------------------------- #
# Range maths: exclusive checkout day, merging.
# --------------------------------------------------------------------------- #
def test_back_to_back_stays_merge_and_preserve_exclusive_end():
    # checkout 12th == check-in 12th -> continuous occupancy [10,14).
    merged = merge_ranges([rng("2026-07-10", "2026-07-12"), rng("2026-07-12", "2026-07-14")])
    assert merged == [rng("2026-07-10", "2026-07-14")]


def test_non_adjacent_ranges_stay_separate():
    merged = union_ranges(
        [rng("2026-07-10", "2026-07-12")],
        [rng("2026-07-15", "2026-07-17")],
    )
    assert merged == [rng("2026-07-10", "2026-07-12"), rng("2026-07-15", "2026-07-17")]


# --------------------------------------------------------------------------- #
# Loop guard: our own generated blocks must never be re-ingested as bookings.
# --------------------------------------------------------------------------- #
def test_parse_skips_our_own_generated_blocks():
    real_then_synced = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n"
        # a genuine booking
        "BEGIN:VEVENT\r\nUID:real-123@airbnb\r\nSUMMARY:Reserved\r\n"
        "DTSTART;VALUE=DATE:20260710\r\nDTEND;VALUE=DATE:20260712\r\nEND:VEVENT\r\n"
        # one of OUR blocks that a platform may have re-exported back
        f"BEGIN:VEVENT\r\nUID:mz-sync-zina-x@maisonzina\r\nSUMMARY:Blocked \u2013 {SYNC_MARKER}\r\n"
        "DTSTART;VALUE=DATE:20260801\r\nDTEND;VALUE=DATE:20260805\r\nEND:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    ranges = parse_busy_ranges(real_then_synced)
    # Only the genuine booking survives; the synced block is ignored.
    assert ranges == [rng("2026-07-10", "2026-07-12")]


def test_build_ics_roundtrips_and_is_tagged():
    ics = build_ics("zina.airbnb", "Zina Room", [rng("2026-07-10", "2026-07-12")])
    text = ics.decode("utf-8")
    assert SYNC_MARKER in text
    assert "mz-sync-zina.airbnb" in text
    # Feeding our own output back in yields nothing (loop guard end-to-end).
    assert parse_busy_ranges(text) == []


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {test.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
