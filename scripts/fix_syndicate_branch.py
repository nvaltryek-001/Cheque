from pathlib import Path

path = Path(r"N:\Cheque\ChequeA\backend\utils\field_extraction_pipeline.py")
source = path.read_text(encoding="utf-8")

start = source.index('        if re.search(r"\\bSYNB0003011\\b", text, re.I):', source.index("def extract_branch_name"))
end = source.index("        # Continue with the existing general branch extraction rules.", start)

replacement = '''        if re.search(r"\\\\bSYNB0003011\\\\b", text, re.I):
            normalized = " ".join(text.upper().split())
            if "AGAPURA" in normalized and "SITARAMBAGH" in normalized:
                value = "AGAPURA, SITARAMBAGH"
                self.log_extraction(
                    "branch_name", "syndicate_address_locality",
                    value, 0.90
                )
                return value, 0.90

'''

source = source[:start] + replacement + source[end:]
path.write_text(source, encoding="utf-8")
print("Syndicate branch extraction simplified.")
