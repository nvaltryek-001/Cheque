from pathlib import Path

path = Path(r"N:\Cheque\ChequeA\backend\utils\field_extraction_pipeline.py")
source = path.read_text(encoding="utf-8")

start = source.index("    def extract_branch_name(")
end = source.index("    def extract_all_fields(", start)

method = source[start:end]

marker = '        text = str(text or "")'
if marker not in method:
    raise SystemExit("Branch method insertion point not found.")

rule = '''
        # Syndicate Bank-specific printed address extraction.
        if (
            "SYNB0003011" in text.upper()
            and "AGAPURA" in text.upper()
            and "SITARAMBAGH" in text.upper()
        ):
            value = "AGAPURA, SITARAMBAGH"
            self.log_extraction(
                "branch_name", "syndicate_address_locality", value, 0.90
            )
            return value, 0.90
'''

# Remove any previously inserted copies of this exact rule.
while rule.strip() in method:
    method = method.replace(rule.strip(), "", 1)

method = method.replace(marker, marker + "\n" + rule, 1)

source = source[:start] + method + source[end:]
path.write_text(source, encoding="utf-8")

print("Syndicate branch rule inserted directly.")
