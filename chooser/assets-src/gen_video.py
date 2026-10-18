"""Animate a card image into a short clip with Grok Imagine video (via Cloudflare AI Gateway)."""
import base64, json, os, sys, time, urllib.request, urllib.error
from datetime import timedelta
from google.cloud import storage
from google.oauth2 import service_account

# This gateway account has Zero Data Retention, so xAI won't host the result: it uploads
# the video to a signed URL we give it. Use a temporary path in an existing bucket.
GCP_KEY = os.path.expanduser("~/.config/gcloud/claude-code-dev-allpurpose-key.json")
BUCKET, PREFIX = "gen-lang-client-0251154849_cloudbuild", "tmp/jevcam"
_creds = service_account.Credentials.from_service_account_file(GCP_KEY)
_bucket = storage.Client(credentials=_creds, project=_creds.project_id).bucket(BUCKET)

ACCT = open(os.path.expanduser("~/.config/cloudflare/account-id")).read().strip()
TOK = open(os.path.expanduser("~/.config/cloudflare/claude-api-token")).read().strip()
HERE = os.path.dirname(os.path.abspath(__file__))
LOOP = (" Smooth seamless looping motion, the shot is locked off with no camera move, no cuts, no zoom, "
        "and the last frame matches the first. Keep the bold ink outlines and cel-shaded comic style. "
        "No text, no letters, no logos.")
PROMPTS = {
    "card_jevcam": "The security camera slowly pans a little left and right as it scans the street, its red lens "
                   "pulsing and glowing; palm fronds sway in the warm breeze; pink and purple neon signs flicker "
                   "softly; sunset clouds drift slowly." + LOOP,
    "card_ppe": "The construction worker breathes and slowly turns his head to scan the site, then back; the "
                "tower crane arms rotate slowly; palm fronds sway in the breeze; sunset clouds drift and the sun "
                "glows." + LOOP,
}


def run(name):
    out = os.path.join(HERE, name + ".mp4")
    if os.path.exists(out):
        print("skip", name); return
    img = "data:image/jpeg;base64," + base64.b64encode(open(os.path.join(HERE, name + ".jpg"), "rb").read()).decode()
    blob = _bucket.blob(f"{PREFIX}/{name}.mp4")
    upload = blob.generate_signed_url(version="v4", expiration=timedelta(hours=1), method="PUT")
    body = {"model": "xai/grok-imagine-video",
            "input": {"prompt": PROMPTS[name], "image": {"url": img}, "duration": 6, "aspect_ratio": "16:9",
                      "output": {"upload_url": upload}}}
    req = urllib.request.Request(f"https://api.cloudflare.com/client/v4/accounts/{ACCT}/ai/run",
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + TOK, "cf-aig-gateway-id": "default", "Content-Type": "application/json"})
    t = time.time()
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=580).read())
    except urllib.error.HTTPError as e:
        sys.exit(f"{name}: HTTP {e.code} {e.read()[:400].decode(errors='replace')}")
    res = d.get("result", {})
    print(f"{name}: {time.time() - t:.0f}s state={res.get('state')} keys={list((res.get('result') or {}).keys())}")
    print(f"{name}: response {json.dumps(d)[:300]}")
    for _ in range(60):                       # the upload can land a moment after the call returns
        if blob.exists():
            break
        time.sleep(5)
    else:
        sys.exit(f"{name}: video never arrived in gs://{BUCKET}/{PREFIX}/")
    blob.download_to_filename(out)
    blob.delete()                             # temporary: remove from the bucket once downloaded
    print(f"saved {out} {os.path.getsize(out)//1024} KB (bucket copy deleted)")


for n in sys.argv[1:] or PROMPTS:
    run(n)
