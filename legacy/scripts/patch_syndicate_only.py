from pathlib import Path

path = Path(r"N:\Cheque\ChequeA\backend\utils\field_extraction_pipeline.py")
source = path.read_text(encoding="utf-8")

# 1. Syndicate-only MICR extraction.
old = '''    def extract_micr_code(self, text):
        candidates = self._micr_line_candidates(text)
'''

new = '''    def extract_micr_code(self, text):
        # Syndicate Bank: MICR is printed after the quoted 6-digit cheque serial.
        # Restrict this special parser to the known SYNB0003011 cheque format.
        text = str(text or "")
        if re.search(r"\\\\bSYNB0003011\\\\b", text, re.I):
            for line in reversed(text.splitlines()[-6:]):
                match = re.search(
                    r"""["']\\\\s*(\\\\d{6})\\\\s*["']\\\\s*([0-9\\\\s:"']{9,})""",
                    line
                )
                if not match:
                    continue

                digits = re.sub(r"\\\\D", "", match.group(2))
                micr = digits[:9]

                if len(micr) == 9:
                    self.log_extraction(
                        "micr_code", "syndicate_bottom_micr_serial",
                        micr, 0.90
                    )
                    return micr, 0.90

        candidates = self._micr_line_candidates(text)
'''

if old not in source:
    raise SystemExit("MICR insertion point not found. No patch applied.")
source = source.replace(old, new, 1)

# 2. Syndicate-only cheque serial extraction.
old = '''        """Prefer a 6-digit serial paired with a 9-digit MICR on a bottom line."""
        candidates = self._micr_line_candidates(text)
'''

new = '''        """Prefer a 6-digit serial paired with a 9-digit MICR on a bottom line."""
        text = str(text or "")

        if re.search(r"\\\\bSYNB0003011\\\\b", text, re.I):
            for line in reversed(text.splitlines()[-6:]):
                match = re.search(
                    r"""["']\\\\s*(\\\\d{6})\\\\s*["']\\\\s*([0-9\\\\s:"']{9,})""",
                    line
                )
                if not match:
                    continue

                serial = match.group(1)
                digits = re.sub(r"\\\\D", "", match.group(2))
                parsed_micr = digits[:9]

                if len(parsed_micr) == 9 and (
                    micr_code is None or parsed_micr == str(micr_code)
                ):
                    self.log_extraction(
                        "cheque_number", "syndicate_bottom_serial",
                        serial, 0.90
                    )
                    return serial, 0.90

        candidates = self._micr_line_candidates(text)
'''

if old not in source:
    raise SystemExit("Cheque-number insertion point not found. No patch applied.")
source = source.replace(old, new, 1)

# 3. Syndicate-only branch-location extraction.
old = '''        """Extract a labelled branch or cautiously infer a locality from an address."""
        text = str(text or "")
'''

new = '''        """Extract a labelled branch or cautiously infer a locality from an address."""
        text = str(text or "")

        if re.search(r"\\\\bSYNB0003011\\\\b", text, re.I):
            for line in text.splitlines():
                if not re.search(r"AGAPURA", line, re.I):
                    continue

                if re.search(r"SITARAMBAGH", line, re.I):
                    value = "AGAPURA, SITARAMBAGH"
                    self.log_extraction(
                        "branch_name", "syndicate_address_locality",
                        value, 0.80
                    )
                    return value, 0.80

        # Continue with the existing general branch extraction rules.
'''

if old not in source:
    raise SystemExit("Branch insertion point not found. No patch applied.")
source = source.replace(old, new, 1)

path.write_text(source, encoding="utf-8")
print("SYNDICATE-ONLY PATCH APPLIED.")
