#!/usr/bin/env python3
"""JevCam - live IR-camera object detection on a Raspberry Pi, powered by TypeSafe Jev.

Captures frames from the Pi Camera on a fixed interval, sends each frame to Jev
with a user-defined, natural-language list of target objects, and serves a live
web UI that shows the annotated frame plus per-target presence probability.

The target list ("what it should see") is editable live from the web UI.
"""
import base64
import io
import json
import os
import threading
import time
from datetime import datetime

import requests
from flask import Flask, Response, jsonify, request, send_file
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TYPESAFE_KEY_FILE = os.path.expanduser("~/.config/typesafe/api-key")
TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-preview"            # jev-1.14.0 - supports box questions
MAX_SIDE = 1568                  # Jev image max per-side pixels
CAP_SIZE = (1568, 882)          # capture size (within Jev limits, 16:9)
PRESENCE_DRAW_THRESHOLD = 0.5    # draw a box only if presence noul >= this
BOX_CONF_FLOOR = 0.15            # and box confidence >= this

BOX_COLORS = [
    (0, 200, 255), (0, 220, 120), (255, 170, 0), (255, 90, 160),
    (150, 120, 255), (255, 80, 80), (60, 210, 210), (200, 200, 40),
]

# Mutable runtime config, protected by CONFIG_LOCK
config = {
    "interval": 5.0,
    "targets": ["person", "cell phone", "laptop"],
    "running": True,
}
CONFIG_LOCK = threading.Lock()

# Latest result, protected by STATE_LOCK
state = {
    "ts": None,
    "detections": [],      # [{label, present, presence, box, box_conf, drawn}]
    "latency_ms": None,
    "error": None,
    "frames": 0,
}
STATE_LOCK = threading.Lock()

latest_jpeg = {"bytes": None}   # annotated JPEG bytes
JPEG_LOCK = threading.Lock()


def load_key():
    with open(TYPESAFE_KEY_FILE) as f:
        return f.read().strip()


TYPESAFE_KEY = load_key()

# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------
from picamera2 import Picamera2  # noqa: E402
from libcamera import Transform  # noqa: E402

picam = Picamera2()
picam.configure(picam.create_still_configuration(
    main={"size": CAP_SIZE, "format": "RGB888"},
    transform=Transform(hflip=1, vflip=1),   # matches physical mount
))
picam.start()
time.sleep(1.0)


def capture_rgb():
    """Capture a frame as a PIL RGB image."""
    arr = picam.capture_array()            # HxWx3, RGB888
    return Image.fromarray(arr).convert("RGB")


# ---------------------------------------------------------------------------
# Jev
# ---------------------------------------------------------------------------
def build_request(img_b64, targets):
    questions = {}
    for i, t in enumerate(targets):
        questions[f"present_{i}"] = {
            "type": "noul",
            "instructions": f"Is there a {t} clearly visible in `photo`?",
        }
        questions[f"box_{i}"] = {
            "type": "box",
            "instructions": f"the {t} in `photo`",
        }
    return {
        "model": MODEL,
        "state": {"photo": "frame"},
        "assets": {"frame": {"type": "image", "format": "jpg", "data_base64": img_b64}},
        "questions": questions,
    }


