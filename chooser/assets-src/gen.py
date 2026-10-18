"""Generate the chooser's Miami scene layers with Grok Imagine (via Cloudflare AI Gateway)."""
import base64, json, os, sys, time, urllib.request, urllib.error

ACCT = open(os.path.expanduser("~/.config/cloudflare/account-id")).read().strip()
TOK = open(os.path.expanduser("~/.config/cloudflare/claude-api-token")).read().strip()
HERE = os.path.dirname(os.path.abspath(__file__))
KEY = ("on a perfectly flat, pure chroma-key green (#00FF00) background with no gradient, "
       "no ground, no shadow, no text")
SIL = "a flat dark purple-navy (#24123a) silhouette with a thin hot-pink rim light"

JOBS = [
    ("sky", "16:9", None,
     "A breathtaking Miami Beach sunset sky, wide panorama. Smooth gradient from deep indigo and violet at the top, "
     "through vivid magenta and hot pink, to glowing peach and orange lower down. A huge soft glowing sun sits low, "
     "its centre about two thirds of the way down the frame, with warm haze around it. Thin wispy pink and lavender "
     "clouds. No land, no buildings, no water, no birds, no people, no text. Luminous painterly digital illustration."),
    ("water", "2:1", None,
     "Calm Biscayne Bay water at sunset seen from a low angle, filling the entire frame, reflecting hot pink, magenta "
     "and orange light, gentle horizontal ripples and small glittering sparkles. No horizon, no sky, no land, no "
     "boats, no people, no text. Luminous painterly digital illustration."),
    ("skyline", "2:1", None,
     f"Panoramic Miami downtown and Brickell skyline spanning the full width: modern glass towers and a few Art Deco "
     f"buildings of varied heights, drawn as {SIL}, with scattered small warm-yellow lit windows. The buildings stand "
     f"on the bottom edge of the image. Everything above and between the buildings is {KEY}. No water, no reflection."),
    ("palm1", "9:16", None,
     f"A single tall coconut palm tree with a gently curved trunk and a full lush crown of fronds, drawn as {SIL}. "
     f"The whole tree is visible from the base of the trunk at the bottom edge to the tips of the fronds, centred, "
     f"{KEY}."),
    ("palm2", "9:16", None,
     f"A single slender coconut palm tree leaning to the left with a long curved trunk and a wide crown of drooping "
     f"fronds, drawn as {SIL}. The whole tree is visible from the base of the trunk at the bottom edge to the tips "
     f"of the fronds, {KEY}."),
    ("bird_up", "1:1", None,
     f"A single seagull flying, seen from the side, wings raised high at the top of the upstroke, drawn as a flat "
     f"dark purple-navy (#24123a) silhouette, centred with a wide margin, {KEY}."),
    ("bird_down", "1:1", "bird_up",
     "Keep everything IDENTICAL: same seagull, same size, same position, same silhouette colour, same flat pure "
     "green (#00FF00) background. Only change the wings: now pushed down at the bottom of the downstroke."),
]


def run(name, aspect, src, prompt):
    out = os.path.join(HERE, name + ".jpg")
    if os.path.exists(out):
        print("skip", name); return
    inp = {"prompt": prompt, "aspect_ratio": aspect, "response_format": "b64_json"}
    if src:
        inp["image"] = {"url": "data:image/jpeg;base64," + base64.b64encode(open(os.path.join(HERE, src + ".jpg"), "rb").read()).decode(),
                        "type": "image_url"}
    req = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/accounts/{ACCT}/ai/run",
        data=json.dumps({"model": "xai/grok-imagine-image-2.0", "input": inp}).encode(),
        headers={"Authorization": "Bearer " + TOK, "cf-aig-gateway-id": "default", "Content-Type": "application/json"})
    t = time.time()
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=180).read())
    except urllib.error.HTTPError as e:
        sys.exit(f"{name}: HTTP {e.code} {e.read()[:300].decode(errors='replace')}")
    url = d.get("result", {}).get("result", {}).get("image")
    if not url:
        sys.exit(f"{name}: no image: {json.dumps(d)[:300]}")
    open(out, "wb").write(urllib.request.urlopen(url, timeout=60).read())
    print(f"saved {name} ({time.time() - t:.0f}s)")


STYLE = ("Illustrated in the style of a classic open-world crime game loading screen from the 2000s Vice City era: "
         "bold black ink outlines, cel-shaded comic-book shading, saturated sunset palette of hot pink, orange, "
         "teal and purple, dramatic low angle. No text, no letters, no logos, no watermark, no real people.")
CARD_JOBS = [
    ("card_jevcam", "16:9", None,
     "A sleek white security camera with a glowing red lens mounted on the corner of a pastel Art Deco hotel on "
     "Ocean Drive, Miami Beach, at sunset, scanning the street below, palm trees, neon signs glowing, a vintage "
     "convertible cruising past. " + STYLE),
    ("card_ppe", "16:9", None,
     "A confident construction worker in a bright yellow hard hat, high-visibility orange safety vest, safety "
     "glasses and work gloves, standing at the gate of a Miami high-rise construction site at sunset, tower cranes "
     "and the downtown skyline behind, palm trees. " + STYLE),
]

import sys
for job in (CARD_JOBS if "cards" in sys.argv else JOBS):
    run(*job)
