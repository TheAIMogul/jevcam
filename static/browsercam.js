// Browser camera for JevCam: when JevCam runs off the Pi (camera_source "browser"), the page
// that loads this script opens the laptop webcam with getUserMedia and posts JPEG frames to
// JevCam's /api/frame. JevCam then streams and samples them exactly like the Pi camera, so
// the dashboard, PPE Monitor and Jev all see the webcam. Only the top-level page captures,
// so the chooser and an app inside it never open the camera twice. Does nothing on the Pi.
(() => {
  if (window.top !== window || window.__jevcamBrowserCam) return;
  window.__jevcamBrowserCam = true;
  const BASE = new URL(document.currentScript ? document.currentScript.src : location.href).origin;
  const FPS = 15, WIDTH = 1280, HEIGHT = 720, QUALITY = 0.7;

  const pill = document.createElement("div");
  pill.style.cssText = "position:fixed;right:12px;bottom:12px;z-index:2147483647;display:flex;align-items:center;gap:7px;" +
    "padding:7px 12px;border-radius:999px;background:rgba(10,12,14,.82);color:#e8eaec;font:600 12px system-ui,sans-serif;" +
    "box-shadow:0 4px 14px rgba(0,0,0,.35);pointer-events:none;backdrop-filter:blur(6px)";
  const dot = document.createElement("i");
  dot.style.cssText = "width:8px;height:8px;border-radius:50%;background:#f2b33d;display:inline-block";
  const text = document.createElement("span");
  pill.append(dot, text);
  const say = (msg, color) => { text.textContent = msg; dot.style.background = color; if (!pill.isConnected) document.body.appendChild(pill); };

  async function start() {
    let state;
    try { state = await (await fetch(BASE + "/api/state", { cache: "no-store" })).json(); } catch { return; }
    if (state.camera_source !== "browser") return;
    say("Browser camera: asking for access…", "#f2b33d");
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: WIDTH }, height: { ideal: HEIGHT }, frameRate: { ideal: 30 } }, audio: false });
    } catch (e) {
      say("Browser camera blocked: allow camera access for this page", "#ff5a5f");
      return;
    }
    const video = document.createElement("video");
    video.muted = true; video.playsInline = true; video.srcObject = stream;
    await video.play();
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d");
    let busy = false, sent = 0, t0 = performance.now(), failing = false;
    const label = stream.getVideoTracks()[0]?.label || "webcam";
    say(`Browser camera: ${label}`, "#3fcf8e");
    setInterval(() => {
      if (busy || video.readyState < 2) return;
      const w = video.videoWidth, h = video.videoHeight;
      if (!w || !h) return;
      if (canvas.width !== w) { canvas.width = w; canvas.height = h; }
      ctx.drawImage(video, 0, 0, w, h);
      busy = true;
      canvas.toBlob(async (blob) => {
        try {
          const r = await fetch(BASE + "/api/frame", { method: "POST", headers: { "Content-Type": "image/jpeg" }, body: blob });
          if (!r.ok) throw new Error(r.status);
          sent++;
          if (failing) { failing = false; say(`Browser camera: ${label}`, "#3fcf8e"); }
        } catch {
          if (!failing) { failing = true; say("Browser camera: can't reach JevCam on :8000", "#ff5a5f"); }
        } finally { busy = false; }
        const secs = (performance.now() - t0) / 1000;
        if (secs > 2 && !failing) { text.textContent = `Browser camera · ${w}×${h} · ${(sent / secs).toFixed(0)} fps`; sent = 0; t0 = performance.now(); }
      }, "image/jpeg", QUALITY);
    }, 1000 / FPS);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
