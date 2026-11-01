"""Ten-field cheque extraction for Indian CTS-2010 cheques.

Strategy
  1. Normalise the page to a fixed width.
  2. Read the whole page once (header: bank, IFSC, branch).
  3. Read field regions defined by the CTS-2010 layout (date, payee, amounts, account).
     Inside a region, the filled-in text is larger than the pre-printed labels, so
     words are filtered by height.
  4. Read the MICR band with the E-13B template model (cheque number, MICR code).
  5. Validate every field and cross-check amount words vs. figures.
Every field reports value, confidence, source and status (ok | review | missing).
"""
from __future__ import annotations

import re
import time
from collections import Counter
from datetime import date, datetime

import cv2
import numpy as np

from . import amounts
from .engine import BaseEngine, Line
from .micr import MicrModel, read_micr
from .regions import binarize, boxed_digits, digit_canvas, text_rows

FIELD_ORDER = ["bank_name", "branch_name", "ifsc_code", "cheque_number", "micr_code",
               "account_number", "date", "payee_name", "amount_figures", "amount_words"]
FIELD_LABELS = {
    "bank_name": "Bank Name", "branch_name": "Branch Name", "ifsc_code": "IFSC Code",
    "cheque_number": "Cheque Number", "micr_code": "MICR Code", "account_number": "Account Number",
    "date": "Date", "payee_name": "Payee Name", "amount_figures": "Amount (Figures)",
    "amount_words": "Amount (Words)",
}

BANKS = [
    ("AXIS", "Axis Bank", "UTIB"), ("CANARA", "Canara Bank", "CNRB"), ("ICICI", "ICICI Bank", "ICIC"),
    ("SYNDICATE", "Syndicate Bank", "SYNB"), ("HDFC", "HDFC Bank", "HDFC"),
    ("STATE BANK", "State Bank of India", "SBIN"), ("PUNJAB NATIONAL", "Punjab National Bank", "PUNB"),
    ("BANK OF BARODA", "Bank of Baroda", "BARB"), ("KOTAK", "Kotak Mahindra Bank", "KKBK"),
    ("UNION BANK", "Union Bank of India", "UBIN"), ("YES BANK", "Yes Bank", "YESB"),
]
IFSC_TO_BANK = {code: name for _, name, code in BANKS}

# Field regions as fractions of (width, height): x0, y0, x1, y1
REGIONS = {
    "date": (0.70, 0.02, 0.995, 0.20),
    "payee": (0.03, 0.16, 0.80, 0.29),
    "words": (0.03, 0.26, 0.97, 0.47),
    "figures": (0.68, 0.30, 0.995, 0.50),
    "account": (0.02, 0.43, 0.58, 0.62),
    "header": (0.0, 0.0, 0.76, 0.24),
}
WORK_WIDTH = 1600

_LABEL_WORDS = re.compile(
    r"^(pay|rupees?|or|bearer|a/?c\.?|no\.?|date|rs\.?|inr|order|the|sum|of|n0|no:|₹)$", re.I)


def _field(value="", conf=0.0, source="none", raw="", issues=None):
    status = "missing" if not value else ("ok" if conf >= 0.75 and not issues else "review")
    return {"value": value, "confidence": round(float(conf), 3), "source": source, "raw": raw,
            "status": status, "issues": list(issues or [])}


def _crop(img: np.ndarray, key: str) -> np.ndarray:
    h, w = img.shape[:2]
    x0, y0, x1, y1 = REGIONS[key]
    return img[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]


def _prep(region: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY) if region.ndim == 3 else region
    # remove faint coloured security background while keeping dark ink
    gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
    return cv2.copyMakeBorder(gray, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)


def _remove_grid(gray: np.ndarray) -> np.ndarray:
    """Erase long horizontal/vertical box lines (date boxes, amount box)."""
    _, inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    h, w = gray.shape
    vert = cv2.morphologyEx(inv, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(8, int(h * 0.30)))))
    horiz = cv2.morphologyEx(inv, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(10, int(w * 0.25)), 1)))
    lines = cv2.dilate(cv2.bitwise_or(vert, horiz), np.ones((3, 3), np.uint8))
    out = gray.copy()
    out[lines > 0] = 255
    return out


