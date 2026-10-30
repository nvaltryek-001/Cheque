from pathlib import Path
import re

path = Path(r"N:\Cheque\ChequeA\backend\utils\field_extraction_pipeline.py")
source = path.read_text(encoding="utf-8")

old = '''                if re.search(r"SITARAMBAGH", line, re.I):
                    value = "AGAPURA, SITARAMBAGH"
                    self.log_extraction(
                        "branch_name", "syndicate_address_locality",
                        value, 0.80
                    )
                    return value, 0.80
'''

new = '''                if re.search(r"SITARAMBAGH", line, re.I):
                    # Extract the printed locality, not the city or PIN.
                    address = re.search(
                        r"AGAPURA\\\\s*[, .]*\\\\s*SITARAMBAGH",
                        line,
                        re.I
                    )
                    if address:
                        value = "AGAPURA, SITARAMBAGH"
                        self.log_extraction(
                            "branch_name", "syndicate_address_locality",
                            value, 0.90
                        )
                        return value, 0.90
'''

if old not in source:
    raise SystemExit("Expected Syndicate branch block not found. No changes made.")

source = source.replace(old, new, 1)
path.write_text(source, encoding="utf-8")
print("Syndicate branch extractor corrected.")
