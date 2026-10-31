"""PDF and CSV report generation."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from xml.sax.saxutils import escape

from ..ocr.fields import FIELD_LABELS, FIELD_ORDER


def _t(value) -> str:
    """Escape user/OCR text for ReportLab Paragraph markup (prevents markup injection and crashes)."""
    return escape(str(value if value is not None else ""))
from .audit import mask_account


def csv_bytes(header: list[str], rows: list[list]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        # neutralise spreadsheet formula injection
        w.writerow([("'" + str(c)) if isinstance(c, str) and c[:1] in ("=", "+", "-", "@") else c for c in r])
    return buf.getvalue().encode("utf-8-sig")


def cheque_pdf(cheque: dict, verifications: list[dict], generated_by: str, mask: bool = True) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("reportlab is not installed – run pip install reportlab") from exc

    navy = colors.HexColor("#0B1F3A")
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=16 * mm, title=f"Cheque report #{cheque['id']}")
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Heading1"], textColor=navy, fontSize=17, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], textColor=navy, fontSize=12, spaceBefore=10, spaceAfter=4)
    small = ParagraphStyle("small", parent=ss["Normal"], fontSize=8, textColor=colors.HexColor("#5B6475"))
    body = ParagraphStyle("body", parent=ss["Normal"], fontSize=9)
    story = [Paragraph("ChequeVerify AI — Cheque Verification Report", h1),
             Paragraph(f"Record #{cheque['id']} · file {_t(cheque['original_filename'])} · status "
                       f"<b>{cheque['status'].replace('_', ' ')}</b>", body),
             Paragraph(f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by {_t(generated_by)}", small),
             Spacer(1, 6)]

    fields = cheque.get("fields") or {}
    corrected = cheque.get("corrected") or {}
    rows = [["Field", "Value", "Source", "Confidence", "Status"]]
    for k in FIELD_ORDER:
        f = fields.get(k) or {}
        value = corrected.get(k, f.get("value", ""))
        if k == "account_number" and mask:
            value = mask_account(value)
        src = "manual" if k in corrected else f.get("source", "")
        status = "corrected" if k in corrected else f.get("status", "")
        rows.append([FIELD_LABELS[k], Paragraph(_t(value or "—"), body), src,
                     f"{f.get('confidence', 0):.2f}" if k not in corrected else "—", status])
    t = Table(rows, colWidths=[34 * mm, 72 * mm, 20 * mm, 22 * mm, 22 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), navy), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D5DAE3")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F7FA")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story += [Paragraph("Extracted fields", h2), t]

    checks = (cheque.get("validation") or {}).get("checks", [])
    if checks:
        crow = [["Check", "Result", "Detail"]] + [[c["check"].replace("_", " "), c["status"].upper(),
                                                   Paragraph(_t(c["message"]), body)] for c in checks]
        ct = Table(crow, colWidths=[40 * mm, 20 * mm, 110 * mm])
        ct.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), navy), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D5DAE3")),
        ]))
        story += [Paragraph("Validation", h2), ct]

    q = cheque.get("quality") or {}
    story += [Paragraph("Image quality", h2),
              Paragraph(f"Score {q.get('score', '—')}/100 · sharpness {q.get('sharpness', '—')} · "
                        f"brightness {q.get('brightness', '—')} · contrast {q.get('contrast', '—')}", body)]
    for issue in q.get("issues", []):
        story.append(Paragraph("• " + _t(issue["message"]), body))

    story.append(Paragraph("Signature verification", h2))
    if not verifications:
        story.append(Paragraph("No signature verification was run for this cheque.", body))
    for v in verifications:
        review = f" · reviewer: {v['review_decision']}" if v.get("review_decision") else ""
        story.append(Paragraph(
            f"<b>{v['decision']}</b> · score {v['score']:.3f} · signer {_t(v.get('signer_code') or '—')} · "
            f"model {_t(v.get('model_version') or '—')} · {_t(v['created_at'])}{review}", body))
        for r in (v.get("details") or {}).get("reasons", []):
            story.append(Paragraph("– " + _t(r), small))
    story += [Spacer(1, 12), Paragraph(
        "Decision-support output. OCR values flagged 'review' and signature results must be confirmed by an "
        "authorised officer. A similarity score is not proof of authenticity.", small)]
    doc.build(story)
    return buf.getvalue()
