#!/usr/bin/env python3
"""JevCam - live IR-camera object detection on a Raspberry Pi, powered by TypeSafe Jev.

The camera streams continuously (MJPEG, ~20 fps) and is broadcast as-is to the
web UI. A separate detection thread samples the newest frame on a fixed interval
(default once per second), sends it to Jev with a user-defined, natural-language
list of target objects, and publishes the results; the browser draws the boxes
over the live video.

The target list ("what it should see") is editable live from the web UI.
"""
import base64
import hashlib
import json
import os
import subprocess
import threading
import time
from collections import deque
from datetime import datetime

import requests
from flask import Flask, Response, jsonify, request, send_file

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TYPESAFE_KEY_FILE = os.path.expanduser("~/.config/typesafe/api-key")
TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-preview"            # jev-1.14.0 - supports box questions
STREAM_SIZE = (1280, 720)        # broadcast + sample size (within Jev's 1568 limit)
STREAM_FPS = 20                  # broadcast frame rate
STREAM_QUALITY = 70              # MJPEG quality
STALL_SECS = 3.0                 # no frames this long -> restart the camera stream
PRESENCE_DRAW_THRESHOLD = 0.5    # draw a box only if presence noul >= this
BOX_CONF_FLOOR = 0.15            # and box confidence >= this

BOX_COLORS = [
    (0, 200, 255), (0, 220, 120), (255, 170, 0), (255, 90, 160),
    (150, 120, 255), (255, 80, 80), (60, 210, 210), (200, 200, 40),
]

# Mutable runtime config, protected by CONFIG_LOCK
config = {
    "interval": 1.0,             # seconds between Jev samples
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
    "frame_seq": 0,        # frames sampled for Jev (drives the FRAME light)
    "alert": {"active": False, "presence": None, "phrase": "", "target": "", "enabled": True},
}
STATE_LOCK = threading.Lock()

# Timeline for the dashboard: recent presence samples, and an event log of
# changes (detected / cleared, alarm on / off, camera and Jev errors).
START_MS = int(time.time() * 1000)
HISTORY = deque(maxlen=240)        # one sample per Jev response, ~4 min at 1/s
EVENTS = deque(maxlen=150)
HITS = {}                          # label -> times it went from absent to present
TIMELINE_LOCK = threading.Lock()
_prev = {"present": {}, "alert": False, "camera_ok": None, "error": None, "alarms": 0}


def _event(kind, label, value=None):
    EVENTS.append({"t": int(time.time() * 1000), "kind": kind, "label": label, "value": value})


def record_sample(detections, alert):
    with TIMELINE_LOCK:
        HISTORY.append({"t": int(time.time() * 1000),
                        "p": {d["label"]: d["presence"] for d in detections},
                        "a": alert.get("presence")})
        labels = {d["label"] for d in detections}
        for gone in set(_prev["present"]) - labels:      # target removed from the list
            del _prev["present"][gone]
        for d in detections:
            was = _prev["present"].get(d["label"], False)
            if d["present"] and not was:
                _event("detected", d["label"], d["presence"])
                HITS[d["label"]] = HITS.get(d["label"], 0) + 1
            elif was and not d["present"]:
                _event("cleared", d["label"], d["presence"])
            _prev["present"][d["label"]] = d["present"]
        if alert["active"] != _prev["alert"]:
            _event("alarm" if alert["active"] else "alarm_end", alert.get("phrase") or "alarm",
                   alert.get("presence"))
            if alert["active"]:
                _prev["alarms"] += 1
            _prev["alert"] = alert["active"]
        _prev["error"] = None


def record_status(camera_ok=None, error=None):
    with TIMELINE_LOCK:
        if camera_ok is not None and camera_ok != _prev["camera_ok"]:
            # only real drop-outs: offline after it was live, online after a logged drop-out
            if not camera_ok and _prev["camera_ok"] is True:
                _event("camera_off", "camera")
                _prev["camera_dropped"] = True
            elif camera_ok and _prev.get("camera_dropped"):
                _event("camera_on", "camera")
                _prev["camera_dropped"] = False
            _prev["camera_ok"] = camera_ok
        if error and error != _prev["error"]:
            _event("error", error[:120])
            _prev["error"] = error


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
# One long-lived rpicam-vid process streams MJPEG on stdout (hardware JPEG
# encoder). A flaky ribbon cable can make the sensor drop out mid-run ("Camera
# frontend has timed out"); a watchdog kills the process when frames stop, and
# the reader loop respawns it, so the stream self-recovers instead of hanging.


