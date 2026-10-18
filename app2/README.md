# PPE Monitor — demo

One-screen PPE compliance dashboard for a site entrance with a NoIR camera.
Open `index.html` in a browser. No server, no build.

## The 3-minute demo

1. **Idle.** The live feed shows the gate under IR. A worker walks in every
   15–30 s on its own; today's numbers, compliance ring and shift stats are live.
2. **Press `C`.** A compliant worker enters: the yellow *SCANNING* box tracks
   them to the gate line, then locks green with every PPE item ticked.
3. **Press `N`.** A non-compliant worker enters: the box locks red, the feed
   flashes, a banner names the missing item, and the entry lands at the top of
   *Live entries* with an evidence snapshot (click it to enlarge).
4. **Watch list.** The card on the right lists what the camera checks for.
   The four built-ins (hard hat, vest, glasses, gloves) can be toggled; **+ Add
   item** adds anything else, e.g. "Red visitor helmet", with a plain-words
   description, a severity, and a schedule (daily, specific days, or a date
   range). An item scheduled for today is required right away: press `N` and
   the next worker is checked for it too. Entries recorded before an item
   existed are never retro-flagged.
5. **Press `S`.** Settings: alert sensitivity, phone push (ntfy), camera scan
   interval and sound. Changes apply instantly and re-score today's entries.

The two buttons next to **Demo** in the top bar do the same as `C` / `N`.

## What is real vs simulated

Served from the Pi by `server.js` (the `app2` service on port 3001):

- **Real:** the live feed is JevCam's 20 fps stream (`:8000/stream.mjpg`). Once a
  second, `server.js` sends JevCam's newest frame to TypeSafe Jev and asks about
  *the person closest to the camera*: is someone there, where are they, and are
  they wearing each active watch-list item (the item's description goes in
  verbatim). A person gets a yellow *SCANNING* box; after two answers the verdict
  locks green or red and the entry is logged with a snapshot of the real frame.
  The gate has to be empty for 4 s before the next person counts.
- **Simulated:** the seeded history behind today's numbers, and the `C` / `N`
  demo keys. Automatic simulated arrivals pause while the live camera is on.

App 2 asks Jev its own questions, so it never changes JevCam's target list.
Opened as a plain file, or with the camera or Jev down, the page falls back to
the drawn scene and simulated workers.

## Watch list

The *Watch list* card lists what the camera checks for. The four built-in PPE
items stay; **+ Add item** adds any wearable (name, what the camera should look
for, severity, on/off, and a schedule: every day, specific days or a date
range). Only items active today count, and today's entries are re-scored when
the list changes. On the Pi the description is what gets sent to the detector.

## Putting it on pi-07

```bash
./deploy-pi.sh          # copies index.html to pi-07:~/app, backs up the old page, restarts the app
./deploy-pi.sh --check  # only shows what is on the Pi
```

The starter server already on the Pi serves `index.html` on port 3000 / 80.
`server.js` here is an optional Node server for later (live frame from JevCam,
server-side alerts); the page does not need it.

The layout is sized for a projector: full dashboard at 1440×900 and up, and a
compact mode that keeps every card on screen at 1280×720 / 1366×768.
