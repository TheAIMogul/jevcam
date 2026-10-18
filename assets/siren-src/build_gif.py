"""Build a transparent spinning-siren GIF from the Grok keyframes (chroma-key green)."""
import numpy as np
from PIL import Image

KEYS = ["base_left", "f2_centerleft", "f3_center", "f4_centerright", "f5_right", "f6_back"]
OUT_H = 256          # output height px
FRAME_MS = 55        # per frame
BLENDS = 1           # in-between frames per keyframe pair

def key_green(path):
    a = np.asarray(Image.open(path).convert("RGB")).astype(np.float32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    greenness = g - np.maximum(r, b)                 # high on the backdrop
    alpha = np.clip((110 - greenness) / (110 - 45), 0, 1)   # soft edge 45..110
    # despill: pull green down to the other channels where it dominates
    g2 = np.where(g > np.maximum(r, b), np.maximum(r, b), g)
    rgb = np.stack([r, g2, b], -1)
    return np.dstack([rgb, alpha * 255]).astype(np.uint8)

frames = [key_green(k + ".jpg") for k in KEYS]
# common crop = union of opaque bounding boxes
ys, xs = [], []
for f in frames:
    m = f[..., 3] > 128
    yy, xx = np.where(m)
    ys += [yy.min(), yy.max()]; xs += [xx.min(), xx.max()]
pad = 6
y0, y1 = max(min(ys) - pad, 0), min(max(ys) + pad, 1023)
x0, x1 = max(min(xs) - pad, 0), min(max(xs) + pad, 1023)
frames = [f[y0:y1 + 1, x0:x1 + 1] for f in frames]

# loop sequence with blended in-betweens
seq = []
n = len(frames)
for i in range(n):
    a, b = frames[i].astype(np.float32), frames[(i + 1) % n].astype(np.float32)
    seq.append(frames[i])
    for k in range(1, BLENDS + 1):
        t = k / (BLENDS + 1)
        seq.append((a * (1 - t) + b * t).astype(np.uint8))

h, w = seq[0].shape[:2]
out_w = round(w * OUT_H / h)

def to_p(arr):
    im = Image.fromarray(arr, "RGBA").resize((out_w, OUT_H), Image.LANCZOS)
    alpha = np.asarray(im)[..., 3]
    p = im.convert("RGB").quantize(colors=255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    pal = p.getpalette()[:255 * 3]
    pal += [0, 0, 0] * (256 - len(pal) // 3)
    p.putpalette(pal)
    px = np.asarray(p).copy()
    px[alpha < 128] = 255                         # transparent index
    q = Image.fromarray(px, "P"); q.putpalette(pal)
    return q

pframes = [to_p(f) for f in seq]
pframes[0].save("/Users/theaimogul/dev/jevcam/static/siren.gif", save_all=True,
                append_images=pframes[1:], duration=FRAME_MS, loop=0,
                disposal=2, transparency=255, optimize=False)

# preview: a few frames over the banner's red and black, to check edges
prev = Image.new("RGB", (out_w * 6, OUT_H * 2), "black")
for i, idx in enumerate(range(0, len(seq), 2)):
    fr = Image.fromarray(seq[idx], "RGBA").resize((out_w, OUT_H), Image.LANCZOS)
    bin_a = fr.getchannel("A").point(lambda v: 255 if v >= 128 else 0)
    red = Image.new("RGB", (out_w, OUT_H), (228, 53, 43)); red.paste(fr, (0, 0), bin_a)
    blk = Image.new("RGB", (out_w, OUT_H), (0, 0, 0)); blk.paste(fr, (0, 0), bin_a)
    prev.paste(red, (i * out_w, 0)); prev.paste(blk, (i * out_w, OUT_H))
prev.save("preview.png")
print(f"frames={len(seq)} size={out_w}x{OUT_H} loop={len(seq)*FRAME_MS}ms")
