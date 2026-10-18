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

Simulated: the camera scene, the workers and the per-item detections.
Real: everything downstream (verdict, stats, shift tracking, alert log), and
phone pushes via ntfy when a topic is set in Settings.

On the Pi, each watch-list item's description is meant to go verbatim to the
vision model as a detection target (JevCam's `/api/config` takes up to 8).

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
