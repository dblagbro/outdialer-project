from __future__ import annotations

from pathlib import Path
from html import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Flowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "outdialer-project-guide.pdf"
VERSION = "v0.1.0"


class ArchitectureDiagram(Flowable):
    def __init__(self, width: float = 7.0 * inch, height: float = 3.0 * inch):
        super().__init__()
        self.width = width
        self.height = height

    def draw_box(self, x: float, y: float, w: float, h: float, text: str, fill) -> None:
        c = self.canv
        c.setFillColor(fill)
        c.setStrokeColor(colors.HexColor("#5d7187"))
        c.roundRect(x, y, w, h, 6, stroke=1, fill=1)
        c.setFillColor(colors.HexColor("#1b1f24"))
        c.setFont("Helvetica-Bold", 8)
        lines = text.split("\n")
        top = y + h - 14
        for line in lines:
            c.drawCentredString(x + w / 2, top, line)
            top -= 10

    def arrow(self, x1: float, y1: float, x2: float, y2: float) -> None:
        c = self.canv
        c.setStrokeColor(colors.HexColor("#30475e"))
        c.setLineWidth(1.2)
        c.line(x1, y1, x2, y2)
        # simple arrow head
        if x2 >= x1:
            c.line(x2, y2, x2 - 6, y2 + 3)
            c.line(x2, y2, x2 - 6, y2 - 3)
        else:
            c.line(x2, y2, x2 + 6, y2 + 3)
            c.line(x2, y2, x2 + 6, y2 - 3)

    def draw(self) -> None:
        c = self.canv
        c.setFillColor(colors.HexColor("#f6f7f9"))
        c.rect(0, 0, self.width, self.height, fill=1, stroke=0)

        boxes = {
            "browser": (12, 185, 96, 42, "Operator\nBrowser", colors.HexColor("#e8f5ed")),
            "nginx": (145, 185, 100, 42, "nginx\nHTTPS/Auth", colors.HexColor("#eef2f6")),
            "api": (286, 185, 106, 42, "FastAPI\nWeb/API", colors.HexColor("#e8f0fb")),
            "db": (430, 185, 96, 42, "PostgreSQL\nData", colors.HexColor("#fff4df")),
            "worker": (286, 105, 106, 42, "Worker\nScheduler", colors.HexColor("#e8f0fb")),
            "spool": (430, 105, 96, 42, "Asterisk\nSpool", colors.HexColor("#eef2f6")),
            "ast": (286, 25, 106, 42, "Asterisk\nPJSIP/AGI", colors.HexColor("#fdebea")),
            "avaya": (430, 25, 96, 42, "Avaya\nSIP Core", colors.HexColor("#f0f3f6")),
            "ai": (145, 25, 100, 42, "Speech/AI\nBridge", colors.HexColor("#e8f5ed")),
        }
        for item in boxes.values():
            self.draw_box(*item)

        self.arrow(108, 206, 145, 206)
        self.arrow(245, 206, 286, 206)
        self.arrow(392, 206, 430, 206)
        self.arrow(339, 185, 339, 147)
        self.arrow(392, 126, 430, 126)
        self.arrow(478, 105, 478, 67)
        self.arrow(430, 46, 392, 46)
        self.arrow(286, 46, 245, 46)
        c.setFillColor(colors.HexColor("#4f5b68"))
        c.setFont("Helvetica", 7)
        c.drawString(300, 160, "campaign settings, logs, AI decisions")
        c.drawString(398, 88, "call files")
        c.drawString(398, 12, "INVITE / RTP")


