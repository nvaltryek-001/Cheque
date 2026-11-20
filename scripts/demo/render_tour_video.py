"""Render the README product-tour video (docs/demo/ChequeVerify-tour.mp4) from the real
application screenshots in docs/screenshots/.

    python scripts/demo/render_tour_video.py
    ffmpeg -framerate 30 -i $TMP/cv_tour_frames/%05d.png -c:v libx264 -crf 22 -pix_fmt yuv420p docs/demo/ChequeVerify-tour.mp4

Needs numpy + Pillow and the Inter font. Every app screen in the video is a real screenshot;
only the title, workflow and results cards are drawn here (numbers from reports/final/MODEL_EVALUATION.md).
"""
import glob, math, os, tempfile
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "docs" / "screenshots"
OUT = os.environ.get("DEMO_FRAMES_DIR", os.path.join(tempfile.gettempdir(), "cv_tour_frames"))
os.makedirs(OUT, exist_ok=True)
for f in glob.glob(f"{OUT}/*.png"):
    os.remove(f)
W, H, FPS = 1280, 720, 30

INK = (11, 16, 36); INK2 = (20, 28, 60); ON_INK = (232, 236, 251); MUTED = (140, 152, 190)
BRAND = (55, 83, 240); SCAN = (34, 211, 192); GOOD = (63, 182, 139); WARN = (240, 180, 64); BAD = (229, 101, 90)
FD = "/usr/share/fonts/opentype/inter/"
F = lambda s, w="SemiBold": ImageFont.truetype(f"{FD}Inter-{w}.otf", s)
FX = lambda s, w="Bold": ImageFont.truetype(f"{FD}InterDisplay-{w}.otf", s)

def ease(t):
    t = max(0.0, min(1.0, t)); return 1 - (1 - t) ** 3
def ease_io(t):
    t = max(0.0, min(1.0, t)); return t * t * (3 - 2 * t)
def mix(a, b, t):
    return tuple(int(x + (y - x) * t) for x, y in zip(a, b))

def make_bg():
    y = np.linspace(0, 1, H)[:, None]; x = np.linspace(0, 1, W)[None, :]
    base = np.zeros((H, W, 3), np.float32)
    for i in range(3):
        base[..., i] = INK[i] * (1 - 0.4 * y)
    g1 = np.exp(-(((x - 0.12) ** 2) / 0.06 + ((y - 0.15) ** 2) / 0.09))
    g2 = np.exp(-(((x - 0.92) ** 2) / 0.05 + ((y - 0.9) ** 2) / 0.07))
    base += g1[..., None] * np.array(BRAND) * 0.45 + g2[..., None] * np.array(SCAN) * 0.22
    img = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8)).convert("RGBA")
    grid = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(grid)
    for gx in range(0, W, 48):
        d.line([(gx, 0), (gx, H)], fill=(150, 170, 255, 9))
    for gy in range(0, H, 48):
        d.line([(0, gy), (W, gy)], fill=(150, 170, 255, 9))
    img.alpha_composite(grid)
    return img
BG = make_bg()

n_frame = 0
def emit(img):
    global n_frame
    img.convert("RGB").save(f"{OUT}/{n_frame:05d}.png", compress_level=1); n_frame += 1

def fade(img, a, b):
    k = min(ease(a), ease(b))
    return img if k >= 1 else Image.blend(BG, img, k)

def logo(d, x, y, s=1.0):
    d.rounded_rectangle((x, y, x + 40 * s, y + 40 * s), int(10 * s), fill=BRAND)
    d.line([(x + 11 * s, y + 21 * s), (x + 18 * s, y + 28 * s), (x + 30 * s, y + 13 * s)], fill=(255, 255, 255), width=int(4 * s))

def chip(d, x, y, text, col, font=None, fill=None):
    font = font or F(15, "Bold")
    w = d.textlength(text, font=font) + 30
    d.rounded_rectangle((x, y, x + w, y + 34), 17, fill=fill or (24, 32, 70), outline=col, width=2)
    d.text((x + w / 2, y + 17), text, font=font, fill=ON_INK, anchor="mm")
    return w

