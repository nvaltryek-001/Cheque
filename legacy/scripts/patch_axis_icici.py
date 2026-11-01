from pathlib import Path

path = Path(r"N:\Cheque\ChequeA\backend\utils\field_extraction_pipeline.py")
source = path.read_text(encoding="utf-8")

micr_old = '''    def extract_micr_code(self, text):
        candidates = self._micr_line_candidates(text)
'''

micr_new = '''    def extract_micr_code(self, text):
        text = str(text or "")
        upper = text.upper()

        is_axis = "AXIS" in upper or "UTIB" in upper or "UTI8" in upper
        is_icici = "ICICI" in upper or "ICIC" in upper

        if is_axis or is_icici:
            for line in reversed(text.splitlines()[-6:]):
                if ":" not in line:
                    continue

                match = re.search(
                    r"""["']\\\\s*(\\\\d{6})\\\\s*["']{1,2}\\\\s*([0-9\\\\s:"']{9,})""",
                    line
                )
                if not match:
                    continue

                digits = re.sub(r"\\\\D", "", match.group(2))
                micr = digits[:9]

                if len(micr) == 9:
                    self.log_extraction(
                        "micr_code", "axis_icici_bottom_micr",
                        micr, 0.90
                    )
                    return micr, 0.90

        candidates = self._micr_line_candidates(text)
'''

if micr_old not in source:
    raise SystemExit("MICR method insertion point not found.")

source = source.replace(micr_old, micr_new, 1)

cheque_old = '''    def extract_cheque_number(self, text, micr_code=None):
        """Prefer a 6-digit serial paired with a 9-digit MICR on a bottom line."""
        candidates = self._micr_line_candidates(text)
'''

cheque_new = '''    def extract_cheque_number(self, text, micr_code=None):
        """Prefer a 6-digit serial paired with a 9-digit MICR on a bottom line."""
        text = str(text or "")
        upper = text.upper()

        is_axis = "AXIS" in upper or "UTIB" in upper or "UTI8" in upper
        is_icici = "ICICI" in upper or "ICIC" in upper

        if is_axis or is_icici:
            for line in reversed(text.splitlines()[-6:]):
                if ":" not in line:
                    continue

                match = re.search(
                    r"""["']\\\\s*(\\\\d{6})\\\\s*["']{1,2}\\\\s*([0-9\\\\s:"']{9,})""",
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
                        "cheque_number", "axis_icici_bottom_serial",
                        serial, 0.90
                    )
                    return serial, 0.90

        candidates = self._micr_line_candidates(text)
'''

if cheque_old not in source:
    raise SystemExit("Cheque-number method insertion point not found.")

source = source.replace(cheque_old, cheque_new, 1)

path.write_text(source, encoding="utf-8")
print("AXIS + ICICI targeted parser installed.")