def _big_words(lines: list[Line], min_ratio: float = 0.72) -> tuple[str, float]:
    """Join the words whose height is close to the largest text in the region (the filled-in text)."""
    words = []
    for ln in lines:
        if ln.words:
            words.extend(ln.words)
        else:
            words.append((ln.text, ln.conf, ln.x, ln.y, ln.w, ln.h))
    words = [w for w in words if re.search(r"[A-Za-z0-9]", w[0])]
    if not words:
        return "", 0.0
    ref = float(np.percentile([w[5] for w in words], 85))
    keep = [w for w in words if w[5] >= ref * min_ratio]
    if not keep:
        return "", 0.0
    # group into rows, order top-to-bottom then left-to-right
    keep.sort(key=lambda w: (w[3] + w[5] / 2))
    rows, cur = [], [keep[0]]
    for w in keep[1:]:
        if abs((w[3] + w[5] / 2) - (cur[-1][3] + cur[-1][5] / 2)) < ref * 0.6:
            cur.append(w)
        else:
            rows.append(cur)
            cur = [w]
    rows.append(cur)
    text = " ".join(" ".join(x[0] for x in sorted(r, key=lambda t: t[2])) for r in rows)
    return text, float(np.mean([w[1] for w in keep]))


def _clean_name(text: str) -> str:
    text = re.sub(r"[^A-Za-z.\s'&-]", " ", text)
    tokens = [t for t in text.split() if not _LABEL_WORDS.match(t)]
    # drop isolated junk tokens of 1 char that are not initials followed by '.'
    tokens = [t for t in tokens if len(t) > 1 or t.isupper()]
    out = " ".join(tokens).strip(" .-'")
    return re.sub(r"\s{2,}", " ", out)


def _detect_bank(page_text: str, ifsc: str) -> tuple[str, float, str]:
    up = page_text.upper()
    for key, name, _ in BANKS:
        if key in up.replace("\n", " "):
            conf = 0.95 if (not ifsc or IFSC_TO_BANK.get(ifsc[:4]) == name) else 0.6
            return name, conf, "page"
    if ifsc and ifsc[:4] in IFSC_TO_BANK:
        return IFSC_TO_BANK[ifsc[:4]], 0.85, "ifsc"
    return "", 0.0, "none"


def _fix_ifsc(token: str) -> str:
    t = re.sub(r"[^A-Z0-9]", "", token.upper())
    if len(t) != 11:
        return ""
    head = t[:4].replace("0", "O").replace("1", "I").replace("5", "S").replace("8", "B")
    fifth = "0" if t[4] in "0OQD" else t[4]
    return head + fifth + t[5:]


def _find_ifsc(text: str) -> str:
    up = text.upper()
    m = re.search(r"IFS[C]?\s*(?:CODE)?\s*[:\-.]?\s*([A-Z0-9]{4}\s?[0O][A-Z0-9]{6})", up)
    candidates = [m.group(1)] if m else []
    candidates += re.findall(r"\b([A-Z]{4}[0O][A-Z0-9]{6})\b", up)
    for c in candidates:
        fixed = _fix_ifsc(c)
        if re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", fixed):
            return fixed
    return ""


def _find_ifsc_conf(lines: list[Line]):
    """Find the IFSC token and return (value, word confidence, raw OCR token)."""
    for ln in lines:
        words = ln.words or [(ln.text, ln.conf, 0, 0, 0, 0)]
        joined = " ".join(w[0] for w in words)
        if not _find_ifsc(joined):
            continue
        for i, w in enumerate(words):
            for cand in (w[0], w[0] + (words[i + 1][0] if i + 1 < len(words) else "")):
                fixed = _fix_ifsc(cand)
                if re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", fixed or ""):
                    return fixed, float(w[1]), cand
        return _find_ifsc(joined), float(ln.conf), joined
    return None