# ---------- intro ----------
def intro(sec=4.0):
    for k in range(int(sec * FPS)):
        t = k / FPS; img = BG.copy(); d = ImageDraw.Draw(img)
        e = ease((t - 0.1) / 0.8)
        logo(d, W / 2 - 230, 196 - 10 * (1 - e), 1.4)
        d.text((W / 2 - 165, 224 - 10 * (1 - e)), "ChequeVerify AI", font=FX(60), fill=mix(INK, ON_INK, e), anchor="lm")
        e2 = ease((t - 0.5) / 0.8)
        for i, line in enumerate(["Read the cheque.", "Compare the signature.", "Keep the evidence."]):
            ei = ease((t - 0.6 - i * 0.25) / 0.6)
            d.text((W / 2, 310 + i * 44), line, font=FX(34, "SemiBold"), fill=mix(INK, [ON_INK, SCAN, ON_INK][i], ei), anchor="mm")
        e3 = ease((t - 1.6) / 0.7)
        tags = ["10-field OCR", "E-13B MICR", "Signature verification", "Audit trail", "Flask + React"]
        tw = sum(F(15, "Bold").getlength(s) + 30 for s in tags) + 12 * 4
        x = W / 2 - tw / 2
        for s in tags:
            x += chip(d, x, 470 + 12 * (1 - e3), s, mix(INK, BRAND, e3), fill=mix(INK, (24, 32, 70), e3)) + 12
        # scanning line
        sx = int((t * 500) % (W + 300) - 150)
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(ov).rectangle((sx, 0, sx + 3, H), fill=SCAN + (60,))
        img.alpha_composite(ov); d = ImageDraw.Draw(img)
        emit(fade(img, t / 0.35, (sec - t) / 0.35))

# ---------- workflow ----------
STEPS = [("Upload", "file checked by\ncontent & size"), ("Quality", "blur, skew,\nlighting"), ("OCR + MICR", "Tesseract / Paddle\n+ E-13B reader"),
         ("10 fields", "confidence &\nreview status"), ("Validate", "IFSC, amount\nwords vs figures"), ("Signature", "auto crop,\ncompare to specimens"),
         ("Decision", "MATCH / REVIEW\n/ MISMATCH")]
def workflow(sec=6.0):
    for k in range(int(sec * FPS)):
        t = k / FPS; img = BG.copy(); d = ImageDraw.Draw(img)
        d.text((W / 2, 110), "How a cheque is verified", font=FX(44), fill=ON_INK, anchor="mm")
        d.text((W / 2, 156), "Every step is written to the audit log  •  a person makes the final call", font=F(19, "Medium"), fill=MUTED, anchor="mm")
        bw, gap = 152, 18; x0 = (W - (7 * bw + 6 * gap)) / 2; y0 = 250
        for i, (ti, sub) in enumerate(STEPS):
            e = ease((t - 0.3 - i * 0.4) / 0.5)
            x = x0 + i * (bw + gap); y = y0 + 26 * (1 - e)
            col = [BRAND, BRAND, SCAN, SCAN, BRAND, SCAN, GOOD][i]
            d.rounded_rectangle((x, y, x + bw, y + 210), 16, fill=mix(INK, (18, 26, 58), e), outline=mix(INK, col, e), width=2)
            d.ellipse((x + bw / 2 - 30, y + 22, x + bw / 2 + 30, y + 82), fill=mix(INK, col, e))
            d.text((x + bw / 2, y + 52), str(i + 1), font=FX(28), fill=mix(INK, (255, 255, 255), e), anchor="mm")
            d.text((x + bw / 2, y + 112), ti, font=F(19, "Bold"), fill=mix(INK, ON_INK, e), anchor="mm")
            for j, ln in enumerate(sub.split("\n")):
                d.text((x + bw / 2, y + 146 + j * 22), ln, font=F(14, "Medium"), fill=mix(INK, MUTED, e), anchor="mm")
            if i < 6:
                ea = ease((t - 0.55 - i * 0.4) / 0.35)
                ax = x + bw + 2; ay = y0 + 105
                d.line([(ax, ay), (ax + (gap - 4) * ea, ay)], fill=SCAN, width=3)
        pt = (t - 3.3) / 2.0
        if 0 <= pt <= 1:
            px = x0 + bw / 2 + ease_io(pt) * 6 * (bw + gap)
            d.ellipse((px - 9, y0 - 22, px + 9, y0 - 4), fill=WARN)
        e = ease((t - 3.6) / 0.6)
        x = W / 2 - 330
        for s, c in [("MATCH", GOOD), ("REVIEW", WARN), ("MISMATCH", BAD)]:
            d.rounded_rectangle((x, 540, x + 200, 590), 14, fill=mix(INK, c, e))
            d.text((x + 100, 565), s, font=F(20, "Bold"), fill=mix(INK, (255, 255, 255), e), anchor="mm"); x += 230
        d.text((W / 2, 630), "MATCH is only issued when the signer's reference set is ready — otherwise it is lowered to REVIEW", font=F(16, "Medium"), fill=mix(INK, MUTED, e), anchor="mm")
        emit(fade(img, t / 0.3, (sec - t) / 0.3))

