from pathlib import Path
import ast
import re
import sys
from datetime import datetime

ROOT = Path(r"N:\Cheque\ChequeA")
PIPELINE = ROOT / "backend/utils/field_extraction_pipeline.py"
ROUTES = ROOT / "backend/api/routes.py"
LABELS = ROOT / "backend/utils/confirmed_labels.py"


def replace_method(path, method_name, replacement):
    source = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source)

    target = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name:
            target = node
            break

    if target is None:
        raise RuntimeError(f"Method not found: {method_name} in {path}")

    lines = source.splitlines(keepends=True)
    start = target.lineno - 1
    end = target.end_lineno

    indent = len(lines[start]) - len(lines[start].lstrip())
    replacement_lines = replacement.strip("\n").splitlines()
    replacement_text = "\n".join((" " * indent) + line if line else "" for line in replacement_lines) + "\n"

    updated = "".join(lines[:start]) + replacement_text + "".join(lines[end:])
    ast.parse(updated)
    path.write_text(updated, encoding="utf-8")


# ------------------------------------------------------------
# 1. IFSC: exact match first; cautious corrections near label
# ------------------------------------------------------------
replace_method(PIPELINE, "extract_ifsc_code", r'''
def extract_ifsc_code(self, text):
    text = str(text or "").upper()

    # First accept a correctly read IFSC anywhere in the OCR text.
    exact = re.search(r"\b([A-Z]{4}0[A-Z0-9]{6})\b", text)
    if exact:
        value = exact.group(1)
        self.log_extraction("ifsc_code", "exact_regex", value, 0.95)
        return value, 0.95

    # OCR correction is restricted to text close to an IFSC label.
    lines = text.splitlines()
    known_prefixes = set(IFSC_BANKS.keys())

    for index, line in enumerate(lines):
        if not re.search(r"\b(?:IFSC|IFS\s+CODE)\b", line):
            continue

        context = " ".join(lines[max(0, index - 1):min(len(lines), index + 2)])
        tokens = re.findall(r"(?<![A-Z0-9])[A-Z0-9]{11}(?![A-Z0-9])", context)

        for token in tokens:
            candidate = list(token)

            # The fifth IFSC character must be zero.
            if candidate[4] in ("O", "Q", "D"):
                candidate[4] = "0"

            # Correct the common AXIS OCR confusion UTI8 -> UTIB.
            if "".join(candidate[:4]) == "UTI8":
                candidate[3] = "B"

            corrected = "".join(candidate)

            if (
                re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", corrected)
                and corrected[:4] in known_prefixes
            ):
                self.log_extraction(
                    "ifsc_code", "label_context_ocr_correction", corrected, 0.78
                )
                return corrected, 0.78

    self.log_extraction("ifsc_code", "unavailable", None, 0.0)
    return None, 0.0
''')

# ------------------------------------------------------------
# 2. Bank name: never accept UNKNOWN as a real bank
# ------------------------------------------------------------
replace_method(PIPELINE, "extract_bank_name", r'''
def extract_bank_name(self, text, ifsc_code=None, meta=None):
    if ifsc_code:
        bank = IFSC_BANKS.get(str(ifsc_code)[:4])
        if bank:
            self.log_extraction("bank_name", "ifsc_lookup", bank, 0.95)
            return bank, 0.95

    pattern = r"\b([A-Z]{2,}\s+BANK(?:\s+(?:LTD|LIMITED|OF\s+INDIA))?)\b"
    match = re.search(pattern, text or "", re.IGNORECASE)
    if match:
        bank = re.sub(r"\s+", " ", match.group(1)).strip().upper()
        self.log_extraction("bank_name", "regex", bank, 0.85)
        return bank, 0.85

    if meta and "bank" in meta:
        bank = str(meta.get("bank") or "").strip().upper()
        if bank and bank not in {"UNKNOWN", "N/A", "NONE", "NULL"}:
            self.log_extraction("bank_name", "metadata", bank, 0.80)
            return bank, 0.80

    self.log_extraction("bank_name", "unavailable", None, 0.0)
    return None, 0.0
''')

# ------------------------------------------------------------
# 3. Date: normal formats + cautiously detect split digits
# ------------------------------------------------------------
replace_method(PIPELINE, "extract_date", r'''
def extract_date(self, text):
    text = str(text or "")
    patterns = [
        r"(?<!\d)(\d{2}[/-]\d{2}[/-]\d{4})(?!\d)",
        r"(?<!\d)(\d{8})(?!\d)",
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, text):
            value = match.group(1)
            formats = ("%d/%m/%Y", "%d-%m-%Y") if len(value) == 10 else ("%d%m%Y",)
            for fmt in formats:
                try:
                    datetime.strptime(value, fmt)
                    self.log_extraction("date", "date_regex", value, 0.85)
                    return value, 0.85
                except ValueError:
                    pass

    # Some PaddleOCR outputs place each printed date digit on its own line.
    lines = [line.strip() for line in text.splitlines()]
    for index in range(len(lines)):
        if not re.fullmatch(r"\d", lines[index]):
            continue

        digits = []
        for line in lines[index:index + 8]:
            if not re.fullmatch(r"\d", line):
                break
            digits.append(line)

        if len(digits) != 8:
            continue

        # Require a nearby DATE label to avoid interpreting unrelated numbers.
        nearby = " ".join(lines[max(0, index - 4):min(len(lines), index + 12)])
        if not re.search(r"\bDATE\b", nearby, re.IGNORECASE):
            continue

        value = "".join(digits)
        try:
            datetime.strptime(value, "%d%m%Y")
            self.log_extraction("date", "split_digits_near_date_label", value, 0.72)
            return value, 0.72
        except ValueError:
            continue

    self.log_extraction("date", "unavailable", None, 0.0)
    return None, 0.0
''')