def _find_branch(lines: list[Line], bank: str) -> tuple[str, float]:
    best, best_score, conf = "", 0, 0.0
    bank_words = set(bank.upper().split()) if bank else set()
    for ln in lines:
        ws = [w for w in ln.words if (w[1] >= 0.5 and re.search(r"[A-Za-z0-9]{2,}", w[0])) or w[0] in ("-", "–")] if ln.words else []
        ws = [w for j, w in enumerate(ws) if w[0] not in ("-", "–") or 0 < j < len(ws) - 1]
        bank_tokens = {b.replace(" ", "").upper() for _, b, _ in BANKS} | {k.upper() for k, _, _ in BANKS} | {"BANK", "LTD"}
        while ws and re.sub(r"[^A-Za-z]", "", ws[0][0]).upper() in bank_tokens:
            ws = ws[1:]
        while ws and ws[-1][1] < 0.75 and not re.search(r"\d", ws[-1][0]):
            ws = ws[:-1]
        # an Indian address ends with the 6-digit PIN code: drop anything after it
        for j, w in enumerate(ws):
            if re.fullmatch(r"\d{6}[.,]?", w[0]) or (j and re.fullmatch(r"\d{3}", w[0]) and re.fullmatch(r"\d{3}", ws[j - 1][0])):
                ws = ws[:j + 1]
                break
        text = " ".join(w[0] for w in ws) if ln.words else ln.text
        t = re.sub(r"[^\x20-\x7E]", " ", text).strip()
        t = re.sub(r"\s{2,}", " ", t)
        up = t.upper()
        if len(t) < 5 or any(k in up for k in ("IFS", "VALID", "RTGS", "PAYABLE", "DATE", "MONTHS")):
            continue
        letters = sum(c.isalpha() for c in t)
        if letters < 4:
            continue
        score = 1
        if "BRANCH" in up:
            score += 4
        if re.search(r"\b\d{6}\b|\b\d{3}\s?\d{3}\b", t):
            score += 2
        if "," in t or " - " in t:
            score += 1
        if ws and np.mean([w[1] for w in ws if w[0] not in ("-", "–")] or [0]) >= 0.85:
            score += 1  # cleanly read line; misread Devanagari/logo text is low-confidence
        if bank_words and set(up.split()) & bank_words and "BRANCH" not in up:
            score -= 3
        if score > best_score:
            best, best_score, conf = t, score, ln.conf
    return best.strip(" ,.-"), conf if best_score >= 2 else conf * 0.6


def _name_key(s: str) -> str:
    return re.sub(r"[^A-Z]", "", (s or "").upper())


def _read_payee(region: np.ndarray, engine: BaseEngine):
    """Read the payee line from three renderings and vote.

    A name has no checksum, so a confident mis-read ('Kulkarn' for 'Kulkarni') cannot be caught from
    one read. The cleaned ink row, and the original grey pixels upscaled 2x and 3x, are each read
    once. Empty reads abstain. The value is the plurality reading; the field is 'ok' only when every
    non-empty reading agrees (and at least two exist), so any disagreement, a lone reading, weak
    words or a short stray fragment send the name to review with the alternatives listed.
    Returns (value, confidence, raw, issues) or None if no text was found."""
    row = _best_row(text_rows(region, 0.7))
    if row is None:
        return None
    reads = []  # (name, confidence, lines)
    lines = engine.read(row.image, "line")
    reads.append((_clean_name(" ".join(l.text for l in lines)), _mean_conf(lines), lines))
    pad = int(row.char_h * 0.5)
    H, W = region.shape[:2]
    crop = region[max(0, row.y0 - pad):min(H, row.y1 + pad), max(0, row.x0 - pad):min(W, row.x1 + pad)]
    if crop.size:
        g = _prep(crop)
        for fx in (2, 3):
            ls = engine.read(cv2.resize(g, None, fx=fx, fy=fx, interpolation=cv2.INTER_CUBIC), "line")
            reads.append((_clean_name(" ".join(l.text for l in ls)), _mean_conf(ls), ls))
    valid = [r for r in reads if _name_key(r[0])]
    if not valid:
        return None
    votes = Counter(_name_key(r[0]) for r in valid)
    top_key, top_n = max(votes.items(), key=lambda kv: (kv[1], max(r[1] for r in valid if _name_key(r[0]) == kv[0])))
    agreeing = [r for r in valid if _name_key(r[0]) == top_key]
    name, conf, lines = max(agreeing, key=lambda r: r[1])
    issues = []
    if top_n < len(valid):
        others = sorted({r[0] for r in valid if _name_key(r[0]) != top_key})
        issues.append(f"Readings of the name differ (also read: {', '.join(repr(o) for o in others)}) – confirm visually.")
    elif len(valid) < 2:
        issues.append("Name read from one rendering only – confirm visually.")
    words = [w for ln in lines for w in (ln.words or []) if re.search(r"[A-Za-z]{2,}", w[0])]
    weak = [w[0] for w in words if w[1] < 0.5]
    if weak:
        issues.append("Low-confidence word(s): " + ", ".join(weak[:3]) + " – confirm visually.")
    tokens = name.split()
    if len(tokens) > 1 and any(len(t.strip(".")) <= 2 and not t.endswith(".") and not t.isupper() for t in tokens[1:]):
        issues.append("A short fragment in the name suggests a partial read – confirm visually.")
    return name, conf, " | ".join(r[0] for r in reads), issues


