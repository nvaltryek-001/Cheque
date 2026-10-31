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
from datetime import date, datetime

import cv2
import numpy as np

from . import amounts
from .engine import BaseEngine, Line
from .micr import MicrModel, read_micr
from .regions import boxed_digits, digit_canvas, text_rows

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


def _find_branch(lines: list[Line], bank: str) -> tuple[str, float]:
    best, best_score, conf = "", 0, 0.0
    bank_words = set(bank.upper().split()) if bank else set()
    for ln in lines:
        text = " ".join(w[0] for w in ln.words if w[1] >= 0.5 and re.search(r"[A-Za-z0-9]{2,}", w[0])) if ln.words else ln.text
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
        if bank_words and set(up.split()) & bank_words and "BRANCH" not in up:
            score -= 3
        if score > best_score:
            best, best_score, conf = t, score, ln.conf
    return best.strip(" ,.-"), conf if best_score >= 2 else conf * 0.6


def _extract_date(region: np.ndarray, engine: BaseEngine) -> tuple[str, float, str, list]:
    issues: list = []
    candidates = []
    boxed = boxed_digits(region)
    if boxed is not None:
        candidates.append(boxed)
    rows = text_rows(region, 0.7)
    rows.sort(key=lambda r: -r.n)
    for row in rows[:2]:
        candidates += [digit_canvas(row), row.image]
    for img in candidates:
        lines = engine.read(img, "digits")
        raw = " ".join(l.text for l in lines)
        compact = re.sub(r"[^0-9]", "", raw)
        if len(compact) == 8:
            try:
                d = datetime.strptime(compact, "%d%m%Y").date()
                return d.isoformat(), _mean_conf(lines), raw, issues
            except ValueError:
                issues = ["Date digits read but do not form a valid calendar date."]
    return "", 0.0, "", issues


_INDIAN = re.compile(r"^\d{1,2}(?:,\d{2})*,\d{3}$")


def _parse_figures_tokens(text: str, words_value: int | None) -> tuple[int | None, str]:
    """Pick the amount from the OCR tokens of the amount box.

    The rupee sign is often read as a digit glued to the number ('₹ 3,85,000' ->
    '23,85,000'). Candidates are generated by dropping up to two leading digits;
    a candidate equal to the amount in words is preferred (words prevail on Indian
    cheques). Otherwise the first candidate with valid Indian digit grouping wins."""
    t = text.replace("O", "0").replace("o", "0")
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


def _number_words(text: str) -> str:
    """Keep only tokens that are (fuzzy) number words, e.g. 'Rupees Twenty-Two Lakh only ~' -> 'Twenty-Two Lakh only'."""
    kept = []
    for tok in text.split():
        parts = re.findall(r"[A-Za-z]+", tok)
        if not parts:
            continue
        mapped = [amounts._fuzzy(p.lower()) for p in parts]
        if all(m is not None for m in mapped) and not all(m in ("rupees", "rupee", "rs", "inr") for m in mapped):
            kept.append("-".join(m.capitalize() for m in mapped))
    return " ".join(kept)


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
        if valid and bank and ifsc[:4] in IFSC_TO_BANK:
            same = IFSC_TO_BANK[ifsc[:4]] == bank
            add("ifsc_bank_match", "pass" if same else "warn",
                "IFSC prefix matches the bank name." if same else "IFSC prefix belongs to a different bank.")
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
        header_lines = engine.read(_prep(_crop(work, "header")), "block")
        header_text = "\n".join(l.text for l in header_lines)
        page_lines = engine.read(cv2.cvtColor(work, cv2.COLOR_BGR2GRAY), "page")
        page_text = "\n".join(l.text for l in page_lines)
        raw_parts.append(page_text)

        ifsc = _find_ifsc(header_text) or _find_ifsc(page_text)
        if ifsc:
            fields["ifsc_code"] = _field(ifsc, 0.9, "header")
        bank, bconf, bsrc = _detect_bank(header_text + "\n" + page_text, ifsc)
        if bank:
            fields["bank_name"] = _field(bank, bconf, bsrc)
        branch, brconf = _find_branch(header_lines, bank)
        if branch:
            fields["branch_name"] = _field(branch, min(brconf, 0.8), "header",
                                           issues=["Branch text is heuristic – confirm visually."])

        # payee
        prow = _best_row(text_rows(_crop(work, "payee"), 0.7))
        if prow is not None:
            plines = engine.read(prow.image, "line")
            ptext = " ".join(l.text for l in plines)
            payee = _clean_name(ptext)
            if payee:
                fields["payee_name"] = _field(payee, _mean_conf(plines), "region", ptext)

        # amount in words (may wrap onto a second line)
        wparts, wconfs = [], []
        for row in text_rows(_crop(work, "words"), 0.7):
            wl = engine.read(row.image, "line")
            kept = _number_words(" ".join(l.text for l in wl))
            if kept:
                wparts.append(kept)
                wconfs.append(_mean_conf(wl))
        wtext = " ".join(wparts)
        if wtext:
            n = amounts.words_to_number(wtext)
            issues = [] if n else ["Words could not be parsed into a number."]
            conf = float(np.mean(wconfs)) if wconfs else 0.0
            fields["amount_words"] = _field(wtext, conf if n else conf * 0.6, "region", wtext, issues)

        # amount in figures
        words_value = amounts.words_to_number(fields["amount_words"]["value"])
        for row in sorted(text_rows(_crop(work, "figures"), 0.7), key=lambda r: -r.char_h * r.n):
            fl = engine.read(row.image, "line")
            ftext = " ".join(l.text for l in fl)
            value, note = _parse_figures_tokens(ftext, words_value)
            if value:
                issues = [note] if note else []
                fields["amount_figures"] = _field(amounts.format_indian(value) + "/-", _mean_conf(fl), "region", ftext, issues)
                break

        # account number
        best, aconf = "", 0.0
        for row in text_rows(_crop(work, "account"), 0.7):
            for ln in engine.read(row.image, "line"):
                tokens = [re.sub(r"[Oo]", "0", t) for t in ln.text.split()]
                cands = [t for t in tokens if re.fullmatch(r"\d{9,18}", t)]
                if not cands:  # digits printed with spaces between them
                    joined = re.sub(r"(?<=\d) (?=\d)", "", " ".join(t for t in tokens if t.isdigit()))
                    cands = re.findall(r"\d{9,18}", joined)
                for cand in cands:
                    if len(cand) > len(best):
                        best, aconf = cand, ln.conf
        if not best:
            m2 = re.search(r"A/?C\.?\s*(?:NO\.?)?\s*[:\-]?\s*(\d{9,18})", page_text, re.I)
            if m2:
                best, aconf = m2.group(1), 0.6
        if best:
            fields["account_number"] = _field(best, aconf, "region")

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