# ------------------------------------------------------------
# 4. Amount words: accept OCR 'lack' as a lakh spelling variant
# ------------------------------------------------------------
replace_method(PIPELINE, "extract_amount_words", r'''
def extract_amount_words(self, text):
    text = str(text or "")
    lines = [
        re.sub(r"\s+", " ", line).strip(" :-")
        for line in text.splitlines()
        if line.strip()
    ]

    word = (
        r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|"
        r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
        r"eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
        r"eighty|ninety|hundred|thousand|lakh|lakhs|lac|lack|"
        r"crore|crores|and|only)"
    )
    phrase = re.compile(rf"(?i)^{word}(?:[\s-]+{word}){{0,20}}$")

    candidates = []
    for line in lines:
        if not phrase.fullmatch(line):
            continue

        if not re.search(
            r"(?i)\b(?:hundred|thousand|lakh|lakhs|lac|lack|crore|crores|only)\b",
            line
        ):
            continue

        # Normalize only this known OCR spelling variant.
        normalized = re.sub(r"(?i)\black\b", "lakh", line)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        candidates.append(normalized)

    unique = list(dict.fromkeys(candidates))
    if len(unique) == 1:
        value = unique[0]
        self.log_extraction(
            "amount_words", "standalone_line_with_ocr_normalization", value, 0.76
        )
        return value, 0.76

    self.log_extraction("amount_words", "ambiguous_or_unavailable", None, 0.0)
    return None, 0.0
''')

# ------------------------------------------------------------
# 5. Route: don't pass UNKNOWN as bank metadata
# ------------------------------------------------------------
route_source = ROUTES.read_text(encoding="utf-8-sig")
old = "extraction_result = extractor.extract_all_fields(ocr_text, meta={'bank': 'UNKNOWN'})"
new = "extraction_result = extractor.extract_all_fields(ocr_text, meta=None)"

if old not in route_source:
    raise RuntimeError("Expected route metadata line not found; routes.py not patched.")

route_source = route_source.replace(old, new, 1)
ast.parse(route_source)
ROUTES.write_text(route_source, encoding="utf-8")

# ------------------------------------------------------------
# 6. Confirmed labels: optional CSV files must not break API
# ------------------------------------------------------------
replace_method(LABELS, "_load", r'''
@lru_cache(maxsize=1)
def _load():
    confirmed = {cid: dict(vals) for cid, vals in BATCH1.items()}

    if TRAIN_DIR.is_dir():
        for path in sorted(TRAIN_DIR.glob("training_transcriptions_batch*.csv")):
            with open(path, encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f):
                    field = TRAIN_FIELDS.get((row.get("field") or "").strip())
                    value = (row.get("value") or "").strip()
                    if not field or (row.get("unreadable") or "").strip().lower() == "yes" or not value:
                        continue
                    cid = (row.get("cheque_id") or "").split("_")[0].strip()
                    if cid:
                        confirmed.setdefault(cid, {})[field] = value

    if GROUND_TRUTH.is_file():
        with open(GROUND_TRUTH, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                cid = (row.get("cheque_id") or "").strip()
                if not cid:
                    continue

                entry = {
                    "date": (row.get("date") or "").strip(),
                    "amount_figures": (row.get("amount_figures") or "").strip(),
                    "amount_words": (row.get("amount_words") or "").strip(),
                    "payee_name": (row.get("payee_name") or "").strip(),
                }
                confirmed.setdefault(cid, {}).update(
                    {key: value for key, value in entry.items() if value}
                )

    return confirmed
''')

# Final syntax check
for path in (PIPELINE, ROUTES, LABELS):
    ast.parse(path.read_text(encoding="utf-8-sig"))
    print(f"SYNTAX OK: {path.relative_to(ROOT)}")

# Focused behavior tests
sys.path.insert(0, str(ROOT / "backend"))
from utils.field_extraction_pipeline import FieldExtractor

extractor = FieldExtractor()

tests = [
    ("IFSC correction", lambda: extractor.extract_ifsc_code("IFS CODE - UTI80000425")),
    ("Amount words", lambda: extractor.extract_amount_words("Eleven lack forty thousand")),
    ("Date standard", lambda: extractor.extract_date("Date: 19/11/2015")),
    ("Date split", lambda: extractor.extract_date("DATE\n1\n9\n1\n1\n2\n0\n1\n5")),
    ("Unknown bank", lambda: extractor.extract_bank_name("", None, {"bank": "UNKNOWN"})),
]

print("\n========== FOCUSED TESTS ==========")
for name, test in tests:
    result = test()
    print(f"{name}: {result}")

assert extractor.extract_ifsc_code("IFS CODE - UTI80000425")[0] == "UTIB0000425"
assert extractor.extract_amount_words("Eleven lack forty thousand")[0] == "Eleven lakh forty thousand"
assert extractor.extract_date("Date: 19/11/2015")[0] == "19/11/2015"
assert extractor.extract_date("DATE\n1\n9\n1\n1\n2\n0\n1\n5")[0] == "19112015"
assert extractor.extract_bank_name("", None, {"bank": "UNKNOWN"})[0] is None

print("\nPHASE 1 PATCH AND FOCUSED TESTS: PASSED")