def _header_line_reads(region: np.ndarray, engine: BaseEngine) -> list[Line]:
    """Read each small text line of the header on its own, as a clean binarised crop.
    Whole-header reads lose short address lines that sit next to logos or Devanagari text
    (e.g. the Canara branch line); isolated, they read reliably."""
    ink = binarize(region)
    H, W = ink.shape
    blob = cv2.dilate(ink, cv2.getStructuringElement(cv2.MORPH_RECT, (max(9, W // 60), 3)))
    n, _, st, _ = cv2.connectedComponentsWithStats(blob)
    boxes = []
    for i in range(1, n):
        x, y, w, h, _ = st[i]
        if 8 <= h and w >= 3 * min(h, 24):
            boxes.append((int(x), int(y), int(w), int(h)))
    med = float(np.median([b[3] for b in boxes if b[3] <= H * 0.25])) if boxes else 0.0
    split = []
    for x, y, w, h in boxes:
        if med and h > 1.6 * med:  # two lines merged by the dilation: cut at blank rows
            prof = (ink[y:y + h, x:x + w] > 0).sum(1)
            seg_start = None
            for j, v in enumerate(list(prof) + [0]):
                if v > 0 and seg_start is None:
                    seg_start = j
                elif v == 0 and seg_start is not None:
                    if j - seg_start >= 8:
                        split.append((x, y + seg_start, w, j - seg_start))
                    seg_start = None
        elif h <= H * 0.25:
            split.append((x, y, w, h))
    out: list[Line] = []
    for x, y, w, h in split:
        if w < 3 * h:
            continue
        crop = ink[y:y + h, max(0, x - 4):x + w + 4]
        crop = cv2.resize(255 - crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        crop = cv2.copyMakeBorder(crop, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)
        for ln in engine.read(crop, "line"):
            if ln.text.strip():
                out.append(Line(ln.text, ln.conf, int(x), int(y), int(w), int(h), ln.words))
    return out


def _extract_date(region: np.ndarray, engine: BaseEngine) -> tuple[str, float, str, list]:
    """Read the date from several renderings, grouped into independent families:
    the box-strip reconstruction, rows with box separators removed, and plain rows.
    Two reads of the same family share their errors (a box-line remnant on a '7' reads as
    '4' in both), so a date is confirmed only when a different family agrees. An
    unconfirmed date is kept but sent to review; a different date from another family
    is reported as a disagreement."""
    issues: list = []
    candidates: list[tuple[str, np.ndarray]] = []
    boxed = boxed_digits(region)
    if boxed is not None:
        candidates.append(("boxed", boxed))
    for family, bars in (("no_separators", True), ("plain", False)):
        rows = text_rows(region, 0.7, drop_bars=bars)
        rows.sort(key=lambda r: -r.n)
        for row in rows[:2]:
            candidates += [(family, digit_canvas(row)), (family, row.image)]
    found: list[tuple[str, float, str, str]] = []
    for family, img in candidates:
        lines = engine.read(img, "digits")
        raw = " ".join(l.text for l in lines)
        compact = re.sub(r"[^0-9]", "", raw)
        if len(compact) != 8:
            continue
        try:
            d = datetime.strptime(compact, "%d%m%Y").date()
        except ValueError:
            issues = ["Date digits read but do not form a valid calendar date."]
            continue
        if not 1990 <= d.year <= 2100:
            issues = ["Date digits read but the year is implausible."]
            continue
        found.append((d.isoformat(), _mean_conf(lines), raw, family))
    if not found:
        return "", 0.0, "", issues
    value, conf, raw, family = found[0]
    others = [f for f in found if f[3] != family]
    conflict = next((f for f in found if f[0] != value), None)
    if conflict:
        return value, min(conf, 0.5), raw, [f"Readings of the date disagree ({value} vs {conflict[0]}) – confirm visually."]
    if not others:
        return value, min(conf, 0.7), raw, ["Date read from one rendering only – confirm visually."]
    return value, max([conf] + [f[1] for f in others]), raw, []


# Published account-number lengths, used only to send unusual reads to review.
ACCOUNT_LENGTHS = {"Axis Bank": {15}, "ICICI Bank": {12}}


def _digits_of(text: str) -> str:
    """Longest run of digits in a line, joining digits printed with spaces between them
    ('4005123456789 0') and ignoring symbols glued to the number ('=7001…', '«=©63…')."""
    t = re.sub(r"(?<=\d)[Oo]|[Oo](?=\d)", "0", text)   # O/o only inside a number
    t = re.sub(r"(?<=\d)[/|](?=\d)", "", t)          # stray stroke inside the number
    toks = t.split()
    merged: list[str] = []
    for tok in toks:
        # join letter-spaced digits ('4 0 0 5 1 …', '…789 0'); keep separate groups such as a
        # label misread as digits ('255 600123456789') apart
        if merged and tok.isdigit() and merged[-1].isdigit() and (len(tok) == 1 or len(merged[-1]) == 1):
            merged[-1] += tok
        else:
            merged.append(tok)
    runs = re.findall(r"\d+", " ".join(merged))
    return max(runs, key=len) if runs else ""


def _read_account(region: np.ndarray, engine: BaseEngine) -> tuple[str, float, str, list]:
    """Two reads of the account row: the normal line read, and a digits-only read of the
    span that holds the number (labels such as 'A/C NO' excluded). Agreement → normal
    confidence; disagreement → the digits-only value, sent to review."""
    best = ("", 0.0, "", [])
    for row in text_rows(region, 0.7):
        for ln in engine.read(row.image, "line"):
            a = _digits_of(ln.text)
            ndig = lambda w: sum(c.isdigit() for c in w[0])
            words = sorted(ln.words or [], key=lambda w: w[2])
            b, bconf = "", 0.0
            main = max(words, key=ndig) if words else None
            if main is not None and ndig(main) >= 2:
                # span of the main number plus letter-spaced single digits next to it
                x0, x1 = main[2], main[2] + main[4]
                for w in words:
                    near = min(abs(w[2] - x1), abs(x0 - (w[2] + w[4]))) <= 1.5 * max(main[5], 1)
                    if w is not main and 1 <= ndig(w) <= 2 and len(w[0]) <= 2 and near:
                        x0, x1 = min(x0, w[2]), max(x1, w[2] + w[4])
                pad = int(0.6 * row.image.shape[0])
                crop = row.image[:, max(0, x0 - pad // 2):min(row.image.shape[1], x1 + pad)]
                crop = cv2.copyMakeBorder(crop, 8, 8, 12, 12, cv2.BORDER_CONSTANT, value=255)
                dl = engine.read(crop, "digits")
                b, bconf = _digits_of(" ".join(l.text for l in dl)), _mean_conf(dl)
            a = a if 9 <= len(a) <= 18 else ""
            b = b if 9 <= len(b) <= 18 else ""
            if not (a or b):
                continue
            if a and a == b:
                cand = (a, max(float(ln.conf), bconf), ln.text, [])
            elif b:
                note = (f"Two readings disagree ('{a}' vs '{b}') – confirm visually." if a
                        else "Number read only by the digit pass – confirm visually.")
                cand = (b, min(max(float(ln.conf), bconf), 0.6), ln.text, [note])
            else:
                cand = (a, float(ln.conf), ln.text, ["Digit pass could not confirm the number – confirm visually."])
            if re.search(r"\d[/|]\d", ln.text):
                cand = (cand[0], min(cand[1], 0.6), cand[2],
                        cand[3] + ["A stray mark inside the number was ignored – confirm visually."])
            # prefer agreeing reads, then longer numbers
            if (bool(cand[0]), not cand[3], len(cand[0])) > (bool(best[0]), not best[3], len(best[0])):
                best = cand
    return best


_INDIAN = re.compile(r"^\d{1,2}(?:,\d{2})*,\d{3}$")


def _parse_figures_tokens(text: str, words_value: int | None) -> tuple[int | None, str]:
    """Pick the amount from the OCR tokens of the amount box.

    The rupee sign is often read as a digit glued to the number ('₹ 4,15,000' ->
    '24,15,000'). Candidates are generated by dropping up to two leading digits;
    a candidate equal to the amount in words is preferred (words prevail on Indian
    cheques). Otherwise the first candidate with valid Indian digit grouping wins."""
    t = text.replace("O", "0").replace("o", "0")
    t = re.sub(r"(?<=\d),\s+(?=\d)", ",", t)            # '1, 75, 000'  -> '1,75,000'
    t = re.sub(r"(?<=\d)\s+,(?=\d)", ",", t)            # '31 ,45,000'  -> '31,45,000'
    t = re.sub(r"(?<=\d)\.(?=\d{2},\d)", ",", t)        # '38.64,000'   -> '38,64,000' (comma read as dot)
    tokens = re.findall(r"\d[\d,]*(?:\.\d{1,2})?", t)
    cands: list[int] = []
    fallback: list[int] = []
    for tok in sorted(tokens, key=len, reverse=True):
        core = tok.split(".")[0].strip(",")
        if not core:
            continue
        variants = [core]
        digits_before_comma = core.split(",")[0]
        for drop in (1, 2):
            if "," in core and len(digits_before_comma) > drop:
                variants.append(core[drop:])
        for v in variants:
            if _INDIAN.match(v) or ("," not in v and v.isdigit() and len(v) <= 6):
                n = int(v.replace(",", ""))
                if n > 0 and n not in cands:
                    cands.append(n)
        raw = int(core.replace(",", "")) if core.replace(",", "").isdigit() else 0
        if 0 < raw < 10**11:
            fallback.append(raw)  # digit grouping mis-read (e.g. a lost comma)
    if words_value and words_value in fallback and words_value not in cands:
        return words_value, "Digit grouping corrected using the amount in words."
    if not cands:
        return None, ""
    if words_value and words_value in cands:
        note = "" if cands[0] == words_value else "Leading symbol removed using the amount in words."
        return words_value, note
    return cands[0], ""


def _best_row(rows):
    return max(rows, key=lambda r: r.char_h * r.n) if rows else None


def _mean_conf(lines) -> float:
    return float(np.mean([l.conf for l in lines])) if lines else 0.0


def _number_words(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Keep only tokens that are (fuzzy) number words, e.g. 'Rupees Twenty-Two Lakh only ~' -> 'Twenty-Two Lakh only'.
    Also returns the (printed, normalised) pairs for every word whose spelling had to be changed."""
    kept, changed = [], []
    for tok in text.split():
        parts = re.findall(r"[A-Za-z]+", tok)
        if not parts:
            continue
        mapped = [amounts._fuzzy(p.lower()) for p in parts]
        if all(m is not None for m in mapped) and not all(m in ("rupees", "rupee", "rs", "inr") for m in mapped):
            kept.append("-".join(m.capitalize() for m in mapped))
            changed += [(p, m.capitalize()) for p, m in zip(parts, mapped) if p.lower() != m and m not in amounts._FILLER]
    return " ".join(kept), changed


def _validate(fields: dict, today: date | None = None) -> dict:
    today = today or date.today()
    checks = []

    def add(name, status, message):
        checks.append({"check": name, "status": status, "message": message})

    ifsc = fields["ifsc_code"]["value"]
    if ifsc:
        valid = bool(re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", ifsc))
        add("ifsc_format", "pass" if valid else "fail", "IFSC has 4 letters, a 0 and 6 alphanumerics." if valid else "IFSC format invalid.")
        bank = fields["bank_name"]["value"]
        expected = next((code for _, name, code in BANKS if name == bank), None)
        if valid and bank and expected:
            same = ifsc[:4] == expected
            add("ifsc_bank_match", "pass" if same else "warn",
                "IFSC prefix matches the bank name." if same
                else f"IFSC prefix {ifsc[:4]} does not match {bank} ({expected}).")
    acct = fields["account_number"]["value"]
    if acct:
        ok = 9 <= len(acct) <= 18 and acct.isdigit()
        add("account_format", "pass" if ok else "fail", "Account number has 9–18 digits." if ok else "Account number length is unusual.")
    micr = fields["micr_code"]["value"]
    if micr:
        add("micr_format", "pass" if re.fullmatch(r"\d{9}", micr) else "fail", "MICR code has 9 digits.")
    chq = fields["cheque_number"]["value"]
    if chq:
        add("cheque_number_format", "pass" if re.fullmatch(r"\d{6}", chq) else "fail", "Cheque number has 6 digits.")
    d = fields["date"]["value"]
    if d:
        dt = date.fromisoformat(d)
        age = (today - dt).days
        if age > 92:
            add("date_validity", "warn", f"Cheque date is {age} days old (stale after 3 months).")
        elif age < 0:
            add("date_validity", "warn", "Cheque is post-dated.")
        else:
            add("date_validity", "pass", "Cheque date is within the 3-month validity period.")
    fig = fields["amount_figures"]["value"]
    words = fields["amount_words"]["value"]
    if fig and words:
        n_fig = amounts.parse_figures(fig)
        n_words = amounts.words_to_number(words)
        if n_fig and n_words:
            same = n_fig == n_words
            add("amount_consistency", "pass" if same else "fail",
                "Amount in words matches amount in figures." if same
                else f"Words read as {amounts.format_indian(n_words)} but figures read as {amounts.format_indian(n_fig)}.")
        else:
            add("amount_consistency", "warn", "Could not parse both amounts for comparison.")
    missing = [FIELD_LABELS[k] for k in FIELD_ORDER if not fields[k]["value"]]
    if missing:
        add("completeness", "warn", "Missing: " + ", ".join(missing))
    else:
        add("completeness", "pass", "All ten fields extracted.")
    return {"checks": checks,
            "passed": sum(c["status"] == "pass" for c in checks),
            "failed": sum(c["status"] == "fail" for c in checks),
            "warnings": sum(c["status"] == "warn" for c in checks)}


def extract_cheque(img: np.ndarray, engine: BaseEngine, micr_model: MicrModel | None,
                   today: date | None = None) -> dict:
    t0 = time.time()
    h, w = img.shape[:2]
    scale = WORK_WIDTH / w
    work = cv2.resize(img, (WORK_WIDTH, int(h * scale)), interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
    fields: dict = {k: _field() for k in FIELD_ORDER}
    raw_parts = []

    # --- MICR (template model) ---------------------------------------------
    micr = read_micr(img, micr_model)
    if micr["cheque_number"]:
        fields["cheque_number"] = _field(micr["cheque_number"], micr.get("min_glyph_confidence", 0), "micr", micr["raw"])
    if micr["micr_code"]:
        fields["micr_code"] = _field(micr["micr_code"], micr.get("min_glyph_confidence", 0), "micr", micr["raw"])
    if not micr["available"]:
        for k in ("cheque_number", "micr_code"):
            fields[k]["issues"].append("MICR model not trained – run scripts/train_micr.py.")

    if engine.available:
        header_img = _prep(_crop(work, "header"))
        header_img = cv2.resize(header_img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        header_lines = engine.read(header_img, "block")
        header_text = "\n".join(l.text for l in header_lines)
        page_lines = engine.read(cv2.cvtColor(work, cv2.COLOR_BGR2GRAY), "page")
        page_text = "\n".join(l.text for l in page_lines)
        raw_parts.append(page_text)

        line_reads = _header_line_reads(_crop(work, "header"), engine)
        ifsc, iconf, iraw = _find_ifsc_conf(header_lines) or _find_ifsc_conf(page_lines) or ("", 0.0, "")
        if ifsc:
            issues = []
            raw_compact = re.sub(r"[^A-Z0-9]", "", iraw.upper())
            if raw_compact != ifsc:
                issues.append(f"OCR read '{iraw}'; characters were corrected to fit the IFSC format – confirm visually.")
            second = _find_ifsc_conf(line_reads)
            if second and second[0] != ifsc:
                issues.append(f"A second reading gave '{second[0]}' – confirm visually.")
            fields["ifsc_code"] = _field(ifsc, iconf, "header", iraw, issues)
        bank, bconf, bsrc = _detect_bank(header_text + "\n" + page_text, ifsc)
        expected = next((code for _, name, code in BANKS if name == bank), None) if bsrc == "page" else None
        if ifsc and expected and ifsc[:4] != expected and sum(a != b for a, b in zip(ifsc[:4], expected)) == 1:
            # the bank name is printed on the cheque and the IFSC prefix is one character off its code
            fixed = expected + ifsc[4:]
            f = fields["ifsc_code"]
            f["issues"].append(f"Prefix read as {ifsc[:4]}; corrected to {expected} from the printed bank name – confirm visually.")
            fields["ifsc_code"] = _field(fixed, f["confidence"], f["source"], f["raw"], f["issues"])
            ifsc = fixed
            bank, bconf, bsrc = _detect_bank(header_text + "\n" + page_text, ifsc)
        if bank:
            fields["bank_name"] = _field(bank, bconf, bsrc)
        # block mode can merge/lose small address lines; sparse mode and per-line reads find them
        branch, brconf = _find_branch(header_lines + engine.read(header_img, "sparse") + line_reads, bank)
        if branch:
            fields["branch_name"] = _field(branch, min(brconf, 0.8), "header",
                                           issues=["Branch text is heuristic – confirm visually."])

        # payee
        pr = _read_payee(_crop(work, "payee"), engine)
        if pr is not None:
            fields["payee_name"] = _field(pr[0], pr[1], "region", pr[2], pr[3])

        # amount in words (may wrap onto a second line)
        wparts, wconfs, wchanged = [], [], []
        for row in text_rows(_crop(work, "words"), 0.7):
            wl = engine.read(row.image, "line")
            kept, changed = _number_words(" ".join(l.text for l in wl))
            if kept:
                wparts.append(kept)
                wconfs.append(_mean_conf(wl))
                wchanged += changed
        wtext = " ".join(wparts)
        if wtext:
            n = amounts.words_to_number(wtext)
            issues = [] if n else ["Words could not be parsed into a number."]
            if wchanged:
                issues.append("Spelling normalised (" + ", ".join(f"'{a}' → '{b}'" for a, b in wchanged[:3])
                              + ") – compare with the printed words.")
            conf = float(np.mean(wconfs)) if wconfs else 0.0
            fields["amount_words"] = _field(wtext, conf if n else conf * 0.6, "region", wtext, issues)

        # amount in figures
        words_value = amounts.words_to_number(fields["amount_words"]["value"])
        # The figures region can also contain the tail of the words line ("... Forty Only"), so every
        # row is read and the best candidate chosen: one equal to the words wins, otherwise the row with
        # a real amount (≥ 3 digits) and the highest confidence.
        fcands = []
        frows = text_rows(_crop(work, "figures"), 0.7)
        for row in frows:
            fl = engine.read(row.image, "line")
            ftext = " ".join(l.text for l in fl)
            value, note = _parse_figures_tokens(ftext, words_value)
            if value:
                fcands.append((value, note, _mean_conf(fl), ftext))
        if words_value and not any(c[0] == words_value for c in fcands):
            # first read disagrees with the words: one independent digits-only re-read of each row
            for row in frows:
                dl = engine.read(row.image, "digits")
                dtext = " ".join(l.text for l in dl)
                value, _ = _parse_figures_tokens(dtext, words_value)
                if value == words_value:
                    fcands.append((value, "Confirmed by a second, digits-only reading.", _mean_conf(dl), dtext))
                    break
        if fcands:
            match = [c for c in fcands if words_value and c[0] == words_value]
            value, note, fconf, ftext = match[0] if match else max(fcands, key=lambda c: (c[0] >= 100, c[2]))
            fields["amount_figures"] = _field(amounts.format_indian(value) + "/-", fconf, "region", ftext,
                                              [note] if note else [])

        # account number
        acct, aconf, araw, aissues = _read_account(_crop(work, "account"), engine)
        if not acct:
            m2 = re.search(r"A/?C\.?\s*(?:NO\.?)?\s*[:\-]?\s*(\d{9,18})", page_text, re.I)
            if m2:
                acct, aconf, araw, aissues = m2.group(1), 0.6, m2.group(0), ["Read from the full page only."]
        if acct:
            expected = ACCOUNT_LENGTHS.get(fields["bank_name"]["value"])
            if expected and len(acct) not in expected:
                aissues.append(f"{fields['bank_name']['value']} account numbers have "
                               f"{'/'.join(map(str, sorted(expected)))} digits; {len(acct)} were read.")
            fields["account_number"] = _field(acct, aconf, "region", araw, aissues)

        # date
        dval, dconf, draw, dissues = _extract_date(_crop(work, "date"), engine)
        if dval or dissues:
            fields["date"] = _field(dval, dconf, "region", draw, dissues)
    else:
        for k in FIELD_ORDER:
            if k not in ("cheque_number", "micr_code"):
                fields[k]["issues"].append("No OCR engine available – enter manually.")

    # cross-field consistency lowers confidence where amounts disagree
    validation = _validate(fields, today)
    for c in validation["checks"]:
        if c["check"] in ("ifsc_bank_match", "ifsc_format") and c["status"] != "pass" and fields["ifsc_code"]["value"]:
            fields["ifsc_code"]["status"] = "review"
            fields["ifsc_code"]["issues"].append(c["message"])
        if c["check"] == "amount_consistency" and c["status"] == "fail":
            for k in ("amount_figures", "amount_words"):
                if fields[k]["value"]:
                    fields[k]["status"] = "review"
                    fields[k]["issues"].append(c["message"])
        if c["check"] == "amount_consistency" and c["status"] == "pass":
            for k in ("amount_figures", "amount_words"):
                fields[k]["confidence"] = max(fields[k]["confidence"], 0.9)
                if not fields[k]["issues"]:
                    fields[k]["status"] = "ok"
    return {
        "fields": fields,
        "validation": validation,
        "micr": micr,
        "engine": engine.name,
        "raw_text": "\n".join(raw_parts),
        "processing_ms": int((time.time() - t0) * 1000),
        "needs_review": any(f["status"] != "ok" for f in fields.values()) or validation["failed"] > 0,
    }
