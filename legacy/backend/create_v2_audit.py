import inspect
from pathlib import Path
from utils.field_extraction_pipeline import FieldExtractor

out = []

def add(title, source):
    out.append("\n" + "=" * 80)
    out.append(title)
    out.append("=" * 80)
    out.append(source)

add("ACTIVE MODULE", str(inspect.getfile(FieldExtractor)))

methods = [
    "_micr_line_candidates",
    "extract_micr_code",
    "extract_cheque_number",
    "extract_ifsc_code",
    "extract_bank_name",
    "extract_date",
    "extract_amount_words",
    "extract_branch_name",
    "extract_all_fields",
]

for name in methods:
    method = getattr(FieldExtractor, name, None)
    if method is not None:
        add(name, inspect.getsource(method))
    else:
        add(name, "METHOD NOT FOUND")

routes = Path("api/routes.py")
if routes.exists():
    lines = routes.read_text(encoding="utf-8").splitlines()
    add(
        "API ROUTE EXTRACTION INTEGRATION (LINES 225-275)",
        "\n".join(
            f"{i+1}: {lines[i]}"
            for i in range(224, min(275, len(lines)))
        )
    )

output = Path(r"N:\Cheque\ChequeA\reports\V2_INTEGRATION_SOURCE_AUDIT.txt")
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text("\n".join(out), encoding="utf-8")

print("AUDIT CREATED:", output)
print("METHODS INSPECTED:", len(methods))
