import importlib.util
from pathlib import Path

MODULE = Path(__file__).with_name("field_extraction_pipeline_patched.py")
spec = importlib.util.spec_from_file_location("field_extraction_pipeline", MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
FieldExtractor = module.FieldExtractor

# Invented values: the lines that were here came from cheques of a private dataset (see reports/final/SECURITY_AUDIT.md).
CASES = [
    ("AXIS", "246810999888777:13579031", "246810", "999888777"),
    ("CANARA", "135792#999888776#31", "135792", "999888776"),
    ("ICICI", '246813"999888775:24680131', "246813", "999888775"),
    ("SYNDICATE", '135795"999888774:246802"31', "135795", "999888774"),
]

def test_bottom_line_extraction():
    for bank, raw, expected_cheque, expected_micr in CASES:
        extractor = FieldExtractor()
        micr, _ = extractor.extract_micr_code(raw)
        cheque, _ = extractor.extract_cheque_number(raw, micr)
        assert micr == expected_micr, (bank, "MICR", micr)
        assert cheque == expected_cheque, (bank, "cheque", cheque)

def test_account_number_alone_is_not_micr_or_cheque():
    extractor = FieldExtractor()
    text = "A/C NO.\n123456789012345\nAccount details"
    assert extractor.extract_micr_code(text)[0] is None
    assert extractor.extract_cheque_number(text)[0] is None

def test_branch_suffix_label():
    value, confidence = FieldExtractor().extract_branch_name("SECUNDERABAD MAIN Branch")
    assert value == "SECUNDERABAD MAIN"
    assert confidence == 0.75
