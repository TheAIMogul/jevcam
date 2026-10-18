# JevCam

Live object detection on a Raspberry Pi 4 + Camera Module 2 (NoIR/IR), powered by
TypeSafe **Jev**. The camera streams continuously (MJPEG, 20 fps) to the web UI;
a separate thread samples the newest frame once per second, sends it to Jev with a
**user-defined, natural-language list of target objects**, and the browser draws the
returned boxes over the live video along with per-target presence probability.

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
- `app.py` — Flask app: rpicam-vid MJPEG stream + watchdog, 1 fps Jev sampler, `/stream.mjpg`, JSON API, ElevenLabs alarm voice.
- `index.html` — single-page web UI (light theme, editable target list).
- `jevcam.service` — systemd unit.