# ---------- screen tour ----------
SCREENS = [
    ("desktop-sign-in.png", "Secure sign-in", "Password or emailed one-time code  •  3 roles  •  throttling"),
    ("desktop-dashboard.png", "Dashboard", "Live counts, recent cheques, engines and models in use"),
    ("desktop-cheque-verification.png", "Cheque verification", "Upload a cheque → 10 fields with confidence and status"),
    ("desktop-batch-processing.png", "Batch processing", "Up to 20 cheques through the full pipeline in one go"),
    ("desktop-signature-enrollment.png", "Signature enrollment", "Signers, signing sessions, specimen quality gate"),
    ("desktop-signature-verification.png", "Signature verification", "Questioned signature vs enrolled specimens, explained"),
    ("desktop-ocr-dataset.png", "OCR dataset audit", "120 cheques • 4 banks • live split and quality audit"),
    ("desktop-model-training.png", "Model training", "Readiness check, background training & evaluation jobs"),
    ("desktop-reports.png", "Reports", "Per-cheque PDF, CSV exports, audit and evaluation reports"),
    ("desktop-settings.png", "Settings", "Thresholds, users & roles, password, audit log"),
]
def browser_frame(shot, zoom, pan):
    """Return a 1040x650 browser mockup containing the screenshot with zoom/pan."""
    bw, bh = 980, 600; bar = 34
    fr = Image.new("RGBA", (bw, bh), (255, 255, 255, 255)); d = ImageDraw.Draw(fr)
    d.rectangle((0, 0, bw, bar), fill=(236, 239, 247))
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse((14 + i * 20, 12, 26 + i * 20, 24), fill=c)
    d.rounded_rectangle((90, 7, bw - 90, 29), 11, fill=(255, 255, 255))
    d.text((bw / 2, 18), "127.0.0.1:5000  —  ChequeVerify AI", font=F(13, "Medium"), fill=(110, 118, 150), anchor="mm")
    vw, vh = bw, bh - bar
    sw, sh = shot.size
    cw, ch = sw / zoom, sw / zoom * vh / vw
    cx = (sw - cw) * pan[0]; cy = max(0, min(sh - ch, (sh - ch) * pan[1]))
    crop = shot.crop((int(cx), int(cy), int(cx + cw), int(cy + ch))).resize((vw, vh), Image.LANCZOS)
    fr.paste(crop, (0, bar))
    mask = Image.new("L", (bw, bh), 0); ImageDraw.Draw(mask).rounded_rectangle((0, 0, bw, bh), 14, fill=255)
    fr.putalpha(mask)
    return fr

def screen(idx, file, title, sub, sec=3.4):
    shot = Image.open(SHOTS / file).convert("RGB")
    pans = [(0.5, 0.0), (0.0, 0.0), (1.0, 0.0), (0.6, 0.2)]
    p_to = pans[idx % len(pans)]
    for k in range(int(sec * FPS)):
        t = k / FPS; img = BG.copy(); d = ImageDraw.Draw(img)
        # zoom gently in after 1s
        z = 1.0 + 0.32 * ease_io((t - 1.0) / 2.0)
        pan = (0.5 + (p_to[0] - 0.5) * ease_io((t - 1.0) / 2.0), p_to[1] * ease_io((t - 1.0) / 2.0))
        fr = browser_frame(shot, z, pan)
        e = ease(t / 0.5)
        x, y = 150, 84 + int(30 * (1 - e))
        sh = Image.new("RGBA", (fr.width + 60, fr.height + 60), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle((30, 40, fr.width + 30, fr.height + 30), 16, fill=(0, 0, 0, 140))
        img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(16)), (x - 30, y - 30))
        img.alpha_composite(fr, (x, y))
        d = ImageDraw.Draw(img)
        # header strip
        logo(d, 40, 26, 0.8)
        d.text((80, 42), "ChequeVerify AI", font=F(20, "Bold"), fill=ON_INK, anchor="lm")
        tag = f"SCREEN {idx + 1:02d} / {len(SCREENS)}"
        tw = d.textlength(tag, font=F(13, "Bold")) + 28
        d.rounded_rectangle((W - 40 - tw, 27, W - 40, 57), 15, fill=(24, 32, 70), outline=SCAN)
        d.text((W - 40 - tw / 2, 42), tag, font=F(13, "Bold"), fill=ON_INK, anchor="mm")
        # caption card
        ec = ease((t - 0.3) / 0.5)
        cw = max(d.textlength(title, font=F(26, "Bold")), d.textlength(sub, font=F(16, "Medium"))) + 50
        cx, cy = 110, 604 + int(20 * (1 - ec))
        d.rounded_rectangle((cx, cy, cx + cw, cy + 86), 16, fill=mix(INK, (14, 20, 48), ec), outline=mix(INK, BRAND, ec), width=2)
        d.rectangle((cx + 2, cy + 18, cx + 6, cy + 68), fill=mix(INK, SCAN, ec))
        d.text((cx + 24, cy + 30), title, font=F(26, "Bold"), fill=mix(INK, ON_INK, ec), anchor="lm")
        d.text((cx + 24, cy + 62), sub, font=F(16, "Medium"), fill=mix(INK, MUTED, ec), anchor="lm")
        emit(fade(img, t / 0.25, (sec - t) / 0.25))

