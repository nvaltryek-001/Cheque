import re

FIELD_ORDER = [
    "bank_name",
    "cheque_number",
    "micr_code",
    "account_number",
    "date",
    "payee_name",
    "amount_figures",
    "amount_words",
    "ifsc_code",
]

BANK_RE = re.compile(r"\b([A-Z]{2,}\s+BANK(?:\s+(?:LTD|LIMITED|OF\s+INDIA))?)\b")
IFSC_RE = re.compile(r"\b([A-Z]{4}0[A-Z0-9]{6})\b")
ACCOUNT_RE = re.compile(r"A/?C\s*NO\.?\s*:?\s*((?:\d\s+)+\d(?!\d)|\d{6,18})", re.IGNORECASE)
MICR_RE = re.compile(r"(?<!\d)(\d{6})[\s\W]*(\d{3})[\s\W]*(\d{3})[\s\W]*(\d{3})(?!\d)")


IFSC_BANKS = {
    "ICIC": "ICICI BANK",
    "UTIB": "AXIS BANK",
    "SYNB": "SYNDICATE BANK",
    "CNRB": "CANARA BANK",
    "SBIN": "STATE BANK OF INDIA",
    "HDFC": "HDFC BANK",
    "UBIN": "UNION BANK OF INDIA",
    "PUNB": "PUNJAB NATIONAL BANK",
    "IDIB": "INDIAN BANK",
    "BARB": "BANK OF BARODA",
}


def _printed(ocr_text):
    text = ocr_text or ""
    out = {}
    m = IFSC_RE.search(text)
    out["ifsc_code"] = m.group(1) if m else None
    out["bank_name"] = IFSC_BANKS.get(out["ifsc_code"][:4]) if out["ifsc_code"] else None
    if out["bank_name"] is None:
        m = BANK_RE.search(text)
        out["bank_name"] = m.group(1) if m else None
    m = ACCOUNT_RE.search(text)
    out["account_number"] = re.sub(r"\s", "", m.group(1)) if m else None
    out["cheque_number"] = None
    out["micr_code"] = None
    account_digits = re.sub(r"\D", "", out["account_number"] or "")
    for m in MICR_RE.finditer(text):
        digits = m.group(1) + m.group(2) + m.group(3) + m.group(4)
        if account_digits and digits == account_digits:
            continue
        out["cheque_number"] = m.group(1)
        out["micr_code"] = m.group(2) + m.group(3) + m.group(4)
        break
    return out


def _digits_or_none(value, length):
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if len(digits) == length else None


def build_fields(ocr, vlm, confirmed=None):
    printed = _printed((ocr or {}).get("text"))
    handwritten = dict((vlm or {}).get("fields") or {})
    if confirmed:
        handwritten.update({
            "date": confirmed.get("date", handwritten.get("date")),
            "amount_figures": confirmed.get("amount_figures", handwritten.get("amount_figures")),
            "amount_words": confirmed.get("amount_words", handwritten.get("amount_words")),
            "payee_name": confirmed.get("payee_name", handwritten.get("payee_name")),
        })
    values = {
        "bank_name": printed["bank_name"],
        "cheque_number": printed["cheque_number"] or _digits_or_none(handwritten.get("cheque_number"), 6),
        "micr_code": printed["micr_code"] or _digits_or_none(handwritten.get("micr_code"), 9),
        "account_number": printed["account_number"] or re.sub(r"\D", "", str(handwritten.get("account_number") or "")) or None,
        "date": handwritten.get("date"),
        "payee_name": handwritten.get("payee_name"),
        "amount_figures": handwritten.get("amount_figures"),
        "amount_words": handwritten.get("amount_words"),
        "ifsc_code": printed["ifsc_code"],
    }
    return {key: values[key] for key in FIELD_ORDER}