def call_jev(img_b64, targets):
    body = build_request(img_b64, targets)
    r = requests.post(
        TYPESAFE_URL,
        headers={
            "Authorization": f"Bearer {TYPESAFE_KEY}",
            "Content-Type": "application/json",
            "TypeSafe-Danger-Zone-Images": "true",
        },
        data=json.dumps(body),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def parse_answers(resp, targets):
    ans = resp.get("answers", {})
    out = []
    for i, t in enumerate(targets):
        p = ans.get(f"present_{i}", {})
        b = ans.get(f"box_{i}", {})
        presence = p.get("noul")
        box = b.get("box")
        box_conf = b.get("confidence")
        present = presence is not None and presence >= PRESENCE_DRAW_THRESHOLD
        drawn = bool(present and box and box_conf and box_conf >= BOX_CONF_FLOOR)
        out.append({
            "label": t, "present": present, "presence": presence,
            "box": box, "box_conf": box_conf, "drawn": drawn,
            "color": BOX_COLORS[i % len(BOX_COLORS)],
        })
    return out


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------
def _font(size):
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def annotate(img, detections, ts_str):
    W, H = img.size
    d = ImageDraw.Draw(img, "RGBA")
    font = _font(max(18, W // 55))
    small = _font(max(14, W // 75))
    for det in detections:
        if not det["drawn"]:
            continue
        b = det["box"]
        c = tuple(det["color"])
        x0, y0, x1, y1 = b["left"]*W, b["top"]*H, b["right"]*W, b["bottom"]*H
        for w in range(4):
            d.rectangle([x0-w, y0-w, x1+w, y1+w], outline=c)
        label = f"{det['label']} {int((det['box_conf'] or 0)*100)}%"
        tb = d.textbbox((0, 0), label, font=font)
        tw, th = tb[2]-tb[0], tb[3]-tb[1]
        d.rectangle([x0, max(0, y0-th-10), x0+tw+12, y0], fill=c)
        d.text((x0+6, max(0, y0-th-8)), label, fill=(0, 0, 0), font=font)
    # timestamp footer
    foot = f"JevCam  {ts_str}"
    tb = d.textbbox((0, 0), foot, font=small)
    d.rectangle([0, H-(tb[3]-tb[1])-10, (tb[2]-tb[0])+12, H], fill=(0, 0, 0, 180))
    d.text((6, H-(tb[3]-tb[1])-8), foot, fill=(255, 255, 255), font=small)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Capture / detect loop
# ---------------------------------------------------------------------------
def loop():
    while True:
        with CONFIG_LOCK:
            interval = config["interval"]
            targets = list(config["targets"])
            running = config["running"]
        if not running or not targets:
            _store_plain(targets)
            time.sleep(min(interval, 2.0))
            continue
        t0 = time.time()
        err = None
        detections = []
        try:
            img = capture_rgb()
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=88)
            img_b64 = base64.b64encode(buf.getvalue()).decode()
            resp = call_jev(img_b64, targets)
            detections = parse_answers(resp, targets)
        except Exception as e:  # noqa: BLE001
            err = str(e)
        latency = int((time.time()-t0)*1000)
        ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            img_for_draw = img if not err else capture_rgb()
            jpeg = annotate(img_for_draw, detections, ts_str)
            with JPEG_LOCK:
                latest_jpeg["bytes"] = jpeg
        except Exception as e:  # noqa: BLE001
            err = err or str(e)
        with STATE_LOCK:
            state.update({
                "ts": ts_str, "detections": detections,
                "latency_ms": latency, "error": err,
                "frames": state["frames"] + 1,
            })
        dt = time.time() - t0
        time.sleep(max(0.1, interval - dt))


def _store_plain(targets):
    """When paused/no targets, still refresh the live image."""
    try:
        img = capture_rgb()
        ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        jpeg = annotate(img, [], ts_str)
        with JPEG_LOCK:
            latest_jpeg["bytes"] = jpeg
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Web
# ---------------------------------------------------------------------------
app = Flask(__name__)
HERE = os.path.dirname(os.path.abspath(__file__))


@app.route("/")
def index():
    return send_file(os.path.join(HERE, "index.html"))


@app.route("/latest.jpg")
def latest():
    with JPEG_LOCK:
        b = latest_jpeg["bytes"]
    if not b:
        return Response("no frame yet", status=503)
    return Response(b, mimetype="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@app.route("/api/state")
def api_state():
    with STATE_LOCK:
        s = dict(state)
    with CONFIG_LOCK:
        s["config"] = dict(config)
    return jsonify(s)


@app.route("/api/config", methods=["POST"])
def api_config():
    data = request.get_json(force=True, silent=True) or {}
    with CONFIG_LOCK:
        if "targets" in data and isinstance(data["targets"], list):
            config["targets"] = [str(t).strip() for t in data["targets"] if str(t).strip()][:8]
        if "interval" in data:
            try:
                config["interval"] = max(1.0, min(60.0, float(data["interval"])))
            except (TypeError, ValueError):
                pass
        if "running" in data:
            config["running"] = bool(data["running"])
        out = dict(config)
    return jsonify(out)


if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8000, threaded=True)
