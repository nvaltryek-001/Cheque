from pathlib import Path

p = Path("apply_extraction_fix.py")
s = p.read_text(encoding="utf-8-sig")
old = 'updated, count = re.subn(pattern, replacement.rstrip() + "\\n\\n", source, count=1)'
new = 'updated, count = re.subn(pattern, lambda _: replacement.rstrip() + "\\n\\n", source, count=1)'

if old not in s:
    raise SystemExit("Expected line not found; stopping safely.")

p.write_text(s.replace(old, new, 1), encoding="utf-8")
print("Patch script corrected.")
