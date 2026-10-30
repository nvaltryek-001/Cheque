import importlib.util
from pathlib import Path

MODULE = Path(__file__).with_name("field_extraction_pipeline_patched.py")
spec = importlib.util.spec_from_file_location("field_extraction_pipeline", MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
FieldExtractor = module.FieldExtractor

CASES = [
    ("AXIS", "309090500211012:42616031", "309090", "500211012"),
    ("CANARA", "405112#500015048#31", "405112", "500015048"),
    ("ICICI", '162952"500229009:55145231', "162952", "500229009"),
    ("SYNDICATE", '110246"500025033:290062"31', "110246", "500025033"),
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
    text = "A/C NO.\n911010049001598\nAccount details"
    assert extractor.extract_micr_code(text)[0] is None
    assert extractor.extract_cheque_number(text)[0] is None

def test_branch_suffix_label():
    value, confidence = FieldExtractor().extract_branch_name("SECUNDERABAD MAIN Branch")
    assert value == "SECUNDERABAD MAIN"
    assert confidence == 0.75