class SequenceDiagram(Flowable):
    def __init__(self, width: float = 7.0 * inch, height: float = 3.4 * inch):
        super().__init__()
        self.width = width
        self.height = height

    def draw(self) -> None:
        c = self.canv
        cols = [
            ("Worker", 35),
            ("DB", 110),
            ("Asterisk", 195),
            ("Avaya", 295),
            ("Callee", 390),
            ("AI/API", 475),
        ]
        c.setFont("Helvetica-Bold", 8)
        c.setFillColor(colors.HexColor("#1b1f24"))
        for label, x in cols:
            c.drawCentredString(x, self.height - 16, label)
            c.setStrokeColor(colors.HexColor("#bac4cf"))
            c.line(x, self.height - 24, x, 16)

        steps = [
            (35, 110, "find eligible contact"),
            (35, 195, "write .call file"),
            (195, 295, "INVITE"),
            (295, 390, "route call"),
            (390, 195, "answer"),
            (195, 475, "fetch script / decide"),
            (195, 390, "play prompt / get digit"),
            (195, 475, "post transcript/digit"),
            (475, 195, "final action"),
            (195, 110, "post result"),
        ]
        y = self.height - 42
        c.setFont("Helvetica", 7)
        for x1, x2, label in steps:
            c.setStrokeColor(colors.HexColor("#30475e"))
            c.line(x1, y, x2, y)
            if x2 >= x1:
                c.line(x2, y, x2 - 5, y + 3)
                c.line(x2, y, x2 - 5, y - 3)
                text_x = x1 + 4
            else:
                c.line(x2, y, x2 + 5, y + 3)
                c.line(x2, y, x2 + 5, y - 3)
                text_x = x2 + 4
            c.setFillColor(colors.HexColor("#4f5b68"))
            c.drawString(text_x, y + 4, label)
            y -= 22


def styles():
    base = getSampleStyleSheet()
    base.add(
        ParagraphStyle(
            name="TitleCenter",
            parent=base["Title"],
            alignment=TA_CENTER,
            fontSize=22,
            leading=28,
            textColor=colors.HexColor("#263238"),
            spaceAfter=12,
        )
    )
    base.add(
        ParagraphStyle(
            name="Small",
            parent=base["BodyText"],
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#4f5b68"),
        )
    )
    base["Heading1"].textColor = colors.HexColor("#263238")
    base["Heading2"].textColor = colors.HexColor("#30475e")
    base["BodyText"].fontSize = 9
    base["BodyText"].leading = 12
    return base


def p(text: str, style_name: str = "BodyText"):
    return Paragraph(text, STYLES[style_name])


def bullet(text: str):
    return Paragraph("- " + text, STYLES["BodyText"])


def simple_table(rows, widths=None):
    cell_style = ParagraphStyle(
        name="TableCell",
        parent=STYLES["BodyText"],
        fontSize=8,
        leading=10,
    )
    header_style = ParagraphStyle(
        name="TableHeader",
        parent=cell_style,
        fontName="Helvetica-Bold",
        textColor=colors.HexColor("#263238"),
    )
    wrapped = []
    for row_index, row in enumerate(rows):
        style = header_style if row_index == 0 else cell_style
        wrapped.append([Paragraph(escape(str(cell)), style) for cell in row])
    table = Table(wrapped, colWidths=widths, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f3f6")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#263238")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#c8d1dc")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def header_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#687386"))
    canvas.drawString(doc.leftMargin, 0.35 * inch, "Outdialer Project Guide")
    canvas.drawRightString(letter[0] - doc.rightMargin, 0.35 * inch, f"Page {doc.page}")
    canvas.restoreState()


STYLES = styles()


