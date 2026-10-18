#!/usr/bin/env python3
"""JevCam - live IR-camera object detection on a Raspberry Pi, powered by TypeSafe Jev.

Captures frames from the Pi Camera on a fixed interval, sends each frame to Jev
with a user-defined, natural-language list of target objects, and serves a live
web UI that shows the annotated frame plus per-target presence probability.

The target list ("what it should see") is editable live from the web UI.
"""
import base64
import hashlib
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
    # Alarm: when alert_target is seen, the UI flashes red + speaks alert_phrase
    "alert_target": "a person wearing a New York (NY) shirt",
    "alert_phrase": "New Yorker Alert",
    "alert_enabled": True,
}
CONFIG_LOCK = threading.Lock()

# Latest result, protected by STATE_LOCK
state = {
    "ts": None,
    "detections": [],      # [{label, present, presence, box, box_conf, drawn}]
    "latency_ms": None,
    "error": None,
    "frames": 0,
    "jev_count": 0,        # successful Jev responses (drives the JEV light)
    "frame_seq": 0,        # new annotated frames stored (drives the FRAME light)
    "alert": {"active": False, "presence": None, "phrase": "", "target": "", "enabled": True},
}
STATE_LOCK = threading.Lock()

latest_jpeg = {"bytes": None}   # annotated JPEG bytes
JPEG_LOCK = threading.Lock()


def load_key():
    with open(TYPESAFE_KEY_FILE) as f:
        return f.read().strip()


TYPESAFE_KEY = load_key()

# ---------------------------------------------------------------------------
# ElevenLabs TTS (yelling radio-DJ alarm voice)
# ---------------------------------------------------------------------------
ELEVEN_KEY_FILE = os.path.expanduser("~/.config/elevenlabs/theberkleytwins-api-key")
ELEVEN_VOICE = "FmJ4FDkdrYIKzBTruTkV"   # David Hertal - Radio DJ
ELEVEN_MODEL = "eleven_v3"              # expressive; supports [shouting] tag
TTS_DIR = "/tmp/jevcam_tts"
os.makedirs(TTS_DIR, exist_ok=True)
try:
    with open(ELEVEN_KEY_FILE) as f:
        ELEVEN_KEY = f.read().strip()
except OSError:
    ELEVEN_KEY = ""
TTS_LOCK = threading.Lock()


def tts_path(phrase):
    h = hashlib.sha1(f"{ELEVEN_VOICE}|{ELEVEN_MODEL}|{phrase}".encode()).hexdigest()[:16]
    return os.path.join(TTS_DIR, f"{h}.mp3")


def generate_tts(phrase):
    """Generate (and cache) a yelling radio-DJ MP3 for phrase. Returns path or None."""
    phrase = (phrase or "").strip()
    if not phrase or not ELEVEN_KEY:
        return None
    path = tts_path(phrase)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    with TTS_LOCK:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return path
        up = phrase.upper()
        body = {
            "text": f"[shouting] {up}! {up}!",
            "model_id": ELEVEN_MODEL,
            "voice_settings": {"stability": 0.3, "similarity_boost": 0.8, "style": 0.9},
        }
        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVEN_VOICE}"
            "?output_format=mp3_44100_128",
            headers={"xi-api-key": ELEVEN_KEY, "Content-Type": "application/json"},
            data=json.dumps(body), timeout=30,
        )
        r.raise_for_status()
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(r.content)
        os.replace(tmp, path)
        return path

# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------
# A flaky ribbon cable can make the sensor drop out mid-run ("Camera frontend
# has timed out"), which would block an in-process picamera2 capture forever and
# freeze the loop. To stay resilient we capture via a short-lived rpicam-still
# process with a hard timeout: a fresh pipeline each frame self-recovers the
# instant the sensor comes back, and a stuck capture is killed, not hung.
import subprocess  # noqa: E402

CAP_PATH = "/tmp/jevcam_cap.jpg"
CAP_TIMEOUT = 6           # seconds; kill a stuck capture (fast recovery on cable blips)
RPICAM = "rpicam-still"


