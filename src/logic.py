"""Pure hierarchy logic for Maison Zina (no I/O, fully unit-testable).

The rules, derived from the physical reality of the guesthouse:

* A single room booked  -> block BOTH full-house listings. (Other rooms stay free.)
* A full house booked   -> block ALL rooms and the other full house.
* Rooms are independent of each other.
* The same room across platforms is one physical unit (its occupancy is the
  union across platforms).

Because a room booking only ever propagates *up* to the full houses and never
sideways to other rooms, the original sync loop is structurally impossible.
"""

from __future__ import annotations

from ical_utils import DateRange, union_ranges


def compute_blocks(
    listings: list[dict],
    platforms: list[str],
    busy: dict[str, list[DateRange]],
) -> dict[str, list[DateRange]]:
    """Map each ``"<id>.<platform>"`` feed key to the ranges it must block.

    ``busy`` maps each source feed key to its *real* booked ranges.
    """
    rooms = [l for l in listings if l["type"] == "room"]
    full_houses = [l for l in listings if l["type"] == "full_house"]

    def occupancy(listing_id: str) -> list[DateRange]:
        # A listing's true occupancy = union of its bookings across platforms.
        return union_ranges(*[busy.get(f"{listing_id}.{p}", []) for p in platforms])

    room_busy = {r["id"]: occupancy(r["id"]) for r in rooms}
    fh_busy = {fh["id"]: occupancy(fh["id"]) for fh in full_houses}

    any_room = union_ranges(*room_busy.values()) if room_busy else []
    any_fh = union_ranges(*fh_busy.values()) if fh_busy else []
    house_in_use = union_ranges(any_room, any_fh)

    blocks: dict[str, list[DateRange]] = {}
    for listing in listings:
        if listing["type"] == "room":
            # This room's own cross-platform occupancy + any full-house booking.
            blocked = union_ranges(room_busy[listing["id"]], any_fh)
        else:
            # Any full-house listing is unavailable whenever the house is in use.
            blocked = house_in_use
        for platform in platforms:
            blocks[f"{listing['id']}.{platform}"] = blocked
    return blocks
