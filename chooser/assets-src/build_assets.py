"""Turn the Grok layers into web assets: key out the green, crop, size, export."""
import os
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "assets")


def keyed(name):
    a = np.asarray(Image.open(os.path.join(HERE, name + ".jpg")).convert("RGB")).astype(np.float32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    greenness = g - np.maximum(r, b)
    alpha = np.clip((110 - greenness) / (110 - 40), 0, 1)
    g2 = np.where(g > np.maximum(r, b), np.maximum(r, b), g)          # despill
    return Image.fromarray(np.dstack([r, g2, b, alpha * 255]).astype(np.uint8), "RGBA")


def crop(im, pad=4):
    x0, y0, x1, y1 = im.getchannel("A").point(lambda v: 255 if v > 24 else 0).getbbox()
    return im.crop((max(0, x0 - pad), max(0, y0 - pad), min(im.width, x1 + pad), min(im.height, y1 + pad)))


def fit_h(im, h):
    return im.resize((round(im.width * h / im.height), h), Image.LANCZOS)


def save(im, name, **kw):
    path = os.path.join(OUT, name)
    im.save(path, **kw)
    print(f"{name:16s} {im.size}  {os.path.getsize(path) // 1024} KB")


# far layers: plain photos
save(Image.open(os.path.join(HERE, "sky.jpg")).convert("RGB"), "sky.jpg", quality=86)
w = Image.open(os.path.join(HERE, "water.jpg")).convert("RGB")      # itself + mirror: repeats with no seam
wt = Image.new("RGB", (w.width * 2, w.height)); wt.paste(w, (0, 0)); wt.paste(w.transpose(Image.FLIP_LEFT_RIGHT), (w.width, 0))
save(wt, "water.jpg", quality=84)

# skyline: tight crop, then itself + its mirror so it can repeat seamlessly across any width
sk = fit_h(crop(keyed("skyline"), pad=0), 420)
tile = Image.new("RGBA", (sk.width * 2, sk.height))
tile.paste(sk, (0, 0)); tile.paste(sk.transpose(Image.FLIP_LEFT_RIGHT), (sk.width, 0))
save(tile, "skyline.webp", quality=88, method=6)

# palms: cropped so the trunk base is the bottom edge (it sways from there)
for n in ("palm1", "palm2"):
    save(fit_h(crop(keyed(n), pad=2), 1000), f"{n}.webp", quality=88, method=6)

# bird: both wing frames on one shared box, side by side as a 2-frame sprite sheet
up, down = keyed("bird_up"), keyed("bird_down")
boxes = [im.getchannel("A").point(lambda v: 255 if v > 24 else 0).getbbox() for im in (up, down)]
x0, y0 = min(b[0] for b in boxes) - 6, min(b[1] for b in boxes) - 6
x1, y1 = max(b[2] for b in boxes) + 6, max(b[3] for b in boxes) + 6
side = max(x1 - x0, y1 - y0); cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
box = (cx - side // 2, cy - side // 2, cx + side // 2, cy + side // 2)
F = 160
sheet = Image.new("RGBA", (F * 2, F))
for i, im in enumerate((up, down)):
    sheet.paste(im.crop(box).resize((F, F), Image.LANCZOS), (i * F, 0))
save(sheet, "bird.webp", quality=90, method=6)

# preview of the keyed sprites over the sunset, to check the edges
bg = Image.open(os.path.join(HERE, "sky.jpg")).convert("RGBA").resize((1280, 720))
bg.alpha_composite(fit_h(tile, 200), (0, 520))
bg.alpha_composite(fit_h(Image.open(os.path.join(OUT, "palm1.webp")), 520), (40, 200))
bg.alpha_composite(fit_h(Image.open(os.path.join(OUT, "palm2.webp")), 470), (1000, 250))
bg.alpha_composite(sheet, (560, 120))
bg.convert("RGB").save(os.path.join(HERE, "preview.jpg"), quality=85)