class Camera:
    def __init__(self):
        self.cond = threading.Condition()
        self.frame = None          # latest JPEG bytes
        self.seq = 0
        self.last_t = 0.0
        self.spawn_t = 0.0
        self.proc = None
        self.error = "starting camera"
        self._times = deque(maxlen=120)

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        threading.Thread(target=self._watchdog, daemon=True).start()

    def _cmd(self):
        w, h = STREAM_SIZE
        return [
            "rpicam-vid", "-t", "0", "-n", "--codec", "mjpeg",
            "--quality", str(STREAM_QUALITY), "--framerate", str(STREAM_FPS),
            "--width", str(w), "--height", str(h),
            "--hflip", "--vflip",                    # matches physical mount
            "--flush", "-o", "-",
        ]

    def _run(self):
        while True:
            try:
                self.spawn_t = time.time()
                self.proc = subprocess.Popen(self._cmd(), stdout=subprocess.PIPE,
                                             stderr=subprocess.DEVNULL, bufsize=0)
                self._read(self.proc.stdout)
            except Exception as e:  # noqa: BLE001
                self.error = f"camera stream failed: {e}"
            finally:
                p = self.proc
                if p and p.poll() is None:
                    p.kill()
                    p.wait()
            if not self.error:
                self.error = ("camera stream stopped - check the ribbon cable is "
                              "fully seated (sensor not responding)")
            time.sleep(1.0)

    def _read(self, f):
        buf = b""
        while True:
            chunk = f.read(65536)
            if not chunk:
                return
            buf += chunk
            while True:
                s = buf.find(b"\xff\xd8")
                if s < 0:
                    buf = buf[-1:]
                    break
                e = buf.find(b"\xff\xd9", s + 2)
                if e < 0:
                    buf = buf[s:]
                    break
                self._publish(buf[s:e + 2])
                buf = buf[e + 2:]

    def _publish(self, jpg):
        now = time.time()
        with self.cond:
            self.frame = jpg
            self.seq += 1
            self.last_t = now
            self.error = None
            self._times.append(now)
            self.cond.notify_all()

    def _watchdog(self):
        while True:
            time.sleep(1.0)
            p = self.proc
            if not p or p.poll() is not None:
                continue
            # allow a few seconds for the pipeline to start before the first frame
            ref = self.last_t if self.last_t > self.spawn_t else self.spawn_t + 5.0
            if time.time() - ref > STALL_SECS:
                self.error = ("camera stalled - restarting stream (check the ribbon "
                              "cable is fully seated)")
                p.kill()

    def ok(self):
        return self.frame is not None and time.time() - self.last_t < STALL_SECS

    def fps(self):
        now = time.time()
        return round(sum(1 for t in list(self._times) if now - t <= 2.0) / 2.0, 1)

    def latest(self):
        with self.cond:
            return self.seq, self.frame

    def wait_frame(self, last_seq, timeout=2.0):
        with self.cond:
            self.cond.wait_for(lambda: self.seq != last_seq, timeout=timeout)
            return self.seq, self.frame


cam = Camera()

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
# Detection loop (samples the live stream)
# ---------------------------------------------------------------------------
def detect_loop():
    while True:
        with CONFIG_LOCK:
            interval = config["interval"]
            targets = list(config["targets"])
            running = config["running"]
            alert_target = config["alert_target"].strip()
            alert_phrase = config["alert_phrase"]
            alert_enabled = config["alert_enabled"]
        idle_alert = {"active": False, "presence": None, "phrase": alert_phrase,
                      "target": alert_target, "enabled": alert_enabled}
        if not running or (not targets and not alert_target):
            with STATE_LOCK:
                state["alert"] = idle_alert
            time.sleep(0.5)
            continue
        _, jpg = cam.latest()
        if jpg is None or not cam.ok():
            record_status(camera_ok=False)
            with STATE_LOCK:
                state["alert"] = idle_alert
            time.sleep(0.5)
            continue
        record_status(camera_ok=True)
        t0 = time.time()
        with STATE_LOCK:
            state["frame_seq"] += 1
        err = None
        try:
            resp = call_jev(base64.b64encode(jpg).decode(), targets, alert_target or None)
            detections = parse_answers(resp, targets)
            alert = dict(idle_alert)
            if alert_target:
                ap = resp.get("answers", {}).get("alert_present", {}).get("noul")
                alert["presence"] = ap
                alert["active"] = bool(alert_enabled and ap is not None
                                       and ap >= PRESENCE_DRAW_THRESHOLD)
        except Exception as e:  # noqa: BLE001
            err = str(e)
        latency = int((time.time() - t0) * 1000)
        if err is None:
            record_sample(detections, alert)
        else:
            record_status(error=err)
        with STATE_LOCK:
            state["frames"] += 1
            state["latency_ms"] = latency
            state["error"] = err
            if err is None:
                state.update({
                    "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "detections": detections, "alert": alert,
                    "jev_count": state["jev_count"] + 1,
                })
        time.sleep(max(0.0, interval - (time.time() - t0)))


# ---------------------------------------------------------------------------
# Web
# ---------------------------------------------------------------------------
app = Flask(__name__)
HERE = os.path.dirname(os.path.abspath(__file__))


@app.route("/")
def index():
    return send_file(os.path.join(HERE, "index.html"))


@app.route("/stream.mjpg")
def stream():
    def gen():
        seq = -1
        while True:
            s, f = cam.wait_frame(seq, timeout=2.0)
            if f is None or s == seq:
                continue
            seq = s
            yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                   + str(len(f)).encode() + b"\r\n\r\n" + f + b"\r\n")
    return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame",
                    headers={"Cache-Control": "no-store"})


@app.route("/latest.jpg")
def latest():
    _, b = cam.latest()
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
    s["stream_fps"] = cam.fps()
    s["camera_ok"] = cam.ok()
    if not s["camera_ok"]:
        s["error"] = cam.error or "camera offline - check the ribbon cable"
    return jsonify(s)


@app.route("/api/timeline")
def api_timeline():
    with TIMELINE_LOCK:
        return jsonify({"samples": list(HISTORY), "events": list(EVENTS)[-80:],
                        "hits": dict(HITS), "alarms": _prev["alarms"], "started": START_MS})


@app.route("/api/config", methods=["POST"])
def api_config():
    data = request.get_json(force=True, silent=True) or {}
    with CONFIG_LOCK:
        if "targets" in data and isinstance(data["targets"], list):
            config["targets"] = [str(t).strip() for t in data["targets"] if str(t).strip()][:8]
        if "interval" in data:
            try:
                config["interval"] = max(0.5, min(60.0, float(data["interval"])))
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
    cam.start()
    threading.Thread(target=detect_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8000, threaded=True)