def build_story():
    story = []
    story.append(p("Outdialer Project Guide", "TitleCenter"))
    story.append(p(f"{VERSION} - Dockerized SIP outdialer for Avaya-connected RSVP campaigns", "Small"))
    story.append(Spacer(1, 0.2 * inch))
    story.append(p("Purpose", "Heading1"))
    story.append(
        p(
            "Outdialer Project places consented outbound RSVP calls through an Avaya SIP environment, "
            "plays configurable prompts, collects DTMF or speech responses, updates contact status, "
            "and gives operators web-based visibility into calls, SIP traces, diagnostics, and AI decisions."
        )
    )
    story.append(p("Architecture", "Heading1"))
    story.append(ArchitectureDiagram())
    story.append(Spacer(1, 0.12 * inch))
    story.append(
        simple_table(
            [
                ["Component", "Responsibility"],
                ["outdialer-api", "FastAPI web UI, settings, contacts, logs, AI decisions, AGI result endpoint."],
                ["outdialer-worker", "Finds eligible contacts, enforces call windows/retries, writes Asterisk call files."],
                ["asterisk", "PJSIP signaling/media, generated prompts, AGI call flow, DTMF and speech capture."],
                ["postgres", "Campaign, contact, call attempt, and diagnostic event storage."],
                ["speech/flowise", "Optional TTS/STT and AI chatflow decision services."],
            ],
            [1.4 * inch, 5.3 * inch],
        )
    )
    story.append(PageBreak())
    story.append(p("Call Flow", "Heading1"))
    story.append(SequenceDiagram())
    story.append(Spacer(1, 0.12 * inch))
    story.append(p("Fast-Start Audio", "Heading2"))
    story.append(p("Recommended default: <b>Observe Milliseconds = 0</b>. This avoids pre-greeting recording/transcription and starts speech immediately after answer."))
    story.append(p("Dialplan wait after answer is 0.2 seconds to allow media cut-through without creating long silence."))
    story.append(p("SIP Routing", "Heading1"))
    for item in [
        "Request-URI/To target uses sip:DIALED_NUMBER@AVAYA_SIP_CONTACT_HOST.",
        "From and asserted identity use CALLER_ID_NUMBER@AVAYA_FROM_DOMAIN.",
        "Dial prefix is applied after number normalization.",
        "The Avaya target must be a SIP listener, not a domain controller or non-SIP host.",
    ]:
        story.append(bullet(item))
    story.append(Spacer(1, 0.1 * inch))
    story.append(
        simple_table(
            [
                ["Failure", "Likely Cause", "First Check"],
                ["403 invalid From domain", "Avaya rejected From domain or caller ID", "AVAYA_FROM_DOMAIN and Avaya domain/adaptation"],
                ["404 no route available", "Avaya cannot route dialed digits", "Dial prefix, number format, Session Manager pattern"],
                ["No audio", "RTP/NAT/firewall/media path", "RTP range, external media address, LOCAL_NET"],
                ["Slow greeting", "Pre-observe or slow uncached TTS", "Observe=0, TTS_TIMEOUT_SECONDS, prompt cache"],
            ],
            [1.6 * inch, 2.4 * inch, 2.7 * inch],
        )
    )
    story.append(PageBreak())
    story.append(p("Operator Runbook", "Heading1"))
    for item in [
        "Open the HTTPS protected UI and choose the campaign.",
        "Review Dashboard readiness, call window, retry time, and eligible contacts.",
        "Import or edit Contacts. Use auto-refresh while monitoring active campaigns.",
        "Confirm caller ID, dial prefix, number format, AI Flow, and Voice Script.",
        "Start campaign, watch Call Log/Diagnostics/Asterisk SIP Trace, then stop campaign when done.",
    ]:
        story.append(bullet(item))
    story.append(p("Backup And Restore", "Heading1"))
    for item in [
        "Back up PostgreSQL with pg_dump.",
        "Store .env securely outside Git.",
        "Back up nginx auth/cert configuration separately.",
        "Recordings and logs are optional private artifacts and should not be published.",
    ]:
        story.append(bullet(item))
    story.append(p("Security", "Heading1"))
    for item in [
        "Do not commit .env, recordings, logs, database dumps, htpasswd files, or real Avaya credentials.",
        "Protect the UI with HTTPS and authentication.",
        "Call only expected or consented contacts.",
        "Use recognizable caller ID, reasonable call windows, and modest retries.",
    ]:
        story.append(bullet(item))
    story.append(p("Release Artifacts", "Heading1"))
    story.append(
        simple_table(
            [
                ["Artifact", "Name"],
                ["GitHub repo", "dblagbro/outdialer-project"],
                ["App image", "dblagbro/outdialer-project-app:v0.1.0 and :latest"],
                ["Asterisk image", "dblagbro/outdialer-project-asterisk:v0.1.0 and :latest"],
                ["PDF", "docs/outdialer-project-guide.pdf"],
            ],
            [1.6 * inch, 5.1 * inch],
        )
    )
    return story


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUT),
        pagesize=letter,
        rightMargin=0.55 * inch,
        leftMargin=0.55 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.6 * inch,
        title="Outdialer Project Guide",
        author="Outdialer Project",
    )
    doc.build(build_story(), onFirstPage=header_footer, onLaterPages=header_footer)
    print(OUT)


if __name__ == "__main__":
    main()
