"""Memory-only A4 renderer for authorized, explicitly non-final snapshots."""
from __future__ import annotations

from html import escape
from io import BytesIO
from hashlib import sha256

from .print_policy import PrintDenied, PrintDocument


def render_pdf(doc: PrintDocument) -> bytes:
    if not isinstance(doc, PrintDocument) or doc.is_signed or doc.released_to_chart:
        raise PrintDenied('Unsigned print policy required')
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, KeepTogether

    stream = BytesIO()
    def annotate(canvas, _):
        canvas.saveState()
        canvas.setFillColor(colors.Color(.72, .18, .18, alpha=.15))
        canvas.setFont('Helvetica-Bold', 9)
        canvas.translate(65, 65)
        canvas.rotate(37)
        canvas.drawString(0, 0, doc.watermark[:100])
        canvas.restoreState()
    styles=getSampleStyleSheet()
    note=ParagraphStyle('rad6_note', parent=styles['Normal'], fontSize=9,
                        textColor=colors.HexColor('#842020'), spaceBefore=12, spaceAfter=12)
    row=ParagraphStyle('rad6_field', parent=styles['Normal'], fontSize=9, leading=13,
                       spaceAfter=8, wordWrap='CJK')
    story=[Paragraph('CLINIC SYSTEM PRO - CONTROLLED DOCUMENT COPY',styles['Heading2']),
           Paragraph(escape(doc.title),styles['Heading3']),
           Paragraph(escape(doc.watermark),note),
           Paragraph(f'Clinic reference: {doc.clinic_id} &nbsp;&nbsp; Record reference: {doc.record_id}',row),
           Paragraph('Source version: '+(str(doc.source_version) if doc.source_version else 'UNVERSIONED LIVE SNAPSHOT'),row)]
    for key,value in doc.fields:
        # Escape *all* server text, preventing PDF markup or arbitrary URLs.
        text=escape(value).replace('\n','<br/>')
        story.append(Paragraph('<b>'+escape(key)+':</b> '+text,row))
    story.extend([Spacer(1,12),Paragraph('SOURCE SHA-256: '+doc.source_digest,note),
                  Paragraph('This copy does not prove payment, clinical verification, diagnosis, signature, delivery, or chart release.',note)])
    try:
        SimpleDocTemplate(stream,pagesize=A4,rightMargin=45,leftMargin=45,
            topMargin=48,bottomMargin=48,pageCompression=0).build(
            story,onFirstPage=annotate,onLaterPages=annotate)
    except Exception as exc:
        raise PrintDenied('Print renderer failed safely') from exc
    value=stream.getvalue()
    if not value.startswith(b'%PDF-') or len(value)>3*1024*1024:
        raise PrintDenied('PDF output exceeded safety bounds')
    return value
