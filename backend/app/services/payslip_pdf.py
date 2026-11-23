"""Payslip PDF generation (reportlab, pure Python — no system dependencies).

`build_payslips_pdf(slips)` takes a list of plain dicts (see `PayslipData` keys used in
`_draw_slip`) and returns the bytes of a PDF with one A4 page per employee.
"""
import io
import os
from datetime import date
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

COMPANY_NAME = "RMH Advisors Private Limited"
# Printed under the company name. Change here if the registered address changes.
COMPANY_ADDRESS = "A-19 APOORVA CO-OP SOC ANAND NAGAR NAVGHAR, VASAI WEST VASAI"
FOOTER_TEXT = "This is a computer generated slip does not required signature"

LOGO_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "rmh_logo.png")

FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"

MONTH_ABBR = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
         "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen",
         "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _two_digits(n: int) -> str:
    if n < 20:
        return _ONES[n]
    return _TENS[n // 10] + (("-" + _ONES[n % 10]) if n % 10 else "")


def _three_digits(n: int) -> str:
    parts = []
    if n >= 100:
        parts.append(_ONES[n // 100] + " Hundred")
        n %= 100
    if n:
        parts.append(_two_digits(n))
    return " ".join(parts)


def _indian_words(n: int) -> str:
    if n == 0:
        return "Zero"
    parts = []
    for divisor, name in ((10_000_000, "Crore"), (100_000, "Lakh"), (1_000, "Thousand")):
        if n >= divisor:
            parts.append(_three_digits(n // divisor) + " " + name)
            n %= divisor
    if n:
        parts.append(_three_digits(n))
    return " ".join(parts)


def amount_in_words(amount: float) -> str:
    """Indian-style words, e.g. 11451 -> 'Rupees Eleven Thousand Four Hundred Fifty-One Only'."""
    negative = amount < 0
    cents = int(round(abs(amount) * 100))
    rupees, paise = divmod(cents, 100)
    words = "Rupees " + _indian_words(rupees)
    if paise:
        words += " and " + _two_digits(paise) + " Paise"
    words += " Only"
    return ("Minus " if negative else "") + words


def _money(value: float) -> str:
    return f"{value:,.2f}"


def _plain(value: float) -> str:
    return f"{value:.2f}"


def _fit(c: canvas.Canvas, text: str, x: float, y: float, max_width: float,
         font: str = FONT, size: float = 8.5, align: str = "left"):
    """Draw text, shrinking the font (down to 6pt) so it never spills out of its column."""
    text = text or ""
    while size > 6 and stringWidth(text, font, size) > max_width:
        size -= 0.25
    c.setFont(font, size)
    if align == "right":
        c.drawRightString(x, y, text)
    elif align == "center":
        c.drawCentredString(x, y, text)
    else:
        c.drawString(x, y, text)


def _fmt_date(value: Optional[date]) -> str:
    return value.strftime("%d-%b-%Y") if value else ""


def _draw_slip(c: canvas.Canvas, s: dict):
    page_w, page_h = A4
    left = 30
    width = page_w - 2 * left
    right = left + width
    top = page_h - 36

    # ── Header: logo + company name / address / title ───────────────────────
    logo_h = 52
    logo_w = logo_h * (898 / 404)
    if os.path.exists(LOGO_PATH):
        c.drawImage(LOGO_PATH, left, top - logo_h, width=logo_w, height=logo_h, mask="auto")

    centre = left + width / 2 + 25
    c.setFillColor(colors.black)
    _fit(c, COMPANY_NAME.upper(), centre, top - 14, width - logo_w - 20, FONT_BOLD, 14, "center")
    _fit(c, COMPANY_ADDRESS, centre, top - 32, width - logo_w - 20, FONT, 7.5, "center")
    title = f"Salary Slip for the Month of {MONTH_ABBR[s['month'] - 1]}-{s['year']}"
    _fit(c, title, centre, top - 52, width - logo_w - 20, FONT, 9.5, "center")

    # ── Employee block ──────────────────────────────────────────────────────
    y = top - logo_h - 22
    row_h = 14
    left_rows = [
        ("ID / Name", f"{s['employee_id']} / {(s['name'] or '').upper()}"),
        ("Designation", (s.get("designation") or "").upper()),
        ("Location", (s.get("work_location") or "").upper()),
        ("Salary Bank", (s.get("bank_name") or "").upper()),
        ("PF No./UAN", s.get("pf_uan_no") or ""),
    ]
    right_rows = [
        ("Department", (s.get("department") or "").upper()),
        ("DOJ", _fmt_date(s.get("date_of_joining"))),
        ("PAN", s.get("pan_number") or ""),
        ("Salary A/c No.", s.get("bank_account_no") or ""),
        ("E.S.I.C. No.", s.get("esic_no") or ""),
    ]
    right_x = left + width * 0.62
    for i, (label, value) in enumerate(left_rows):
        ry = y - i * row_h
        _fit(c, label, left + 2, ry, 70, FONT, 8.5)
        _fit(c, ": " + value, left + 72, ry, right_x - left - 80, FONT, 8.5)
    for i, (label, value) in enumerate(right_rows):
        ry = y - i * row_h
        _fit(c, label, right_x, ry, 70, FONT, 8.5)
        _fit(c, ": " + value, right_x + 72, ry, right - right_x - 74, FONT, 8.5)

    # ── Table ───────────────────────────────────────────────────────────────
    table_top = y - len(left_rows) * row_h + 4
    cols = [  # (width,)
        ("days_desc", 62), ("days", 38),
        ("earn_desc", 138), ("rate", 62), ("earned", 62),
        ("ded_desc", 113), ("ded", 60),
    ]
    xs = [left]
    for _, w in cols:
        xs.append(xs[-1] + w)
    xs[-1] = right  # absorb rounding

    earnings = s["earnings"]      # list of (description, rate or None, amount)
    deductions = s["deductions"]  # list of (description, amount)
    body_rows = max(9, len(earnings), len(deductions))
    head1_h, head2_h, line_h, total_h = 15, 15, 14, 16
    body_h = body_rows * line_h
    table_h = head1_h + head2_h + body_h + total_h
    table_bottom = table_top - table_h

    c.setStrokeColor(colors.black)
    c.setLineWidth(0.7)
    shade = colors.Color(0.85, 0.85, 0.85)

    # shaded header row 2 and total row
    c.setFillColor(shade)
    c.rect(left, table_top - head1_h - head2_h, width, head2_h, stroke=0, fill=1)
    c.rect(left, table_bottom, width, total_h, stroke=0, fill=1)
    c.setFillColor(colors.black)

    # outer border + row separators
    c.rect(left, table_bottom, width, table_h, stroke=1, fill=0)
    c.line(left, table_top - head1_h, right, table_top - head1_h)
    c.line(left, table_top - head1_h - head2_h, right, table_top - head1_h - head2_h)
    c.line(left, table_bottom + total_h, right, table_bottom + total_h)

    # group header
    groups = [(0, 2, "Days Details"), (2, 5, "Earning Details"), (5, 7, "Deduction Details")]
    for a, b, label in groups:
        _fit(c, label, (xs[a] + xs[b]) / 2, table_top - 11, xs[b] - xs[a] - 4, FONT, 8.5, "center")
    # vertical dividers: full height at group edges, body-only for the rest
    for idx in (2, 5):
        c.line(xs[idx], table_top, xs[idx], table_bottom)
    c.line(xs[1], table_top - head1_h, xs[1], table_bottom)
    for idx in (3, 4, 6):
        c.line(xs[idx], table_top - head1_h, xs[idx], table_bottom)
    c.setStrokeColor(colors.Color(0.55, 0.55, 0.55))

    # column headings
    heads = ["Description", "Days", "Description", "Rate", "Earned Amt.", "Description", "Deducted Amt."]
    hy = table_top - head1_h - 11
    for i, label in enumerate(heads):
        _fit(c, label, (xs[i] + xs[i + 1]) / 2, hy, xs[i + 1] - xs[i] - 4, FONT, 8.5, "center")

    # body
    by0 = table_top - head1_h - head2_h - 10
    _fit(c, "Present", xs[0] + 3, by0, xs[1] - xs[0] - 6)
    _fit(c, _plain(s["paid_days"]), xs[2] - 3, by0, xs[2] - xs[1] - 4, align="right")

    for i, (desc, rate, amount) in enumerate(earnings):
        ry = by0 - i * line_h
        _fit(c, desc, xs[2] + 3, ry, xs[3] - xs[2] - 6)
        if rate is not None:
            _fit(c, _money(rate), xs[4] - 3, ry, xs[4] - xs[3] - 4, align="right")
        _fit(c, _money(amount), xs[5] - 3, ry, xs[5] - xs[4] - 4, align="right")
    for i, (desc, amount) in enumerate(deductions):
        ry = by0 - i * line_h
        _fit(c, desc, xs[5] + 3, ry, xs[6] - xs[5] - 6)
        _fit(c, _money(amount), right - 3, ry, xs[7] - xs[6] - 4, align="right")

    # totals
    ty = table_bottom + 5
    _fit(c, "TOTAL", xs[0] + 3, ty, 50, FONT, 8.5)
    _fit(c, _plain(s["paid_days"]), xs[2] - 3, ty, xs[2] - xs[1] - 4, FONT, 8.5, "right")
    _fit(c, _plain(s["rate_total"]), xs[4] - 3, ty, xs[4] - xs[3] - 4, FONT, 8.5, "right")
    _fit(c, _plain(s["gross"]), xs[5] - 3, ty, xs[5] - xs[4] - 4, FONT, 8.5, "right")
    _fit(c, _plain(s["total_deductions"]), right - 3, ty, xs[7] - xs[6] - 4, FONT, 8.5, "right")

    # ── Net payable, words, employer contribution ───────────────────────────
    ny = table_bottom - 13
    _fit(c, f"({amount_in_words(s['net'])})", left + 2, ny, width * 0.62, FONT, 8.5)
    _fit(c, "Net Payable : Rs.", right - 85, ny, 90, FONT_BOLD, 8.5, "right")
    _fit(c, _plain(s["net"]), right - 3, ny, 80, FONT_BOLD, 9, "right")

    if s.get("employer_pf") or s.get("employer_esic"):
        epf, eesic = s.get("employer_pf", 0.0), s.get("employer_esic", 0.0)
        _fit(
            c,
            f"Emplr Cont. :- (PF:{epf:.2f} ESIC:{eesic:.2f} ) = {epf + eesic:.2f}",
            left + 2, ny - 13, width * 0.7, FONT, 8.5,
        )

    # ── Footer ──────────────────────────────────────────────────────────────
    _fit(c, FOOTER_TEXT, left + width / 2, table_bottom - 150, width, FONT, 8, "center")


def build_payslips_pdf(slips: list) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle("Payslip")
    c.setAuthor(COMPANY_NAME)
    for slip in slips:
        _draw_slip(c, slip)
        c.showPage()
    c.save()
    return buf.getvalue()