def capture_rgb():
    """Capture a frame to disk via rpicam-still and return it as a PIL RGB image.

    Raises RuntimeError on timeout/failure (loop treats it as a transient error
    and retries next cycle) instead of blocking forever.
    """
    cmd = [
        RPICAM, "--nopreview", "-n", "-t", "900",
        "--hflip", "--vflip",                    # matches physical mount
        "--width", str(CAP_SIZE[0]), "--height", str(CAP_SIZE[1]),
        "-o", CAP_PATH,
    ]
    try:
        subprocess.run(cmd, check=True, timeout=CAP_TIMEOUT,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.TimeoutExpired:
        raise RuntimeError("camera capture timed out - check the ribbon cable is "
                           "fully seated (sensor not responding)")
    except subprocess.CalledProcessError as e:
        msg = (e.stderr or b"").decode(errors="replace")[-200:].strip()
        raise RuntimeError(f"camera capture failed: {msg or 'rpicam-still error'}")
    return Image.open(CAP_PATH).convert("RGB")


# ---------------------------------------------------------------------------
# Jev
# ---------------------------------------------------------------------------
def build_request(img_b64, targets, alert_target=None):
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
    if alert_target:
        questions["alert_present"] = {
            "type": "noul",
            "instructions": f"Is there {alert_target} clearly visible in `photo`?",
        }
    return {
        "model": MODEL,
        "state": {"photo": "frame"},
        "assets": {"frame": {"type": "image", "format": "jpg", "data_base64": img_b64}},
        "questions": questions,
    }


def call_jev(img_b64, targets, alert_target=None):
    body = build_request(img_b64, targets, alert_target)
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
            alert_target = config["alert_target"].strip()
            alert_phrase = config["alert_phrase"]
            alert_enabled = config["alert_enabled"]
        if not running or (not targets and not alert_target):
            _store_plain(targets)
            with STATE_LOCK:
                state["alert"] = {"active": False, "presence": None,
                                  "phrase": alert_phrase, "target": alert_target,
                                  "enabled": alert_enabled}
            time.sleep(min(interval, 2.0))
            continue
        t0 = time.time()
        err = None
        jev_ok = False
        frame_new = False
        detections = []
        alert = {"active": False, "presence": None, "phrase": alert_phrase,
                 "target": alert_target, "enabled": alert_enabled}
        try:
            img = capture_rgb()
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=88)
            img_b64 = base64.b64encode(buf.getvalue()).decode()
            resp = call_jev(img_b64, targets, alert_target or None)
            jev_ok = True
            detections = parse_answers(resp, targets)
            if alert_target:
                ap = resp.get("answers", {}).get("alert_present", {}).get("noul")
                alert["presence"] = ap
                alert["active"] = bool(alert_enabled and ap is not None
                                       and ap >= PRESENCE_DRAW_THRESHOLD)
        except Exception as e:  # noqa: BLE001
            err = str(e)
        latency = int((time.time()-t0)*1000)
        ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            img_for_draw = img if not err else capture_rgb()
            jpeg = annotate(img_for_draw, detections, ts_str)
            with JPEG_LOCK:
                latest_jpeg["bytes"] = jpeg
            frame_new = True
        except Exception as e:  # noqa: BLE001
            err = err or str(e)
        with STATE_LOCK:
            state.update({
                "ts": ts_str, "detections": detections,
                "latency_ms": latency, "error": err,
                "frames": state["frames"] + 1, "alert": alert,
                "jev_count": state["jev_count"] + (1 if jev_ok else 0),
                "frame_seq": state["frame_seq"] + (1 if frame_new else 0),
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
        with STATE_LOCK:
            state["frame_seq"] += 1
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


@app.route("/api/alert_audio")
def api_alert_audio():
    phrase = request.args.get("phrase")
    if phrase is None:
        with CONFIG_LOCK:
            phrase = config["alert_phrase"]
    try:
        path = generate_tts(phrase)
    except Exception as e:  # noqa: BLE001
        return Response(f"tts error: {e}", status=502)
    if not path:
        return Response("no tts (missing phrase or key)", status=404)
    return send_file(path, mimetype="audio/mpeg",
                     max_age=86400, conditional=True)


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
        if "alert_target" in data:
            config["alert_target"] = str(data["alert_target"]).strip()[:140]
        if "alert_phrase" in data:
            config["alert_phrase"] = str(data["alert_phrase"]).strip()[:120]
        if "alert_enabled" in data:
            config["alert_enabled"] = bool(data["alert_enabled"])
        out = dict(config)
    return jsonify(out)


if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8000, threaded=True)
