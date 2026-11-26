"""HR letter generation as Word (.docx) files (python-docx).

Each letter type is registered in LETTER_TYPES with a builder that fills a Document. Adding a new
letter type means adding one entry here plus a request model and endpoint in app/routers/letters.py.
"""
import io
import os
from datetime import date
from typing import Dict

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Mm, Pt, RGBColor
from PIL import Image, ImageDraw

COMPANY_NAME = "RMH Advisors Private Limited"
OFFER_COMPANY = "RMH Advisors Pvt. Ltd."

# Letterhead details, printed on letters that use it. Change here if they change.
LETTERHEAD_PHONES = "8767671999 / 9822321566"
LETTERHEAD_EMAIL = "connect@rmhadvisors.com"
LETTERHEAD_WEBSITE = "rmhadvisors.in"
LETTERHEAD_ADDRESS = ("3rd Floor, Apoorva Bldg, Flat No. 19, Vartak School Road, "
                      "Veer Savarkar Nagar, Anand Nagar, Vasai (W) 401 202.")
LOGO_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "rmh_logo.png")

FONT = "Times New Roman"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

PRONOUNS = {
    "male": {"he": "he", "He": "He", "his": "his", "His": "His", "him": "him"},
    "female": {"he": "she", "He": "She", "his": "her", "His": "Her", "him": "her"},
}


# ── Text helpers ─────────────────────────────────────────────────────────────

class B(str):
    """A bold piece of text inside a paragraph."""


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def format_long_date(d: date) -> str:
    """12th May 2026"""
    return f"{_ordinal(d.day)} {d.strftime('%B %Y')}"


def _numeric_date(d: date) -> str:
    """20/11/2026"""
    return d.strftime("%d/%m/%Y")


def _day_of_month_year(d: date) -> str:
    """23rd day of November 2026"""
    return f"{_ordinal(d.day)} day of {d.strftime('%B %Y')}"


def _parse_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a valid date")


def _address_lines(raw: str) -> list:
    return [line.strip() for line in raw.splitlines() if line.strip()]


_NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
                 "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
                 "seventeen", "eighteen", "nineteen", "twenty", "twenty-one", "twenty-two",
                 "twenty-three", "twenty-four"]


def _months_text(raw: str, label: str) -> str:
    """'2' -> 'two (2)'"""
    try:
        n = int(float(raw))
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a whole number of months")
    if not 1 <= n <= 24:
        raise ValueError(f"{label} must be between 1 and 24 months")
    return f"{_NUMBER_WORDS[n]} ({n})"


def _whole_months(raw: str, label: str) -> int:
    try:
        n = int(float(raw))
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a whole number of months")
    if not 1 <= n <= 60:
        raise ValueError(f"{label} must be between 1 and 60 months")
    return n


def _indian_amount(raw: str) -> str:
    """5000 -> '5,000', 125000 -> '1,25,000', 5000.5 -> '5,000.50'"""
    try:
        amount = float(raw)
    except (TypeError, ValueError):
        raise ValueError("Compensation must be a number")
    if amount <= 0:
        raise ValueError("Compensation must be greater than zero")
    whole, _, frac = f"{amount:.2f}".partition(".")
    head, tail = whole[:-3], whole[-3:]
    if head:
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        tail = ",".join(groups) + "," + tail
    return tail if frac == "00" else f"{tail}.{frac}"


def _percent_text(raw: str) -> str:
    try:
        pct = float(raw)
    except (TypeError, ValueError):
        raise ValueError("TDS percentage must be a number")
    if not 0 <= pct <= 100:
        raise ValueError("TDS percentage must be between 0 and 100")
    return f"{pct:g}%"


# ── Document helpers ─────────────────────────────────────────────────────────

def _add_runs(paragraph, parts, size=12, underline=False, bold_all=False):
    """Add text parts to a paragraph: str is plain, B(str) bold, "\\n" is a line break."""
    for part in parts:
        pieces = str(part).split("\n")
        for i, piece in enumerate(pieces):
            if i:
                paragraph.add_run().add_break()
            if not piece:
                continue
            run = paragraph.add_run(piece)
            run.bold = True if (bold_all or isinstance(part, B)) else None
            run.underline = underline or None
            run.font.size = Pt(size)


