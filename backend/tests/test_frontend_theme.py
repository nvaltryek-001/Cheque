"""Guards for the web app's look: text contrast, self-hosted fonts and the Content-Security-Policy rules.

These read the source files in Frontend/ (no browser, no build), so they run everywhere, including CI jobs that never
install Node. The page is served with `default-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' blob:`,
so a stylesheet that pulls a font or image from another site, or markup with an inline style attribute, would break
the page in production while looking fine in a quick local check.
"""
import re
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "Frontend"
CSS = (FRONTEND / "src" / "styles.css").read_text(encoding="utf-8")


def tokens() -> dict[str, str]:
    root = re.search(r":root\s*\{(.*?)\n\}", CSS, re.S).group(1)
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;", root))


T = tokens()


def luminance(hex_colour: str) -> float:
    channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def colour(name: str) -> str:
    return T[name] if name in T else name          # a token name or a literal #rrggbb


# (text colour, background, where it is used). Normal text needs 4.5:1 (WCAG 2.1 AA).
TEXT_PAIRS = [
    ("text", "paper", "body text on the canvas"), ("text", "card", "body text on cards"),
    ("text-2", "card", "secondary text"), ("text-2", "paper", "secondary text on the canvas"),
    ("text-3", "card", "muted text on cards"), ("text-3", "paper", "muted text on the canvas"),
    ("text-3", "#f7f8fd", "table header text"),
    ("brand", "card", "links and ghost buttons"), ("brand", "paper", "links on the canvas"), ("brand", "brand-soft", "info pills"),
    ("#ffffff", "brand", "primary buttons"), ("#ffffff", "brand-2", "primary button hover"),
    ("good", "good-soft", "match pills"), ("warn", "warn-soft", "review pills"), ("bad", "bad-soft", "mismatch pills and errors"),
    ("good", "card", "good figures"), ("warn", "card", "warning figures and text"), ("bad", "card", "error text"),
    ("#6f4307", "warn-soft", "amber notices"), ("#233190", "#eef1ff", "info notices"),
    ("#6b7394", "card", "input placeholders"),
    ("#ffffff", "ink", "white on the dark chrome"), ("on-ink", "ink", "text on the dark chrome"),
    ("on-ink-2", "ink", "navigation items"), ("on-ink-3", "ink", "navigation group labels"),
    ("#cdd5f4", "ink", "login headline, second line"), ("#a7b2de", "ink", "login headline, third line"),
    ("#052a25", "scan", "labels on the aqua detection boxes"),
]


@pytest.mark.parametrize("fg,bg,where", TEXT_PAIRS)
def test_text_is_readable(fg, bg, where):
    ratio = contrast(colour(fg), colour(bg))
    assert ratio >= 4.5, f"{where}: {fg} on {bg} is {ratio:.2f}:1, needs 4.5:1"


@pytest.mark.parametrize("fg,bg,where", [
    ("scan", "ink", "aqua accents on the dark chrome"), ("brand", "card", "focus ring and controls"),
    ("scan-deep", "card", "viewfinder corners"), ("field-line", "card", "input borders (WCAG 1.4.11)"),
])
def test_graphics_have_enough_contrast(fg, bg, where):
    ratio = contrast(colour(fg), colour(bg))
    assert ratio >= 3.0, f"{where}: {fg} on {bg} is {ratio:.2f}:1"       # non-text contrast, WCAG 2.1 AA


def test_tokens_were_found():
    assert len(T) >= 25 and {"ink", "paper", "brand", "scan", "good", "warn", "bad"} <= set(T)


# ------------------------------------------------------------------ fonts and external resources
def test_every_font_file_the_stylesheet_names_is_shipped_with_its_licence():
    urls = re.findall(r'url\("(/fonts/[^"]+)"\)', CSS)
    assert len(urls) >= 5
    for u in urls:
        assert (FRONTEND / "public" / u.lstrip("/")).is_file(), f"{u} is referenced but not in Frontend/public"
    licence = (FRONTEND / "public" / "fonts" / "OFL.txt").read_text(encoding="utf-8")
    assert "SIL OPEN FONT LICENSE" in licence and "Inter Project Authors" in licence


def test_stylesheet_loads_nothing_from_other_origins():
    assert not re.findall(r"https?://", re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)), "an absolute URL would be blocked by the CSP"
    assert "@import" not in CSS
    assert not re.search(r"url\(\s*['\"]?data:", CSS), "data: images are not allowed by img-src"


def test_html_shell_loads_only_local_files():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert not re.findall(r'(?:src|href)="https?://', html)
    for font in re.findall(r'href="(/fonts/[^"]+)"', html):
        assert (FRONTEND / "public" / font.lstrip("/")).is_file()


def test_no_inline_style_attributes_in_markup():
    """React's style={{...}} prop sets properties through the CSSOM, which the policy allows; a style="..." string
    attribute or a <style> element would be blocked."""
    offenders = []
    for path in (FRONTEND / "src").rglob("*.tsx"):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r'\sstyle="|<style[\s>]|dangerouslySetInnerHTML', text):
            offenders.append(f"{path.name}: {m.group(0).strip()}")
    assert not offenders, offenders


def test_svg_assets_have_no_scripts_or_external_references():
    for svg in (FRONTEND / "public").glob("*.svg"):
        text = svg.read_text(encoding="utf-8")
        assert "<script" not in text and "href=" not in text and "http" not in text.replace("http://www.w3.org/2000/svg", "")


def test_motion_respects_the_reduced_motion_setting():
    block = re.search(r"@media \(prefers-reduced-motion: reduce\)\s*\{(.*?)\n\}", CSS, re.S).group(1)
    for selector in (".ca-scan", ".ca-det", ".spinner", ".img-loading", ".stamp"):
        assert selector in block, f"{selector} animates but is not switched off for reduced motion"


def test_every_animated_rule_is_covered_or_functional():
    """Animations that are not user-triggered must stay few: the stamp, the loading shimmer and spinners, and the one sign-in drawing."""
    names = set(re.findall(r"animation:\s*([a-z-]+)", CSS))
    assert names <= {"stamp-in", "spin", "sheen", "scan-down", "det-in", "ink-draw", "none"}, names
