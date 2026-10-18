# pi-07 app chooser

Launcher for the two apps on pi-07. Open http://pi-07.local:8080 (or the Pi's
own screen, where it starts full-screen at login).

| Key | App | Port |
|---|---|---|
| `1` | JevCam: live object detection | 8000 |
| `2` | PPE Monitor: site-entry safety compliance | 3001 |

Arrow keys choose, `Enter` opens, `1` / `2` open directly. The chosen app runs
full-screen inside the chooser; the **Apps** button (bottom left) comes back.

## What starts at boot

- `chooser.service`: this page on :8080 (`chooser.py`, standard library only)
- `app2.service`: PPE Monitor (`node server.js`, `PORT=3001`)
- `jevcam.service`: JevCam (already set up)
- `~/.config/autostart/pi07-chooser.desktop` runs `kiosk.sh` at desktop login:
  Chromium in kiosk mode on Wayland, own profile. `Alt+F4` exits.

To add an app, add an entry to `apps.json` (port + a health path that returns 200).
