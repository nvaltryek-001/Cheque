import re
from datetime import datetime
from typing import Dict, Tuple

FIELD_ORDER = ["bank_name", "cheque_number", "micr_code", "account_number", "date", "payee_name", "amount_figures", "amount_words", "ifsc_code", "branch_name"]

IFSC_BANKS = {
    "ICIC": "ICICI BANK", "UTIB": "AXIS BANK", "SYNB": "SYNDICATE BANK", "CNRB": "CANARA BANK",
    "SBIN": "STATE BANK OF INDIA", "HDFC": "HDFC BANK", "UBIN": "UNION BANK OF INDIA",
    "PUNB": "PUNJAB NATIONAL BANK", "IDIB": "INDIAN BANK", "BARB": "BANK OF BARODA",
}

class FieldExtractor:
    """Extract cheque fields without inventing missing values."""

    def __init__(self, metadata=None):
        self.metadata = metadata or {}
        self.extraction_log = []

    def log_extraction(self, field, method, value, confidence):
        self.extraction_log.append({"field": field, "method": method, "value": value, "confidence": confidence})

    def extract_ifsc_code(self, text):
        pattern = r"\b([A-Z]{4}0[A-Z0-9]{6})\b"
        m = re.search(pattern, text or "")
        if m:
            self.log_extraction("ifsc_code", "regex", m.group(1), 0.95)
            return m.group(1), 0.95
        return None, 0.0

    def extract_bank_name(self, text, ifsc_code=None, meta=None):
        if ifsc_code:
            bank = IFSC_BANKS.get(ifsc_code[:4])
            if bank:
                self.log_extraction("bank_name", "ifsc_lookup", bank, 0.95)
                return bank, 0.95

        pattern = r"\b([A-Z]{2,}\s+BANK(?:\s+(?:LTD|LIMITED|OF\s+INDIA))?)\b"
        m = re.search(pattern, text or "", re.IGNORECASE)
        if m:
            self.log_extraction("bank_name", "regex", m.group(1), 0.85)
            return m.group(1), 0.85

        if meta and "bank" in meta:
            bank = meta.get("bank", "").upper()
            if bank:
                self.log_extraction("bank_name", "metadata", bank, 0.99)
                return bank, 0.99

        self.log_extraction("bank_name", "unavailable", None, 0.0)
        return None, 0.0

    def _micr_line_candidates(self, text):
        """Read cheque number + MICR only from a MICR-like line near the image bottom.

        OCR often concatenates the cheque serial (6 digits), MICR (9 digits),
        and auxiliary symbols/digits. We never search arbitrary account/date lines.
        """
        lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
        candidates = []
        for line_index, line in enumerate(lines[-6:]):
            runs = re.findall(r"\d+", line)
            # OCR may merge the first 15 digits into one run.
            if len(runs) == 1 and len(runs[0]) >= 15:
                digits = runs[0]
                runs = [digits[:6], digits[6:15]]
            elif runs and len(runs[0]) >= 15:
                digits = runs[0]
                runs = [digits[:6], digits[6:15]] + runs[1:]

            if len(runs) < 2:
                continue
            cheque_candidate, micr_candidate = runs[0], runs[1]
            digit_count = sum(len(run) for run in re.findall(r"\d+", line))
            has_separator = bool(re.search(r"\D+", line))
            # A plain 15-digit account number is not a MICR line. Require
            # either separators between printed MICR groups or trailing
            # auxiliary MICR digits after the 6+9 core.
            if (
                len(cheque_candidate) == 6
                and len(micr_candidate) == 9
                and (has_separator or digit_count > 15)
            ):
                candidates.append((cheque_candidate, micr_candidate, line))
        # Keep only unique pairs. Multiple different candidates are ambiguous.
        unique = list(dict.fromkeys((c[0], c[1]) for c in candidates))
        return unique

    def extract_micr_code(self, text):
        candidates = self._micr_line_candidates(text)
        micrs = list(dict.fromkeys(micr for _, micr in candidates))
        if len(micrs) == 1:
            value = micrs[0]
            self.log_extraction("micr_code", "bottom_micr_line", value, 0.80)
            return value, 0.80
        self.log_extraction("micr_code", "bottom_micr_line_missing_or_ambiguous", None, 0.0)
        return None, 0.0

    def extract_cheque_number(self, text, micr_code=None):
        """Prefer a 6-digit serial paired with a 9-digit MICR on a bottom line."""
        candidates = self._micr_line_candidates(text)
        cheque_numbers = list(dict.fromkeys(
            cheque for cheque, micr in candidates
            if micr_code is None or micr == str(micr_code)
        ))
        if len(cheque_numbers) == 1:
            value = cheque_numbers[0]
            self.log_extraction("cheque_number", "bottom_micr_line_serial", value, 0.80)
            return value, 0.80

        text = text or ""
        label = re.compile(
            r"\b(?:CHEQUE|CHECK|CHQ)\s*(?:NO\.?|NUMBER|NUM|#)\s*[:.\-]?\s*",
            re.I
        )
        values = []
        for match in label.finditer(text):
            number = re.match(r"\s*(\d{4,8})(?!\d)", text[match.end():match.end()+100])
            if number:
                value = number.group(1)
                if not micr_code or value not in str(micr_code):
                    values.append(value)
        values = list(dict.fromkeys(values))
        if len(values) == 1:
            self.log_extraction("cheque_number", "explicit_cheque_label", values[0], 0.90)
            return values[0], 0.90

        self.log_extraction("cheque_number", "label_or_bottom_line_missing_or_ambiguous", None, 0.0)
        return None, 0.0

    def extract_account_number(self, text):
        """Extract an account number close to an A/C or account label."""
        lines = str(text or "").splitlines()
        label_pattern = re.compile(
            r"\b(?:A\s*/?\s*C|ACCOUNT)\s*(?:NO\.?|NUMBER|NUM|#)?",
            re.I
        )
        blocked_pattern = re.compile(
            r"\b(?:MICR|IFSC|IFS\s+CODE|RUPEES|PAY|DATE|DDMMYYYY)\b",
            re.I
        )

        for i, line in enumerate(lines):
            match = label_pattern.search(line)
            if not match:
                continue

            # OCR can place the account number immediately before the label.
            candidates = []
            if i > 0:
                candidates.append(lines[i - 1])
            candidates.append(line[match.end():])
            candidates.extend(lines[i + 1:min(i + 4, len(lines))])

            for candidate in candidates:
                if blocked_pattern.search(candidate):
                    continue

                groups = re.findall(
                    r"(?<!\d)(\d(?:[\s-]*\d){9,17})(?!\d)",
                    candidate
                )
                for group in groups:
                    digits = re.sub(r"\D", "", group)
                    if 10 <= len(digits) <= 18:
                        self.log_extraction(
                            "account_number", "near_account_label", digits, 0.80
                        )
                        return digits, 0.80

        return None, 0.0


    def extract_date(self, text):
        text = text or ""
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
                        self.log_extraction("date", "regex", value, 0.85)
                        return value, 0.85
                    except ValueError:
                        pass

        self.log_extraction("date", "unavailable", None, 0.0)
        return None, 0.0

    def extract_amount_figures(self, text):
        """Prefer explicit currency amounts and avoid guessing from bare numbers."""
        text = text or ""

        # Indian and international grouping, with an optional decimal fraction.
        number = r"(?:\d{1,3}(?:,\d{2})*,\d{3}|\d+)(?:\.\d{1,2})?"

        # Prefer currency-labelled candidates, including common OCR variants.
        currency_pattern = re.compile(
            rf"(?i)(?:\bINR\b|\bRs\.?|\bRupees?\b|₹|â‚¹)\s*({number})"
        )
        labelled = []
        for match in currency_pattern.finditer(text):
            value = match.group(1)
            digits = re.sub(r"\D", "", value)
            if digits:
                labelled.append((value, digits))

        unique_labelled = {digits: value for value, digits in labelled}

        if len(unique_labelled) == 1:
            value = next(iter(unique_labelled.values()))
            self.log_extraction("amount_figures", "currency_label", value, 0.85)
            return value, 0.85

        if len(unique_labelled) > 1:
            self.log_extraction("amount_figures", "ambiguous_currency_candidates", None, 0.0)
            return None, 0.0

        # Without a currency label, only accept a single clearly grouped amount.
        grouped_pattern = re.compile(
            r"(?<![\d,])(\d{1,3}(?:,\d{2})*,\d{3}(?:\.\d{1,2})?)(?![\d,])"
        )
        grouped = []
        for match in grouped_pattern.finditer(text):
            value = match.group(1)
            digits = re.sub(r"\D", "", value)
            if digits:
                grouped.append((value, digits))

        unique_grouped = {digits: value for value, digits in grouped}
        if len(unique_grouped) == 1:
            value = next(iter(unique_grouped.values()))
            self.log_extraction("amount_figures", "unique_grouped_candidate", value, 0.60)
            return value, 0.60

        self.log_extraction("amount_figures", "ambiguous_or_unavailable", None, 0.0)
        return None, 0.0

    def extract_amount_words(self, text):
        """Find a clearly worded amount without inventing missing values."""
        text = text or ""
        lines = [
            re.sub(r"\s+", " ", line).strip(" :-")
            for line in text.splitlines()
            if line.strip()
        ]

        word = (
            r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|"
            r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
            r"eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
            r"eighty|ninety|hundred|thousand|lakh|lakhs|lac|crore|crores|"
            r"and|only)"
        )
        phrase = re.compile(
            rf"(?i)^{word}(?:[\s-]+{word}){{0,20}}$"
        )

        candidates = []
        for line in lines:
            if phrase.fullmatch(line) and re.search(
                r"(?i)\b(?:hundred|thousand|lakh|lakhs|lac|crore|crores|only)\b",
                line
            ):
                normalized = re.sub(r"\s+", " ", line).strip()
                candidates.append(normalized)

        unique = list(dict.fromkeys(candidates))
        if len(unique) == 1:
            value = unique[0]
            self.log_extraction(
                "amount_words", "standalone_amount_words_line", value, 0.80
            )
            return value, 0.80

        self.log_extraction(
            "amount_words", "ambiguous_or_unavailable", None, 0.0
        )
        return None, 0.0


    def extract_payee_name(self, text):
        """Prefer a name adjacent to a PAY label; avoid unrelated names."""
        text = text or ""
        lines = [
            re.sub(r"\s+", " ", line).strip(" :-")
            for line in text.splitlines()
            if line.strip()
        ]

        excluded = re.compile(
            r"(?i)^(?:pay|pay to|to|order|or bearer|bearer|rupees?|"
            r"date|ddmmYYYY|ddmmyyyy|amount|rupees|rupees only|"
            r"axis bank(?: ltd)?|ifsc(?: code)?|ifs code|micr|"
            r"account(?: number)?|valid for.*|please sign.*|.*\d.*)$"
        )
        name_pattern = re.compile(r"[A-Za-z][A-Za-z .'-]{1,60}")

        def valid_name(candidate):
            candidate = candidate.strip(" :-")
            return (
                bool(name_pattern.fullmatch(candidate))
                and not excluded.fullmatch(candidate)
                and len(candidate.split()) >= 2
            )

        for i, line in enumerate(lines):
            inline = re.match(r"(?i)^pay(?:\s+to)?\s+(.+)$", line)
            if inline:
                candidate = re.split(
                    r"(?i)\s+(?:rupees?|or bearer|bearer)\b",
                    inline.group(1)
                )[0].strip()
                if valid_name(candidate):
                    self.log_extraction(
                        "payee_name", "inline_pay_label", candidate, 0.75
                    )
                    return candidate, 0.75

            if re.fullmatch(r"(?i)pay(?:\s+to)?", line):
                # OCR reading order can place the payee immediately before PAY.
                before = [
                    lines[j] for j in range(max(0, i - 3), i)
                    if valid_name(lines[j])
                ]
                after = [
                    lines[j] for j in range(i + 1, min(len(lines), i + 4))
                    if valid_name(lines[j])
                ]

                if len(before) == 1:
                    value = before[0]
                    self.log_extraction(
                        "payee_name", "unique_name_before_pay_label",
                        value, 0.65
                    )
                    return value, 0.65

                # Only use the following context if no plausible preceding name.
                if not before and len(after) == 1:
                    value = after[0]
                    self.log_extraction(
                        "payee_name", "unique_name_after_pay_label",
                        value, 0.65
                    )
                    return value, 0.65

        self.log_extraction("payee_name", "unavailable", None, 0.0)
        return None, 0.0


    def extract_branch_name(self, text):
        """Extract a labelled branch or cautiously infer a locality from an address."""
        text = str(text or "")
        excluded = re.compile(
            r"\b(?:BANK|IFSC|MICR|PIN|DATE|PAY|RUPEES|ACCOUNT|"
            r"VALID|HYDERABAD|INDIA)\b",
            re.I
        )

        # Prefer an explicitly labelled branch.
        labelled_patterns = [
            r"(?im)^\s*branch(?:\s+name)?\s*[:\-]\s*"
            r"([A-Za-z][A-Za-z .'-]{1,60}?)\s*$",
            r"(?im)^\s*branch\s+([A-Za-z][A-Za-z .'-]{1,60}?)\s*$",
        ]

        for pattern in labelled_patterns:
            match = re.search(pattern, text)
            if match:
                value = match.group(1).strip(" .-")
                if len(value) >= 3 and not re.search(r"\d", value) and not excluded.search(value):
                    self.log_extraction("branch_name", "explicit_branch_label", value, 0.80)
                    return value, 0.80

        # Handle a simple locality followed by a PIN, e.g. "Kompally-500014".
        for line in text.splitlines():
            match = re.match(
                r"^\s*([A-Za-z][A-Za-z .'-]{2,40}?)\s*[-,]\s*(\d{6})\s*$",
                line
            )
            if match:
                value = match.group(1).strip(" .-")
                if len(value) >= 3 and not excluded.search(value):
                    self.log_extraction(
                        "branch_name", "locality_before_pin_code", value, 0.72
                    )
                    return value, 0.72

        # Handle a multi-part address ending in a PIN.
        # Example: "MEHDIPATNAM, HYDERABAD [AP], HYDERABAD, 500028".
        # The first address component is a locality candidate, not proof
        # of the bank's official branch name.
        for line in text.splitlines():
            address = re.match(r"^\s*(.+,\s*)(\d{6})\s*$", line)
            if not address:
                continue

            parts = [part.strip(" ,.-") for part in address.group(1).split(",")]
            if len(parts) < 2:
                continue

            candidate = parts[0]
            candidate = re.sub(r"\s*\[[^\]]*\]\s*", "", candidate).strip()

            if (
                re.fullmatch(r"[A-Za-z][A-Za-z .'-]{2,40}", candidate)
                and not excluded.search(candidate)
            ):
                self.log_extraction(
                    "branch_name", "address_locality_before_pin_code",
                    candidate, 0.60
                )
                return candidate, 0.60

        # OCR may read a printed branch name followed by the word "Branch",
        # for example "SECUNDERABAD MAIN Branch". This is an explicit label.
        for line in text.splitlines():
            match = re.match(
                r"^\s*([A-Za-z][A-Za-z .'-]{2,50}?)\s+Branch\s*$",
                line, re.I
            )
            if match:
                value = match.group(1).strip(" .-")
                if not excluded.search(value):
                    self.log_extraction("branch_name", "name_before_branch_label", value, 0.75)
                    return value, 0.75

        self.log_extraction("branch_name", "unavailable", None, 0.0)
        return None, 0.0

    def extract_all_fields(self, ocr_text, meta=None):
        """Extract all fields with zero-blank guarantee."""
        results = {}
        confidence = {}

        ifsc_value, ifsc_conf = self.extract_ifsc_code(ocr_text)
        results["ifsc_code"] = ifsc_value
        confidence["ifsc_code"] = ifsc_conf

        results["bank_name"], confidence["bank_name"] = self.extract_bank_name(ocr_text, ifsc_value, meta)
        results["micr_code"], confidence["micr_code"] = self.extract_micr_code(ocr_text)
        results["cheque_number"], confidence["cheque_number"] = self.extract_cheque_number(ocr_text, results["micr_code"])
        results["account_number"], confidence["account_number"] = self.extract_account_number(ocr_text)
        results["date"], confidence["date"] = self.extract_date(ocr_text)
        results["amount_figures"], confidence["amount_figures"] = self.extract_amount_figures(ocr_text)
        results["amount_words"], confidence["amount_words"] = self.extract_amount_words(ocr_text)
        results["payee_name"], confidence["payee_name"] = self.extract_payee_name(ocr_text)
        results["branch_name"], confidence["branch_name"] = self.extract_branch_name(ocr_text)

        # Missing extraction must remain missing, never fabricated.
        for field in FIELD_ORDER:
            if results.get(field) is None or results.get(field) == "":
                results[field] = None
                confidence[field] = 0.0

        return {
            "fields": {k: results[k] for k in FIELD_ORDER},
            "confidence": confidence,
            "extraction_log": self.extraction_log,
            "has_blanks": any(v is None or v == "" for v in results.values())
        }