def _p(doc, *parts, align=None, before=0, after=12, line=17, left=None, hanging=None,
       page_break=False, keep_next=False, underline=False, bold=False, size=12):
    p = doc.add_paragraph()
    fmt = p.paragraph_format
    fmt.space_before = Pt(before)
    fmt.space_after = Pt(after)
    fmt.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    fmt.line_spacing = Pt(line)
    if align is not None:
        p.alignment = align
    if left is not None:
        fmt.left_indent = Mm(left)
    if hanging is not None:
        fmt.first_line_indent = Mm(-hanging)
    if page_break:
        fmt.page_break_before = True
    if keep_next:
        fmt.keep_with_next = True
    _add_runs(p, parts, size=size, underline=underline, bold_all=bold)
    return p


def _gap(doc, mm):
    """Empty vertical space of `mm` millimetres."""
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    p.paragraph_format.line_spacing = Mm(mm)
    return p


def _signature_block(doc, include_date=True, compact=False, company=COMPANY_NAME):
    line = "_" * 28
    _p(doc, "Warm regards,\n", B(f"For {company}"))
    _gap(doc, 12 if compact else 22)
    _p(doc, "Authorized Signatory")
    _gap(doc, 6 if compact else 14)
    _p(doc, f"Signature:{line}")
    _gap(doc, 6)
    _p(doc, f"Name:{line}", after=0 if not include_date else 12)
    if include_date:
        _gap(doc, 6)
        _p(doc, f"Date:{line}", after=0)


# ── Letterhead (logo, contact icons, address, watermark) ─────────────────────