# ---------- results ----------
def results(sec=6.0):
    cards = [("20 / 20", "MICR cheque numbers", "held-out test split", GOOD),
             ("196 / 200", "Fields correct", "20 test cheques · 4 wrong flagged", GOOD),
             ("0 / 60", "Different signer accepted", "signature fusion + thresholds", GOOD),
             ("609", "Backend tests", "pytest suite, green in CI", BRAND),
             ("4 banks", "Axis · Canara · ICICI · Syndicate", "120 cheque images", BRAND),
             ("≈ 4 s", "OCR per cheque", "on 2 CPUs", BRAND)]
    for k in range(int(sec * FPS)):
        t = k / FPS; img = BG.copy(); d = ImageDraw.Draw(img)
        d.text((W / 2, 100), "Measured, not estimated", font=FX(44), fill=ON_INK, anchor="mm")
        d.text((W / 2, 144), "Results from reports/final/MODEL_EVALUATION.md", font=F(18, "Medium"), fill=MUTED, anchor="mm")
        for i, (big, cap, sub, col) in enumerate(cards):
            e = ease((t - 0.2 - i * 0.15) / 0.7)
            c, r = i % 3, i // 3
            x = 110 + c * 360; y = 200 + r * 210 + int(20 * (1 - e))
            d.rounded_rectangle((x, y, x + 340, y + 185), 18, fill=mix(INK, (18, 26, 58), e), outline=mix(INK, col, e), width=2)
            d.text((x + 170, y + 66), big, font=FX(48), fill=mix(INK, SCAN if col == GOOD else ON_INK, e), anchor="mm")
            d.text((x + 170, y + 120), cap, font=F(18, "Bold"), fill=mix(INK, ON_INK, e), anchor="mm")
            d.text((x + 170, y + 148), sub, font=F(14, "Medium"), fill=mix(INK, MUTED, e), anchor="mm")
        e = ease((t - 1.6) / 0.6)
        d.text((W / 2, 660), "Decision support, not a guarantee — field labels not human-verified; signature tests use synthetic edits.", font=F(15, "Medium"), fill=mix(INK, WARN, e), anchor="mm")
        emit(fade(img, t / 0.3, (sec - t) / 0.3))

def outro(sec=3.4):
    for k in range(int(sec * FPS)):
        t = k / FPS; img = BG.copy(); d = ImageDraw.Draw(img)
        e = ease(t / 0.7)
        logo(d, W / 2 - 200, 236, 1.2)
        d.text((W / 2 - 140, 260), "ChequeVerify AI", font=FX(52), fill=mix(INK, ON_INK, e), anchor="lm")
        e2 = ease((t - 0.5) / 0.7)
        cmd = "./start.sh setup  &&  ./start.sh"
        w = d.textlength(cmd, font=F(20, "Medium")) + 50
        d.rounded_rectangle((W / 2 - w / 2, 340, W / 2 + w / 2, 390), 12, fill=mix(INK, (6, 10, 24), e2), outline=mix(INK, BRAND, e2))
        d.text((W / 2, 365), cmd, font=F(20, "Medium"), fill=mix(INK, SCAN, e2), anchor="mm")
        d.text((W / 2, 450), "github.com/nvaltryek-001/Cheque", font=F(19, "Medium"), fill=mix(INK, MUTED, ease((t - 1) / 0.7)), anchor="mm")
        emit(fade(img, t / 0.3, (sec - t) / 0.4))

if __name__ == "__main__":
    intro(); workflow()
    for i, s in enumerate(SCREENS):
        screen(i, *s)
    results(); outro()
    print("frames", n_frame, "->", OUT)
