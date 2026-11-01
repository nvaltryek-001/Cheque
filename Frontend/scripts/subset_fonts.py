#!/usr/bin/env python3
"""Rebuild the self-hosted font files in Frontend/public/fonts/.

The application's Content-Security-Policy allows fonts only from its own origin, so Inter is served from here rather
than from a font CDN. Inter is released under the SIL Open Font License 1.1 (see public/fonts/OFL.txt); the files are
Latin-only subsets of Inter 4.0 (the upstream OTF files, e.g. the Debian package `fonts-inter`) saved as WOFF.

    python3 Frontend/scripts/subset_fonts.py [directory with the Inter OTF files]

Needs the fontTools package. The generated files are committed, so this only has to be run to change the weights.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fontTools import subset

SOURCE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/usr/share/fonts/opentype/inter")
TARGET = Path(__file__).resolve().parent.parent / "public" / "fonts"

# output name -> upstream file
FACES = {
    "inter-400.woff": "Inter-Regular.otf",
    "inter-500.woff": "Inter-Medium.otf",
    "inter-600.woff": "Inter-SemiBold.otf",
    "inter-display-600.woff": "InterDisplay-SemiBold.otf",
    "inter-display-700.woff": "InterDisplay-Bold.otf",
}

# Basic Latin, Latin-1, and the punctuation / arrows / maths the interface uses.
UNICODES = (
    list(range(0x20, 0x7F)) + list(range(0xA0, 0x100)) +
    [0x131, 0x152, 0x153, 0x2C6, 0x2DA, 0x2DC, 0x2013, 0x2014, 0x2018, 0x2019, 0x201A, 0x201C, 0x201D, 0x201E,
     0x2020, 0x2021, 0x2022, 0x2026, 0x2030, 0x2039, 0x203A, 0x20AC, 0x2122, 0x2190, 0x2191, 0x2192, 0x2193,
     0x2212, 0x2260, 0x2264, 0x2265]
)
# kerning, ligatures, contextual alternates, tabular figures (tnum), slashed zero (zero), case-sensitive forms (case)
FEATURES = ["kern", "liga", "calt", "ccmp", "locl", "mark", "mkmk", "tnum", "zero", "case", "cv11", "ss01"]


def main() -> int:
    TARGET.mkdir(parents=True, exist_ok=True)
    for out, upstream in FACES.items():
        src = SOURCE / upstream
        if not src.is_file():
            print(f"missing {src}", file=sys.stderr)
            return 1
        opts = subset.Options()
        opts.flavor = "woff"
        opts.layout_features = FEATURES
        opts.hinting = False
        opts.desubroutinize = True
        opts.name_IDs = [0, 1, 2, 3, 4, 5, 6, 13, 14]       # keep copyright and licence strings
        opts.notdef_outline = True
        font = subset.load_font(str(src), opts)
        sub = subset.Subsetter(opts)
        sub.populate(unicodes=UNICODES)
        sub.subset(font)
        subset.save_font(font, str(TARGET / out), opts)
        print(f"{out:26s} {(TARGET / out).stat().st_size / 1024:6.1f} KB  <- {upstream}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
