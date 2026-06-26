# Maison Zina — homemade calendar sync

A free, self-hosted "channel manager" that keeps Airbnb and Booking.com calendars
in sync for a guesthouse that is sold **both** as individual rooms **and** as a
whole house — without the cross-listing **sync loop** that over-blocks rooms, and
without any paid third-party service.

It runs on **GitHub Actions** (free cron) and publishes per-listing `.ics` feeds
on **GitHub Pages** (free hosting).

---

## The problem this solves

With naive iCal links, a single room booking cascades into a loop:

> Zina is booked → the Full House calendars get blocked → the Full House
> calendars re-export that block → it lands back on the **other rooms**, falsely
> blocking them.

The loop exists because a "busy" date has no meaning attached: a full-house
listing can't tell "I'm busy because I was booked" from "I'm busy because one
room is taken", and it re-propagates the block.

## How this fixes it

1. **Remove every native iCal link between your listings.** Nothing imports from
   another listing directly anymore.
2. **One script becomes the single source of truth.** It reads each listing's
   *real* bookings, applies the hierarchy **once**, and writes a dedicated feed
   per listing.
3. **Each listing imports only its own generated feed.**

Because the logic is applied once and a room booking only ever propagates **up**
to the full houses (never sideways to other rooms), the loop is structurally
impossible. A built-in tag on every generated event means even if a platform
re-exports one of our blocks, it is ignored on the next read — so it can never be
re-ingested as a booking.

### The rules encoded (see `src/logic.py`)

| Event | Blocks |
|-------|--------|
| A room is booked | both Full House listings |
| A Full House is booked | all rooms + the other Full House |
| Same room, other platform | that room only (cross-platform sync) |
| Another room is booked | nothing (rooms are independent) |

The non-individually-bookable 5th room is never sold alone, so it produces no
bookings; both Full House listings already block whenever the house is in use, so
it is fully covered.

---

## Project layout

```
calendar-sync/
├─ config.yaml              # listing topology (NO URLs) — edit names here
├─ feeds.example.json       # template for the 12 feed URLs
├─ requirements.txt
├─ src/
│  ├─ ical_utils.py         # fetch/parse/generate iCal + loop guard
│  ├─ logic.py              # the pure room↔full-house hierarchy
│  └─ sync.py               # orchestrator (fetch → compute → publish)
├─ tests/test_logic.py      # proves the loop is gone (7 tests)
├─ docs/                    # GitHub Pages output (generated .ics + index.html)
├─ state/                   # last-known-good cache (fail-safe)
└─ .github/workflows/sync.yml
```

---

## One-time setup

### 1. Turn OFF all native calendar links

In both Airbnb and Booking, **remove every imported calendar** you previously
added between your own listings. This is what stops the loop. (Leave links to
genuinely external calendars, if any, alone.)

### 2. Collect the 12 export URLs

For each of the 12 listings, copy its calendar **export** (`.ics`) URL:

- **Airbnb:** Listing → Calendar → Availability → *Connect to another website* →
  **Export Calendar** → copy the link.
- **Booking.com:** Extranet → Rates & Availability → **Sync calendars** → copy
  the listing's export link.

### 3. Create the GitHub repo

1. Create a **new repository** (public is fine and required for free Pages — the
   published feeds contain busy dates only, no guest data).
2. Push the **contents of this `calendar-sync/` folder** as the repo root:

   ```bash
   cd calendar-sync
   git init
   git add .
   git commit -m "Initial calendar sync setup"
   git branch -M main
   git remote add origin https://github.com/<you>/<repo>.git
   git push -u origin main
   ```

### 4. Add the feed URLs as a secret (keeps them private)

1. Copy `feeds.example.json`, fill in your 12 real URLs.
2. In GitHub: **Settings → Secrets and variables → Actions → New repository
   secret**.
   - Name: `FEEDS_JSON`
   - Value: paste the full JSON object.

The URLs never live in the repo, so the repo can stay public safely.

### 5. Adjust `config.yaml` if needed

Rename listings (`Zina Room`, `Room 2`, …) to match yours. Keep `type: room`
for the four bookable rooms and `type: full_house` for the two whole-house
listings.

### 6. Enable GitHub Pages

**Settings → Pages → Build and deployment → Deploy from a branch** → branch
`main`, folder `/docs` → Save.

### 7. First run

**Actions → Maison Zina Calendar Sync → Run workflow.** When it finishes, your
feeds are live at:

```
https://<you>.github.io/<repo>/index.html      ← list of all feeds
https://<you>.github.io/<repo>/zina.airbnb.ics
https://<you>.github.io/<repo>/fh5.booking.ics
...
```

### 8. Import each generated feed back into its listing

In each listing's **import** slot, paste the matching generated URL (e.g. Airbnb
Zina imports `…/zina.airbnb.ics`). Each listing imports **only its own** feed.

Done. From now on the workflow runs every 15 minutes automatically.

---

## Local testing (optional)

```bash
cd calendar-sync
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python tests/test_logic.py          # 7/7 should pass

# Dry run against your real feeds without committing:
cp feeds.example.json feeds.local.json   # fill in real URLs (gitignored)
python src/sync.py                        # writes docs/*.ics locally
```

`feeds.local.json` is git-ignored so your URLs are never committed.

---

## Safety behaviours built in

- **Fail-safe caching.** If a feed fails to load, the last-known-good data is
  used. If a feed has no cache and can't be fetched, the run **aborts without
  writing** — it never unblocks dates on a transient error (which could cause a
  double booking). Previously published feeds stay untouched.
- **Loop guard.** Generated events are tagged (`UID` prefix `mz-sync-`,
  summary `Blocked – Maison Zina sync`) and skipped on read, so derived blocks
  can never be re-ingested.
- **Exclusive checkout day.** Date ranges are half-open `[check-in, checkout)`,
  so back-to-back stays (one guest checks out the day another checks in) do not
  falsely collide.
- **Self-healing.** Cancel a booking and its derived blocks disappear on the
  next run automatically.

## Known limitation

Airbnb and Booking refresh imported calendars on **their own schedule** (minutes
to a few hours). That polling lag is inherent to iCal and exists with any free
solution — only paid push-API channel managers remove it. This tool removes the
**loop** and automates the **logic**; it cannot make platform polling instant.
So a small double-booking window can still exist; keep an eye on same-day
overlapping bookings.
