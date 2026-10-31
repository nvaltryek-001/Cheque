import re
from pathlib import Path
from datetime import datetime

path = Path(r"backend/utils/field_extraction_pipeline.py")
source = path.read_text(encoding="utf-8")

def replace_method(source, name, replacement):
    pattern = rf"(?ms)^    def {name}\(self,.*?(?=^    def |\Z)"
    updated, count = re.subn(pattern, lambda _: replacement.rstrip() + "\n\n", source, count=1)
    if count != 1:
        raise RuntimeError(f"Could not safely replace {name}")
    return updated

date_method = r'''
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
'''

words_method = r'''
    def extract_amount_words(self, text):
        text = text or ""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        number_words = (
            r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|"
            r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
            r"eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
            r"eighty|ninety|hundred|thousand|lakh|lakhs|crore|crores|"
            r"and|only|rupee|rupees)"
        )
        excluded = re.compile(
            r"(?i)^(?:valid|three months|months|date|ddmmyyyy|pay|"
            r"or bearer|bearer|axis bank|ifsc|ifs code|account|rupees)$"
        )

        for i, line in enumerate(lines):
            if re.search(r"(?i)\brupees?\b", line):
                candidates = []
                for following in lines[i + 1:i + 5]:
                    if re.search(r"(?i)\b(?:or bearer|bearer|pay)\b", following):
                        break
                    if re.search(r"\d", following) or excluded.match(following):
                        if candidates:
                            break
                        continue
                    if re.fullmatch(
                        rf"(?i)(?:{number_words})(?:[\s-]+{number_words}){{0,14}}",
                        following
                    ):
                        candidates.append(following)
                    elif candidates:
                        break
                if candidates:
                    value = " ".join(candidates)
                    self.log_extraction("amount_words", "labelled_regex", value, 0.80)
                    return value, 0.80

        self.log_extraction("amount_words", "unavailable", None, 0.0)
        return None, 0.0
'''

payee_method = r'''
    def extract_payee_name(self, text):
        text = text or ""
        lines = [re.sub(r"\s+", " ", x).strip(" :-") for x in text.splitlines()]
        lines = [x for x in lines if x]

        excluded = re.compile(
            r"(?i)^(?:pay|to|order|or bearer|bearer|rupees?|date|ddmmyyyy|"
            r"amount|axis bank(?: ltd)?|ifsc(?: code)?|ifs code|micr|"
            r"account(?: number)?|valid for.*|please sign.*|.*\d.*)$"
        )

        # Prefer text between the PAY label and the next known cheque field.
        for i, line in enumerate(lines):
            if re.fullmatch(r"(?i)pay(?: to)?", line):
                candidates = []
                for following in lines[i + 1:i + 5]:
                    if re.search(r"(?i)\b(?:rupees?|or bearer|bearer)\b", following):
                        break
                    if excluded.match(following):
                        continue
                    if re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,60}", following):
                        candidates.append(following)
                if candidates:
                    value = candidates[0]
                    self.log_extraction("payee_name", "pay_label", value, 0.70)
                    return value, 0.70

        # Conservative fallback: a short name-like line immediately before
        # OR BEARER. Never return the complete OCR document as a name.
        for i, line in enumerate(lines):
            if re.search(r"(?i)\bor bearer\b|\bbearer\b", line):
                for candidate in reversed(lines[max(0, i - 3):i]):
                    if excluded.match(candidate):
                        continue
                    if re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,60}", candidate):
                        value = candidate.strip()
                        self.log_extraction("payee_name", "near_bearer", value, 0.55)
                        return value, 0.55

        self.log_extraction("payee_name", "unavailable", None, 0.0)
        return None, 0.0
'''

for name, method in [
    ("extract_date", date_method),
    ("extract_amount_words", words_method),
    ("extract_payee_name", payee_method),
]:
    source = replace_method(source, name, method)

compile(source, str(path), "exec")
path.write_text(source, encoding="utf-8")
print("Updated extraction methods successfully.")