def _icon_png(kind: str) -> bytes:
    """Dark round badge with a white phone / envelope / globe glyph."""
    size = 512
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = size / 2
    u = c / 10
    d.ellipse([0, 0, size - 1, size - 1], fill=(58, 58, 58, 255))
    white = (255, 255, 255, 255)

    def pt(dx, dy):
        return (c + dx * u, c - dy * u)

    if kind == "phone":
        # handset: a thick curved bar with a blob at each end
        p0, p1, p2, p3 = pt(-4, 4), pt(-5, -3), pt(3, -5), pt(4, -4)
        pts = []
        for i in range(31):
            t = i / 30
            a = (1 - t) ** 3
            b = 3 * (1 - t) ** 2 * t
            cc = 3 * (1 - t) * t ** 2
            dd = t ** 3
            pts.append((a * p0[0] + b * p1[0] + cc * p2[0] + dd * p3[0],
                        a * p0[1] + b * p1[1] + cc * p2[1] + dd * p3[1]))
        d.line(pts, fill=white, width=int(1.7 * u), joint="curve")
        for x, y in (p0, p3):
            r = 1.6 * u
            d.ellipse([x - r, y - r, x + r, y + r], fill=white)
    elif kind == "mail":
        w, h = 3.4 * u, 2.4 * u
        lw = int(0.95 * u)
        d.rectangle([c - w, c - h, c + w, c + h], outline=white, width=lw)
        d.line([pt(-3.4, 2.4), pt(0, -0.4), pt(3.4, 2.4)], fill=white, width=lw, joint="curve")
    else:  # web / globe
        g = 4.2 * u
        lw = int(0.8 * u)
        d.ellipse([c - g, c - g, c + g, c + g], outline=white, width=lw)
        d.ellipse([c - g * 0.45, c - g, c + g * 0.45, c + g], outline=white, width=lw)
        d.line([c - g, c, c + g, c], fill=white, width=lw)
        d.line([c, c - g, c, c + g], fill=white, width=lw)
    img = img.resize((128, 128), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _faded_logo_png(opacity: float = 0.07) -> bytes:
    img = Image.open(LOGO_PATH).convert("RGBA")
    alpha = img.getchannel("A").point(lambda a: int(a * opacity))
    img.putalpha(alpha)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _float_behind_page_centre(run, picture):
    """Turn an inline picture into a page-centred picture sitting behind the text (a watermark)."""
    inline = picture._inline
    cx, cy = inline.extent.cx, inline.extent.cy
    anchor = parse_xml(
        f'<wp:anchor {nsdecls("wp", "a")} distT="0" distB="0" distL="0" distR="0" simplePos="0" '
        f'relativeHeight="0" behindDoc="1" locked="0" layoutInCell="1" allowOverlap="1">'
        f'<wp:simplePos x="0" y="0"/>'
        f'<wp:positionH relativeFrom="page"><wp:align>center</wp:align></wp:positionH>'
        f'<wp:positionV relativeFrom="page"><wp:align>center</wp:align></wp:positionV>'
        f'<wp:extent cx="{cx}" cy="{cy}"/>'
        f'<wp:effectExtent l="0" t="0" r="0" b="0"/><wp:wrapNone/></wp:anchor>'
    )
    for tag in ("wp:docPr", "wp:cNvGraphicFramePr", "a:graphic"):
        anchor.append(inline.find(qn(tag)))
    inline.getparent().replace(inline, anchor)


def _set_cell_width(cell, mm):
    cell.width = Mm(mm)


def _paragraph_bottom_border(paragraph, color="1F2D5A", size=10):
    ppr = paragraph._p.get_or_add_pPr()
    ppr.append(parse_xml(
        f'<w:pBdr {nsdecls("w")}><w:bottom w:val="single" w:sz="{size}" w:space="4" '
        f'w:color="{color}"/></w:pBdr>'
    ))


def _add_letterhead(doc):
    section = doc.sections[0]
    section.header_distance = Mm(8)
    header = section.header
    header.is_linked_to_previous = False

    # First (tiny) paragraph carries the watermark.
    anchor_par = header.paragraphs[0]
    anchor_par.paragraph_format.space_after = Pt(0)
    anchor_par.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    anchor_par.paragraph_format.line_spacing = Pt(1)
    if os.path.exists(LOGO_PATH):
        run = anchor_par.add_run()
        wm_w = 130
        pic = run.add_picture(io.BytesIO(_faded_logo_png()), width=Mm(wm_w))
        _float_behind_page_centre(run, pic)

    # Same geometry as the original letterhead: runs from 15 mm to 195 mm, wider than the text area.
    cols = (111, 9, 60)
    table = header.add_table(3, 3, width=Mm(sum(cols)))
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    table.autofit = False
    tbl_pr = table._tbl.tblPr
    tbl_pr.append(parse_xml(f'<w:tblInd {nsdecls("w")} w:w="{int(10 * 56.7) * -1}" w:type="dxa"/>'))
    tbl_pr.append(parse_xml(
        f'<w:tblCellMar {nsdecls("w")}><w:top w:w="0" w:type="dxa"/><w:left w:w="0" w:type="dxa"/>'
        f'<w:bottom w:w="0" w:type="dxa"/><w:right w:w="0" w:type="dxa"/></w:tblCellMar>'))
    for row in table.rows:
        for cell, width in zip(row.cells, cols):
            _set_cell_width(cell, width)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER

    logo_cell = table.cell(0, 0).merge(table.cell(2, 0))
    logo_par = logo_cell.paragraphs[0]
    logo_par.paragraph_format.space_after = Pt(0)
    if os.path.exists(LOGO_PATH):
        logo_par.add_run().add_picture(LOGO_PATH, width=Mm(55))

    contacts = (("phone", LETTERHEAD_PHONES), ("mail", LETTERHEAD_EMAIL), ("web", LETTERHEAD_WEBSITE))
    for i, (icon, text) in enumerate(contacts):
        icon_par = table.cell(i, 1).paragraphs[0]
        icon_par.paragraph_format.space_after = Pt(3)
        icon_par.add_run().add_picture(io.BytesIO(_icon_png(icon)), width=Mm(6.4))
        text_par = table.cell(i, 2).paragraphs[0]
        text_par.paragraph_format.space_after = Pt(3)
        run = text_par.add_run(text)
        run.bold = True
        run.font.size = Pt(11)

    address = header.add_paragraph()
    address.alignment = WD_ALIGN_PARAGRAPH.CENTER
    address.paragraph_format.space_before = Pt(4)
    address.paragraph_format.left_indent = Mm(-10)
    address.paragraph_format.right_indent = Mm(-10)
    address.paragraph_format.space_after = Pt(0)
    run = address.add_run(LETTERHEAD_ADDRESS)
    run.bold = True
    run.font.size = Pt(8.6)
    _paragraph_bottom_border(address)


# ── Letters ──────────────────────────────────────────────────────────────────

def _experience_letter(doc, v: Dict[str, str]):
    p = PRONOUNS[v["gender"]]
    start = _parse_date(v["tenure_from"], "Tenure start")
    end = _parse_date(v["tenure_to"], "Tenure end")
    if end < start:
        raise ValueError("Tenure end date cannot be before the start date")
    letter_date = _parse_date(v["date"], "Date")
    tenure = f"{format_long_date(start)} to {format_long_date(end)}"

    _gap(doc, 18)
    _p(doc, B("Date:"), " " + format_long_date(letter_date), align=WD_ALIGN_PARAGRAPH.RIGHT, after=28)
    _p(doc, B("To Whom It May Concern"), align=WD_ALIGN_PARAGRAPH.CENTER, after=24)
    _p(doc, "This is to certify that ", B(v["employee_name"]), " was employed with ",
       B(COMPANY_NAME), " as a ", B(v["designation"]), " in the ", B(v["department"]),
       " from ", B(tenure), ".")
    _p(doc, f"During {p['his']} tenure with us, {p['he']} was responsible for assisting in "
            f"{v['tasks']}, supporting day-to-day financial operations, and working on "
            f"assignments as directed by {p['his']} reporting manager, ", B(v["manager_name"]),
       f". {p['He']} demonstrated sincerity, a willingness to learn, and the ability to "
       f"complete assigned tasks responsibly.")
    _p(doc, f"{p['His']} conduct during {p['his']} employment was found to be satisfactory, and "
            f"{p['he']} maintained a professional attitude towards {p['his']} responsibilities "
            f"and colleagues.")
    _p(doc, f"We wish {p['him']} all the best in {p['his']} future endeavours.", after=18)
    _signature_block(doc)


def _to_block(doc, v):
    _p(doc, B("To,"), "\n" + v["employee_name"] + ",\n" + "\n".join(_address_lines(v["address"])))


def _relieving_letter(doc, v: Dict[str, str]):
    start = _parse_date(v["tenure_from"], "Tenure start")
    end = _parse_date(v["tenure_to"], "Tenure end")
    if end < start:
        raise ValueError("Tenure end date cannot be before the start date")
    letter_date = _parse_date(v["date"], "Date")
    relieving = _parse_date(v["relieving_date"], "Relieving date")
    tenure = f"{format_long_date(start)} to {format_long_date(end)}"

    _p(doc, B("Date:"), " " + _numeric_date(letter_date), align=WD_ALIGN_PARAGRAPH.RIGHT, before=10, after=17)
    _to_block(doc, v)
    _p(doc, B("Subject: " + (v.get("subject") or "Relieving Letter")))
    _p(doc, f"Dear {v['employee_name']},")
    _p(doc, "This is to inform you that you have been relieved from your duties at ",
       B(COMPANY_NAME), " with effect from the close of working hours on ",
       B(format_long_date(relieving)), ".")
    _p(doc, "You were employed with us as a ", B(v["designation"]), " in the ", B(v["department"]),
       " from ", B(tenure), ". During this period, you reported to ", B(v["manager_name"]),
       f" and were responsible for assisting in {v['tasks']}.")
    _p(doc, "We confirm that you have completed all assigned responsibilities and handed over "
            "your duties satisfactorily. We also confirm that there are no dues pending from "
            "your side to the company.")
    _p(doc, "We appreciate your efforts during your tenure with us and wish you success in your "
            "future endeavours.", after=6)
    _signature_block(doc, include_date=False, compact=True)


def _offer_letter(doc, v: Dict[str, str]):
    letter_date = _parse_date(v["date"], "Date")
    joining = _parse_date(v["joining_date"], "Date of joining")
    probation = _months_text(v["probation_months"], "Probation period")
    notice = _months_text(v["notice_months"], "Notice period")
    amount = _indian_amount(v["compensation"])
    tds = _percent_text(v["tds_percent"])

    _p(doc, B("Date:"), " " + _numeric_date(letter_date), align=WD_ALIGN_PARAGRAPH.RIGHT, before=10, after=11)
    _to_block(doc, v)
    _p(doc, B("Subject: " + (v.get("subject") or "Offer of Contractual Employment")))
    _p(doc, f"Dear {v['employee_name']},")
    _p(doc, "We are pleased to offer you a contractual position as an ", B(v["designation"]),
       " in the ", B(v["department"]), " with ", B(OFFER_COMPANY),
       " on a contractual basis, with effect from ", B(format_long_date(joining)), ".")
    _p(doc, f"Your offer is subject to a probation period of {probation} months commencing from "
            f"your date of joining. During this period, your performance, conduct, and "
            f"suitability for the assigned role will be evaluated.")
    _p(doc, f"Upon successful completion of the probation period, your contractual engagement "
            f"shall continue. In the event of termination of this contractual engagement, the "
            f"company requires you to provide {notice} months’ prior notice and serve the "
            f"full notice period.")
    _p(doc, "Your compensation will be ", B(f"Rs. {amount}/- per month"),
       " on a full-time basis. In case of part-time engagement, compensation will be provided on "
       "an hourly basis. Please note that ", B(tds), " TDS will be deducted from your "
       "compensation. You will report to ", B(v["manager_name"]),
       " and are expected to perform your duties with dedication, sincerity, and "
       "professionalism throughout your engagement.")
    _p(doc, "We look forward to your association with the organization and wish you success in "
            "your role.")
    _p(doc, "Warm regards,\n", B(f"For {OFFER_COMPANY}"))
    _gap(doc, 14)
    _p(doc, "Authorized Signatory", after=0)

    _p(doc, B("ACKNOWLEDGEMENT AND ACCEPTANCE"), page_break=True, before=11)
    _p(doc, "I, ", B(v["employee_name"]), ", acknowledge that I have read and understood the terms "
            "and conditions stated in this appointment letter and accept them.")
    _gap(doc, 6)
    _p(doc, "Signature:" + "_" * 28)
    _gap(doc, 6)
    _p(doc, "Name:" + "_" * 28, after=0)


COMPANY_ADDRESS = ("3rd Floor, Apoorva Building, Flat No. 19,\n"
                   "Vartak School Road, Veer Savarkar Nagar, Anand Nagar,\n"
                   "Vasai (West), Dist. Palghar – 401202")


def _bond_letter(doc, v: Dict[str, str]):
    agreement = _parse_date(v["date"], "Agreement date")
    effective = _parse_date(v["effective_date"], "Effective date")
    bond_from = _parse_date(v["bond_from"], "Bond period start")
    bond_to = _parse_date(v["bond_to"], "Bond period end")
    if bond_to < bond_from:
        raise ValueError("Bond period end cannot be before its start")
    if v.get("title") not in ("Mr.", "Ms.", "Mrs."):
        raise ValueError("Title must be Mr., Ms. or Mrs.")
    penalty = _whole_months(v["penalty_months"], "Penalty")
    notice = _whole_months(v["notice_months"], "Notice period")
    who = f"{v['title']} {v['employee_name']}"
    address = "\n".join(_address_lines(v["address"]))
    line = "_" * 24

    def clause(*parts):
        return _p(doc, *parts, after=3, line=16)

    def heading(text, page_break=False):
        return _p(doc, B(text), before=9, after=1, line=16, keep_next=True, page_break=page_break)

    def bullet(*parts):
        return _p(doc, "•\t", *parts, after=2, line=16, left=9, hanging=6)

    _p(doc, B("EMPLOYMENT BOND / SERVICE AGREEMENT"), align=WD_ALIGN_PARAGRAPH.CENTER,
       underline=True, before=2, after=16)
    clause("This Employment Bond (“Agreement”) is made on this ",
           B(_day_of_month_year(agreement)) + ",")
    heading("BETWEEN")
    clause(B(OFFER_COMPANY), ", having its registered office at\n" + COMPANY_ADDRESS +
           "\n(hereinafter referred to as the “Company”),")
    heading("AND")
    clause(B(who), ", residing at\n" + address + "\n(hereinafter referred to as the “Employee”).")
    heading("1. POSITION")
    clause("The Employee is appointed to the position of ", B(v["designation"]), " in the ",
           B(v["department"]), " effective ", B(format_long_date(effective)) + ".")
    heading("2. TERM OF AGREEMENT")
    clause("The Employee agrees to serve the Company for a fixed minimum period from ",
           B(format_long_date(bond_from)), " to ", B(format_long_date(bond_to)),
           " (“Bond Period”).")
    heading("3. BREACH OF AGREEMENT")
    clause("In case the Employee resigns or leaves employment before completion of the Bond Period:")
    bullet("The Employee shall pay a penalty equivalent to ", B(str(penalty)), " months’ salary.")
    heading("4. COMPLETION OF BOND PERIOD")
    clause("Upon successful completion of the Bond Period:")
    bullet("The Employee shall be released from all obligations under this Agreement.")
    bullet("If the Employee chooses to continue employment thereafter, any future resignation "
           "will require serving a ", B(str(notice)), "-month notice period as per Company policy.")
    heading("5. TERMINATION BY COMPANY", page_break=True)
    clause("If the Company terminates the Employee without misconduct before completion of the "
           "Bond Period, the Employee shall not be liable to pay the penalty.")
    heading("6. CONFIDENTIALITY")
    clause("The Employee agrees to maintain confidentiality of all company information during "
           "and after employment.")
    heading("7. GOVERNING LAW")
    clause("This Agreement shall be governed by and interpreted in accordance with the laws of India.")
    heading("8. ACCEPTANCE")
    clause("Both parties acknowledge that they have read, understood, and agreed to the terms "
           "of this Agreement.")
    _gap(doc, 6)
    clause(B(f"For {OFFER_COMPANY}"), f"\nName: {v['signatory_name']}"
           f"\nDesignation: {v['signatory_designation']}")
    _p(doc, f"Signature:{line}", before=20, after=0, line=16)
    _p(doc, f"Date:{line}", before=11, after=0, line=16)
    _gap(doc, 9)
    clause(B("Employee"), f"\n{who}")
    _p(doc, f"Signature:{line}", before=20, after=0, line=16)
    _p(doc, f"Date:{line}", before=11, after=0, line=16)


LETTER_TYPES: Dict[str, dict] = {
    "experience": {"label": "Experience Letter", "build": _experience_letter, "letterhead": False},
    "relieving": {"label": "Relieving Letter", "build": _relieving_letter, "letterhead": True},
    "bond": {"label": "Employment Bond", "build": _bond_letter, "letterhead": True},
    "offer": {"label": "Offer of Contractual Employment", "build": _offer_letter, "letterhead": True},
}


def build_letter_docx(letter_type: str, values: Dict[str, str]) -> bytes:
    """Render a letter. Raises KeyError for unknown type, ValueError for bad input."""
    spec = LETTER_TYPES[letter_type]
    values = {k: (str(v).strip() if v is not None else "") for k, v in values.items()}
    values.setdefault("gender", "male")
    if values["gender"] not in PRONOUNS:
        raise ValueError("Gender must be male or female")

    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Mm(210), Mm(297)
    letterhead = spec.get("letterhead", False)
    section.left_margin = section.right_margin = Mm(25)
    section.top_margin = Mm(46 if letterhead else 20)
    section.bottom_margin = Mm(12 if letterhead else 20)

    normal = doc.styles["Normal"]
    normal.font.name = FONT
    normal.font.size = Pt(12)
    normal.font.color.rgb = RGBColor(0, 0, 0)
    rfonts = normal.element.get_or_add_rPr().get_or_add_rFonts()
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rfonts.set(qn(attr), FONT)
    normal.paragraph_format.space_after = Pt(0)

    doc.core_properties.title = spec["label"]
    doc.core_properties.author = COMPANY_NAME

    if letterhead:
        _add_letterhead(doc)
    spec["build"](doc, values)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
