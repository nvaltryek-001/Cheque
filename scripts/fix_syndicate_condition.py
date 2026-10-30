from pathlib import Path

path = Path(r"N:\Cheque\ChequeA\backend\utils\field_extraction_pipeline.py")
source = path.read_text(encoding="utf-8")

start = source.index("    def extract_branch_name(")
end = source.index("    def extract_all_fields(", start)

section = source[start:end]
lines = section.splitlines()

changed = False
for i, line in enumerate(lines):
    if "SYNB0003011" in line and "if " in line:
        indent = line[:len(line) - len(line.lstrip())]
        lines[i] = indent + 'if "SYNB0003011" in text.upper():'
        changed = True
        break

if not changed:
    raise SystemExit("Could not locate the Syndicate IFSC condition. No change made.")

path.write_text(
    source[:start] + "\n".join(lines) + "\n" + source[end:],
    encoding="utf-8"
)
print("Syndicate IFSC condition corrected.")
