# JevCam

Live object detection on a Raspberry Pi 4 + Camera Module 2 (NoIR/IR), powered by
TypeSafe **Jev**. Captures frames on a fixed interval, sends each to Jev with a
**user-defined, natural-language list of target objects**, and serves a live web UI
showing the annotated frame plus per-target presence probability and bounding boxes.

Define *what it should see* right in the web UI — no fixed class list, no model training.

## Run (on the Pi)
```
python3 -m venv --system-site-packages .venv
.venv/bin/pip install flask requests pillow
.venv/bin/python app.py        # serves on :8000
```
Requires the TypeSafe API key at `~/.config/typesafe/api-key`.

Installed as a systemd service (`jevcam.service`): `sudo systemctl status jevcam`.

## Files
- `app.py` — Flask app: capture loop, Jev calls, box drawing, JSON/image API.
- `index.html` — single-page web UI (light theme, editable target list).
- `jevcam.service` — systemd unit.
